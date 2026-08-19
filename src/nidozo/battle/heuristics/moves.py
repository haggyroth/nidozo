"""Move scoring — one legal move in, one advisory score dict out.

Covers type effectiveness, the rough damage estimate (stat stages, weather,
STAB, burn, accuracy), priority and speed order, and PP pressure. Status moves
are delegated to the status annotator.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle, Pokemon
from poke_env.battle.move import Move
from poke_env.battle.move_category import MoveCategory

from nidozo.battle.heuristics.damage import (
    _effective_speed,
    _stage_mult,
    _weather_damage_mod,
)
from nidozo.battle.heuristics.status import _annotate_status_move
from nidozo.battle.heuristics.type_chart import _effectiveness_label


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
