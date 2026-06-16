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

from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder
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
