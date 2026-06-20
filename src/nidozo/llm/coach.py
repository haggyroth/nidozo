"""CoachAgent — queries a second LLM for free-form strategic advice before the player acts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from nidozo.llm.backend import Message, ModelBackend, Usage

logger = logging.getLogger(__name__)

_COACH_PROMPT_DIR = Path(__file__).parent / "prompts" / "coach"


class CoachAgent:
    """Wraps a ModelBackend to provide advisory analysis before a player's turn.

    The coach receives the same battle state as the player (same hidden-info
    rules), but is asked for free-form analysis rather than a structured action.
    Its advice is appended to the player's prompt so the player can reason over
    it alongside the heuristic scores.

    Args:
        backend: Any object satisfying the ModelBackend protocol.  To control
                 token budget, configure the backend itself (e.g. pass max_tokens
                 to the backend constructor) rather than this class.
    """

    def __init__(self, backend: ModelBackend) -> None:
        self._backend = backend

        self._system_text = (_COACH_PROMPT_DIR / "system.txt").read_text()
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(_COACH_PROMPT_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._turn_template = self._jinja_env.get_template("turn.txt.jinja")

    @property
    def last_usage(self) -> Usage | None:
        """Token usage from the coach backend's most recent call (#225)."""
        return getattr(self._backend, "last_usage", None)

    async def analyze(self, battle_state: dict[str, Any]) -> str | None:
        """Return free-form strategic advice for the current turn.

        Returns None on any error — the player falls back to acting without advice.
        """
        try:
            turn_content = self._turn_template.render(**battle_state)
            messages: list[Message] = [
                Message(role="system", content=self._system_text),
                Message(role="user", content=turn_content),
            ]
            advice = await self._backend.complete(messages)
            if not advice:
                logger.debug("Coach returned empty response — skipping")
                return None
            return advice.strip()
        except Exception as exc:
            logger.warning("Coach analyze() failed: %s", exc)
            return None
