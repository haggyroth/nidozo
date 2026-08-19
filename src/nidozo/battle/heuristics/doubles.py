"""Doubles scoring — targeting metadata and the two-slot entry point.

Doubles adds one thing singles does not have: a target choice. These helpers
classify each move's targeting (spread, choose-a-foe, ally-only, auto) and warn
when a spread move will also hit your own ally.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import DoubleBattle, Pokemon, Target
from poke_env.battle.move import Move
from poke_env.battle.move_category import MoveCategory

from nidozo.battle.heuristics.context import _current_weather
from nidozo.battle.heuristics.moves import _score_move
from nidozo.battle.heuristics.switching import _score_switch
from nidozo.battle.heuristics.type_chart import _effectiveness_label

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
