"""Tests for LLM call deadlines (#278).

`NIDOZO_TURN_TIMEOUT` bounded the player's decision but every neighbouring LLM
call — coach advice, draft picks, lessons, the battle narrative — was unbounded,
so a stalled backend blocked the turn, the draft, or post-battle analysis
forever. These tests use a backend that never answers and assert each call site
still returns, with the degraded result it already had.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nidozo.battle import draft as draft_mod
from nidozo.battle.draft import run_draft
from nidozo.db.store import BattleStore
from nidozo.llm.backend import Message
from nidozo.llm.coach import CoachAgent
from nidozo.llm.lesson_generator import generate_lesson
from nidozo.llm.narrator import generate_battle_narrative
from nidozo.llm.timeouts import (
    DEFAULT_LLM_TIMEOUT,
    complete_within,
    resolve_llm_timeout,
)
from tests.test_coach import _minimal_state

_HANG = 3600.0


class _HangingBackend:
    """The stalled-local-model case from #278: `complete` never returns."""

    async def complete(self, messages: list[Message]) -> str:
        await asyncio.sleep(_HANG)
        return "never reached"


class _EchoBackend:
    def __init__(self, response: str = "hello") -> None:
        self._response = response
        self.seen: list[Message] = []

    async def complete(self, messages: list[Message]) -> str:
        self.seen = messages
        return self._response


def _messages() -> list[Message]:
    return [Message(role="user", content="hi")]


# ---------------------------------------------------------------------------
# resolve_llm_timeout / complete_within
# ---------------------------------------------------------------------------


def test_resolve_uses_the_configured_default_when_unspecified() -> None:
    assert resolve_llm_timeout(None) == DEFAULT_LLM_TIMEOUT
    assert DEFAULT_LLM_TIMEOUT > 0


def test_resolve_honours_an_explicit_deadline() -> None:
    assert resolve_llm_timeout(12.5) == 12.5


@pytest.mark.parametrize("disabled", [0.0, -1.0])
def test_resolve_disables_the_deadline_for_zero_or_negative(disabled: float) -> None:
    assert resolve_llm_timeout(disabled) is None


async def test_complete_within_returns_the_backend_response() -> None:
    backend = _EchoBackend("advice")
    messages = _messages()
    result = await complete_within(backend, messages, timeout=5.0, what="Test")
    assert result == "advice"
    assert backend.seen == messages


async def test_complete_within_passes_through_when_unbounded() -> None:
    backend = _EchoBackend("unbounded")
    assert await complete_within(backend, _messages(), timeout=None, what="Test") == "unbounded"


async def test_complete_within_cuts_off_a_hanging_backend() -> None:
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="Test call timed out"):
        await complete_within(_HangingBackend(), _messages(), timeout=0.05, what="Test call")
    assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------------------
# Coach — must not hold the turn open
# ---------------------------------------------------------------------------


async def test_coach_returns_none_instead_of_hanging() -> None:
    coach = CoachAgent(backend=_HangingBackend(), timeout=0.05)
    t0 = time.monotonic()
    advice = await coach.analyze(_minimal_state())
    assert advice is None
    assert time.monotonic() - t0 < 2.0


async def test_coach_still_returns_advice_when_the_backend_answers() -> None:
    coach = CoachAgent(backend=_EchoBackend("  Keep the momentum.  "), timeout=5.0)
    assert await coach.analyze(_minimal_state()) == "Keep the momentum."


# ---------------------------------------------------------------------------
# Lesson + narrative — post-battle analysis must not stall the run
# ---------------------------------------------------------------------------


async def test_lesson_returns_empty_on_a_hanging_backend() -> None:
    t0 = time.monotonic()
    lesson = await generate_lesson(
        backend=_HangingBackend(),
        player_role="p1",
        winner=1,
        total_turns=10,
        opponent_label="random/random",
        turns=[],
        timeout=0.05,
    )
    assert lesson == ""
    assert time.monotonic() - t0 < 2.0


async def test_narrative_returns_empty_on_a_hanging_backend() -> None:
    t0 = time.monotonic()
    narrative = await generate_battle_narrative(
        backend=_HangingBackend(),
        analysis={},
        p1_label="p1",
        p2_label="p2",
        winner=1,
        total_turns=10,
        timeout=0.05,
    )
    assert narrative == ""
    assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------------------
# Draft — each pick is bounded, retries back off, and the fallback still lands
# ---------------------------------------------------------------------------


async def test_draft_falls_back_instead_of_hanging(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(draft_mod, "_RETRY_BACKOFF_SECONDS", 0.0)
    store = BattleStore(tmp_path / "hang.db")
    try:
        model_id = store.get_or_create_model("stub", "hang-model", "v3")
        t0 = time.monotonic()
        result = await run_draft(
            backend=_HangingBackend(),
            model_id=model_id,
            tier="ou",
            store=store,
            team_size=2,
            timeout=0.05,
        )
    finally:
        store.close()

    assert time.monotonic() - t0 < 5.0
    assert len(result.picked) == 2
    assert result.team_string.strip()


async def test_draft_backs_off_between_retries(tmp_path, monkeypatch) -> None:
    """Retries must be spaced out — an immediate retry against a stalled backend
    just burns the retry budget in the same instant (#278)."""
    monkeypatch.setattr(draft_mod, "_RETRY_BACKOFF_SECONDS", 0.2)
    store = BattleStore(tmp_path / "backoff.db")
    try:
        model_id = store.get_or_create_model("stub", "backoff-model", "v3")
        t0 = time.monotonic()
        await run_draft(
            backend=_HangingBackend(),
            model_id=model_id,
            tier="ou",
            store=store,
            team_size=1,
            timeout=0.01,
        )
    finally:
        store.close()

    # 3 attempts × ~0.01s timeouts plus backoffs of 0.2s and 0.4s.
    assert time.monotonic() - t0 >= 0.4


# ---------------------------------------------------------------------------
# LLMPlayer — a coach with its own deadline disabled must not stall the turn
# ---------------------------------------------------------------------------


async def test_player_acts_when_a_coach_would_hang_forever() -> None:
    """Backstop for a coach constructed without a deadline: `choose_move` must
    still return an action rather than waiting on the advisor."""
    from nidozo.battle.llm_player import LLMPlayer

    async def _hang(_state: object) -> str | None:
        await asyncio.sleep(_HANG)
        return None

    coach = MagicMock()
    coach.analyze = AsyncMock(side_effect=_hang)
    build_messages = MagicMock(
        return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    backend = MagicMock()
    backend.complete = AsyncMock(
        return_value='{"reasoning":"surf","action_type":"move","identifier":"surf"}'
    )
    battle = MagicMock()
    battle.turn = 1
    battle.battle_tag = "gen3randombattle-timeout-test"

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value={"turn": 1}),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = build_messages
        player._store = None
        player._battle_id = None
        player._player_role = "p1"
        player._on_thinking = None
        player._lessons = []
        player._coach = coach
        player._turn_timeout = 0.05
        player._personality = None

        t0 = time.monotonic()
        await player.choose_move(battle)

    assert time.monotonic() - t0 < 2.0
    # It acted on its own judgement, with no advice injected.
    assert build_messages.call_args[1].get("coach_advice") is None
    backend.complete.assert_called()
