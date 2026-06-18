"""draft — LLM-driven team selection before a battle.

Each player independently picks 6 Pokémon from a tier-filtered pool using
sequential single-pick calls.  The draft phase emits ``draft_pick`` and
``draft_complete`` WebSocket events so the frontend can show the draft live.

Usage (inside the battle runner)::

    from nidozo.battle.draft import run_draft

    result = await run_draft(
        backend=p1_backend,
        model_id=p1_model_id,
        tier="ou",
        store=store,
        bus=bus,
        player_role="p1",
    )
    team_string = result.team_string
    team_id     = result.team_id
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nidozo.battle.team_builder import build_team_string, get_pool_info, load_movesets
from nidozo.battle.tiers import TIER_DISPLAY, get_pool
from nidozo.llm.backend import Message, ModelBackend

if TYPE_CHECKING:
    from nidozo.api.events import EventBus
    from nidozo.db.store import BattleStore

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3  # retries per pick on parse/validation failure


@dataclass
class DraftResult:
    """Result of a completed draft phase."""

    model_id: int
    tier: str
    picked: list[str]          # species IDs in pick order
    team_string: str           # Showdown export format
    team_id: int               # DB primary key
    reasoning: str             # concatenated reasoning from all picks


def _build_draft_messages(
    tier_display: str,
    pick_num: int,
    total_picks: int,
    picked: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    system_prompt: str,
) -> list[Message]:
    """Build [system, user] messages for one draft pick."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader

    prompts_dir = Path(__file__).parent.parent / "llm" / "prompts" / "v3"
    env = Environment(loader=FileSystemLoader(str(prompts_dir)), autoescape=False)
    template = env.get_template("draft_turn.txt.jinja")

    turn_text = template.render(
        tier_display=tier_display,
        pick_num=pick_num,
        total_picks=total_picks,
        picked=picked,
        pool=pool,
    )

    return [
        Message(role="system", content=system_prompt),
        Message(role="user", content=turn_text),
    ]


def _parse_pick_response(response: str, available_names: set[str]) -> tuple[str, str] | None:
    """Parse the LLM's pick JSON.  Returns (pick, reasoning) or None on failure."""
    # Strip markdown fences if present
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from surrounding text
        import re
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    pick_raw: str = str(obj.get("pick", "")).strip()
    reasoning: str = str(obj.get("reasoning", "")).strip()

    if not pick_raw:
        return None

    # Normalise to lowercase for lookup
    pick_norm = pick_raw.lower().replace(" ", "").replace("-", "").replace("'", "").replace(".", "")

    # Try exact match first, then normalised
    if pick_raw in available_names:
        return pick_raw, reasoning
    for name in available_names:
        norm = name.lower().replace(" ", "").replace("-", "").replace("'", "").replace(".", "")
        if norm == pick_norm:
            return name, reasoning

    return None


async def run_draft(
    backend: ModelBackend,
    model_id: int,
    tier: str,
    store: BattleStore,
    bus: EventBus | None = None,
    player_role: str = "p1",
    prompt_version: str = "v3",
    team_size: int = 6,
    doubles: bool = False,
) -> DraftResult:
    """Run the draft for one player, picking *team_size* Pokémon.

    Emits ``draft_pick`` events via *bus* as each pick is made, and a
    ``draft_complete`` event when all picks are selected.

    Args:
        backend:       LLM backend to use for pick calls.
        model_id:      DB model ID (used to save team + draft session).
        tier:          Tier key (e.g. ``"ou"``).
        store:         BattleStore instance.
        bus:           EventBus for WS events (optional; skipped if None).
        player_role:   ``"p1"`` or ``"p2"`` (for WS events).
        prompt_version: Prompt version to log (default ``"v3"``).
        team_size:     Number of Pokémon to draft (default 6).

    Returns:
        :class:`DraftResult` with the completed team details.
    """
    from pathlib import Path

    # Load moveset data and compute pool
    movesets = load_movesets()
    pool_ids = get_pool(tier, set(movesets.keys()))
    pool_info = get_pool_info(pool_ids, movesets)  # [{species_id, species, types}]
    from nidozo.battle.tiers import resolve_format

    tier_display = TIER_DISPLAY.get(tier, tier.upper())
    showdown_format = resolve_format(tier, doubles=doubles, team_size=team_size)

    # Load system prompt
    system_path = Path(__file__).parent.parent / "llm" / "prompts" / "v3" / "draft_system.txt"
    system_prompt = system_path.read_text(encoding="utf-8").strip()

    picked_ids: list[str] = []
    picked_info: list[dict[str, Any]] = []
    all_reasoning: list[str] = []

    # Build a mutable pool (display-name strings, to match what the model sees)
    remaining_pool = list(pool_info)  # list of dicts

    for pick_num in range(1, team_size + 1):
        available_names: set[str] = {m["species"] for m in remaining_pool}
        messages = _build_draft_messages(
            tier_display=tier_display,
            pick_num=pick_num,
            total_picks=team_size,
            picked=picked_info,
            pool=remaining_pool,
            system_prompt=system_prompt,
        )

        # Try up to _MAX_RETRIES times to get a valid pick
        pick_species: str | None = None
        pick_reasoning = ""
        for attempt in range(_MAX_RETRIES):
            try:
                raw = await backend.complete(messages)
                parsed = _parse_pick_response(raw, available_names)
                if parsed is not None:
                    pick_species, pick_reasoning = parsed
                    break
                logger.warning(
                    "Draft pick %d/%d (role=%s): parse failed (attempt %d/%d), response: %.100s",
                    pick_num, team_size, player_role, attempt + 1, _MAX_RETRIES, raw,
                )
            except Exception as exc:
                logger.error(
                    "Draft pick %d/%d (role=%s): backend error (attempt %d/%d): %s",
                    pick_num, team_size, player_role, attempt + 1, _MAX_RETRIES, exc,
                )

        # Fallback: pick first remaining if all retries failed
        if pick_species is None:
            pick_species = remaining_pool[0]["species"]
            pick_reasoning = "(fallback — parse failed)"
            logger.warning(
                "Draft pick %d/%d (role=%s): all retries exhausted, falling back to %s",
                pick_num, team_size, player_role, pick_species,
            )

        # Find the matching entry in remaining_pool
        picked_entry = next(m for m in remaining_pool if m["species"] == pick_species)
        species_id = picked_entry["species_id"]

        picked_ids.append(species_id)
        picked_info.append(picked_entry)
        all_reasoning.append(f"Pick {pick_num} ({pick_species}): {pick_reasoning}")
        remaining_pool = [m for m in remaining_pool if m["species"] != pick_species]

        # Emit WS event for this pick
        if bus is not None:
            await bus.publish({
                "type": "draft_pick",
                "player_role": player_role,
                "pick_num": pick_num,
                "species": pick_species,
                "types": picked_entry["types"],
                "reasoning": pick_reasoning,
            })

        logger.info(
            "Draft pick %d/%d (role=%s): %s — %s",
            pick_num, team_size, player_role, pick_species, pick_reasoning[:80],
        )

    # Build team string
    team_string = build_team_string(picked_ids, movesets)
    full_reasoning = "\n".join(all_reasoning)

    # Persist to DB
    team_id = store.save_team(
        model_id=model_id,
        tier=tier,
        format_=showdown_format,
        pokemon=picked_ids,
        team_string=team_string,
    )
    store.save_draft_session(
        model_id=model_id,
        tier=tier,
        pool_size=len(pool_ids),
        picked=picked_ids,
        prompt_version=prompt_version,
        reasoning=full_reasoning,
    )

    # Emit draft_complete event
    if bus is not None:
        await bus.publish({
            "type": "draft_complete",
            "player_role": player_role,
            "team": [{"species": p["species"], "types": p["types"]} for p in picked_info],
        })

    logger.info(
        "Draft complete (role=%s, tier=%s): %s",
        player_role, tier, ", ".join(p["species"] for p in picked_info),
    )

    return DraftResult(
        model_id=model_id,
        tier=tier,
        picked=picked_ids,
        team_string=team_string,
        team_id=team_id,
        reasoning=full_reasoning,
    )
