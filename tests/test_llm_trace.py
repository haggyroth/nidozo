"""Prompt and response bodies are opt-in (#284).

A DEBUG build wrote two large bodies per turn — the rendered prompt and the raw
model response — to stdout and to ``LOG_FILE``. Both carry full battle state,
and the prompt is the one artifact most likely to grow a future secret (a
pasted API key, a private team). The log level is a poor gate for that: DEBUG is
also what you turn on to watch HTTP traffic and store timing, neither of which
needs a transcript of the battle.

So the bodies sit behind ``NIDOZO_TRACE_LLM``, and DEBUG keeps a summary that
still answers "did the prompt get built, and how big was it".

Two levels of test here, deliberately:

* the helper's own behaviour, and
* the same assertion driven end-to-end through ``OpenAIBackend.complete`` and
  ``LLMPlayer.choose_move`` — because the failure mode this issue describes is
  a call *site* that logs the body directly, which unit-testing the helper
  would not catch. A negative control (revert one call site) is what proves the
  end-to-end tests are load-bearing.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nidozo.llm.trace import _MAX_TRACE_CHARS, log_prompt, log_response, trace_enabled

_PROMPT_BODY = "BATTLE STATE: p1 Charizard 100% vs p2 Blastoise 100%"
_RESPONSE_BODY = '{"reasoning":"surf is super effective","action_type":"move"}'


@pytest.fixture
def debug_logger(caplog: pytest.LogCaptureFixture) -> logging.Logger:
    """A logger that captures at DEBUG regardless of ambient LOG_LEVEL."""
    caplog.set_level(logging.DEBUG, logger="nidozo.test.trace")
    return logging.getLogger("nidozo.test.trace")


@pytest.fixture
def untraced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default: no trace flag in the environment."""
    monkeypatch.delenv("NIDOZO_TRACE_LLM", raising=False)


@pytest.fixture
def traced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIDOZO_TRACE_LLM", "1")


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On", " true "])
def test_truthy_values_enable_tracing(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NIDOZO_TRACE_LLM", value)
    assert trace_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "yes please", "2"])
def test_everything_else_leaves_tracing_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("NIDOZO_TRACE_LLM", value)
    assert trace_enabled() is False


def test_an_unset_variable_leaves_tracing_off(
    monkeypatch: pytest.MonkeyPatch, untraced: None
) -> None:
    assert trace_enabled() is False


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------

def test_prompt_body_is_absent_by_default(
    debug_logger: logging.Logger, caplog: pytest.LogCaptureFixture, untraced: None
) -> None:
    """The default writes a summary, not the body — but still says something."""
    log_prompt(debug_logger, "gpt-test", [{"role": "user", "content": _PROMPT_BODY}])

    written = caplog.text
    assert _PROMPT_BODY not in written
    # The summary has to be useful on its own, or nobody will trust turning the
    # body off: which model, how much text, and how to get the rest.
    assert "gpt-test" in written
    assert "1 messages" in written
    assert str(len(_PROMPT_BODY)) in written
    assert "NIDOZO_TRACE_LLM=1" in written


def test_prompt_body_appears_when_tracing(
    debug_logger: logging.Logger, caplog: pytest.LogCaptureFixture, traced: None
) -> None:
    log_prompt(
        debug_logger,
        "gpt-test",
        [
            {"role": "system", "content": "You are a Pokemon trainer."},
            {"role": "user", "content": _PROMPT_BODY},
        ],
    )

    written = caplog.text
    assert _PROMPT_BODY in written
    assert "You are a Pokemon trainer." in written
    # Roles are what make a multi-message prompt readable.
    assert "[system]" in written
    assert "[user]" in written
    assert "2 messages" in written


def test_response_body_is_absent_by_default(
    debug_logger: logging.Logger, caplog: pytest.LogCaptureFixture, untraced: None
) -> None:
    log_response(debug_logger, "gpt-test", _RESPONSE_BODY)

    written = caplog.text
    assert _RESPONSE_BODY not in written
    assert "gpt-test" in written
    assert str(len(_RESPONSE_BODY)) in written
    assert "NIDOZO_TRACE_LLM=1" in written


def test_response_body_appears_when_tracing(
    debug_logger: logging.Logger, caplog: pytest.LogCaptureFixture, traced: None
) -> None:
    log_response(debug_logger, "gpt-test", _RESPONSE_BODY)

    assert _RESPONSE_BODY in caplog.text


def test_nothing_is_logged_above_debug(
    caplog: pytest.LogCaptureFixture, untraced: None
) -> None:
    """At INFO a traced body is still not written — the flag gates DEBUG output."""
    caplog.set_level(logging.INFO, logger="nidozo.test.info")
    logger = logging.getLogger("nidozo.test.info")

    log_prompt(logger, "gpt-test", [{"role": "user", "content": _PROMPT_BODY}])
    log_response(logger, "gpt-test", _RESPONSE_BODY)

    assert caplog.text == ""


def test_a_traced_body_is_capped(
    debug_logger: logging.Logger, caplog: pytest.LogCaptureFixture, traced: None
) -> None:
    """Tracing a runaway prompt must not be how you fill the disk.

    The failure being traced and the failure the cap prevents are the same one.
    """
    log_response(debug_logger, "gpt-test", "x" * (_MAX_TRACE_CHARS + 500))

    written = caplog.text
    assert "500 chars truncated" in written
    # The record is the cap plus the message around it, not the whole body.
    assert len(written) < _MAX_TRACE_CHARS + 1000


def test_extra_fields_survive_the_summary(
    debug_logger: logging.Logger, caplog: pytest.LogCaptureFixture, untraced: None
) -> None:
    """The battle-scoped extras the call site already passed must not be dropped."""
    log_response(
        debug_logger, "[p1] turn 3", _RESPONSE_BODY,
        extra={"battle_id": 12, "player": "p1"},
    )

    record = caplog.records[-1]
    assert record.battle_id == 12  # type: ignore[attr-defined]
    assert record.player == "p1"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# End to end: the real call sites
# ---------------------------------------------------------------------------

def _make_openai_backend():
    from nidozo.llm.openai import OpenAIBackend

    with patch("openai.AsyncOpenAI.__init__", return_value=None):
        backend = OpenAIBackend(model="gpt-test", api_key="test-key")
    backend._client = MagicMock()
    return backend


def _openai_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.reasoning_content = None
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = None
    return resp


@pytest.mark.asyncio
async def test_openai_backend_does_not_write_the_prompt_or_response(
    caplog: pytest.LogCaptureFixture, untraced: None
) -> None:
    """OpenAIBackend.complete — the prompt site and the response site."""
    caplog.set_level(logging.DEBUG, logger="nidozo.llm.openai")
    backend = _make_openai_backend()
    backend._client.chat.completions.create = AsyncMock(
        return_value=_openai_response(_RESPONSE_BODY)
    )

    await backend.complete([{"role": "user", "content": _PROMPT_BODY}])

    written = caplog.text
    assert "LLM prompt to gpt-test" in written, "the DEBUG summary never fired"
    assert "LLM response from gpt-test" in written
    assert _PROMPT_BODY not in written
    assert _RESPONSE_BODY not in written


@pytest.mark.asyncio
async def test_openai_backend_writes_both_bodies_when_tracing(
    caplog: pytest.LogCaptureFixture, traced: None
) -> None:
    caplog.set_level(logging.DEBUG, logger="nidozo.llm.openai")
    backend = _make_openai_backend()
    backend._client.chat.completions.create = AsyncMock(
        return_value=_openai_response(_RESPONSE_BODY)
    )

    await backend.complete([{"role": "user", "content": _PROMPT_BODY}])

    written = caplog.text
    assert _PROMPT_BODY in written
    assert _RESPONSE_BODY in written


def _make_player(backend, **kwargs):
    from nidozo.battle.llm_player import LLMPlayer

    with patch("poke_env.player.Player.__init__", return_value=None):
        player = LLMPlayer(backend=backend, **kwargs)
    player.choose_random_move = MagicMock()
    player.create_order = MagicMock()
    player._prompt_builder.build_messages = MagicMock(
        return_value=[{"role": "user", "content": _PROMPT_BODY}]
    )
    return player


def _echo_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value=_RESPONSE_BODY)
    return backend


def _mock_battle() -> MagicMock:
    """Shaped like tests/test_llm_player.py's fixture: the serializer walks it."""
    battle = MagicMock()
    battle.turn = 3
    battle.battle_tag = "gen3randombattle-test"
    battle.format = "gen3randombattle"
    battle.weather = {}
    battle.fields = []
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    battle.active_pokemon = None
    battle.opponent_active_pokemon = None
    battle.team = {}
    battle.opponent_team = {}
    battle.force_switch = False
    battle.available_moves = []
    battle.available_switches = []
    return battle


@pytest.mark.asyncio
async def test_llm_player_does_not_write_the_raw_response(
    caplog: pytest.LogCaptureFixture, untraced: None
) -> None:
    """LLMPlayer.choose_move — the third site, which logs the same body again."""
    caplog.set_level(logging.DEBUG)
    player = _make_player(_echo_backend())

    with patch(
        "nidozo.battle.llm_player.parse_action", return_value=MagicMock(message="x")
    ):
        await player.choose_move(_mock_battle())

    written = caplog.text
    assert "raw LLM response" not in written
    assert _RESPONSE_BODY not in written
    # The summary replaced it, tagged with the player and turn.
    assert "LLM response from [p1] turn 3" in written


@pytest.mark.asyncio
async def test_llm_player_writes_the_raw_response_when_tracing(
    caplog: pytest.LogCaptureFixture, traced: None
) -> None:
    caplog.set_level(logging.DEBUG)
    player = _make_player(_echo_backend())

    with patch(
        "nidozo.battle.llm_player.parse_action", return_value=MagicMock(message="x")
    ):
        await player.choose_move(_mock_battle())

    assert _RESPONSE_BODY in caplog.text
