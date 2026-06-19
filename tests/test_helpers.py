"""Tests for api/helpers.py — _model_name, _build_backend, _build_coach,
and _build_streaming_player."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nidozo.api.helpers import (
    _JSON_OUTPUT_PROMPT_VERSIONS,
    _build_backend,
    _build_coach,
    _build_streaming_player,
    _model_name,
)

# ---------------------------------------------------------------------------
# _model_name
# ---------------------------------------------------------------------------


def test_model_name_returns_provided_value() -> None:
    assert _model_name("anthropic", "claude-haiku-3-5") == "claude-haiku-3-5"


def test_model_name_none_returns_anthropic_default() -> None:
    assert _model_name("anthropic", None) == "claude-sonnet-4-6"


def test_model_name_none_returns_openai_default() -> None:
    assert _model_name("openai", None) == "gpt-4o"


def test_model_name_none_returns_random_default() -> None:
    assert _model_name("random", None) == "random"


def test_model_name_lmstudio_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LM_STUDIO_MODEL", "my-local-model")
    assert _model_name("lmstudio", None) == "my-local-model"


def test_model_name_lmstudio_fallback_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LM_STUDIO_MODEL", raising=False)
    assert _model_name("lmstudio", None) == "local-model"


def test_model_name_unknown_provider_returns_provider_name() -> None:
    """For unrecognised providers there is no default entry — the provider name is returned."""
    assert _model_name("unknown-provider", None) == "unknown-provider"


# ---------------------------------------------------------------------------
# _build_backend
# ---------------------------------------------------------------------------


def test_build_backend_anthropic_returns_anthropic_backend() -> None:
    with patch("nidozo.llm.AnthropicBackend") as mock_cls:
        mock_cls.return_value = MagicMock()
        backend = _build_backend("anthropic", "claude-haiku-3-5")

    mock_cls.assert_called_once_with(model="claude-haiku-3-5", api_key=mock_cls.call_args[1]["api_key"])
    assert backend is mock_cls.return_value


def test_build_backend_anthropic_uses_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    with patch("nidozo.llm.AnthropicBackend") as mock_cls:
        mock_cls.return_value = MagicMock()
        _build_backend("anthropic", None)

    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "test-key-123"


def test_build_backend_openai_returns_openai_backend() -> None:
    with patch("nidozo.llm.OpenAIBackend") as mock_cls:
        mock_cls.return_value = MagicMock()
        backend = _build_backend("openai", "gpt-4o-mini", json_mode=True)

    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["json_mode"] is True
    assert backend is mock_cls.return_value


def test_build_backend_lmstudio_returns_openai_backend_with_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("LM_STUDIO_MODEL", "gemma-3-4b")

    with patch("nidozo.llm.OpenAIBackend") as mock_cls:
        mock_cls.return_value = MagicMock()
        _build_backend("lmstudio", None)

    _, kwargs = mock_cls.call_args
    assert kwargs["base_url"] == "http://localhost:9999/v1"
    assert kwargs["model"] == "gemma-3-4b"
    assert kwargs["api_key"] == "lm-studio"


def test_build_backend_lmstudio_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LM_STUDIO_BASE_URL", raising=False)
    with patch("nidozo.llm.OpenAIBackend") as mock_cls:
        mock_cls.return_value = MagicMock()
        _build_backend("lmstudio", "some-model")

    _, kwargs = mock_cls.call_args
    assert kwargs["base_url"] == "http://localhost:1234/v1"


# ---------------------------------------------------------------------------
# _build_coach
# ---------------------------------------------------------------------------


def test_build_coach_returns_none_when_no_provider() -> None:
    assert _build_coach(None, None) is None


def test_build_coach_returns_none_for_empty_string() -> None:
    assert _build_coach("", None) is None


def test_build_coach_returns_coach_agent() -> None:
    mock_backend = MagicMock()
    mock_coach = MagicMock()

    with patch("nidozo.llm.AnthropicBackend", return_value=mock_backend), \
         patch("nidozo.llm.coach.CoachAgent", return_value=mock_coach) as mock_coach_cls:
        result = _build_coach("anthropic", "claude-haiku-3-5")

    mock_coach_cls.assert_called_once_with(backend=mock_backend)
    assert result is mock_coach


def test_build_coach_lmstudio_uses_openai_backend_no_json_mode() -> None:
    """Coach always uses json_mode=False regardless of provider."""
    mock_backend = MagicMock()

    with patch("nidozo.llm.OpenAIBackend", return_value=mock_backend) as mock_cls, \
         patch("nidozo.llm.coach.CoachAgent"):
        _build_coach("lmstudio", "my-model")

    _, kwargs = mock_cls.call_args
    assert kwargs["json_mode"] is False


# ---------------------------------------------------------------------------
# _build_streaming_player — random provider
# ---------------------------------------------------------------------------


def _fake_cfg() -> MagicMock:
    return MagicMock()


def test_build_streaming_player_random_returns_random_bot() -> None:
    mock_bot = MagicMock()
    cfg = _fake_cfg()

    with patch("nidozo.battle.streaming_player.StreamingRandomBot", return_value=mock_bot) as mock_cls:
        result = _build_streaming_player(
            provider="random",
            model=None,
            role="p1",
            prompt_version="v1",
            store=MagicMock(),
            battle_id=1,
            bus=MagicMock(),
            cfg=cfg,
            fmt="gen3randombattle",
        )

    mock_cls.assert_called_once()
    assert result is mock_bot


# ---------------------------------------------------------------------------
# _build_streaming_player — LLM provider, json_mode logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,prompt_version,expect_json",
    [
        ("lmstudio", "v4", True),    # v4 + lmstudio → json_mode on
        ("lmstudio", "v2", True),    # v2 + lmstudio → json_mode on
        ("openai",   "v3", True),    # v3 + openai   → json_mode on
        ("openai",   "v1", False),   # v1 — not a JSON prompt version
        ("lmstudio", "v1", False),   # v1 — not a JSON prompt version
        ("anthropic","v4", False),   # v4 + anthropic → json_mode off (not in allowed set)
    ],
)
def test_build_streaming_player_json_mode(
    provider: str,
    prompt_version: str,
    expect_json: bool,
) -> None:
    mock_player = MagicMock()
    mock_backend = MagicMock()

    backend_patch = (
        "nidozo.llm.AnthropicBackend" if provider == "anthropic" else "nidozo.llm.OpenAIBackend"
    )

    with patch(backend_patch, return_value=mock_backend) as mock_backend_cls, \
         patch("nidozo.battle.streaming_player.StreamingLLMPlayer", return_value=mock_player):
        _build_streaming_player(
            provider=provider,
            model="some-model",
            role="p1",
            prompt_version=prompt_version,
            store=MagicMock(),
            battle_id=1,
            bus=MagicMock(),
            cfg=_fake_cfg(),
            fmt="gen3randombattle",
        )

    _, kwargs = mock_backend_cls.call_args
    if provider == "anthropic":
        # AnthropicBackend has no json_mode kwarg — just check it was called
        pass
    else:
        assert kwargs.get("json_mode") is expect_json


def test_build_streaming_player_with_coach() -> None:
    """When coach_provider is set, a CoachAgent is created and passed to StreamingLLMPlayer."""
    mock_player = MagicMock()
    mock_backend = MagicMock()
    mock_coach = MagicMock()

    with patch("nidozo.llm.OpenAIBackend", return_value=mock_backend), \
         patch("nidozo.llm.coach.CoachAgent", return_value=mock_coach), \
         patch("nidozo.battle.streaming_player.StreamingLLMPlayer", return_value=mock_player) as mock_cls:
        _build_streaming_player(
            provider="lmstudio",
            model="model-a",
            role="p1",
            prompt_version="v4",
            store=MagicMock(),
            battle_id=1,
            bus=MagicMock(),
            cfg=_fake_cfg(),
            fmt="gen3randombattle",
            coach_provider="lmstudio",
            coach_model="coach-model",
        )

    _, kwargs = mock_cls.call_args
    assert kwargs["coach"] is mock_coach


def test_build_streaming_player_with_team() -> None:
    """The team kwarg is forwarded to StreamingLLMPlayer when provided."""
    with patch("nidozo.llm.OpenAIBackend"), \
         patch("nidozo.battle.streaming_player.StreamingLLMPlayer") as mock_cls:
        mock_cls.return_value = MagicMock()
        _build_streaming_player(
            provider="lmstudio",
            model="model-b",
            role="p2",
            prompt_version="v3",
            store=MagicMock(),
            battle_id=2,
            bus=MagicMock(),
            cfg=_fake_cfg(),
            fmt="gen3ou",
            team="Pikachu @ ...",
        )

    _, kwargs = mock_cls.call_args
    assert kwargs["team"] == "Pikachu @ ..."


def test_build_streaming_player_no_team_key_omitted() -> None:
    """When team is None, the 'team' key must not appear in the StreamingLLMPlayer kwargs."""
    with patch("nidozo.llm.OpenAIBackend"), \
         patch("nidozo.battle.streaming_player.StreamingLLMPlayer") as mock_cls:
        mock_cls.return_value = MagicMock()
        _build_streaming_player(
            provider="lmstudio",
            model="model-c",
            role="p1",
            prompt_version="v2",
            store=MagicMock(),
            battle_id=3,
            bus=MagicMock(),
            cfg=_fake_cfg(),
            fmt="gen3randombattle",
            team=None,
        )

    _, kwargs = mock_cls.call_args
    assert "team" not in kwargs


def test_json_output_prompt_versions_set() -> None:
    """Every JSON-output prompt version (v2 onward) must be in the set; only the
    legacy text prompt v1 is excluded. Guards against the set going stale when a
    new prompt version is added (as happened with v8/v9)."""
    assert _JSON_OUTPUT_PROMPT_VERSIONS == frozenset(
        {"v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"}
    )
    assert "v1" not in _JSON_OUTPUT_PROMPT_VERSIONS
