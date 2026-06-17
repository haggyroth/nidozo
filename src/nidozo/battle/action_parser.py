"""
ActionParser — extracts a BattleOrder from an LLM response.

Supports two response formats:

1. JSON (v2 prompt) — tried first:
   {"reasoning": "...", "action_type": "move", "identifier": "thunderbolt"}
   {"reasoning": "...", "action_type": "switch", "identifier": "masquerain"}

2. Text (v1 prompt) — fallback regex parser:
    ACTION: move 2              — 1-based slot number
    ACTION: move thunderbolt    — move name
    ACTION: switch 3            — 1-based slot number
    ACTION: switch masquerain   — Pokémon species name
    ACTION: thunderbolt         — bare move name (no keyword)

Multiple ACTION lines are allowed in text mode; the last valid one is used.
Returns None on complete parse failure; caller falls back to random.
"""

from __future__ import annotations

import json
import logging
import re
from difflib import get_close_matches
from typing import Any

from poke_env.battle import AbstractBattle, DoubleBattle
from poke_env.player.battle_order import (
    BattleOrder,
    DoubleBattleOrder,
    PassBattleOrder,
    SingleBattleOrder,
)
from poke_env.player.player import Player

logger = logging.getLogger(__name__)

# Matches "ACTION: move/switch <slot_or_name>"
# [\s*]* handles markdown variants like "**ACTION:** move X" or "**ACTION: move X**"
_ACTION_RE = re.compile(
    r"ACTION:[\s*]*(move|switch)\s+(\S+)", re.IGNORECASE
)

# Strips <think>...</think> blocks emitted by reasoning models (Qwen, DeepSeek R1, etc.)
# before action parsing so the actual response content is always reached.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Matches "ACTION: <bare_name>" with no move/switch keyword
_BARE_ACTION_RE = re.compile(
    r"ACTION:\s*([A-Za-z][\w]*)", re.IGNORECASE
)

_KEYWORDS = {"move", "switch"}

# Fuzzy-match cutoff for move and switch name resolution.
# 0.82 accepts a 1-char error on names of 5+ chars and rejects wild guesses.
_FUZZY_CUTOFF = 0.82


def _normalize(s: str) -> str:
    """Lowercase, strip non-alphanumeric for fuzzy name comparison."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _strip_keyword_prefix(identifier: str) -> str:
    """Strip an accidental action-type prefix from an identifier.

    Models using the v2 JSON prompt occasionally produce identifiers like
    ``"switch 1"`` or ``"move thunderbolt"`` instead of just ``"1"`` or
    ``"thunderbolt"``.  Stripping the prefix lets the rest of the resolver
    handle the value normally.
    """
    lower = identifier.lower()
    for kw in ("switch ", "move "):
        if lower.startswith(kw):
            return identifier[len(kw):].strip()
    return identifier


def _resolve_move(
    identifier: str,
    battle: AbstractBattle,
    player: Player,
    *,
    terastallize: bool = False,
) -> BattleOrder | None:
    """Resolve a move identifier (slot number or name) to a BattleOrder."""
    moves = battle.available_moves
    if not moves:
        logger.warning("ACTION: move requested but no moves available")
        return None

    identifier = _strip_keyword_prefix(identifier)

    # Guard: only pass terastallize=True when the battle permits it.
    # If the player requests tera_move but can't Tera, fall back to a normal move.
    if terastallize and not getattr(battle, "can_tera", False):
        logger.debug("ACTION: tera_move requested but battle.can_tera is False — using normal move")
        terastallize = False

    # Try numeric slot — extract leading digits to handle trailing markdown (e.g. "2**")
    m = re.match(r"(\d+)", identifier)
    if m:
        slot = int(m.group(1))
        idx = slot - 1
        if 0 <= idx < len(moves):
            return player.create_order(moves[idx], terastallize=terastallize)
        logger.warning("ACTION: move slot %d out of range (have %d)", slot, len(moves))
        return None

    # Try move name match (normalized — exact first, then fuzzy)
    norm = _normalize(identifier)
    norm_to_move = {_normalize(m.id): m for m in moves}

    if norm in norm_to_move:
        return player.create_order(norm_to_move[norm], terastallize=terastallize)

    # Fuzzy fallback: tolerate typos like "thunderolt" → "thunderbolt", "icebeam" → "ice beam"
    close = get_close_matches(norm, norm_to_move.keys(), n=1, cutoff=_FUZZY_CUTOFF)
    if close:
        matched_id = norm_to_move[close[0]].id
        logger.debug("ACTION: fuzzy-matched move %r → %r", identifier, matched_id)
        return player.create_order(norm_to_move[close[0]], terastallize=terastallize)

    logger.debug("ACTION: move name %r not found in available moves", identifier)
    return None


def _resolve_switch(
    identifier: str,
    battle: AbstractBattle,
    player: Player,
) -> BattleOrder | None:
    """Resolve a switch identifier (slot number or species name) to a BattleOrder."""
    switches = battle.available_switches
    if not switches:
        logger.warning("ACTION: switch requested but no switches available")
        return None

    identifier = _strip_keyword_prefix(identifier)

    # Try numeric slot — extract leading digits to handle trailing markdown (e.g. "2**")
    m = re.match(r"(\d+)", identifier)
    if m:
        slot = int(m.group(1))
        idx = slot - 1
        if 0 <= idx < len(switches):
            return player.create_order(switches[idx])
        logger.warning("ACTION: switch slot %d out of range (have %d)", slot, len(switches))
        return None

    # Try species name match (normalized — exact first, then fuzzy)
    norm = _normalize(identifier)
    norm_to_mon = {_normalize(mon.species): mon for mon in switches}

    if norm in norm_to_mon:
        return player.create_order(norm_to_mon[norm])

    # Fuzzy fallback: tolerate typos like "agron" → "aggron", "deoxysspeed" → "deoxyssp"
    close = get_close_matches(norm, norm_to_mon.keys(), n=1, cutoff=_FUZZY_CUTOFF)
    if close:
        matched_species = norm_to_mon[close[0]].species
        logger.debug(
            "ACTION: fuzzy-matched switch %r → %r", identifier, matched_species
        )
        return player.create_order(norm_to_mon[close[0]])

    logger.debug("ACTION: switch name %r not found in available switches", identifier)
    return None


# ---------------------------------------------------------------------------
# Doubles resolution — per-slot orders combined into a DoubleBattleOrder
# ---------------------------------------------------------------------------

# Maps a human target token to a showdown target position for a given slot.
# Showdown positions: own slot 0 = -1, own slot 1 = -2, foe 1 = +1, foe 2 = +2.
_FOE_1 = 1
_FOE_2 = 2


def _pass_order() -> PassBattleOrder:
    """Typed wrapper around poke-env's untyped ``PassBattleOrder`` constructor."""
    return PassBattleOrder()  # type: ignore[no-untyped-call]


def _resolve_target_token(token: str, slot_idx: int) -> int | None:
    """Map an LLM target token (e.g. "foe_1", "ally") to a showdown target int.

    ``slot_idx`` is the acting Pokémon's slot (0 or 1). Returns None for an
    unrecognised token so the caller can fall back to auto-targeting.
    """
    t = _normalize(token)
    own_pos = -1 if slot_idx == 0 else -2
    ally_pos = -2 if slot_idx == 0 else -1

    if t in ("foe1", "opp1", "opponent1", "enemy1", "1"):
        return _FOE_1
    if t in ("foe2", "opp2", "opponent2", "enemy2", "2"):
        return _FOE_2
    if t in ("ally", "partner", "teammate"):
        return ally_pos
    if t in ("self", "me", "user"):
        return own_pos
    return None


def _resolve_move_doubles(
    identifier: str,
    target: str | None,
    slot_idx: int,
    battle: DoubleBattle,
    player: Player,
    *,
    terastallize: bool = False,
) -> SingleBattleOrder | None:
    """Resolve a move for one active slot into a SingleBattleOrder with target."""
    moves = battle.available_moves[slot_idx] if slot_idx < len(battle.available_moves) else []
    if not moves:
        logger.warning("Doubles: slot %d has no available moves", slot_idx)
        return None

    identifier = _strip_keyword_prefix(identifier)

    # Guard terastallize against the per-slot can_tera flag.
    can_tera = getattr(battle, "can_tera", [False, False])
    if terastallize and not (slot_idx < len(can_tera) and can_tera[slot_idx]):
        logger.debug("Doubles: tera_move requested but slot %d can't Tera", slot_idx)
        terastallize = False

    chosen = _match_move_in_list(identifier, moves)
    if chosen is None:
        logger.debug("Doubles: move %r not found for slot %d", identifier, slot_idx)
        return None

    active = battle.active_pokemon
    acting_mon = active[slot_idx] if slot_idx < len(active) else None

    # Determine valid showdown targets for this move; pick the one matching the
    # requested token, else the first valid (poke-env handles spread/self moves
    # by returning EMPTY_TARGET_POSITION).
    valid_targets: list[int] = []
    if acting_mon is not None:
        try:
            valid_targets = battle.get_possible_showdown_targets(chosen, acting_mon)
        except Exception:  # noqa: BLE001
            valid_targets = []

    resolved_target = 0  # EMPTY_TARGET_POSITION default
    if target:
        requested = _resolve_target_token(target, slot_idx)
        if requested is not None and (not valid_targets or requested in valid_targets):
            resolved_target = requested
        elif valid_targets:
            # Requested target invalid for this move — fall back to a sensible default.
            resolved_target = _default_target(valid_targets)
    elif valid_targets:
        resolved_target = _default_target(valid_targets)

    return player.create_order(
        chosen, terastallize=terastallize, move_target=resolved_target
    )


def _default_target(valid_targets: list[int]) -> int:
    """Pick a sensible default from a move's valid showdown targets.

    Prefer 0 (EMPTY — spread/self/auto moves), then the first foe, then anything.
    """
    if 0 in valid_targets:
        return 0
    for foe in (_FOE_1, _FOE_2):
        if foe in valid_targets:
            return foe
    return valid_targets[0] if valid_targets else 0


def _match_move_in_list(identifier: str, moves: list[Any]) -> Any | None:
    """Resolve a move identifier (slot number or name) within a single slot's list."""
    m = re.match(r"(\d+)", identifier)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(moves):
            return moves[idx]
        return None

    norm = _normalize(identifier)
    norm_to_move = {_normalize(mv.id): mv for mv in moves}
    if norm in norm_to_move:
        return norm_to_move[norm]
    close = get_close_matches(norm, norm_to_move.keys(), n=1, cutoff=_FUZZY_CUTOFF)
    if close:
        return norm_to_move[close[0]]
    return None


def _resolve_switch_doubles(
    identifier: str,
    slot_idx: int,
    battle: DoubleBattle,
    player: Player,
) -> SingleBattleOrder | None:
    """Resolve a switch for one active slot into a SingleBattleOrder."""
    switches = (
        battle.available_switches[slot_idx]
        if slot_idx < len(battle.available_switches) else []
    )
    if not switches:
        logger.warning("Doubles: slot %d has no available switches", slot_idx)
        return None

    identifier = _strip_keyword_prefix(identifier)

    m = re.match(r"(\d+)", identifier)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(switches):
            return player.create_order(switches[idx])
        return None

    norm = _normalize(identifier)
    norm_to_mon = {_normalize(mon.species): mon for mon in switches}
    if norm in norm_to_mon:
        return player.create_order(norm_to_mon[norm])
    close = get_close_matches(norm, norm_to_mon.keys(), n=1, cutoff=_FUZZY_CUTOFF)
    if close:
        return player.create_order(norm_to_mon[close[0]])
    return None


def _resolve_slot_action(
    slot_action: dict[str, Any],
    slot_idx: int,
    battle: DoubleBattle,
    player: Player,
) -> SingleBattleOrder | None:
    """Resolve one slot's action dict into a SingleBattleOrder.

    Expected shape: {"action_type": "move"|"switch"|"tera_move"|"pass",
                     "identifier": "...", "target": "foe_1"}
    Returns None if unresolvable (caller decides fallback).
    """
    action_type = str(slot_action.get("action_type", "")).lower().strip()
    identifier = str(slot_action.get("identifier", "")).strip()
    target = slot_action.get("target")
    target_str = str(target).strip() if target is not None else None

    if action_type == "pass":
        return _pass_order()

    if action_type in ("move", "tera_move"):
        if not identifier:
            return None
        return _resolve_move_doubles(
            identifier, target_str, slot_idx, battle, player,
            terastallize=action_type == "tera_move",
        )
    if action_type == "switch":
        if not identifier:
            return None
        return _resolve_switch_doubles(identifier, slot_idx, battle, player)

    logger.debug("Doubles: unknown action_type %r for slot %d", action_type, slot_idx)
    return None


def _parse_doubles_json(
    response: str,
    battle: DoubleBattle,
    player: Player,
) -> DoubleBattleOrder | None:
    """Parse a doubles JSON response into a DoubleBattleOrder.

    Expected shape:
      {"reasoning": "...",
       "actions": [
         {"action_type": "move", "identifier": "thunderbolt", "target": "foe_1"},
         {"action_type": "switch", "identifier": "garchomp"}
       ]}

    The two entries map to active slot 0 and slot 1 respectively. A slot whose
    Pokémon has already fainted (only one active) may be omitted or set to
    "pass".
    """
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    if not text.startswith("{"):
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = json.loads(_sanitize_json_strings(text))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        logger.debug("Doubles JSON missing 'actions' list: %s", data)
        return None

    active = battle.active_pokemon

    orders: list[SingleBattleOrder | None] = [None, None]
    for slot_idx in range(2):
        # Slot with no active Pokémon → pass automatically.
        if slot_idx >= len(active) or active[slot_idx] is None:
            orders[slot_idx] = _pass_order()
            continue
        if slot_idx < len(actions) and isinstance(actions[slot_idx], dict):
            orders[slot_idx] = _resolve_slot_action(
                actions[slot_idx], slot_idx, battle, player
            )

    # If a slot couldn't be resolved, leave it for the caller's fallback unless
    # the other slot succeeded — in which case fill the gap with a pass so the
    # successful slot's action is still submitted.
    if orders[0] is None and orders[1] is None:
        return None
    first = orders[0] if orders[0] is not None else _pass_order()
    second = orders[1] if orders[1] is not None else _pass_order()
    return DoubleBattleOrder(first_order=first, second_order=second)


def _sanitize_json_strings(text: str) -> str:
    """Escape bare control characters inside JSON string literals.

    Some small models (e.g. ministral-3-3b) embed literal newlines in the
    ``reasoning`` field instead of the required ``\\n`` escape, producing:

        "reasoning": "
        some text",   ← 0x0A byte makes json.loads raise InvalidControlCharacter

    This replaces literal \\n / \\r / \\t inside any JSON string token with
    their escaped forms so the document becomes parseable.  The substitution
    is a no-op on already-valid JSON.
    """
    def _escape_in_string(m: re.Match[str]) -> str:
        raw: str = m.group()
        return (
            raw
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    # Match JSON string literals: opening ", then any run of (non-quote
    # non-backslash chars | backslash + any char), then closing ".
    # re.DOTALL lets [^"\\] already match newlines (char-class ignores DOTALL),
    # but is set so the alternate \\. also matches literal newlines.
    return re.sub(r'"(?:[^"\\]|\\.)*"', _escape_in_string, text, flags=re.DOTALL)


def _parse_json_action(
    response: str,
    battle: AbstractBattle,
    player: Player,
) -> BattleOrder | None:
    """Try to parse response as a JSON action object (v2 prompt format).

    Expected shape: {"action_type": "move"|"switch", "identifier": "...", "reasoning": "..."}
    The 'reasoning' key is logged for context but not required for parsing.

    Two parse attempts are made:
    1. Standard ``json.loads`` — handles well-formed responses.
    2. Control-char sanitization + retry — recovers responses where the model
       embedded literal newlines inside the ``reasoning`` string value instead
       of the required ``\\n`` escape sequence.
    """
    text = response.strip()

    # Strip markdown code fences first (e.g. ```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()

    if not text.startswith("{"):
        return None

    data: dict[str, Any] | None = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Recovery: some models produce literal control chars inside strings.
        try:
            data = json.loads(_sanitize_json_strings(text))
            logger.debug("JSON parsed after control-char sanitization")
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    action_type = str(data.get("action_type", "")).lower().strip()
    identifier = str(data.get("identifier", "")).strip()

    if not action_type or not identifier:
        logger.debug("JSON action missing action_type or identifier: %s", data)
        return None

    if action_type in ("move", "tera_move"):
        terastallize = action_type == "tera_move"
        order = _resolve_move(identifier, battle, player, terastallize=terastallize)
        if order is None:
            logger.debug("JSON: move %r not resolved — available: %s",
                         identifier, [m.id for m in battle.available_moves])
        return order
    elif action_type == "switch":
        order = _resolve_switch(identifier, battle, player)
        if order is None:
            logger.debug("JSON: switch %r not resolved — available: %s",
                         identifier, [m.species for m in battle.available_switches])
        return order
    else:
        logger.debug("JSON: unknown action_type %r", action_type)
        return None


def parse_action(
    response: str,
    battle: AbstractBattle,
    player: Player,
) -> BattleOrder | None:
    """Return a BattleOrder from the LLM response, or None on failure.

    Tries JSON parsing first (v2 prompt), then falls back to the legacy
    regex-based text parser (v1 prompt). Using the last valid ACTION line
    so a model that self-corrects mid-response gets the right answer.
    """
    if not response:
        return None

    # Strip <think>...</think> blocks before all parsing.
    # Reasoning models (Qwen 3, DeepSeek R1, etc.) emit these before their actual
    # response.  The raw response is preserved in the DB for analysis — we only
    # strip at parse time.
    response = _THINK_RE.sub("", response).strip()
    if not response:
        return None

    # Doubles battles use a distinct JSON shape (an "actions" array, two slots,
    # per-move target field) and produce a DoubleBattleOrder. Route there first.
    if isinstance(battle, DoubleBattle):
        return _parse_doubles_json(response, battle, player)

    # Pass 0: JSON structured output (v2 prompt)
    order = _parse_json_action(response, battle, player)
    if order is not None:
        return order

    # Pass 1: ACTION: move/switch <identifier>
    matches = _ACTION_RE.findall(response)
    for action_type, identifier in reversed(matches):
        at = action_type.lower()
        if at == "move":
            order = _resolve_move(identifier, battle, player)
        else:
            order = _resolve_switch(identifier, battle, player)
        if order is not None:
            return order

    # Pass 2: ACTION: <bare_name> (no move/switch keyword) — try as move name
    bare_matches = _BARE_ACTION_RE.findall(response)
    for name in reversed(bare_matches):
        if name.lower() in _KEYWORDS:
            continue
        order = _resolve_move(name, battle, player)
        if order is not None:
            logger.debug("Resolved bare ACTION: %r as move", name)
            return order

    logger.warning("No parseable action found in LLM response")
    return None
