"""Singles entry point — assembles the per-turn advisory payload."""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle

from nidozo.battle.heuristics.context import _battle_context, _current_weather
from nidozo.battle.heuristics.moves import _score_move
from nidozo.battle.heuristics.switching import _score_switch


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
