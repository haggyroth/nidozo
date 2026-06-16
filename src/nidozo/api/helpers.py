"""Shared factory helpers for building backends and players."""

from __future__ import annotations

import os
from typing import Any

from nidozo.llm.backend import ModelBackend

# Prompt versions that require structured JSON output (grammar-sampling / response_format).
# Update this set whenever a new JSON-output prompt version is added.
_JSON_OUTPUT_PROMPT_VERSIONS: frozenset[str] = frozenset({"v2", "v3", "v4", "v5", "v6"})


def _model_name(provider: str, model: str | None) -> str:
    defaults = {
        "anthropic": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "lmstudio": os.environ.get("LM_STUDIO_MODEL", "local-model"),
        "random": "random",
    }
    return model or defaults.get(provider, provider)


def _build_backend(provider: str, model: str | None, json_mode: bool = False) -> ModelBackend:
    """Construct a ModelBackend for the given provider.  json_mode=False for lessons."""
    from nidozo.llm import AnthropicBackend, OpenAIBackend

    if provider == "anthropic":
        return AnthropicBackend(
            model=model or "claude-sonnet-4-6",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    if provider == "openai":
        return OpenAIBackend(
            model=model or "gpt-4o",
            api_key=os.environ.get("OPENAI_API_KEY"),
            json_mode=json_mode,
        )
    # lmstudio — same json_schema response_format; LM Studio rejects json_object.
    return OpenAIBackend(
        model=model or os.environ.get("LM_STUDIO_MODEL", "local-model"),
        api_key="lm-studio",
        base_url=os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        json_mode=json_mode,
    )


def _build_coach(
    coach_provider: str | None,
    coach_model: str | None,
) -> Any | None:
    """Build a CoachAgent if a coach provider is configured, else return None."""
    if not coach_provider:
        return None
    from nidozo.llm.coach import CoachAgent

    backend = _build_backend(coach_provider, coach_model, json_mode=False)
    return CoachAgent(backend=backend)


def _build_streaming_player(
    provider: str,
    model: str | None,
    role: str,
    prompt_version: str,
    store: Any,
    battle_id: int,
    bus: Any,
    cfg: Any,
    fmt: str,
    lessons: list[str] | None = None,
    team: str | None = None,
    coach_provider: str | None = None,
    coach_model: str | None = None,
    personality: str | None = None,
) -> Any:
    from nidozo.battle.streaming_player import StreamingLLMPlayer, StreamingRandomBot

    if provider == "random":
        return StreamingRandomBot(
            event_bus=bus,
            player_role=role,
            battle_format=fmt,
            server_configuration=cfg,
        )

    use_json_mode = prompt_version in _JSON_OUTPUT_PROMPT_VERSIONS and provider in ("lmstudio", "openai")
    backend = _build_backend(provider, model, json_mode=use_json_mode)
    coach = _build_coach(coach_provider, coach_model)

    kwargs: dict[str, Any] = {
        "backend": backend,
        "event_bus": bus,
        "player_role": role,
        "prompt_version": prompt_version,
        "store": store,
        "battle_id": battle_id,
        "battle_format": fmt,
        "server_configuration": cfg,
        "lessons": lessons,
        "coach": coach,
        "personality": personality,
    }
    if team is not None:
        kwargs["team"] = team

    return StreamingLLMPlayer(**kwargs)
