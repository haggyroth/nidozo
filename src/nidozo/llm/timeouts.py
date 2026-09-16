"""Deadlines for LLM calls that are *not* the player's own turn decision (#278).

`NIDOZO_TURN_TIMEOUT` has always bounded the player's choice inside
`battle/llm_player.py`, but every neighbouring LLM call — coach advice, draft
picks, post-battle lessons, the battle narrative — was unbounded. A stalled
backend (an unloaded LM Studio model, a network hang) therefore blocked the
turn, the draft, or post-battle analysis *forever*, which is exactly what the
turn timeout exists to prevent.

These calls now share one knob, `NIDOZO_LLM_TIMEOUT` (seconds, default 120).
Each call site still degrades the way it already did — the coach returns no
advice, a draft pick falls back to the first remaining Pokémon, lessons and
narratives come back empty — so a timeout costs context, never the battle.
"""

from __future__ import annotations

import asyncio
import logging
import os

from nidozo.llm.backend import Message, ModelBackend

logger = logging.getLogger(__name__)

# Deadline for coach / draft / lesson / narrative calls. The player's own turn
# keeps its separate, shorter NIDOZO_TURN_TIMEOUT (90s default).
DEFAULT_LLM_TIMEOUT: float = float(os.environ.get("NIDOZO_LLM_TIMEOUT", "120"))


def resolve_llm_timeout(value: float | None = None) -> float | None:
    """Resolve an explicit timeout, falling back to the configured default.

    ``None`` means "not specified" and yields `DEFAULT_LLM_TIMEOUT`; zero or a
    negative number disables the deadline, mirroring how ``turn_timeout`` is
    handled on the player.
    """
    timeout = DEFAULT_LLM_TIMEOUT if value is None else value
    return timeout if timeout > 0 else None


async def complete_within(
    backend: ModelBackend,
    messages: list[Message],
    *,
    timeout: float | None,
    what: str,
) -> str:
    """Call ``backend.complete`` with a deadline; ``None`` leaves it unbounded.

    Raises ``TimeoutError`` (with a message naming the caller) when the deadline
    passes, so the call site's existing error handling degrades as usual instead
    of waiting on a backend that may never answer.
    """
    if timeout is None:
        return await backend.complete(messages)
    try:
        return await asyncio.wait_for(backend.complete(messages), timeout=timeout)
    except TimeoutError:
        logger.error("%s: LLM call exceeded its %.0fs deadline", what, timeout)
        raise TimeoutError(f"{what} timed out after {timeout:.0f}s") from None
