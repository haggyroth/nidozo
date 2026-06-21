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


# ---------------------------------------------------------------------------
# Pure / standalone helpers
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

from nidozo.api import orchestration  # noqa: E402
from nidozo.api.orchestration import (  # noqa: E402
    _bracket_advance_slot,
    _random_preset_team,
    _showdown_cfg,
)


def test_bracket_advance_slot_uses_winner_when_decided() -> None:
    assert _bracket_advance_slot(1, 3, 5) == 1
    assert _bracket_advance_slot(2, 3, 5) == 2


def test_bracket_advance_slot_tiebreaks_to_better_seed() -> None:
    # On a tie (winner None) the lower seed number advances.
    assert _bracket_advance_slot(None, 2, 5) == 1
    assert _bracket_advance_slot(None, 5, 2) == 2


def test_random_preset_team_builds_requested_size() -> None:
    team6 = _random_preset_team("ou", 6)
    assert team6.count("\n\n") == 5  # 6 mons → 5 separators
    team3 = _random_preset_team("ou", 3)
    assert team3.count("\n\n") == 2


def test_showdown_cfg_defaults_to_localhost(monkeypatch) -> None:
    monkeypatch.delenv("NIDOZO_SHOWDOWN_HOST", raising=False)
    monkeypatch.delenv("NIDOZO_SHOWDOWN_PORT", raising=False)
    cfg = _showdown_cfg()
    assert cfg.websocket_url == "ws://localhost:8000/showdown/websocket"


def test_showdown_cfg_honours_env(monkeypatch) -> None:
    monkeypatch.setenv("NIDOZO_SHOWDOWN_HOST", "showdown")
    monkeypatch.setenv("NIDOZO_SHOWDOWN_PORT", "9999")
    cfg = _showdown_cfg()
    assert cfg.websocket_url == "ws://showdown:9999/showdown/websocket"


async def test_spawn_post_battle_schedules_lessons_and_narrative(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_lessons(store, battle_id, winner, total_turns, turns, **kw) -> None:
        calls.append("lessons")

    async def fake_narrative(store, battle_id, winner, total_turns, **kw) -> None:
        calls.append("narrative")

    monkeypatch.setattr(orchestration, "generate_and_store_lessons", fake_lessons)
    monkeypatch.setattr(orchestration, "generate_and_store_narrative", fake_narrative)

    store = MagicMock()
    store.get_turns_basic.return_value = []
    outcome = _BattleOutcome(winner=1, total_turns=5, real_tag="t", new_badges=[])

    orchestration._spawn_post_battle(
        store=store, battle_id=7, outcome=outcome,
        lessons_kwargs={
            "p1_provider": "a", "p1_model": "m", "p1_id": 1, "p1_opponent": "b/n",
            "p2_provider": "b", "p2_model": "n", "p2_id": 2, "p2_opponent": "a/m",
        },
        narrative_kwargs={
            "p1_label": "a/m", "p2_label": "b/n",
            "p1_provider": "a", "p1_model": "m", "p2_provider": "b", "p2_model": "n",
        },
    )

    # Let the fire-and-forget tasks run.
    await asyncio.sleep(0.01)
    assert sorted(calls) == ["lessons", "narrative"]
    store.get_turns_basic.assert_called_once_with(7)


# ---------------------------------------------------------------------------
# run_battles — full body driven with fake players (no Showdown / LLM)
# ---------------------------------------------------------------------------

async def test_run_battles_random_executes_full_body(tmp_path, monkeypatch) -> None:
    """A random-vs-random run exercises run_battles end to end with fakes."""
    from nidozo.api.models import StartBattleRequest

    store = BattleStore(tmp_path / "rb.db")
    try:
        p1id = store.get_or_create_model("random", "random", "v9")
        p2id = store.get_or_create_model("random", "random", "v9")
        bid = store.create_battle("pending-rb", "gen9randombattle", p1id, p2id)
        bus = _FakeBus()

        def fake_build(provider, model, role, *args, **kwargs):
            return _FakePlayer(won=(role == "p1"), tag="battle-rb-1", turns=4)

        monkeypatch.setattr(orchestration, "_build_streaming_player", fake_build)

        req = StartBattleRequest(
            p1_provider="random", p2_provider="random", tier="random", n_battles=1,
        )
        await orchestration.run_battles(req, [bid], store, bus, {})

        # Let the fire-and-forget post-battle tasks settle (both random → no-ops).
        await asyncio.sleep(0.01)

        row = store.get_battle(bid)
        assert row is not None
        assert row["status"] == "completed"
        assert row["winner"] == 1
        assert row["battle_tag"] == "battle-rb-1"

        types = {e["type"] for e in bus.events}
        assert "battle_start" in types
        assert "battle_end" in types
    finally:
        store.close()


# ---------------------------------------------------------------------------
# run_tournament / run_season — full bodies driven with fake players
# ---------------------------------------------------------------------------

import uuid  # noqa: E402


def _random_specs() -> list[dict]:
    return [
        {"provider": "random", "model_name": "random", "coach_provider": None,
         "coach_model": None, "personality": None, "preset": None},
        {"provider": "random", "model_name": "random2", "coach_provider": None,
         "coach_model": None, "personality": None, "preset": None},
    ]


def _fake_build_unique(provider, model, role, *args, **kwargs):
    # Unique tag per call so update_battle_tag never collides across battles.
    return _FakePlayer(won=(role == "p1"), tag=f"battle-{uuid.uuid4().hex[:8]}", turns=3)


async def test_run_tournament_round_robin_completes(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import PlayerSpec, StartTournamentRequest

    store = BattleStore(tmp_path / "rt.db")
    try:
        specs = _random_specs()
        tid = store.create_tournament(
            players=specs, rounds=1, prompt_version="v9", total_battles=2,
            tier="random", tournament_format="round_robin",
        )
        a = store.get_or_create_model("random", "random", "v9")
        b = store.get_or_create_model("random", "random2", "v9")
        bids = [
            store.create_battle(f"t-{tid}-0", "gen9randombattle", a, b, tournament_id=tid),
            store.create_battle(f"t-{tid}-1", "gen9randombattle", b, a, tournament_id=tid),
        ]
        bus = _FakeBus()
        monkeypatch.setattr(orchestration, "_build_streaming_player", _fake_build_unique)

        req = StartTournamentRequest(
            players=[PlayerSpec(provider="random"), PlayerSpec(provider="random", model="random2")],
            rounds=1, tier="random", tournament_format="round_robin",
        )
        await orchestration.run_tournament(req, tid, bids, specs, store, bus, {})
        await asyncio.sleep(0.01)

        assert store.get_tournament(tid)["status"] == "completed"
        assert sum(1 for e in bus.events if e["type"] == "battle_end") == 2
        assert any(e["type"] == "tournament_end" for e in bus.events)
    finally:
        store.close()


async def test_run_season_round_robin_completes(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import PlayerSpec, StartSeasonRequest

    store = BattleStore(tmp_path / "rs.db")
    try:
        specs = _random_specs()
        sid = store.create_season(
            name="S1", tier="random", fmt="gen9randombattle",
            participants=specs, rounds=1, prompt_version="v9", total_battles=2,
        )
        a = store.get_or_create_model("random", "random", "v9")
        b = store.get_or_create_model("random", "random2", "v9")
        bids = [
            store.create_battle(f"s-{sid}-0", "gen9randombattle", a, b, season_id=sid),
            store.create_battle(f"s-{sid}-1", "gen9randombattle", b, a, season_id=sid),
        ]
        bus = _FakeBus()
        monkeypatch.setattr(orchestration, "_build_streaming_player", _fake_build_unique)

        req = StartSeasonRequest(
            name="S1",
            players=[PlayerSpec(provider="random"), PlayerSpec(provider="random", model="random2")],
            rounds=1, tier="random",
        )
        await orchestration.run_season(req, sid, bids, specs, store, bus, {})
        await asyncio.sleep(0.01)

        assert store.get_season(sid)["status"] == "completed"
        assert sum(1 for e in bus.events if e["type"] == "battle_end") == 2
        assert any(e["type"] == "season_end" for e in bus.events)
    finally:
        store.close()


async def test_run_bracket_single_elim_completes(tmp_path, monkeypatch) -> None:
    """A 2-player single-elim bracket plays its one match and crowns a champion."""
    from nidozo.api.models import PlayerSpec, StartTournamentRequest

    store = BattleStore(tmp_path / "rbk.db")
    try:
        specs = _random_specs()
        tid = store.create_tournament(
            players=specs, rounds=1, prompt_version="v9", total_battles=1,
            tier="random", tournament_format="single_elim",
        )
        bus = _FakeBus()
        monkeypatch.setattr(orchestration, "_build_streaming_player", _fake_build_unique)

        req = StartTournamentRequest(
            players=[PlayerSpec(provider="random"), PlayerSpec(provider="random", model="random2")],
            rounds=1, tier="random", tournament_format="single_elim",
        )
        await orchestration.run_bracket_tournament(req, tid, specs, store, bus, {})
        await asyncio.sleep(0.01)

        assert store.get_tournament(tid)["status"] == "completed"
        end = next(e for e in bus.events if e["type"] == "tournament_end")
        assert end["champion"] is not None
        assert any(e["type"] == "bracket_update" for e in bus.events)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Draft / cancellation / failure branches (#236)
# ---------------------------------------------------------------------------

class _RaisingPlayer:
    """Fake player whose battle_against raises a chosen exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.n_won_battles = 0
        self.battles: dict[str, Any] = {}

    async def battle_against(self, other: Any, n_battles: int = 1) -> None:
        raise self._exc

    async def terminate(self) -> None:
        pass


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None


async def test_run_battles_draft_branch(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import StartBattleRequest

    store = BattleStore(tmp_path / "draft.db")
    try:
        p1 = store.get_or_create_model("anthropic", "claude-x", "v3")
        p2 = store.get_or_create_model("anthropic", "claude-y", "v3")
        bid = store.create_battle("d", "gen9nationaldex", p1, p2)

        async def fake_draft(backend, model_id, tier, store_, bus_, role, team_size=6, doubles=False):
            team_id = store_.save_team(model_id, tier, "gen9nationaldex", ["pikachu"], "Pikachu")
            return {"team_string": "Pikachu", "team_id": team_id}

        monkeypatch.setattr(orchestration, "run_draft_phase", fake_draft)
        monkeypatch.setattr(orchestration, "_build_backend", lambda *a, **k: object())
        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *a, **k: _FakePlayer(won=True, tag=f"b-{uuid.uuid4().hex[:8]}", turns=3))
        monkeypatch.setattr(orchestration, "generate_and_store_lessons", _noop)
        monkeypatch.setattr(orchestration, "generate_and_store_narrative", _noop)

        req = StartBattleRequest(
            p1_provider="anthropic", p1_model="claude-x",
            p2_provider="anthropic", p2_model="claude-y",
            tier="ou", draft=True, n_battles=1,
        )
        bus = _FakeBus()
        await orchestration.run_battles(req, [bid], store, bus, {})

        roles = {e.get("player_role") for e in bus.events if e["type"] == "draft_start"}
        assert roles == {"p1", "p2"}
        assert store.get_battle(bid)["tier"] == "ou"  # set_battle_teams ran
    finally:
        store.close()


async def test_run_battles_cancellation_strands_queued(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import StartBattleRequest

    store = BattleStore(tmp_path / "cancel.db")
    try:
        p1 = store.get_or_create_model("random", "random", "v9")
        p2 = store.get_or_create_model("random", "random2", "v9")
        b1 = store.create_battle("c1", "gen9randombattle", p1, p2)
        b2 = store.create_battle("c2", "gen9randombattle", p1, p2)

        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *a, **k: _RaisingPlayer(asyncio.CancelledError()))

        req = StartBattleRequest(p1_provider="random", p2_provider="random", tier="random", n_battles=2)
        bus = _FakeBus()
        import pytest
        with pytest.raises(asyncio.CancelledError):
            await orchestration.run_battles(req, [b1, b2], store, bus, {})

        assert store.get_battle(b1)["status"] == "cancelled"
        assert store.get_battle(b2)["status"] == "cancelled"  # stranded → cancelled
        assert any(e["type"] == "battle_cancelled" and e["battle_id"] == b2 for e in bus.events)
    finally:
        store.close()


async def test_run_battles_failure_marks_failed(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import StartBattleRequest

    store = BattleStore(tmp_path / "fail.db")
    try:
        p1 = store.get_or_create_model("random", "random", "v9")
        p2 = store.get_or_create_model("random", "random2", "v9")
        bid = store.create_battle("f", "gen9randombattle", p1, p2)

        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *a, **k: _RaisingPlayer(ValueError("boom")))

        req = StartBattleRequest(p1_provider="random", p2_provider="random", tier="random", n_battles=1)
        bus = _FakeBus()
        await orchestration.run_battles(req, [bid], store, bus, {})  # no raise

        assert store.get_battle(bid)["status"] == "failed"
        assert any(e["type"] == "error" and e["battle_id"] == bid for e in bus.events)
    finally:
        store.close()


async def test_run_tournament_stops_when_cancelled(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import PlayerSpec, StartTournamentRequest

    store = BattleStore(tmp_path / "tcancel.db")
    try:
        specs = _random_specs()
        tid = store.create_tournament(
            players=specs, rounds=1, prompt_version="v9", total_battles=2,
            tier="random", tournament_format="round_robin",
        )
        a = store.get_or_create_model("random", "random", "v9")
        b = store.get_or_create_model("random", "random2", "v9")
        bids = [store.create_battle(f"tc-{i}", "gen9randombattle", a, b, tournament_id=tid) for i in range(2)]
        store.cancel_tournament(tid)  # flip status before the runner starts

        req = StartTournamentRequest(
            players=[PlayerSpec(provider="random"), PlayerSpec(provider="random", model="random2")],
            rounds=1, tier="random",
        )
        bus = _FakeBus()
        await orchestration.run_tournament(req, tid, bids, specs, store, bus, {})
        assert any(e["type"] == "tournament_cancelled" for e in bus.events)
        # The runner stops before playing anything — no battle was completed.
        assert not any(e["type"] == "battle_end" for e in bus.events)
        assert store.get_battle(bids[0])["status"] == "pending"
    finally:
        store.close()


async def test_run_bracket_marks_failed_on_match_error(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import PlayerSpec, StartTournamentRequest

    store = BattleStore(tmp_path / "bfail.db")
    try:
        specs = _random_specs()
        tid = store.create_tournament(
            players=specs, rounds=1, prompt_version="v9", total_battles=1,
            tier="random", tournament_format="single_elim",
        )
        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *a, **k: _RaisingPlayer(ValueError("boom")))

        req = StartTournamentRequest(
            players=[PlayerSpec(provider="random"), PlayerSpec(provider="random", model="random2")],
            rounds=1, tier="random", tournament_format="single_elim",
        )
        bus = _FakeBus()
        await orchestration.run_bracket_tournament(req, tid, specs, store, bus, {})
        assert store.get_tournament(tid)["status"] == "failed"
        assert any(e["type"] == "tournament_failed" for e in bus.events)
    finally:
        store.close()


async def test_run_season_stops_when_cancelled(tmp_path, monkeypatch) -> None:
    from nidozo.api.models import PlayerSpec, StartSeasonRequest

    store = BattleStore(tmp_path / "scancel.db")
    try:
        specs = _random_specs()
        sid = store.create_season(
            name="S", tier="random", fmt="gen9randombattle",
            participants=specs, rounds=1, prompt_version="v9", total_battles=2,
        )
        a = store.get_or_create_model("random", "random", "v9")
        b = store.get_or_create_model("random", "random2", "v9")
        bids = [store.create_battle(f"sc-{i}", "gen9randombattle", a, b, season_id=sid) for i in range(2)]
        store.cancel_season(sid)  # status -> cancelled

        # run_season normally flips status back to 'running' first; stub that out
        # so the loop's cancel check fires. Also guard against ever building a
        # real player (which would hang trying to reach Showdown).
        monkeypatch.setattr(store, "set_season_running", lambda _sid: None)
        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *a, **k: _FakePlayer(won=True, tag="x", turns=1))

        req = StartSeasonRequest(
            name="S",
            players=[PlayerSpec(provider="random"), PlayerSpec(provider="random", model="random2")],
            rounds=1, tier="random",
        )
        bus = _FakeBus()
        await orchestration.run_season(req, sid, bids, specs, store, bus, {})
        assert any(e["type"] == "season_cancelled" for e in bus.events)
        assert not any(e["type"] == "battle_end" for e in bus.events)
    finally:
        store.close()


def _experiment_setup(store):
    va = {"provider": "openai", "model_name": "gpt-4o", "prompt_version": "v9"}
    vb = {"provider": "openai", "model_name": "gpt-4o", "prompt_version": "v8"}
    a = store.get_or_create_model(va["provider"], va["model_name"], va["prompt_version"])
    b = store.get_or_create_model(vb["provider"], vb["model_name"], vb["prompt_version"])
    eid = store.create_experiment(
        name="x", variant_a=va, variant_b=vb, a_model_id=a, b_model_id=b,
        tier="random", fmt="gen9randombattle", n_battles=2,
    )
    bids = []
    for i in range(2):
        p1, p2 = (a, b) if i % 2 == 0 else (b, a)
        bids.append(store.create_battle(f"x-{eid}-{i}", "gen9randombattle", p1, p2, experiment_id=eid))
    return va, vb, a, b, eid, bids


def _experiment_req():
    from nidozo.api.models import ExperimentVariant, StartExperimentRequest
    return StartExperimentRequest(
        name="x",
        variant_a=ExperimentVariant(provider="openai", model="gpt-4o", prompt_version="v9"),
        variant_b=ExperimentVariant(provider="openai", model="gpt-4o", prompt_version="v8"),
        n_battles=2, tier="random",
    )


async def test_run_experiment_marks_failed_battle(tmp_path, monkeypatch) -> None:
    store = BattleStore(tmp_path / "xfail.db")
    try:
        va, vb, a, b, eid, bids = _experiment_setup(store)
        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *args, **kw: _RaisingPlayer(ValueError("boom")))
        bus = _FakeBus()
        await orchestration.run_experiment(_experiment_req(), eid, bids, va, vb, a, b, store, bus, {})
        assert any(e["type"] == "error" for e in bus.events)
        assert store.get_battle(bids[0])["status"] == "failed"
        assert store.get_experiment(eid)["status"] == "completed"
    finally:
        store.close()


async def test_run_experiment_cancelled_during_battle(tmp_path, monkeypatch) -> None:
    store = BattleStore(tmp_path / "xcancel.db")
    try:
        va, vb, a, b, eid, bids = _experiment_setup(store)
        monkeypatch.setattr(orchestration, "_build_streaming_player",
                            lambda *args, **kw: _RaisingPlayer(asyncio.CancelledError()))
        bus = _FakeBus()
        with pytest.raises(asyncio.CancelledError):
            await orchestration.run_experiment(_experiment_req(), eid, bids, va, vb, a, b, store, bus, {})
        assert store.get_experiment(eid)["status"] == "cancelled"
        assert any(e["type"] == "experiment_cancelled" for e in bus.events)
    finally:
        store.close()
