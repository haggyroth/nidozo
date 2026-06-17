"""Tests for the pure/mockable helpers in api/orchestration.py.

Covers:
  - _terminate_players: swallows individual errors, skips None
  - _battle_and_teardown: always tears down on success, error, and cancellation
  - _bracket_advance_slot: deterministic tiebreak
  - _random_preset_team: delegates to team_builder + tiers
  - generate_and_store_narrative: happy path, both-random skip, empty turns, error
  - generate_and_store_lessons: happy path, random-player skip, error swallowed

No live Showdown server required. All poke-env / LLM calls are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nidozo.api.orchestration import (
    _battle_and_teardown,
    _bracket_advance_slot,
    _random_preset_team,
    _terminate_players,
    generate_and_store_lessons,
    generate_and_store_narrative,
)

# ---------------------------------------------------------------------------
# _terminate_players
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_players_calls_terminate_on_each() -> None:
    p1 = AsyncMock()
    p2 = AsyncMock()
    await _terminate_players(p1, p2)
    p1.terminate.assert_awaited_once()
    p2.terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminate_players_skips_none() -> None:
    p1 = AsyncMock()
    await _terminate_players(None, p1)
    p1.terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminate_players_swallows_individual_errors() -> None:
    p1 = AsyncMock()
    p1.terminate.side_effect = RuntimeError("websocket closed")
    p2 = AsyncMock()
    # Must not raise — one failure must not block the other teardown.
    await _terminate_players(p1, p2)
    p2.terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminate_players_empty_args_is_noop() -> None:
    # Should not raise with no arguments.
    await _terminate_players()


# ---------------------------------------------------------------------------
# _battle_and_teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_battle_and_teardown_calls_battle_against_then_teardown() -> None:
    p1 = AsyncMock()
    p2 = AsyncMock()
    p1.battle_against = AsyncMock()

    await _battle_and_teardown(p1, p2)

    p1.battle_against.assert_awaited_once_with(p2, n_battles=1)
    p1.terminate.assert_awaited_once()
    p2.terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_battle_and_teardown_tears_down_even_on_error() -> None:
    p1 = AsyncMock()
    p2 = AsyncMock()
    p1.battle_against = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await _battle_and_teardown(p1, p2)

    # Both players must still be torn down.
    p1.terminate.assert_awaited_once()
    p2.terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_battle_and_teardown_tears_down_on_cancellation() -> None:
    p1 = AsyncMock()
    p2 = AsyncMock()

    async def slow_battle(*_: object, **__: object) -> None:
        await asyncio.sleep(10)

    p1.battle_against = slow_battle

    task = asyncio.create_task(_battle_and_teardown(p1, p2))
    await asyncio.sleep(0)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    p1.terminate.assert_awaited_once()
    p2.terminate.assert_awaited_once()


# ---------------------------------------------------------------------------
# _bracket_advance_slot
# ---------------------------------------------------------------------------


def test_bracket_advance_slot_winner_1() -> None:
    assert _bracket_advance_slot(1, p1_seed=2, p2_seed=1) == 1


def test_bracket_advance_slot_winner_2() -> None:
    assert _bracket_advance_slot(2, p1_seed=1, p2_seed=2) == 2


def test_bracket_advance_slot_tie_p1_better_seed() -> None:
    # Lower seed number = better seed → p1 advances.
    assert _bracket_advance_slot(None, p1_seed=1, p2_seed=4) == 1


def test_bracket_advance_slot_tie_p2_better_seed() -> None:
    assert _bracket_advance_slot(None, p1_seed=3, p2_seed=2) == 2


# ---------------------------------------------------------------------------
# _random_preset_team
# ---------------------------------------------------------------------------


def test_random_preset_team_returns_string() -> None:
    fake_movesets = {"bulbasaur": {"moves": ["tackle", "growl"], "item": None, "nature": "Hardy",
                                   "evs": {}, "ivs": {}, "ability": "overgrow"}}
    fake_pool = ["bulbasaur", "charmander", "squirtle", "pikachu", "jigglypuff", "mewtwo"]

    with (
        patch("nidozo.api.orchestration.random") as mock_random,
        patch("nidozo.battle.team_builder.load_movesets", return_value=fake_movesets),
        patch("nidozo.battle.team_builder.all_species", return_value=set(fake_pool)),
        patch("nidozo.battle.tiers.get_pool", return_value=fake_pool),
        patch("nidozo.battle.team_builder.build_team_string", return_value="Bulbasaur @ ...\n..."),
    ):
        mock_random.sample.return_value = fake_pool[:6]
        result = _random_preset_team("gen3ou")

    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# generate_and_store_narrative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_store_narrative_happy_path() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    # These are deferred local imports inside the function, so patch at source.
    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
        patch(
            "nidozo.llm.narrator.generate_battle_narrative",
            new=AsyncMock(return_value="Ash won a close fight."),
        ),
    ):
        await generate_and_store_narrative(
            store=store,
            battle_id=42,
            winner=1,
            total_turns=20,
            p1_label="Ash",
            p2_label="Gary",
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p2_provider="openai",
            p2_model="gpt-4o",
        )

    store.set_battle_narrative.assert_called_once_with(42, "Ash won a close fight.")


@pytest.mark.asyncio
async def test_generate_and_store_narrative_both_random_returns_early() -> None:
    store = MagicMock()

    await generate_and_store_narrative(
        store=store,
        battle_id=1,
        winner=1,
        total_turns=10,
        p1_label="Bot1",
        p2_label="Bot2",
        p1_provider="random",
        p1_model=None,
        p2_provider="random",
        p2_model=None,
    )

    store.get_turns_with_state.assert_not_called()
    store.set_battle_narrative.assert_not_called()


@pytest.mark.asyncio
async def test_generate_and_store_narrative_falls_back_to_p2_provider() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    captured_provider: list[str] = []

    def fake_build_backend(provider: str, model: str | None, **_: object) -> object:
        captured_provider.append(provider)
        return AsyncMock()

    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", side_effect=fake_build_backend),
        patch(
            "nidozo.llm.narrator.generate_battle_narrative",
            new=AsyncMock(return_value="Gary won."),
        ),
    ):
        await generate_and_store_narrative(
            store=store,
            battle_id=2,
            winner=2,
            total_turns=12,
            p1_label="Bot",
            p2_label="Gary",
            p1_provider="random",
            p1_model=None,
            p2_provider="openai",
            p2_model="gpt-4o",
        )

    assert captured_provider == ["openai"]


@pytest.mark.asyncio
async def test_generate_and_store_narrative_empty_turns_returns_early() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = []

    with (
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
    ):
        await generate_and_store_narrative(
            store=store,
            battle_id=3,
            winner=1,
            total_turns=0,
            p1_label="Ash",
            p2_label="Gary",
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p2_provider="random",
            p2_model=None,
        )

    store.set_battle_narrative.assert_not_called()


@pytest.mark.asyncio
async def test_generate_and_store_narrative_error_is_swallowed() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    with (
        patch("nidozo.analysis.analyze_battle", side_effect=RuntimeError("boom")),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
    ):
        # Must not propagate — narrative generation must never crash the battle pipeline.
        await generate_and_store_narrative(
            store=store,
            battle_id=4,
            winner=1,
            total_turns=5,
            p1_label="Ash",
            p2_label="Gary",
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p2_provider="random",
            p2_model=None,
        )

    store.set_battle_narrative.assert_not_called()


@pytest.mark.asyncio
async def test_generate_and_store_narrative_empty_narrative_not_stored() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
        patch(
            "nidozo.llm.narrator.generate_battle_narrative",
            new=AsyncMock(return_value=""),
        ),
    ):
        await generate_and_store_narrative(
            store=store,
            battle_id=5,
            winner=1,
            total_turns=10,
            p1_label="Ash",
            p2_label="Gary",
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p2_provider="random",
            p2_model=None,
        )

    store.set_battle_narrative.assert_not_called()


# ---------------------------------------------------------------------------
# generate_and_store_lessons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_store_lessons_stores_for_both_models() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
        patch(
            "nidozo.llm.lesson_generator.generate_lesson",
            new=AsyncMock(return_value="P1 lesson."),
        ),
    ):
        await generate_and_store_lessons(
            store,
            battle_id=10,
            winner=1,
            total_turns=15,
            turns=[],
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p1_id=1,
            p1_opponent="Gary",
            p2_provider="openai",
            p2_model="gpt-4o",
            p2_id=2,
            p2_opponent="Ash",
        )

    # Both players should have a lesson stored.
    assert store.create_lesson.call_count == 2


@pytest.mark.asyncio
async def test_generate_and_store_lessons_skips_random_provider() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
        patch(
            "nidozo.llm.lesson_generator.generate_lesson",
            new=AsyncMock(return_value="Lesson."),
        ),
    ):
        await generate_and_store_lessons(
            store,
            battle_id=11,
            winner=1,
            total_turns=10,
            turns=[],
            p1_provider="random",
            p1_model=None,
            p1_id=None,
            p1_opponent="Gary",
            p2_provider="openai",
            p2_model="gpt-4o",
            p2_id=2,
            p2_opponent="Bot",
        )

    # Only p2 should receive a lesson.
    assert store.create_lesson.call_count == 1


@pytest.mark.asyncio
async def test_generate_and_store_lessons_error_is_swallowed() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
        patch(
            "nidozo.llm.lesson_generator.generate_lesson",
            new=AsyncMock(side_effect=RuntimeError("LLM exploded")),
        ),
    ):
        # Must not propagate.
        await generate_and_store_lessons(
            store,
            battle_id=12,
            winner=1,
            total_turns=5,
            turns=[],
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p1_id=1,
            p1_opponent="Gary",
            p2_provider="random",
            p2_model=None,
            p2_id=None,
            p2_opponent="Ash",
        )

    store.create_lesson.assert_not_called()


@pytest.mark.asyncio
async def test_generate_and_store_lessons_no_lesson_text_not_stored() -> None:
    store = MagicMock()
    store.get_turns_with_state.return_value = [{"turn": 1}]
    store.get_battle_teams.return_value = (None, None)

    with (
        patch("nidozo.analysis.analyze_battle", return_value={}),
        patch("nidozo.api.orchestration._build_backend", return_value=AsyncMock()),
        patch(
            "nidozo.llm.lesson_generator.generate_lesson",
            new=AsyncMock(return_value=""),
        ),
    ):
        await generate_and_store_lessons(
            store,
            battle_id=13,
            winner=1,
            total_turns=5,
            turns=[],
            p1_provider="anthropic",
            p1_model="claude-opus-4-8",
            p1_id=1,
            p1_opponent="Gary",
            p2_provider="random",
            p2_model=None,
            p2_id=None,
            p2_opponent="Ash",
        )

    store.create_lesson.assert_not_called()
