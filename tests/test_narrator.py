"""Tests for llm/narrator.py — battle narrative generation.

All tests run without a live Showdown server or real LLM:
  - ModelBackend is replaced with an AsyncMock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nidozo.llm.narrator import (
    _build_narrative_context,
    _fmt_summary,
    generate_battle_narrative,
)

# ---------------------------------------------------------------------------
# _fmt_summary
# ---------------------------------------------------------------------------


def test_fmt_summary_no_turns() -> None:
    assert _fmt_summary({}, "Ash") == "Ash: no turn data"


def test_fmt_summary_zero_total_turns() -> None:
    assert _fmt_summary({"total_turns": 0}, "Ash") == "Ash: no turn data"


def test_fmt_summary_quality_pct_present() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 6, "good": 2, "qpct": None, "decision_quality_pct": 80}, "Ash")
    assert "80% quality" in result
    assert result.startswith("Ash:")


def test_fmt_summary_quality_pct_absent_uses_good_over_total() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3}, "Ash")
    assert "8/10 good turns" in result


def test_fmt_summary_single_blunder() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3, "blunders": 1}, "Ash")
    assert "1 blunder" in result
    assert "blunders" not in result.split("1 blunder")[1]


def test_fmt_summary_multiple_blunders() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3, "blunders": 3}, "Ash")
    assert "3 blunders" in result


def test_fmt_summary_no_blunders_omitted() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3, "blunders": 0}, "Ash")
    assert "blunder" not in result


def test_fmt_summary_single_switch() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3, "switch_turns": 1}, "Ash")
    assert "1 switch" in result
    assert "switches" not in result.split("1 switch")[1]


def test_fmt_summary_multiple_switches() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3, "switch_turns": 4}, "Ash")
    assert "4 switches" in result


def test_fmt_summary_no_switches_omitted() -> None:
    result = _fmt_summary({"total_turns": 10, "optimal": 5, "good": 3, "switch_turns": 0}, "Ash")
    assert "switch" not in result


# ---------------------------------------------------------------------------
# _build_narrative_context
# ---------------------------------------------------------------------------


def test_build_narrative_context_winner_p1() -> None:
    result = _build_narrative_context({}, "Ash", "Gary", "Ash", 20)
    assert "Ash won in 20 turns" in result
    assert "Ash (P1) vs Gary (P2)" in result


def test_build_narrative_context_winner_p2() -> None:
    result = _build_narrative_context({}, "Ash", "Gary", "Gary", 15)
    assert "Gary won in 15 turns" in result


def test_build_narrative_context_tie() -> None:
    result = _build_narrative_context({}, "Ash", "Gary", "neither player (tie)", 10)
    assert "neither player (tie) won" in result


def test_build_narrative_context_includes_player_summaries() -> None:
    analysis = {
        "p1_summary": {"total_turns": 10, "optimal": 5, "good": 3},
        "p2_summary": {"total_turns": 10, "optimal": 4, "good": 2},
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Ash", 10)
    assert "Ash:" in result
    assert "Gary:" in result


def test_build_narrative_context_skips_summaries_when_zero_turns() -> None:
    analysis = {
        "p1_summary": {"total_turns": 0},
        "p2_summary": {"total_turns": 0},
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Ash", 5)
    # No player summary lines should appear
    assert "Ash: no" not in result


def test_build_narrative_context_key_moments_p1_role() -> None:
    analysis = {
        "key_moments": [
            {"turn_number": 5, "type": "blunder", "description": "missed Surf", "player_role": "p1"},
        ]
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Gary", 20)
    assert "T5 [Ash] (blunder): missed Surf" in result


def test_build_narrative_context_key_moments_p2_role() -> None:
    analysis = {
        "key_moments": [
            {"turn_number": 3, "type": "turning_point", "description": "landed Fire Blast", "player_role": "p2"},
        ]
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Gary", 20)
    assert "T3 [Gary] (turning_point): landed Fire Blast" in result


def test_build_narrative_context_key_moments_no_role() -> None:
    analysis = {
        "key_moments": [
            {"turn_number": 7, "type": "crit", "description": "critical hit landed", "player_role": None},
        ]
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Ash", 20)
    assert "T7 (crit): critical hit landed" in result


def test_build_narrative_context_caps_key_moments_at_five() -> None:
    moments = [
        {"turn_number": i, "type": "event", "description": f"event {i}", "player_role": None}
        for i in range(8)
    ]
    result = _build_narrative_context({"key_moments": moments}, "A", "B", "A", 20)
    # Only the first 5 moments should appear
    assert "event 4" in result
    assert "event 5" not in result


def test_build_narrative_context_variance_report_with_crits_and_misses() -> None:
    analysis = {
        "variance_report": {
            "verdict": "High variance",
            "total_events": 3,
            "crits": [{"turn_number": 2}, {"turn_number": 8}],
            "misses": [{"turn_number": 5}],
        }
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Ash", 20)
    assert "RNG: High variance" in result
    assert "2, 8" in result
    assert "5" in result


def test_build_narrative_context_variance_verdict_skipped_when_zero_events() -> None:
    analysis = {
        "variance_report": {
            "verdict": "Low variance",
            "total_events": 0,
        }
    }
    result = _build_narrative_context(analysis, "Ash", "Gary", "Ash", 20)
    assert "RNG:" not in result


def test_build_narrative_context_empty_analysis() -> None:
    result = _build_narrative_context({}, "Ash", "Gary", "Ash", 10)
    assert "Ash won in 10 turns" in result
    assert "Key moments:" not in result
    assert "RNG:" not in result


# ---------------------------------------------------------------------------
# generate_battle_narrative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_battle_narrative_winner_p1() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="Ash dominated the battle from the start.")
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", 1, 15)
    assert result == "Ash dominated the battle from the start."
    # The p1_label should appear in the message sent to the backend.
    call_messages = backend.complete.call_args[0][0]
    assert any("Ash" in m["content"] for m in call_messages)


@pytest.mark.asyncio
async def test_generate_battle_narrative_winner_p2() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="Gary swept decisively.")
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", 2, 12)
    assert result == "Gary swept decisively."
    call_messages = backend.complete.call_args[0][0]
    assert any("Gary won" in m["content"] for m in call_messages)


@pytest.mark.asyncio
async def test_generate_battle_narrative_tie() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="A hard-fought draw.")
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", None, 25)
    assert result == "A hard-fought draw."
    call_messages = backend.complete.call_args[0][0]
    assert any("neither player (tie)" in m["content"] for m in call_messages)


@pytest.mark.asyncio
async def test_generate_battle_narrative_strips_whitespace() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="  Narrative with padding.  \n")
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", 1, 10)
    assert result == "Narrative with padding."


@pytest.mark.asyncio
async def test_generate_battle_narrative_backend_returns_none() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value=None)
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", 1, 10)
    assert result == ""


@pytest.mark.asyncio
async def test_generate_battle_narrative_backend_returns_empty() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="")
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", 1, 10)
    assert result == ""


@pytest.mark.asyncio
async def test_generate_battle_narrative_backend_raises_returns_empty() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(side_effect=RuntimeError("API timeout"))
    result = await generate_battle_narrative(backend, {}, "Ash", "Gary", 1, 10)
    assert result == ""


@pytest.mark.asyncio
async def test_generate_battle_narrative_includes_system_prompt() -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="A story.")
    await generate_battle_narrative(backend, {}, "Ash", "Gary", 1, 10)
    messages = backend.complete.call_args[0][0]
    assert messages[0]["role"] == "system"
    assert "commentator" in messages[0]["content"].lower()
