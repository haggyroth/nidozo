"""Prompt and response tracing policy for the LLM layer (#284).

A DEBUG build logged two large bodies per turn: the rendered prompt (the whole
serialized battle state, plus lessons, coach advice, and every legal action) and
the raw model response. That is the largest log volume the system produces, and
it puts battle state — and whatever a future prompt pulls in — somewhere nobody
chose: stdout, and ``LOG_FILE`` if set, which is exactly the artifact an operator
ships to a log aggregator or pastes into an issue.

So the bodies are opt-in. DEBUG still says what happened — which model, how many
messages, how many characters — and ``NIDOZO_TRACE_LLM=1`` adds the text itself
for the person who is debugging prompt construction and needs to see it. Trace
output is capped, because the failure mode being traced (a runaway prompt) is
the same one that would fill the disk.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

# Opt-in: full prompt/response bodies at DEBUG. Anything else leaves them out.
_TRACE_ENV = "NIDOZO_TRACE_LLM"

# Ceiling on a traced body. Real prompts run a few thousand characters; this
# only exists so a pathological one cannot turn a log file into a disk problem.
_MAX_TRACE_CHARS = 20_000


def trace_enabled() -> bool:
    """Whether the full prompt/response bodies should be logged."""
    return os.environ.get(_TRACE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def log_prompt(
    logger: logging.Logger,
    subject: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Log a rendered prompt: always a summary at DEBUG, the body only if tracing."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    if trace_enabled():
        body = "\n---\n".join(
            f"[{m.get('role', '?')}]\n{m.get('content', '')}" for m in messages
        )
        logger.debug(
            "LLM prompt to %s (%d messages, %d chars):\n%s",
            subject, len(messages), chars, _clip(body),
            extra=extra,
        )
    else:
        logger.debug(
            "LLM prompt to %s: %d messages, %d chars (set %s=1 to log the body)",
            subject, len(messages), chars, _TRACE_ENV,
            extra=extra,
        )


def log_response(
    logger: logging.Logger,
    subject: str,
    content: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Log a model response: always a summary at DEBUG, the body only if tracing."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    if trace_enabled():
        logger.debug(
            "LLM response from %s (%d chars):\n%s",
            subject, len(content), _clip(content),
            extra=extra,
        )
    else:
        logger.debug(
            "LLM response from %s: %d chars (set %s=1 to log the body)",
            subject, len(content), _TRACE_ENV,
            extra=extra,
        )


def _clip(text: str) -> str:
    if len(text) <= _MAX_TRACE_CHARS:
        return text
    omitted = len(text) - _MAX_TRACE_CHARS
    return f"{text[:_MAX_TRACE_CHARS]}\n… [{omitted} chars truncated]"
