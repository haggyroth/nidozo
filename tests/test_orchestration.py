"""Tests for the shared battle-execution helpers in orchestration.py.

These exercise ``_play_battle`` directly with fakes (no Showdown server, no
LLM), covering the lifecycle every runner now shares: status → battle_start →
play + teardown → winner/tag/turns → finish_battle → battle_end → badges.
"""

from __future__ import annotations

from typing import Any

from nidozo.api.orchestration import _BattleOutcome, _play_battle
from nidozo.db.store import BattleStore


class _FakeBattle:
    def __init__(self, turn: int) -> None:
        self.turn = turn


class _FakePlayer:
    """Stand-in for a poke-env streaming player used by _play_battle."""

    def __init__(self, *, won: bool, tag: str, turns: int) -> None:
        self.n_won_battles = 1 if won else 0
        self._tag = tag
        self._turns = turns
        self.battles: dict[str, _FakeBattle] = {}
        self.terminated = False

    async def battle_against(self, other: Any, n_battles: int = 1) -> None:
        # Simulate the battle resolving and registering a battle room.
        self.battles[self._tag] = _FakeBattle(self._turns)

    async def terminate(self) -> None:
        self.terminated = True


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _seed_battle(store: BattleStore) -> int:
    p1 = store.get_or_create_model("anthropic", "claude-x", "v9")
    p2 = store.get_or_create_model("openai", "gpt-x", "v9")
    return store.create_battle("pending-tag", "gen9randombattle", p1, p2)


async def test_play_battle_records_p1_win(tmp_path) -> None:
    store = BattleStore(tmp_path / "play.db")
    try:
        battle_id = _seed_battle(store)
        bus = _FakeBus()
        p1 = _FakePlayer(won=True, tag="battle-gen9-123", turns=11)
        p2 = _FakePlayer(won=False, tag="battle-gen9-123", turns=11)

        outcome = await _play_battle(
            battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
            p1_label="anthropic/claude-x", p2_label="openai/gpt-x",
            start_extra={"tier": "random"},
            end_extra={"tournament_id": 42},
        )

        assert isinstance(outcome, _BattleOutcome)
        assert outcome.winner == 1
        assert outcome.total_turns == 11
        assert outcome.real_tag == "battle-gen9-123"

        # Both players were torn down even on the happy path.
        assert p1.terminated and p2.terminated

        # DB reflects a completed, won battle with the real tag.
        row = store.get_battle(battle_id)
        assert row is not None
        assert row["status"] == "completed"
        assert row["winner"] == 1
        assert row["total_turns"] == 11
        assert row["battle_tag"] == "battle-gen9-123"

        # Event sequence: battle_start (with extra) then battle_end (with tag + extra).
        types = [e["type"] for e in bus.events]
        assert types[0] == "battle_start"
        assert types.count("battle_end") == 1
        start = bus.events[0]
        assert start["p1"] == "anthropic/claude-x"
        assert start["tier"] == "random"
        end = next(e for e in bus.events if e["type"] == "battle_end")
        assert end["winner"] == 1
        assert end["battle_tag"] == "battle-gen9-123"
        assert end["tournament_id"] == 42
    finally:
        store.close()


async def test_play_battle_records_tie(tmp_path) -> None:
    store = BattleStore(tmp_path / "tie.db")
    try:
        battle_id = _seed_battle(store)
        bus = _FakeBus()
        p1 = _FakePlayer(won=False, tag="battle-tie-1", turns=4)
        p2 = _FakePlayer(won=False, tag="battle-tie-1", turns=4)

        outcome = await _play_battle(
            battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
            p1_label="a/x", p2_label="b/y",
        )

        assert outcome.winner is None
        row = store.get_battle(battle_id)
        assert row is not None
        assert row["status"] == "completed"
        assert row["winner"] is None
    finally:
        store.close()
