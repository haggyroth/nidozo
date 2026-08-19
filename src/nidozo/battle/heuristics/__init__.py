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

Package layout — each module owns one concern, and the dependency arrows only
ever point downward:

- ``damage``      — stat stages, weather modifiers, speed, damage estimation
- ``type_chart``  — type effectiveness, grounding, effectiveness labels
- ``status``      — status tables and status-move annotation
- ``hazards``     — entry hazard chip damage and removal
- ``context``     — the top-level battle-context advisory block
- ``moves``       — single-move scoring
- ``switching``   — switch scoring
- ``singles``     — the singles entry point, ``score_actions``
- ``doubles``     — targeting metadata and ``score_doubles_actions``

This module re-exports the full former surface of ``heuristics.py``, so callers
and tests import from ``nidozo.battle.heuristics`` exactly as before.
"""

from __future__ import annotations

from nidozo.battle.heuristics.context import (
    _active_matchup_quality,
    _battle_context,
    _current_weather,
    _remaining_count,
)
from nidozo.battle.heuristics.damage import (
    _STAGE_MULT,
    _WEATHER_MODS,
    _effective_speed,
    _estimate_incoming_damage,
    _stage_mult,
    _weather_damage_mod,
)
from nidozo.battle.heuristics.doubles import (
    _ALLY_TARGETS,
    _AUTO_TARGETS,
    _CHOOSE_FOE_TARGETS,
    _HIT_ALLY_TARGETS,
    _SPREAD_FOE_TARGETS,
    _move_target_hint,
    _score_move_doubles,
    score_doubles_actions,
)
from nidozo.battle.heuristics.hazards import _HAZARD_REMOVAL_MOVES, _hazard_switch_notes
from nidozo.battle.heuristics.moves import _score_move
from nidozo.battle.heuristics.singles import score_actions
from nidozo.battle.heuristics.status import (
    _STATUS_IMPACT,
    _STATUS_MOVE_EFFECTS,
    _annotate_status_move,
)
from nidozo.battle.heuristics.switching import _is_primarily_physical, _score_switch
from nidozo.battle.heuristics.type_chart import (
    _effectiveness_label,
    _is_grounded,
    _type_effectiveness_vs,
)

__all__ = [
    # Public API
    "score_actions",
    "score_doubles_actions",
    # Re-exported internals — kept for backwards compatibility with existing
    # callers and tests that reach past the public surface.
    "_ALLY_TARGETS",
    "_AUTO_TARGETS",
    "_CHOOSE_FOE_TARGETS",
    "_HAZARD_REMOVAL_MOVES",
    "_HIT_ALLY_TARGETS",
    "_SPREAD_FOE_TARGETS",
    "_STAGE_MULT",
    "_STATUS_IMPACT",
    "_STATUS_MOVE_EFFECTS",
    "_WEATHER_MODS",
    "_active_matchup_quality",
    "_annotate_status_move",
    "_battle_context",
    "_current_weather",
    "_effective_speed",
    "_effectiveness_label",
    "_estimate_incoming_damage",
    "_hazard_switch_notes",
    "_is_grounded",
    "_is_primarily_physical",
    "_move_target_hint",
    "_remaining_count",
    "_score_move",
    "_score_move_doubles",
    "_score_switch",
    "_stage_mult",
    "_type_effectiveness_vs",
    "_weather_damage_mod",
]
