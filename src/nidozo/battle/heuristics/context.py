"""Battle context — the top-level advisory block handed to the prompt builder.

Answers the questions a player asks before picking an action: how the two sides'
speeds compare, what phase the battle is in, how the active matchup reads, what
the weather and statuses are doing, and whether we are about to get KO'd.

The speed comparison is base-for-base, so it reports who is faster at equal
investment rather than who moves first — see ``_comparable_speed``.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle, Pokemon
from poke_env.battle.move_category import MoveCategory
from poke_env.battle.pokemon_type import PokemonType
from poke_env.data.gen_data import GenData

from nidozo.battle.heuristics.damage import _comparable_speed, _estimate_incoming_damage
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

    # Speed comparison. Both sides go through _comparable_speed (#289), which
    # works from base speed — see it for why the own side's real stat cannot be
    # one half of this comparison.
    if own is not None and opp is not None:
        own_spd = _comparable_speed(own)
        opp_spd = _comparable_speed(opp)
        faster = own_spd > opp_spd
        speed_note = (
            f"Faster by base speed ({own_spd:.0f} vs {opp_spd:.0f})"
            if faster
            else (
                f"Slower by base speed ({own_spd:.0f} vs {opp_spd:.0f})"
                if own_spd < opp_spd
                else f"Base-speed tie ({own_spd:.0f} vs {opp_spd:.0f}) — equal "
                     "investment makes move order a coin flip"
            )
        )
        ctx["speed"] = {
            # Named for what it is: base speed decides this, not move order.
            # Own investment is invisible to the comparison, and the opponent's
            # is unknowable, so "you move first" was a claim we could not support.
            "faster_by_base_speed": faster,
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
                # Determine STAB bonus: Tera same-type = 2× STAB; new type = 1.5× STAB.
                #
                # Showdown's rule (sim/battle-actions.ts): stab = 2 when
                # `pokemon.terastallized === type && pokemon.getTypes(false, true)
                # .includes(type)` — the *pre-Tera* types. We can read those publicly:
                # this block only runs while `battle.can_tera` is true, so nothing on
                # our side has Terastallized yet and `own.types` is still the pre-Tera
                # list. The old code reached for poke-env's private `_type_1`/`_type_2`,
                # which are the *species* types: they miss a type change (Soak, Burn
                # Up, Roost) that `own.types` carries and Showdown's `getTypes` honours,
                # and they were read with a None default, so a rename in a poke-env
                # bump would have silently reclassified every Tera as a new type.
                same_type = tera_name in {t.name for t in own.types}
                stab_note = "same as base typing (2× STAB bonus)" if same_type else "new type (1.5× STAB on matching moves)"
                # Defensive benefit: check if Tera type changes the matchup vs current opponent
                if opp is not None:
                    opp_damaging = [
                        m for m in opp.moves.values()
                        if m.category != MoveCategory.STATUS and m.base_power > 0
                    ]
                    if opp_damaging:
                        # How hard the opponent's known moves hit a Pokémon of the Tera
                        # type alone — Terastallizing replaces the typing. poke-env's own
                        # `PokemonType.damage_multiplier` rather than indexing the chart by
                        # hand (#290): the chart is keyed `{defender: {attacker: mult}}`
                        # (`pokemon_type.py`, `type_chart[type_1.name][self.name]`, where
                        # `self` is the attacker), and the hand-indexed version here had
                        # the two backwards, so every Tera read its defensive value exactly
                        # inverted — Fire moves against a Water Tera were reported as "still
                        # weak", and Ground against a Flying Tera as neutral rather than
                        # immune, because a missing key falls back to 1.0.
                        tera_pt = PokemonType.from_name(tera_name)
                        type_chart = GenData.from_gen(9).type_chart
                        worst_mult = max(
                            (
                                m.type.damage_multiplier(tera_pt, type_chart=type_chart)
                                for m in opp_damaging
                                if getattr(m, "type", None) is not None
                            ),
                            default=1.0,
                        )
                        if worst_mult == 0.0:
                            def_note = f"immune to opponent's known moves as {tera_name}"
                        elif worst_mult <= 0.5:
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
