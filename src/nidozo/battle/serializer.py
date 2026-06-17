"""
Battle state serializer.

Converts a poke-env Battle into a structured dict suitable for rendering into
an LLM prompt. Hidden-information rules are enforced here:

  - Own Pokémon: full detail (moves + PP, exact HP, stats, item, ability, boosts, status).
  - Opponent Pokémon: only what has been legitimately revealed in this battle
    (moves used, HP%, status, visible boosts, types from Pokédex). Never exact
    HP, never unrevealed moves/items/ability.

Treat any leak of opponent hidden info as a correctness bug.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import AbstractBattle, DoubleBattle, Move, Pokemon
from poke_env.data.gen_data import GenData

from nidozo.battle.heuristics import score_actions, score_doubles_actions


def _species_name(mon: Pokemon) -> str:
    """Return a display-friendly species name suitable for sprites and prompts.

    poke-env normalises species IDs via ``to_id_str()`` — stripping all
    special characters including hyphens — so form Pokémon come out as e.g.
    ``"deoxysspeed"``.  Showdown sprite filenames and the Pokédex ``name``
    field use the hyphenated form (``"Deoxys-Speed"``), which is also more
    readable in LLM prompts.

    Looks up the ``name`` field from the Gen 9 Pokédex entry and falls back
    to the raw poke-env ID if the entry is missing or the lookup fails.
    """
    try:
        entry = GenData.from_gen(mon.gen).pokedex.get(mon.species)
        if entry:
            return entry.get("name") or mon.species
    except Exception:  # noqa: BLE001
        pass
    return mon.species


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def serialize_battle(battle: AbstractBattle, *, light: bool = False) -> dict[str, Any]:
    """Return a structured dict representing the current battle state.

    Routes to doubles serialization automatically when the battle is a
    ``DoubleBattle``.  The returned dict is safe to render into either
    player's prompt — it encodes perspective-correct information for the
    player whose turn it is.

    Args:
        light: When True, return a *render-only* snapshot: every field needed
            to draw the battlefield (active/bench Pokémon, HP, status, weather,
            fields, side conditions) but **without** decision-context fields
            (``heuristics``, ``opponent_threat_map``, ``available_moves``,
            ``available_switches``).  Those are (a) expensive — ``score_actions``
            runs damage/type math over every legal option — and (b) *stale*
            between a turn resolving and poke-env parsing the next ``|request|``,
            because ``battle.available_moves`` still reflects the previous turn.
            Used for zero-lag ``state_update`` events (OP-01); the frontend
            preserves the last full turn's advisory when it merges these in.
    """
    if isinstance(battle, DoubleBattle):
        return _serialize_doubles_battle(battle, light=light)
    return _serialize_singles_battle(battle, light=light)


def _serialize_singles_battle(battle: AbstractBattle, *, light: bool = False) -> dict[str, Any]:
    """Serialize a singles (1v1) battle state."""
    state: dict[str, Any] = {
        "is_doubles": False,
        "turn": battle.turn,
        "format": battle.format,
        "weather": _serialize_weather(battle),
        "fields": _serialize_fields(battle),
        "my_side_conditions": _serialize_side_conditions(battle.side_conditions),
        "opponent_side_conditions": _serialize_side_conditions(
            battle.opponent_side_conditions
        ),
        "my_active": _serialize_own_pokemon(battle.active_pokemon),
        "my_team": [
            _serialize_own_pokemon(p)
            for p in battle.team.values()
            if not p.active
        ],
        "opponent_active": _serialize_opponent_pokemon(battle.opponent_active_pokemon),
        "opponent_team": [
            _serialize_opponent_pokemon(p)
            for p in battle.opponent_team.values()
            if not p.active
        ],
        "opponent_team_size_seen": len(battle.opponent_team),
        "force_switch": battle.force_switch,
        # can_tera: whether the player can still Terastallize this battle.
        # Once used, this becomes False for the rest of the battle.
        "can_tera": bool(getattr(battle, "can_tera", False)),
        # recent_events is injected by LLMPlayer (stateful, not derivable from
        # a single battle snapshot). Absent here; always present in prompts.
        "recent_events": [],
    }

    if light:
        # Render-only: omit decision context. Keys are still present (empty/None)
        # so the shape is stable and the frontend can detect "no advisory here".
        state["available_moves"] = []
        state["available_switches"] = []
        state["heuristics"] = None
        state["opponent_threat_map"] = []
        return state

    state["available_moves"] = [_serialize_move(m) for m in battle.available_moves]
    state["available_switches"] = [_species_name(p) for p in battle.available_switches]
    state["heuristics"] = score_actions(battle)
    state["opponent_threat_map"] = _compute_threat_map(battle)
    return state


def _serialize_doubles_battle(battle: DoubleBattle, *, light: bool = False) -> dict[str, Any]:
    """Serialize a doubles (2v2) battle state.

    The state shape differs from singles:
    - ``my_active`` / ``opponent_active``: list of up to 2 Pokémon dicts
      (one per slot), each with a ``slot`` key (0 or 1).
    - ``force_switch``: list of 2 booleans.
    - ``can_tera``: list of 2 booleans.
    - ``available_moves`` / ``available_switches``: two lists (per slot).
    """
    # Doubles has two active Pokémon per side (either may be None if only one remains)
    my_active_list = battle.active_pokemon  # List[Optional[Pokemon]]
    opp_active_list = battle.opponent_active_pokemon  # List[Optional[Pokemon]]
    force_switch = list(battle.force_switch)  # List[bool], len 2
    can_tera_list = list(getattr(battle, "can_tera", [False, False]))  # List[bool]

    # Bench = team members that aren't active in either slot
    active_ids = {id(p) for p in my_active_list if p is not None}
    my_bench = [
        _serialize_own_pokemon(p)
        for p in battle.team.values()
        if id(p) not in active_ids
    ]
    opp_active_ids = {id(p) for p in opp_active_list if p is not None}
    opp_bench = [
        _serialize_opponent_pokemon(p)
        for p in battle.opponent_team.values()
        if id(p) not in opp_active_ids
    ]

    my_active_serialized = [
        {**(_serialize_own_pokemon(p) or {}), "slot": i}
        if p is not None else None
        for i, p in enumerate(my_active_list)
    ]
    opp_active_serialized = [
        {**(_serialize_opponent_pokemon(p) or {}), "slot": i}
        if p is not None else None
        for i, p in enumerate(opp_active_list)
    ]

    state: dict[str, Any] = {
        "is_doubles": True,
        "turn": battle.turn,
        "format": battle.format,
        "weather": _serialize_weather(battle),
        "fields": _serialize_fields(battle),
        "my_side_conditions": _serialize_side_conditions(battle.side_conditions),
        "opponent_side_conditions": _serialize_side_conditions(
            battle.opponent_side_conditions
        ),
        "my_active": my_active_serialized,
        "my_team": my_bench,
        "opponent_active": opp_active_serialized,
        "opponent_team": opp_bench,
        "opponent_team_size_seen": len(battle.opponent_team),
        "force_switch": force_switch,
        "can_tera": can_tera_list,
        "recent_events": [],
    }

    if light:
        state["available_moves"] = [[], []]
        state["available_switches"] = [[], []]
        state["heuristics"] = None
        state["opponent_threat_map"] = []
        return state

    # available_moves / switches are per-slot lists-of-lists in DoubleBattle
    raw_moves: list[list[Move]] = battle.available_moves
    raw_switches: list[list[Pokemon]] = battle.available_switches
    state["available_moves"] = [
        [_serialize_move(m) for m in slot_moves]
        for slot_moves in raw_moves
    ]
    state["available_switches"] = [
        [_species_name(p) for p in slot_switches]
        for slot_switches in raw_switches
    ]
    state["heuristics"] = score_doubles_actions(battle)
    state["opponent_threat_map"] = _compute_threat_map(battle)
    return state


# ---------------------------------------------------------------------------
# Own Pokémon — full information
# ---------------------------------------------------------------------------

def _serialize_own_pokemon(mon: Pokemon | None) -> dict[str, Any] | None:
    if mon is None:
        return None
    moves = {
        name: _serialize_move(m) for name, m in mon.moves.items()
    }
    # Actual in-battle stats (populated by poke-env from the |request| message).
    # May be None for bench mons whose stats haven't been reported yet.
    actual = {k: v for k, v in (mon.stats or {}).items() if v is not None}
    last_move = mon.last_move
    tera = getattr(mon, "tera_type", None)
    return {
        "species": _species_name(mon),
        "level": mon.level,
        # types already reflects Tera type when Terastallized (poke-env handles this).
        "types": [t.name for t in mon.types],
        "hp_fraction": round(mon.current_hp_fraction, 3),
        "fainted": mon.fainted,
        "status": mon.status.name if mon.status else None,
        "boosts": {k: v for k, v in mon.boosts.items() if v != 0},
        "item": mon.item,
        "ability": mon.ability,
        "base_stats": mon.base_stats,
        # actual_stats: real computed stats for this battle (level/EVs/nature applied).
        "actual_stats": actual if actual else None,
        "moves": moves,
        "effects": [e.name for e in mon.effects],
        # last_move: the most recent move used by this Pokémon (revealed information).
        "last_move": _serialize_move_basic(last_move) if last_move else None,
        # Terastallization: own team knows their Tera type from teambuilder.
        "is_terastallized": bool(getattr(mon, "is_terastallized", False)),
        "tera_type": tera.name if tera is not None else None,
    }


# ---------------------------------------------------------------------------
# Opponent Pokémon — revealed information only
# ---------------------------------------------------------------------------

def _serialize_opponent_pokemon(mon: Pokemon | None) -> dict[str, Any] | None:
    if mon is None:
        return None
    # mon.moves only contains moves the opponent has *used* — poke-env tracks
    # this for us. We do NOT include possible_abilities or estimated stats.
    revealed_moves = {name: _serialize_move_basic(m) for name, m in mon.moves.items()}
    last_move = mon.last_move
    is_tera = bool(getattr(mon, "is_terastallized", False))
    # Tera type is hidden-info until the opponent actually Terastallizes — at
    # that point the animation reveals it publicly, so it's safe to surface.
    tera = getattr(mon, "tera_type", None) if is_tera else None
    return {
        "species": _species_name(mon),
        "level": mon.level,
        # types already reflects Tera type when Terastallized (poke-env handles this).
        "types": [t.name for t in mon.types],
        "hp_fraction": round(mon.current_hp_fraction, 3),
        "fainted": mon.fainted,
        "status": mon.status.name if mon.status else None,
        "boosts": {k: v for k, v in mon.boosts.items() if v != 0},
        # item/ability: None until poke-env registers them as legitimately revealed.
        # poke-env can return "unknown" as a sentinel — treat that as unrevealed.
        "item": mon.item if mon.item not in (None, "unknown") else None,
        "ability": mon.ability if mon.ability not in (None, "unknown") else None,
        # base_stats are Pokédex-public knowledge (not hidden battle information).
        "base_stats": mon.base_stats,
        "revealed_moves": revealed_moves,
        # Explicit count so the prompt can show "N/4 moves revealed" without
        # the model having to count dictionary entries itself.
        "moves_revealed": len(mon.moves),
        # last_move: the most recent move the opponent used this battle (already revealed).
        "last_move": _serialize_move_basic(last_move) if last_move else None,
        # Terastallization: only exposed once it has occurred (publicly revealed event).
        "is_terastallized": is_tera,
        "tera_type": tera.name if tera is not None else None,
    }


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------

def _safe_priority(move: Move) -> int:
    """Return move priority, defaulting to 0 for pseudo-moves like 'recharge'."""
    try:
        return move.priority
    except KeyError:
        return 0


def _serialize_move(move: Move) -> dict[str, Any]:
    try:
        return {
            "id": move.id,
            "type": move.type.name,
            "category": move.category.name,
            "base_power": move.base_power,
            "accuracy": move.accuracy,
            "pp": move.current_pp,
            "max_pp": move.max_pp,
            "priority": _safe_priority(move),
        }
    except (KeyError, AttributeError):
        # Fallback for pseudo-moves (e.g. 'recharge') that lack full data
        return {
            "id": move.id,
            "type": "NORMAL",
            "category": "STATUS",
            "base_power": 0,
            "accuracy": True,
            "pp": 1,
            "max_pp": 1,
            "priority": 0,
        }


def _serialize_move_basic(move: Move) -> dict[str, Any]:
    """Opponent moves: omit PP (we don't track it for opponents)."""
    try:
        return {
            "id": move.id,
            "type": move.type.name,
            "category": move.category.name,
            "base_power": move.base_power,
            "accuracy": move.accuracy,
            "priority": _safe_priority(move),
        }
    except (KeyError, AttributeError):
        return {"id": move.id, "type": "NORMAL", "category": "STATUS",
                "base_power": 0, "accuracy": True, "priority": 0}


# ---------------------------------------------------------------------------
# Opponent threat map — which of YOUR mons each revealed opponent threatens
# ---------------------------------------------------------------------------

def _compute_threat_map(battle: AbstractBattle) -> list[dict[str, Any]]:
    """For each revealed opponent mon, list your non-fainted mons it threatens.

    "Threatens" means at least one of the opponent's types deals ≥2× damage to
    that mon.  Also records mons that resist all of the opponent's types (≤0.5×).
    This is type-chart only — it does not account for moveset (the opponent may
    not have a STAB move for every type it has).
    """
    result: list[dict[str, Any]] = []

    # All YOUR mons (active + bench, not fainted)
    your_mons = [
        m for m in battle.team.values()
        if not m.fainted
    ]
    if not your_mons:
        return result

    # All REVEALED opponent mons (active + bench), deduped.
    # opponent_team already contains the active mon, so iterating both would
    # double-count it and produce duplicate threat-map entries.
    opp_mons = [
        m for m in battle.opponent_team.values()
        if m is not None and not m.fainted
    ]

    for opp in opp_mons:
        opp_types = opp.types
        threatens: list[str] = []
        resists: list[str] = []

        for own in your_mons:
            try:
                # Max damage multiplier from any of opponent's STAB types
                max_mult = max(
                    (own.damage_multiplier(t) for t in opp_types),
                    default=1.0,
                )
                if max_mult >= 2.0:
                    threatens.append(_species_name(own))
                elif max_mult <= 0.5:
                    resists.append(_species_name(own))
            except Exception:  # noqa: BLE001
                pass

        result.append({
            "species": _species_name(opp),
            "threatens": threatens,
            "resists": resists,
        })

    return result


# ---------------------------------------------------------------------------
# Field / weather / side conditions
# ---------------------------------------------------------------------------

def _serialize_weather(battle: AbstractBattle) -> str | None:
    if not battle.weather:
        return None
    # weather is a Dict[Weather, int] where int is turns remaining
    weather_item = next(iter(battle.weather.items()))
    return weather_item[0].name


def _serialize_fields(battle: AbstractBattle) -> list[str]:
    return [field.name for field in battle.fields]


def _serialize_side_conditions(conditions: dict[Any, Any]) -> list[str]:
    return [cond.name for cond in conditions]
