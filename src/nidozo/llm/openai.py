"""OpenAI-compatible backend — covers OpenAI cloud and local LM Studio."""

import logging
import time
from typing import Any

import openai

from nidozo.llm.backend import Message, Usage

logger = logging.getLogger(__name__)

# JSON Schema for the v2 structured action output.
# LM Studio (and OpenAI) grammar-sample against this to guarantee valid JSON
# that exactly matches our expected shape.
_ACTION_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "battle_action",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "2-4 sentence analysis: type matchups, speed, HP, status.",
                },
                "action_type": {
                    "type": "string",
                    "enum": ["move", "switch"],
                },
                "identifier": {
                    "type": "string",
                    "description": (
                        "Move name, Pokémon species name, or 1-based slot number "
                        "(e.g. 'thunderbolt', 'masquerain', '2')."
                    ),
                },
            },
            "required": ["reasoning", "action_type", "identifier"],
            "additionalProperties": False,
        },
    },
}


class OpenAIBackend:
    """OpenAI-compatible chat backend.

    Args:
        model: Model identifier string.
        max_tokens: Maximum tokens to generate.
        api_key: API key (use any non-empty string for LM Studio, e.g. "lm-studio").
        base_url: Override base URL — set to "http://localhost:1234/v1" for LM Studio.
        json_mode: If True, send the json_schema response_format to grammar-sample
                   structured JSON output. Used by both LM Studio and OpenAI —
                   LM Studio's API accepts only 'json_schema' or 'text' (it
                   rejects 'json_object' with a 400), and OpenAI cloud benefits
                   from the schema's type enforcement. Do NOT use with Anthropic.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        max_tokens: int = 1536,
        api_key: str | None = None,
        base_url: str | None = None,
        json_mode: bool = False,
    ) -> None:
        # base_url=None → OpenAI cloud; set to "http://localhost:1234/v1" for LM Studio
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens
        self._json_mode = json_mode
        # Token usage from the most recent complete() call (#225). None until the
        # first call, or when the provider reports no usage (some local models).
        self.last_usage: Usage | None = None

    async def complete(self, messages: list[Message]) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if self._json_mode:
            # Full structured-output schema. LM Studio accepts only json_schema
            # or text (json_object → 400); OpenAI cloud enforces the shape.
            kwargs["response_format"] = _ACTION_JSON_SCHEMA

        if logger.isEnabledFor(logging.DEBUG):
            prompt_text = "\n---\n".join(
                f"[{m['role']}]\n{m.get('content', '')}" for m in messages
            )
            logger.debug("LLM prompt to %s:\n%s", self._model, prompt_text)

        t0 = time.monotonic()
        response = await self._client.chat.completions.create(**kwargs)
        elapsed = time.monotonic() - t0

        msg = response.choices[0].message
        content = msg.content or ""

        if not content:
            # Qwen 3 (and other thinking models) route all output through
            # reasoning_content, leaving content empty.  When using json_mode
            # the actual JSON lands in reasoning_content — use it as the response.
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                logger.debug(
                    "content empty for %s — using reasoning_content (%d chars)",
                    self._model, len(reasoning),
                )
                content = reasoning

        usage = getattr(response, "usage", None)
        if usage:
            self.last_usage = Usage(
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            )
            logger.info(
                "%s — prompt=%d completion=%d tokens, %.1fs",
                self._model,
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
                elapsed,
                extra={
                    "model": self._model,
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "elapsed_s": round(elapsed, 2),
                },
            )
        else:
            self.last_usage = None
            logger.info("%s — %.1fs (no usage data)", self._model, elapsed)

        if not content:
            finish = getattr(response.choices[0], "finish_reason", "unknown")
            logger.warning(
                "Empty response from %s (finish_reason=%s). "
                "Check that the model is loaded and the model ID is correct.",
                self._model,
                finish,
            )
        elif logger.isEnabledFor(logging.DEBUG):
            logger.debug("LLM response from %s:\n%s", self._model, content)

        return content
