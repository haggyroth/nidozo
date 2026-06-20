"""Tests for the lesson-efficacy controls + cohort comparison (#227)."""

from __future__ import annotations

from typing import Any

from nidozo.api import orchestration
from nidozo.api.models import StartBattleRequest
from nidozo.db.store import BattleStore


def _finished(store, p1, p2, winner, tag, lessons_enabled):
    bid = store.create_battle(tag, "gen9randombattle", p1, p2, lessons_enabled=lessons_enabled)
    store.finish_battle(bid, winner=winner, total_turns=5)
    return bid


def test_lesson_efficacy_splits_cohorts(tmp_path) -> None:
    store = BattleStore(tmp_path / "eff.db")
    try:
        m = store.get_or_create_model("anthropic", "claude-x", "v9")
        opp = store.get_or_create_model("openai", "gpt-x", "v9")
        # With lessons: 2 wins, 1 loss.
        _finished(store, m, opp, 1, "w1", True)
        _finished(store, m, opp, 1, "w2", True)
        _finished(store, opp, m, 1, "w3", True)   # opp (p1) wins → m loses
        # Without lessons: 1 win, 1 loss.
        _finished(store, m, opp, 1, "n1", False)
        _finished(store, opp, m, 1, "n2", False)

        eff = store.get_lesson_efficacy(m)
        assert eff["with_lessons"]["wins"] == 2
        assert eff["with_lessons"]["losses"] == 1
        assert eff["with_lessons"]["win_rate"] == round(2 / 3, 4)
        assert eff["without_lessons"]["wins"] == 1
        assert eff["without_lessons"]["losses"] == 1
        assert eff["without_lessons"]["win_rate"] == 0.5
    finally:
        store.close()


def test_lesson_efficacy_is_in_model_stats(tmp_path) -> None:
    store = BattleStore(tmp_path / "eff2.db")
    try:
        m = store.get_or_create_model("anthropic", "claude-x", "v9")
        stats = store.get_model_stats(m)
        assert stats is not None
        assert "lesson_efficacy" in stats
        assert set(stats["lesson_efficacy"]) == {"with_lessons", "without_lessons"}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Runner honours the flag (suppresses lesson injection)
# ---------------------------------------------------------------------------

class _FakeBattle:
    def __init__(self, turn: int) -> None:
        self.turn = turn


class _FakePlayer:
    def __init__(self) -> None:
        self.n_won_battles = 1
        self.battles = {"battle-x": _FakeBattle(3)}

    async def battle_against(self, other: Any, n_battles: int = 1) -> None:
        pass

    async def terminate(self) -> None:
        pass


class _FakeBus:
    async def publish(self, event: dict[str, Any]) -> None:
        pass


async def test_run_battles_suppresses_lessons_when_disabled(tmp_path, monkeypatch) -> None:
    store = BattleStore(tmp_path / "supp.db")
    try:
        p1 = store.get_or_create_model("anthropic", "claude-x", "v9")
        p2 = store.get_or_create_model("anthropic", "claude-y", "v9")
        seed = store.create_battle("seed", "gen9randombattle", p1, p2)
        store.create_lesson(p1, seed, "an earlier lesson")  # p1 has a stored lesson

        bid = store.create_battle("t", "gen9randombattle", p1, p2, lessons_enabled=False)

        captured: dict[str, Any] = {}

        def fake_build(provider, model, role, prompt_version, *args, **kwargs):
            captured[role] = kwargs.get("lessons")
            return _FakePlayer()

        async def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(orchestration, "_build_streaming_player", fake_build)
        monkeypatch.setattr(orchestration, "generate_and_store_lessons", _noop)
        monkeypatch.setattr(orchestration, "generate_and_store_narrative", _noop)

        req = StartBattleRequest(
            p1_provider="anthropic", p1_model="claude-x",
            p2_provider="anthropic", p2_model="claude-y",
            tier="random", lessons_enabled=False,
        )
        await orchestration.run_battles(req, [bid], store, _FakeBus(), {})

        # Even though p1 has a stored lesson, none was injected (flag off).
        assert captured["p1"] is None
        assert captured["p2"] is None
    finally:
        store.close()
