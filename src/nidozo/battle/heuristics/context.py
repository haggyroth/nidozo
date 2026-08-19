"""Battle context — the top-level advisory block handed to the prompt builder.

Answers the questions a player asks before picking an action: who moves first,
what phase the battle is in, how the active matchup reads, what the weather and
statuses are doing, and whether we are about to get KO'd.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle, Pokemon
from poke_env.battle.move_category import MoveCategory

from nidozo.battle.heuristics.damage import _effective_speed, _estimate_incoming_damage
from nidozo.battle.heuristics.status import _STATUS_IMPACT


def _current_weather(battle: AbstractBattle) -> str | None:
    try:
        weather = battle.weather
        if not weather:
            return None
        key = next(iter(weather))
        return key.name if hasattr(key, "name") else str(key)
    except (StopIteration, AttributeError, TypeError):
        return None


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
        elif own_remaining + opp_remaining <= len(battle.team):
            # Midgame when fewer than half the total Pokémon remain (scales with team size:
            # ≤6 for 6v6, ≤4 for 4v4, ≤3 for 3v3). battle.team is the full roster.
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
