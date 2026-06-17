"""
Heuristic scorer — produces advisory context for each legal action each turn.

This is NOT a decision function. It computes signals that the LLM can reason
over: type matchups, rough damage estimates, speed tiers, status effects,
switch value, and battle phase. The model chooses freely; this surfaces
structure it would otherwise have to infer from raw numbers.

Design rules:
- Advisory, never prescriptive. Notes say "you move first", not "use this".
- Gen 9 mechanics: paralysis reduces speed to 50%, burn halves physical attack,
  18-type chart (includes Fairy), Terastal ignored for damage estimates.
- Damage estimates are rough but directionally correct. They incorporate
  stat stages, weather, STAB, and accuracy — enough to tell a 2HKO from a
  4HKO, not a precise damage calculator.
- All computations are guarded; a None opponent or missing stat never crashes.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle, DoubleBattle, Pokemon, Target
from poke_env.battle.move import Move
from poke_env.battle.move_category import MoveCategory

# ---------------------------------------------------------------------------
# Stat stage multipliers: stage -6..+6
# ---------------------------------------------------------------------------

_STAGE_MULT: dict[int, float] = {
    -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
     0: 1.0,
     1: 3/2,  2: 4/2,  3: 5/2,  4: 6/2,  5: 7/2,  6: 8/2,
}


def _stage_mult(stage: int) -> float:
    return _STAGE_MULT.get(max(-6, min(6, stage)), 1.0)


# ---------------------------------------------------------------------------
# Weather damage modifiers
# ---------------------------------------------------------------------------

# Maps (weather_name, move_type_name) → multiplier
_WEATHER_MODS: dict[tuple[str, str], float] = {
    ("RAINDANCE", "WATER"):  1.5,
    ("RAINDANCE", "FIRE"):   0.5,
    ("SUNNYDAY",  "FIRE"):   1.5,
    ("SUNNYDAY",  "WATER"):  0.5,
    # Sandstorm boosts Rock-type SpD but doesn't modify move damage
    # Hail/Snow has no move-damage modifier
}


def _weather_damage_mod(weather_name: str | None, move_type_name: str) -> float:
    if not weather_name:
        return 1.0
    return _WEATHER_MODS.get((weather_name.upper(), move_type_name.upper()), 1.0)


# ---------------------------------------------------------------------------
# Status effect mechanical impact summaries
# ---------------------------------------------------------------------------

_STATUS_IMPACT: dict[str, str] = {
    "BRN": "Burn: physical attack halved; takes 1/16 HP per turn",
    "PAR": "Paralysis: speed reduced to 50%; 25% chance to be fully paralyzed each turn",
    "PSN": "Poison: takes 1/8 HP per turn",
    "TOX": "Badly Poisoned: damage increases each turn (1/16, 2/16, 3/16…)",
    "SLP": "Sleep: cannot act (1–3 turns in Gen 9)",
    "FRZ": "Frozen: cannot act until thawed (rare thaw chance each turn)",
}

# Status move annotations — what each status inflicts and why it matters
_STATUS_MOVE_EFFECTS: dict[str, dict[str, Any]] = {
    # Sleep
    "spore":        {"inflicts": "SLP", "note": "inflicts Sleep (most reliable — 100% accurate)"},
    "sleeppowder":  {"inflicts": "SLP", "note": "inflicts Sleep (75% accurate)"},
    "hypnosis":     {"inflicts": "SLP", "note": "inflicts Sleep (60% accurate)"},
    "sing":         {"inflicts": "SLP", "note": "inflicts Sleep (55% accurate)"},
    "grasswhistle": {"inflicts": "SLP", "note": "inflicts Sleep (55% accurate)"},
    "lovelykiss":   {"inflicts": "SLP", "note": "inflicts Sleep (75% accurate)"},
    "yawn":         {"inflicts": "SLP", "note": "inflicts Sleep next turn (opponent can switch)"},
    # Paralysis
    "thunderwave":  {"inflicts": "PAR", "note": "inflicts Paralysis (100% accurate) — slows opponent to 50% speed, 25% chance to not act"},
    "stunspore":    {"inflicts": "PAR", "note": "inflicts Paralysis (75% accurate)"},
    "glare":        {"inflicts": "PAR", "note": "inflicts Paralysis (75% accurate) — hits Normal types unlike Thunder Wave"},
    "bodyslam":     {"inflicts": "PAR", "note": "30% paralysis chance on hit"},
    "lick":         {"inflicts": "PAR", "note": "30% paralysis chance on hit"},
    # Burn
    "willowisp":    {"inflicts": "BRN", "note": "inflicts Burn (85% accurate) — halves opponent's physical attack"},
    # Poison
    "toxic":        {"inflicts": "TOX", "note": "inflicts Badly Poisoned — damage escalates each turn; high value in longer battles"},
    "poisonpowder": {"inflicts": "PSN", "note": "inflicts Poison (75% accurate)"},
    "poisongas":    {"inflicts": "PSN", "note": "inflicts Poison (55% accurate)"},
    # Stat boosts — own
    "swordsdance":  {"stat_boost": {"atk": +2}, "note": "raises Attack +2 stages"},
    "nastyplot":    {"stat_boost": {"spa": +2}, "note": "raises Sp. Atk +2 stages"},
    "calmmind":     {"stat_boost": {"spa": +1, "spd": +1}, "note": "raises Sp. Atk and Sp. Def +1 stage each"},
    "dragondance":  {"stat_boost": {"atk": +1, "spe": +1}, "note": "raises Attack and Speed +1 stage each"},
    "bulkup":       {"stat_boost": {"atk": +1, "def": +1}, "note": "raises Attack and Defense +1 stage each"},
    "agility":      {"stat_boost": {"spe": +2}, "note": "raises Speed +2 stages — may enable you to outspeed threats"},
    "amnesia":      {"stat_boost": {"spd": +2}, "note": "raises Sp. Def +2 stages"},
    "growth":       {"stat_boost": {"spa": +1}, "note": "raises Sp. Atk +1 stage"},
    "meditate":     {"stat_boost": {"atk": +1}, "note": "raises Attack +1 stage"},
    "sharpen":      {"stat_boost": {"atk": +1}, "note": "raises Attack +1 stage"},
    "workup":       {"stat_boost": {"atk": +1, "spa": +1}, "note": "raises Attack and Sp. Atk +1 stage each"},
    "batonpass":    {"baton_pass": True, "note": "passes stat boosts/drops to the next ally — use while boosted"},
    # Stat drops — opponent
    "screech":      {"stat_drop": {"def": -2}, "note": "lowers opponent Defense -2 stages — amplifies physical moves"},
    "charm":        {"stat_drop": {"atk": -2}, "note": "lowers opponent Attack -2 stages — reduces physical damage taken"},
    "growl":        {"stat_drop": {"atk": -1}, "note": "lowers opponent Attack -1 stage"},
    "leer":         {"stat_drop": {"def": -1}, "note": "lowers opponent Defense -1 stage"},
    "tickle":       {"stat_drop": {"atk": -1, "def": -1}, "note": "lowers opponent Attack and Defense -1 stage each"},
    "stringshot":   {"stat_drop": {"spe": -1}, "note": "lowers opponent Speed -1 stage"},
    "featherdance": {"stat_drop": {"atk": -2}, "note": "lowers opponent Attack -2 stages"},
    "sweetscent":   {"stat_drop": {"eva": -1}, "note": "lowers opponent evasion -1 stage"},
    # Utility
    "recover":   {"heal": 0.5, "note": "restores 50% of max HP"},
    "softboiled":{"heal": 0.5, "note": "restores 50% of max HP"},
    "moonlight": {"heal": 0.5, "note": "restores HP (50% normally, more in Sun, less in Sand/Rain)"},
    "morningsun":{"heal": 0.5, "note": "restores HP (50% normally, more in Sun, less in Sand/Rain)"},
    "synthesis": {"heal": 0.5, "note": "restores HP (50% normally, more in Sun, less in Sand/Rain)"},
    "wish":      {"heal": 0.5, "note": "heals ally next turn — use while healthy so the ally benefits"},
    "lightscreen":{"screen": "spa", "note": "halves special damage for 5 turns for your side"},
    "reflect":   {"screen": "atk", "note": "halves physical damage for 5 turns for your side"},
    "substitute":{"substitute": True, "note": "creates a 25% HP decoy — blocks status and chip damage"},
    "leechseed": {"inflicts": "SEED", "note": "drains 1/8 HP per turn from opponent; wasted on Grass types"},
    "spikes":    {"hazard": True, "note": "lays entry hazard — damages grounded opponents on switch-in"},
    "rapidspin": {"hazard": True, "note": "removes entry hazards and Leech Seed from your side"},
    "perishsong":{"perish": True, "note": "both active Pokémon faint in 3 turns unless switched"},
    "encore":    {"encore": True, "note": "forces opponent to repeat their last move for 3 turns"},
    "taunt":     {"taunt": True, "note": "prevents opponent from using status moves for 3 turns"},
    "protect":   {"protect": True, "note": "blocks all moves this turn — good for scouting or stalling"},
    "detect":    {"protect": True, "note": "blocks all moves this turn — same effect as Protect"},
    "roar":      {"phazing": True, "note": "forces opponent to switch; erases their stat boosts"},
    "whirlwind": {"phazing": True, "note": "forces opponent to switch; erases their stat boosts"},
    "haze":      {"haze": True, "note": "resets all stat stages for both sides to zero"},
    "trick":     {"trick": True, "note": "swaps held items with opponent — devastating if you hold a Choice item"},
    "knockoff":  {"knockoff": True, "note": "removes opponent's held item permanently"},
    "spite":     {"spite": True, "note": "reduces PP of opponent's last used move by 4"},
    "painsplit": {"painsplit": True, "note": "averages HP between both active Pokémon — best when opponent is high HP"},
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score_actions(battle: AbstractBattle) -> dict[str, Any]:
    """Return scored move and switch options for the current battle state."""
    own = battle.active_pokemon
    opp = battle.opponent_active_pokemon
    weather = _current_weather(battle)

    move_scores = [
        _score_move(move, own, opp, battle, weather)
        for move in battle.available_moves
    ]
    switch_scores = [
        _score_switch(mon, own, opp, battle)
        for mon in battle.available_switches
    ]

    return {
        "battle_context": _battle_context(own, opp, battle, weather),
        "move_scores": move_scores,
        "switch_scores": switch_scores,
    }


# ---------------------------------------------------------------------------
# Battle context — top-level advisory
# ---------------------------------------------------------------------------

def _current_weather(battle: AbstractBattle) -> str | None:
    try:
        weather = battle.weather
        if not weather:
            return None
        key = next(iter(weather))
        return key.name if hasattr(key, "name") else str(key)
    except (StopIteration, AttributeError, TypeError):
        return None


def _effective_speed(mon: Pokemon, is_own: bool) -> float:
    """Estimate effective speed accounting for stat stages and paralysis."""
    try:
        if is_own:
            raw = (mon.stats or {}).get("spe")
            base_spd = float(raw) if isinstance(raw, int | float) else float(mon.base_stats.get("spe", 80))
        else:
            base_spd = float(mon.base_stats.get("spe", 80))
        stage_raw = mon.boosts.get("spe", 0)
        stage = int(stage_raw) if isinstance(stage_raw, int | float) else 0
        spd = base_spd * _stage_mult(stage)
        # Gen 9: paralysis reduces speed to 50%
        if mon.status and mon.status.name == "PAR":
            spd *= 0.5
        return spd
    except (TypeError, ValueError, AttributeError):
        return 80.0  # safe fallback


def _remaining_count(battle: AbstractBattle, own: bool) -> int:
    """Count non-fainted Pokémon (including active) on the given side."""
    if own:
        team = battle.team
    else:
        team = battle.opponent_team
    return sum(1 for p in team.values() if not p.fainted)


def _active_matchup_quality(own: Pokemon | None, opp: Pokemon | None) -> str:
    """Classify the current type matchup as favorable / neutral / disadvantaged."""
    if own is None or opp is None:
        return "unknown"
    # Check how our own STAB types hit the opponent
    own_offense_mult = max(
        (opp.damage_multiplier(t) for t in own.types),
        default=1.0,
    )
    # Check how opponent STAB types hit us
    opp_offense_mult = max(
        (own.damage_multiplier(t) for t in opp.types),
        default=1.0,
    )
    if own_offense_mult >= 2.0 and opp_offense_mult < 2.0:
        return "favorable"
    if opp_offense_mult >= 2.0 and own_offense_mult < 2.0:
        return "disadvantaged"
    if own_offense_mult >= 2.0 and opp_offense_mult >= 2.0:
        return "double-edged"
    return "neutral"


def _estimate_incoming_damage(
    move: Move,
    attacker: Pokemon,
    defender: Pokemon,
    weather: str | None,
) -> float | None:
    """Estimate damage % dealt to *defender* by *attacker* using *move*.

    Mirrors the formula in ``_score_move`` but from the opponent's perspective.
    Returns a float (percentage of defender's HP) or None on any error.
    """
    try:
        is_physical = move.category == MoveCategory.PHYSICAL
        atk_key = "atk" if is_physical else "spa"
        def_key = "def" if is_physical else "spd"

        # Attacker's offensive stat — base stat only (opponent stats are unknown)
        opp_atk_base = float(attacker.base_stats.get(atk_key, 80))
        opp_atk_stage = attacker.boosts.get(atk_key, 0)
        opp_atk = opp_atk_base * _stage_mult(int(opp_atk_stage))

        # Defender's defensive stat — use actual stats when available
        defender_stats = defender.stats or {}
        own_def_base = float(
            defender_stats.get(def_key)
            or defender.base_stats.get(def_key, 80)
        )
        own_def_stage = defender.boosts.get(def_key, 0)
        own_def = own_def_base * _stage_mult(int(own_def_stage))

        type_mult = defender.damage_multiplier(move)
        if type_mult == 0.0:
            return 0.0

        move_type_name = move.type.name if hasattr(move, "type") else ""
        w_mod = _weather_damage_mod(weather, move_type_name)

        # Damage formula (level 100, no crit, no random roll)
        raw = ((42 * move.base_power * opp_atk / own_def) / 50 + 2) * type_mult * w_mod

        # Defender HP pool — use actual HP stat if known, else base-stat approximation
        own_hp = float(
            defender_stats.get("hp")
            or (defender.base_stats.get("hp", 80) * 2 + 110)
        )
        return raw / own_hp * 100
    except Exception:  # noqa: BLE001
        return None


def _battle_context(
    own: Pokemon | None,
    opp: Pokemon | None,
    battle: AbstractBattle,
    weather: str | None,
) -> dict[str, Any]:
    # Pre-populate all optional keys with None so Jinja2 templates can safely
    # use `{% if ctx.key %}` without raising UndefinedError under StrictUndefined.
    ctx: dict[str, Any] = {
        "speed": None,
        "active_matchup": None,
        "phase": None,
        "own_remaining": None,
        "opp_remaining": None,
        "weather": None,
        "weather_note": None,
        "own_status_impact": None,
        "opp_status": None,
        "opp_status_impact": None,
        "ko_risk_note": None,  # set when opponent's last move threatens a KO
        "tera_note": None,  # set when Terastallize is available and strategically relevant
    }

    # Speed comparison
    if own is not None and opp is not None:
        own_spd = _effective_speed(own, is_own=True)
        opp_spd = _effective_speed(opp, is_own=False)
        faster = own_spd > opp_spd
        speed_note = (
            f"You move FIRST (est. {own_spd:.0f} vs {opp_spd:.0f})"
            if faster
            else (
                f"You move SECOND (est. {own_spd:.0f} vs {opp_spd:.0f})"
                if own_spd < opp_spd
                else f"Speed tie (est. {own_spd:.0f} vs {opp_spd:.0f}) — RNG decides order"
            )
        )
        ctx["speed"] = {
            "you_move_first": faster,
            "speed_tie": own_spd == opp_spd,
            "own_speed_estimate": round(own_spd),
            "opp_speed_estimate": round(opp_spd),
            "note": speed_note,
        }

    # Remaining Pokémon (battle phase)
    try:
        own_remaining = _remaining_count(battle, own=True)
        opp_remaining = _remaining_count(battle, own=False)
        ctx["own_remaining"] = own_remaining
        ctx["opp_remaining"] = opp_remaining
        if own_remaining == 1 and opp_remaining > 1:
            ctx["phase"] = "endgame_behind"
        elif opp_remaining == 1 and own_remaining > 1:
            ctx["phase"] = "endgame_ahead"
        elif own_remaining == 1 and opp_remaining == 1:
            ctx["phase"] = "endgame_last_vs_last"
        elif own_remaining <= 2 or opp_remaining <= 2:
            ctx["phase"] = "late"
        elif own_remaining + opp_remaining <= 6:
            ctx["phase"] = "midgame"
        else:
            ctx["phase"] = "early"
    except Exception:  # noqa: BLE001
        pass

    # Active matchup quality
    if own is not None and opp is not None:
        ctx["active_matchup"] = _active_matchup_quality(own, opp)

    # Weather
    if weather:
        ctx["weather"] = weather
        if weather == "SANDSTORM":
            ctx["weather_note"] = "Sandstorm: non-Rock/Steel/Ground types take 1/16 HP per turn"
        elif weather == "HAIL":
            ctx["weather_note"] = "Hail: non-Ice types take 1/16 HP per turn"
        elif weather == "RAINDANCE":
            ctx["weather_note"] = "Rain: Water moves ×1.5, Fire moves ×0.5"
        elif weather == "SUNNYDAY":
            ctx["weather_note"] = "Sun: Fire moves ×1.5, Water moves ×0.5"

    # Own status impact
    if own is not None and own.status:
        impact = _STATUS_IMPACT.get(own.status.name)
        if impact:
            ctx["own_status_impact"] = impact

    # Opponent status (for evaluating status move value)
    if opp is not None and opp.status:
        ctx["opp_status"] = opp.status.name
        ctx["opp_status_impact"] = _STATUS_IMPACT.get(opp.status.name, opp.status.name)

    # KO risk: estimate whether the opponent's last-used move can KO us this turn.
    # Uses our actual stats (if available) and the opponent's base stats for the estimate.
    if own is not None and opp is not None:
        try:
            opp_last = opp.last_move
            if opp_last is not None and opp_last.category != MoveCategory.STATUS and opp_last.base_power > 0:
                incoming_pct = _estimate_incoming_damage(opp_last, opp, own, weather)
                if incoming_pct is not None:
                    own_hp_pct = own.current_hp_fraction * 100
                    move_name = opp_last.id.replace("_", " ").title()
                    if incoming_pct >= own_hp_pct:
                        ctx["ko_risk_note"] = (
                            f"⚠ KO RISK: opponent's last move ({move_name}) estimated "
                            f"~{incoming_pct:.0f}% damage — at {own_hp_pct:.0f}% HP "
                            f"you will likely be KO'd if they use it again. Consider switching."
                        )
                    elif incoming_pct >= own_hp_pct * 0.75:
                        ctx["ko_risk_note"] = (
                            f"Damage risk: opponent's last move ({move_name}) estimated "
                            f"~{incoming_pct:.0f}% — at {own_hp_pct:.0f}% HP you may survive "
                            f"one more hit, but barely. Prioritize finishing them or switching."
                        )
        except Exception:  # noqa: BLE001
            pass

    # Terastallization advisory — surfaces when the player can still Tera this battle.
    try:
        can_tera = bool(getattr(battle, "can_tera", False))
        if can_tera and own is not None:
            tera_type = getattr(own, "tera_type", None)
            if tera_type is not None:
                tera_name = tera_type.name
                # Determine STAB bonus: Tera same-type = 2× STAB; new type = 1.5× STAB
                base_types = {getattr(own, "_type_1", None), getattr(own, "_type_2", None)} - {None}
                same_type = tera_type in base_types
                stab_note = "same as base typing (2× STAB bonus)" if same_type else "new type (1.5× STAB on matching moves)"
                # Defensive benefit: check if Tera type changes the matchup vs current opponent
                if opp is not None:
                    # Use a lightweight proxy: would the Tera type resist the opponent's best move?
                    from poke_env.battle.move_category import MoveCategory as _MC
                    opp_moves = list(opp.moves.values())
                    opp_damaging = [m for m in opp_moves if m.category != _MC.STATUS and m.base_power > 0]
                    if opp_damaging:
                        # Mock a temporary check using poke-env's type chart
                        from poke_env.data.gen_data import GenData as _GD
                        _gen = _GD.from_gen(9)
                        tera_name_lower = tera_name.lower()
                        type_chart = _gen.type_chart
                        def _defending_mult(atk_type_name: str) -> float:
                            row = type_chart.get(atk_type_name.upper(), {})
                            return float(row.get(tera_name_lower.upper(), 1.0))
                        worst_mult = max(
                            (_defending_mult(m.type.name) for m in opp_damaging if hasattr(m, "type")),
                            default=1.0,
                        )
                        if worst_mult <= 0.5:
                            def_note = f"resists opponent's known moves as {tera_name}"
                        elif worst_mult >= 2.0:
                            def_note = f"still weak to opponent's moves as {tera_name} — no defensive benefit"
                        else:
                            def_note = f"neutral or better vs opponent's known moves as {tera_name}"
                    else:
                        def_note = "opponent moves not yet revealed"
                    ctx["tera_note"] = (
                        f"TERA AVAILABLE: Your {own.species} can Terastallize to {tera_name} type "
                        f"({stab_note}; defense: {def_note}). "
                        f"Use action_type 'tera_move' to Terastallize and attack in one action."
                    )
                else:
                    ctx["tera_note"] = (
                        f"TERA AVAILABLE: Your {own.species} can Terastallize to {tera_name} type "
                        f"({stab_note}). Use action_type 'tera_move' to Terastallize and attack."
                    )
    except Exception:  # noqa: BLE001
        pass

    return ctx


# ---------------------------------------------------------------------------
# Move scoring
# ---------------------------------------------------------------------------

def _score_move(
    move: Move,
    own: Pokemon | None,
    opp: Pokemon | None,
    battle: AbstractBattle,
    weather: str | None,
) -> dict[str, Any]:
    try:
        priority = move.priority
    except KeyError:
        priority = 0  # pseudo-moves like 'recharge' have no priority entry

    # PP warning
    low_pp = False
    try:
        if move.max_pp > 0 and (move.current_pp / move.max_pp) <= 0.25:
            low_pp = True
    except (AttributeError, ZeroDivisionError):
        pass

    score: dict[str, Any] = {
        "move_id": move.id,
        "type_multiplier": None,
        "effectiveness_label": "unknown",
        "estimated_damage_pct": None,
        "accuracy_adjusted_pct": None,
        "priority": priority,
        "is_status": move.category == MoveCategory.STATUS,
        "low_pp": low_pp,
        "notes": [],
    }

    if low_pp:
        score["notes"].append(f"LOW PP ({move.current_pp}/{move.max_pp}) — consider conserving")

    if move.category == MoveCategory.STATUS:
        score["effectiveness_label"] = "status"
        _annotate_status_move(move, score, opp, own)
        return score

    if opp is None:
        return score

    # Type effectiveness
    mult = opp.damage_multiplier(move)
    score["type_multiplier"] = mult
    score["effectiveness_label"] = _effectiveness_label(mult)

    if mult == 0.0:
        score["notes"].append("immune — will deal no damage")
        score["estimated_damage_pct"] = "0%"
        score["accuracy_adjusted_pct"] = "0%"
        return score

    # Weather modifier on this move type
    try:
        move_type_name = move.type.name
    except AttributeError:
        move_type_name = ""
    weather_mod = _weather_damage_mod(weather, move_type_name)

    # Accuracy fraction (True means always hits — e.g. Swift)
    try:
        acc = move.accuracy
        acc_frac = 1.0 if acc is True else float(acc) / 100.0
    except (AttributeError, TypeError, ValueError):
        acc_frac = 1.0

    # Rough damage estimate — standard formula simplified for advisory use
    if own is not None:
        own_stats = own.stats or {}
        is_physical = move.category == MoveCategory.PHYSICAL
        atk_key = "atk" if is_physical else "spa"
        def_key = "def" if is_physical else "spd"

        own_atk_base = own_stats.get(atk_key) or own.base_stats.get(atk_key, 80)
        opp_def_base = opp.base_stats.get(def_key, 80)

        # Stat stages
        own_atk_stage = own.boosts.get(atk_key, 0)
        opp_def_stage  = opp.boosts.get(def_key, 0)
        own_atk = own_atk_base * _stage_mult(own_atk_stage)
        opp_def = opp_def_base * _stage_mult(opp_def_stage)

        # Burn halves physical attack
        if is_physical and own.status and own.status.name == "BRN":
            own_atk *= 0.5

        # Damage formula (level 100, no crit, no random roll), with weather
        raw = ((42 * move.base_power * own_atk / opp_def) / 50 + 2) * mult * weather_mod

        # Express as % of a typical opponent HP pool (base HP × approx level-100 multiplier)
        opp_hp_approx = opp.base_stats.get("hp", 80) * 2 + 110
        pct = min(raw / opp_hp_approx * 100, 999)
        score["estimated_damage_pct"] = f"~{pct:.0f}%"
        score["accuracy_adjusted_pct"] = f"~{pct * acc_frac:.0f}%"

        if pct >= 100:
            score["notes"].append("likely OHKO")
        elif pct >= 50:
            score["notes"].append("likely 2HKO")
        elif pct >= 34:
            score["notes"].append("likely 3HKO")

        # Burn note for physical moves
        if is_physical and own.status and own.status.name == "BRN":
            score["notes"].append("Burn halves your Attack — physical damage is reduced")

    if weather_mod != 1.0:
        mod_label = f"×{weather_mod:.1f} ({weather} boost)" if weather_mod > 1 else f"×{weather_mod:.1f} ({weather} penalty)"
        score["notes"].append(f"Weather modifier: {mod_label}")

    if acc_frac < 1.0:
        score["notes"].append(f"Accuracy: {int(acc_frac * 100)}% — miss rate introduces variance")

    if priority > 0:
        score["notes"].append(f"Priority +{priority} — moves before non-priority attacks regardless of speed")

    # Speed note — does priority change who attacks first?
    if priority == 0 and own is not None and opp is not None:
        own_spd = _effective_speed(own, is_own=True)
        opp_spd = _effective_speed(opp, is_own=False)
        if own_spd > opp_spd:
            score["notes"].append("You move first this turn")
        elif own_spd < opp_spd:
            score["notes"].append("Opponent moves first — you attack after taking damage")
        else:
            score["notes"].append("Speed tie — move order is random (50/50)")

    # STAB
    if own is not None and move.type in own.types:
        score["notes"].append("STAB")

    return score


# ---------------------------------------------------------------------------
# Status move annotation (expanded)
# ---------------------------------------------------------------------------

def _annotate_status_move(
    move: Move,
    score: dict[str, Any],
    opp: Pokemon | None,
    own: Pokemon | None,
) -> None:
    mid = move.id
    entry = _STATUS_MOVE_EFFECTS.get(mid)

    if entry is None:
        # Unknown status move — generic fallback
        score["notes"].append("status move")
        return

    score["notes"].append(entry["note"])

    # Status infliction check — wasted if opponent already has a status
    inflicts = entry.get("inflicts")
    if inflicts and inflicts not in ("SEED",) and opp is not None and opp.status is not None:
        score["notes"].append(
            f"⚠ Opponent already has {opp.status.name} — status moves cannot stack; this would be wasted"
        )

    # Boost moves — show current stage and diminishing value
    stat_boost = entry.get("stat_boost")
    if stat_boost and own is not None:
        stage_notes = []
        for stat, delta in stat_boost.items():
            current = own.boosts.get(stat, 0)
            new_stage = min(6, current + delta)
            if current >= 6:
                stage_notes.append(f"{stat} already at +6 (max) — no further effect")
            else:
                mult_now = _stage_mult(current)
                mult_after = _stage_mult(new_stage)
                gain_pct = int((mult_after / mult_now - 1) * 100)
                stage_notes.append(f"{stat} {current:+d} → {new_stage:+d} (+{gain_pct}% effective stat)")
        if stage_notes:
            score["notes"].extend(stage_notes)

    # Stat drop moves — show opponent's current stage
    stat_drop = entry.get("stat_drop")
    if stat_drop and opp is not None:
        drop_notes = []
        for stat, delta in stat_drop.items():
            current = opp.boosts.get(stat, 0)
            new_stage = max(-6, current + delta)
            if current <= -6:
                drop_notes.append(f"Opponent {stat} already at -6 (min) — no further effect")
            else:
                mult_now = _stage_mult(current)
                mult_after = _stage_mult(new_stage)
                reduction_pct = int((1 - mult_after / mult_now) * 100)
                drop_notes.append(f"Opponent {stat} {current:+d} → {new_stage:+d} (-{reduction_pct}% effective stat)")
        if drop_notes:
            score["notes"].extend(drop_notes)

    # Healing moves — context based on own HP
    heal_frac = entry.get("heal")
    if heal_frac is not None and own is not None:
        hp = own.current_hp_fraction
        if hp >= 0.875:
            score["notes"].append(f"HP is high ({int(hp * 100)}%) — limited recovery value right now")
        elif hp <= 0.5:
            score["notes"].append(f"HP is low ({int(hp * 100)}%) — high recovery value")


def _effectiveness_label(mult: float) -> str:
    if mult == 0.0:
        return "immune (0×)"
    if mult >= 4.0:
        return "super effective (4×)"
    if mult >= 2.0:
        return "super effective (2×)"
    if mult == 1.0:
        return "neutral (1×)"
    if mult <= 0.25:
        return "not very effective (0.25×)"
    return "not very effective (0.5×)"


# ---------------------------------------------------------------------------
# Switch scoring
# ---------------------------------------------------------------------------

def _score_switch(
    incoming: Pokemon,
    own: Pokemon | None,
    opp: Pokemon | None,
    battle: AbstractBattle,
) -> dict[str, Any]:
    score: dict[str, Any] = {
        "species": incoming.species,
        "hp_fraction": round(incoming.current_hp_fraction, 3),
        "switch_quality": 0,   # integer from -3 (bad) to +3 (excellent)
        # defensive_vs_opp: how the incoming mon fares against the opponent's last move /
        # STAB types. One of: "immune", "resists", "neutral", "weak", "unknown"
        "defensive_vs_opp": "unknown",
        # speed_vs_opp: whether incoming is faster, slower, or similar speed to opponent
        "speed_vs_opp": None,
        "notes": [],
    }

    if incoming.fainted:
        score["notes"].append("fainted — cannot be sent out")
        score["switch_quality"] = -3
        return score

    # HP penalty: low HP bench mons have limited switch value
    hp = incoming.current_hp_fraction
    if hp < 0.25:
        score["switch_quality"] -= 2
        score["notes"].append(f"Very low HP ({int(hp * 100)}%) — high risk if switched in")
    elif hp < 0.5:
        score["switch_quality"] -= 1
        score["notes"].append(f"Moderate HP ({int(hp * 100)}%)")
    else:
        score["notes"].append(f"Healthy HP ({int(hp * 100)}%)")

    if opp is None:
        return score

    # Opponent threat types: STAB types + revealed move types
    opp_threat_types = {m.type for m in opp.moves.values() if m.category != MoveCategory.STATUS}
    opp_threat_types.update(opp.types)

    resists, weak_to, immune_to = [], [], []
    for t in opp_threat_types:
        mult = incoming.damage_multiplier(t)
        if mult == 0.0:
            immune_to.append(t.name)
            score["switch_quality"] += 1
        elif mult <= 0.5:
            resists.append(t.name)
            score["switch_quality"] += 1
        elif mult >= 2.0:
            weak_to.append(t.name)
            score["switch_quality"] -= 1

    if immune_to:
        score["notes"].append(f"Immune to {', '.join(immune_to)}")
        score["defensive_vs_opp"] = "immune"
    elif resists and not weak_to:
        score["defensive_vs_opp"] = "resists"
    elif weak_to and not resists:
        score["defensive_vs_opp"] = "weak"
    elif not weak_to and not resists and not immune_to:
        score["defensive_vs_opp"] = "neutral"
    # else mixed — leave as "unknown"

    if resists:
        score["notes"].append(f"Resists {', '.join(resists)}")
    if weak_to:
        score["notes"].append(f"Weak to {', '.join(weak_to)}")

    # Speed comparison vs opponent — helps the model decide whether it gets a free
    # hit on switch-in or eats a hit first.
    try:
        incoming_spd = _effective_speed(incoming, is_own=True)
        opp_spd = _effective_speed(opp, is_own=False)
        if incoming_spd > opp_spd * 1.05:
            score["speed_vs_opp"] = f"faster ({incoming_spd:.0f} vs ~{opp_spd:.0f})"
        elif incoming_spd < opp_spd * 0.95:
            score["speed_vs_opp"] = f"slower ({incoming_spd:.0f} vs ~{opp_spd:.0f})"
        else:
            score["speed_vs_opp"] = f"similar speed ({incoming_spd:.0f} vs ~{opp_spd:.0f})"
    except Exception:  # noqa: BLE001
        pass

    # Offensive type coverage vs opponent
    hitting_types = []
    for t in incoming.types:
        opp_mult = opp.damage_multiplier(t)
        if opp_mult >= 2.0:
            hitting_types.append(t.name)
            score["switch_quality"] += 1

    if hitting_types:
        score["notes"].append(f"{', '.join(hitting_types)} type(s) hit opponent super effectively")

    # Context: is the current active mon in a bad matchup? (high switch incentive)
    if own is not None:
        current_matchup = _active_matchup_quality(own, opp)
        if current_matchup == "disadvantaged":
            score["notes"].append("Active Pokémon is in a disadvantaged matchup — switching out has high value")
            score["switch_quality"] += 1
        elif current_matchup == "favorable":
            score["notes"].append("Active Pokémon has type advantage — consider staying in")

    # Own status: burned/paralyzed active mon may be worth replacing
    if own is not None and own.status:
        status_name = own.status.name
        if status_name == "BRN" and _is_primarily_physical(own):
            score["notes"].append("Active mon is burned (attack halved) — switching may recover offensive pressure")
            score["switch_quality"] += 1
        elif status_name == "PAR":
            score["notes"].append("Active mon is paralyzed (50% speed) — switching avoids full-paralysis turns")

    # Clamp switch_quality to [-3, +3]
    score["switch_quality"] = max(-3, min(3, score["switch_quality"]))

    # Human-readable quality label
    sq = score["switch_quality"]
    if sq >= 2:
        score["quality_label"] = "excellent switch"
    elif sq == 1:
        score["quality_label"] = "good switch"
    elif sq == 0:
        score["quality_label"] = "neutral"
    elif sq == -1:
        score["quality_label"] = "risky switch"
    else:
        score["quality_label"] = "poor switch"

    return score


def _is_primarily_physical(mon: Pokemon) -> bool:
    """Heuristic: is this mon's damage profile mainly physical?"""
    phys_bp = sum(
        m.base_power for m in mon.moves.values()
        if m.category == MoveCategory.PHYSICAL
    )
    spec_bp = sum(
        m.base_power for m in mon.moves.values()
        if m.category == MoveCategory.SPECIAL
    )
    if phys_bp + spec_bp == 0:
        return mon.base_stats.get("atk", 0) > mon.base_stats.get("spa", 0)
    return phys_bp > spec_bp


# ---------------------------------------------------------------------------
# Doubles targeting helpers
# ---------------------------------------------------------------------------

# Move targets that hit all adjacent foes automatically (no explicit target needed).
_SPREAD_FOE_TARGETS: frozenset[Target] = frozenset({
    Target.ALL_ADJACENT_FOES,
    Target.FOE_SIDE,
    Target.ALL,
})

# Move targets that hit all adjacent Pokémon including the ally — warn the player.
_HIT_ALLY_TARGETS: frozenset[Target] = frozenset({
    Target.ALL_ADJACENT,
})

# Move targets that are fully self-managed (no player choice needed).
_AUTO_TARGETS: frozenset[Target] = frozenset({
    Target.SELF,
    Target.ALLY_SIDE,
    Target.ALLY_TEAM,
    Target.ALLIES,
    Target.ALL,
    Target.ALL_ADJACENT_FOES,
    Target.FOE_SIDE,
    Target.RANDOM_NORMAL,
    Target.SCRIPTED,
    Target.ALL_ADJACENT,
})

# Move targets that require or permit an explicit foe choice.
_CHOOSE_FOE_TARGETS: frozenset[Target] = frozenset({
    Target.NORMAL,
    Target.ADJACENT_FOE,
    Target.ANY,
})

# Move targets that can be directed at the ally.
_ALLY_TARGETS: frozenset[Target] = frozenset({
    Target.ADJACENT_ALLY,
    Target.ADJACENT_ALLY_OR_SELF,
    Target.ANY,
})


def _move_target_hint(move: Move, has_ally: bool) -> dict[str, Any]:
    """Return targeting metadata for one move in a doubles context.

    Returns a dict with:
      - ``targeting``: one of ``"spread_foes"``, ``"hits_ally_too"``,
        ``"choose_foe"``, ``"choose_foe_or_ally"``, ``"self_or_ally"``,
        ``"auto"``
      - ``target_note``: human-readable advisory string
    """
    try:
        deduced = move.deduced_target
    except Exception:  # noqa: BLE001
        deduced = None

    # Hits both foes simultaneously (spread) — no explicit target needed
    if deduced in _SPREAD_FOE_TARGETS:
        return {
            "targeting": "spread_foes",
            "target_note": "Hits all opponents automatically — no target needed",
        }

    # Hits all adjacent INCLUDING ally — warn
    if deduced in _HIT_ALLY_TARGETS:
        ally_note = " (⚠ ALSO HITS ALLY)" if has_ally else ""
        return {
            "targeting": "hits_ally_too",
            "target_note": f"Spread: hits all adjacent Pokémon — both foes AND your ally{ally_note}",
        }

    # Choose one foe, or optionally the ally
    if deduced == Target.ANY:
        return {
            "targeting": "choose_foe_or_ally",
            "target_note": 'Choose target: "foe_1", "foe_2", or "ally"',
        }

    # Ally-targeting only (e.g. Helping Hand, Heal Pulse)
    if deduced in {Target.ADJACENT_ALLY, Target.ADJACENT_ALLY_OR_SELF}:
        if has_ally:
            options = '"ally"' if deduced == Target.ADJACENT_ALLY else '"ally" or "self"'
            return {
                "targeting": "self_or_ally",
                "target_note": f"Targets an ally — choose {options}",
            }
        return {
            "targeting": "self_or_ally",
            "target_note": "Would target ally, but no ally is present — no target",
        }

    # Normal single-target foe moves
    if deduced in _CHOOSE_FOE_TARGETS:
        return {
            "targeting": "choose_foe",
            "target_note": 'Choose target: "foe_1" or "foe_2"',
        }

    # Self-targeted or auto-targeted
    return {
        "targeting": "auto",
        "target_note": "Auto-targeted — no target needed",
    }


def _score_move_doubles(
    move: Move,
    own: Pokemon | None,
    slot_idx: int,
    opp_1: Pokemon | None,
    opp_2: Pokemon | None,
    ally: Pokemon | None,
    battle: DoubleBattle,
    weather: str | None,
) -> dict[str, Any]:
    """Score a move in a doubles context, adding targeting metadata."""
    has_ally = ally is not None and not ally.fainted

    # Reuse the singles scorer against the primary foe (opp_1) for damage estimates.
    # We pick the opponent with the weaker defense as the primary target hint.
    primary_opp = opp_1
    if opp_1 is not None and opp_2 is not None and move.category != MoveCategory.STATUS:
        try:
            is_phys = move.category == MoveCategory.PHYSICAL
            def_key = "def" if is_phys else "spd"
            def1 = opp_1.base_stats.get(def_key, 80)
            def2 = opp_2.base_stats.get(def_key, 80)
            primary_opp = opp_1 if def1 <= def2 else opp_2
        except Exception:  # noqa: BLE001
            pass

    score = _score_move(move, own, primary_opp, battle, weather)

    # Add doubles-specific targeting metadata
    hint = _move_target_hint(move, has_ally)
    score["targeting"] = hint["targeting"]
    score["target_note"] = hint["target_note"]

    # If spread but hits ally too — add damage estimate vs ally
    if hint["targeting"] == "hits_ally_too" and ally is not None and own is not None:
        try:
            ally_mult = ally.damage_multiplier(move)
            if ally_mult > 0 and move.base_power > 0:
                score["notes"].append(
                    f"⚠ ALLY HIT: {_effectiveness_label(ally_mult)} vs your "
                    f"{ally.species} — factor this into the decision"
                )
        except Exception:  # noqa: BLE001
            pass

    return score


def score_doubles_actions(battle: DoubleBattle) -> dict[str, Any]:
    """Return scored actions for both active slots in a doubles battle."""
    active = battle.active_pokemon  # List[Optional[Pokemon]], len 2
    opp_active = battle.opponent_active_pokemon  # List[Optional[Pokemon]], len 2

    own_0 = active[0] if len(active) > 0 else None
    own_1 = active[1] if len(active) > 1 else None
    opp_0 = opp_active[0] if len(opp_active) > 0 else None
    opp_1 = opp_active[1] if len(opp_active) > 1 else None
    weather = _current_weather(battle)

    raw_moves: list[list[Move]] = battle.available_moves
    raw_switches: list[list[Pokemon]] = battle.available_switches

    def _slot_scores(slot_idx: int, own: Pokemon | None, ally: Pokemon | None) -> dict[str, Any]:
        slot_moves = raw_moves[slot_idx] if slot_idx < len(raw_moves) else []
        slot_switches = raw_switches[slot_idx] if slot_idx < len(raw_switches) else []
        move_scores = [
            _score_move_doubles(m, own, slot_idx, opp_0, opp_1, ally, battle, weather)
            for m in slot_moves
        ]
        switch_scores = [
            _score_switch(mon, own, opp_0, battle)
            for mon in slot_switches
        ]
        # For switch scores in doubles, also note vs the second opponent
        for mon, ss in zip(slot_switches, switch_scores, strict=False):
            if opp_1 is not None:
                opp_1_mult = max(
                    (mon.damage_multiplier(t) for t in opp_1.types), default=1.0
                )
                if opp_1_mult >= 2.0:
                    ss["notes"].append(
                        f"STAB-type advantage vs {opp_1.species} (foe_2) too"
                    )
        return {
            "move_scores": move_scores,
            "switch_scores": switch_scores,
        }

    slot_0_scores = _slot_scores(0, own_0, own_1)
    slot_1_scores = _slot_scores(1, own_1, own_0)

    # Doubles battle context: simplified (no single speed comparison in 4-mon field)
    ctx: dict[str, Any] = {
        "weather": weather,
        "foe_1_species": opp_0.species if opp_0 else None,
        "foe_2_species": opp_1.species if opp_1 else None,
        "foe_1_hp_pct": round(opp_0.current_hp_fraction * 100) if opp_0 else None,
        "foe_2_hp_pct": round(opp_1.current_hp_fraction * 100) if opp_1 else None,
    }

    return {
        "battle_context": ctx,
        "slot_0": slot_0_scores,
        "slot_1": slot_1_scores,
    }
