"""Switch scoring — what a bench Pokémon is worth bringing in right now.

Weighs HP, defensive typing against the opponent's revealed threats, speed
tier, offensive coverage, the quality of the matchup being escaped, and the
hazard toll on entry, into a clamped -3..+3 switch quality.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle, Pokemon
from poke_env.battle.move_category import MoveCategory

from nidozo.battle.heuristics.context import _active_matchup_quality
from nidozo.battle.heuristics.damage import _effective_speed
from nidozo.battle.heuristics.hazards import _HAZARD_REMOVAL_MOVES, _hazard_switch_notes
from nidozo.battle.heuristics.type_chart import _is_grounded, _type_effectiveness_vs


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

    # Entry hazard switch costs
    hazard_notes = _hazard_switch_notes(incoming, battle)
    score["notes"].extend(hazard_notes)
    # Adjust quality for severe hazard damage
    try:
        from poke_env.battle import SideCondition  # noqa: PLC0415
        side_conds = battle.side_conditions
        if side_conds:
            if SideCondition.STEALTH_ROCK in side_conds:
                rock_mult = _type_effectiveness_vs("ROCK", incoming)
                if rock_mult >= 4.0:
                    score["switch_quality"] -= 2
                elif rock_mult >= 2.0:
                    score["switch_quality"] -= 1
            grounded = _is_grounded(incoming)
            if grounded and SideCondition.TOXIC_SPIKES in side_conds:
                is_poison = any(t is not None and t.name == "POISON" for t in incoming.types)
                if is_poison:
                    score["switch_quality"] += 1  # absorbs hazards — bonus
            # Hazard removal: flag mon that can clear hazards
            has_hazards = any(
                sc in side_conds
                for sc in (
                    SideCondition.STEALTH_ROCK, SideCondition.SPIKES,
                    SideCondition.TOXIC_SPIKES, SideCondition.STICKY_WEB,
                )
            )
            if has_hazards:
                removal_moves = [
                    m.id for m in incoming.moves.values()
                    if m.id in _HAZARD_REMOVAL_MOVES
                ]
                if removal_moves:
                    move_names = ", ".join(m.replace("_", " ").title() for m in removal_moves)
                    score["notes"].append(f"✓ Can clear hazards with {move_names}")
                    score["switch_quality"] += 1
    except Exception:  # noqa: BLE001
        pass

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
