"""Tests for the human player — queue registry and API endpoint."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Queue registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_pending():
    """Ensure _pending is empty before and after each test."""
    from nidozo.battle.human_player import _pending

    _pending.clear()
    yield
    _pending.clear()


def test_register_creates_future():
    from nidozo.battle.human_player import _pending, register_pending

    fut = register_pending(1, "p1")
    assert (1, "p1") in _pending
    assert not fut.done()


def test_resolve_pending_resolves_future():
    from nidozo.battle.human_player import register_pending, resolve_pending

    fut = register_pending(42, "p2")
    resolved = resolve_pending(42, "p2", '{"action_type":"move","identifier":"tackle"}')
    assert resolved is True
    assert fut.done()
    assert fut.result() == '{"action_type":"move","identifier":"tackle"}'


def test_resolve_pending_removes_from_registry():
    from nidozo.battle.human_player import _pending, register_pending, resolve_pending

    register_pending(10, "p1")
    resolve_pending(10, "p1", "action")
    assert (10, "p1") not in _pending


def test_resolve_pending_returns_false_when_none_registered():
    from nidozo.battle.human_player import resolve_pending

    resolved = resolve_pending(99, "p1", "action")
    assert resolved is False


def test_resolve_pending_returns_false_on_double_resolve():
    from nidozo.battle.human_player import register_pending, resolve_pending

    register_pending(5, "p1")
    resolve_pending(5, "p1", "first")
    resolved = resolve_pending(5, "p1", "second")
    assert resolved is False


def test_has_pending_true_when_registered():
    from nidozo.battle.human_player import has_pending, register_pending

    register_pending(7, "p2")
    assert has_pending(7, "p2") is True


def test_has_pending_false_when_not_registered():
    from nidozo.battle.human_player import has_pending

    assert has_pending(999, "p1") is False


def test_has_pending_false_after_resolve():
    from nidozo.battle.human_player import has_pending, register_pending, resolve_pending

    register_pending(3, "p1")
    resolve_pending(3, "p1", "done")
    assert has_pending(3, "p1") is False


def test_cancel_pending_cancels_future():
    from nidozo.battle.human_player import _pending, cancel_pending, register_pending

    fut = register_pending(8, "p1")
    cancel_pending(8, "p1")
    assert fut.cancelled()
    assert (8, "p1") not in _pending


def test_cancel_pending_noop_when_none():
    from nidozo.battle.human_player import cancel_pending

    cancel_pending(404, "p2")  # should not raise


# ---------------------------------------------------------------------------
# API endpoint — POST /api/battles/{id}/human-action
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path):
    from nidozo.api.app import create_app

    return create_app(db_path=tmp_path / "test.db")


@pytest.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_human_action_endpoint_resolves_pending(client):
    from nidozo.battle.human_player import register_pending

    fut = register_pending(1, "p1")
    resp = await client.post(
        "/api/battles/1/human-action",
        json={"player_role": "p1", "action_type": "move", "identifier": "tackle"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert fut.done()


@pytest.mark.asyncio
async def test_human_action_endpoint_409_when_no_pending(client):
    resp = await client.post(
        "/api/battles/1/human-action",
        json={"player_role": "p1", "action_type": "move", "identifier": "tackle"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_human_action_endpoint_validates_player_role(client):
    resp = await client.post(
        "/api/battles/1/human-action",
        json={"player_role": "p3", "action_type": "move", "identifier": "tackle"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_human_action_endpoint_validates_action_type(client):
    resp = await client.post(
        "/api/battles/1/human-action",
        json={"player_role": "p1", "action_type": "flee", "identifier": "tackle"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_human_action_endpoint_encodes_json_correctly(client):
    from nidozo.battle.human_player import register_pending

    fut = register_pending(2, "p2")
    await client.post(
        "/api/battles/2/human-action",
        json={"player_role": "p2", "action_type": "switch", "identifier": "pikachu"},
    )
    payload = json.loads(fut.result())
    assert payload == {"action_type": "switch", "identifier": "pikachu"}


# ---------------------------------------------------------------------------
# Model validation — "human" is a valid Provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_battle_accepts_human_provider(client):
    """human provider is accepted by the API without a 422."""
    resp = await client.post(
        "/api/start-battle",
        json={"p1_provider": "human", "p2_provider": "random", "tier": "random"},
    )
    # May fail for other reasons (Showdown not running) but not 422
    assert resp.status_code != 422


@pytest.mark.asyncio
async def test_human_provider_excluded_from_lessons():
    """generate_and_store_lessons skips human providers like random."""
    from nidozo.api.orchestration import generate_and_store_lessons

    store = MagicMock()
    store.get_turns_with_state.return_value = []
    store.get_battle_teams.return_value = (None, None)

    await generate_and_store_lessons(
        store,
        1,
        1,
        10,
        [],
        p1_provider="human",
        p1_model="human",
        p1_id=1,
        p1_opponent="random/random",
        p2_provider="random",
        p2_model=None,
        p2_id=None,
        p2_opponent="human/human",
    )
    store.create_lesson.assert_not_called()


# ---------------------------------------------------------------------------
# StreamingHumanPlayer.choose_move — future / timeout / parse-fail cycle
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from nidozo.api.events import EventBus  # noqa: E402
from nidozo.battle.human_player import StreamingHumanPlayer  # noqa: E402


class _FakeOrder:
    def __init__(self, message: str) -> None:
        self.message = message


class _FakeBattle:
    battle_tag = "battle-test-1"
    turn = 3
    finished = False
    available_moves: list = []  # len 0 → skip the recharge shortcut


def _make_human_player(bus: EventBus, *, battle_id=1, store=None, timeout=5.0):
    """Build a StreamingHumanPlayer without poke-env's networking __init__."""
    p = StreamingHumanPlayer.__new__(StreamingHumanPlayer)
    p._bus = bus
    p._player_role = "p1"
    p._battle_id = battle_id
    p._store = store
    p._human_timeout = timeout
    p._chose_during_frame = False
    # Stub the poke-env methods choose_move would otherwise call.
    p.choose_random_move = lambda battle: _FakeOrder("randmove")  # type: ignore[method-assign]
    p.create_order = lambda m: _FakeOrder("recharge")  # type: ignore[attr-defined]
    return p


def _drain(q) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_choose_move_times_out_to_random(monkeypatch):
    monkeypatch.setattr("nidozo.battle.human_player.serialize_battle", lambda b, *a, **k: {})
    bus = EventBus()
    q = bus.subscribe()
    player = _make_human_player(bus, battle_id=1, store=None, timeout=0.05)

    order = await player.choose_move(_FakeBattle())

    assert order.message == "randmove"
    events = _drain(q)
    assert any(e["type"] == "human_action_required" for e in events)
    turn = next(e for e in events if e["type"] == "turn")
    assert "timeout fallback" in turn["action"]


@pytest.mark.asyncio
async def test_choose_move_parse_failure_falls_back(monkeypatch):
    monkeypatch.setattr("nidozo.battle.human_player.serialize_battle", lambda b, *a, **k: {})
    monkeypatch.setattr("nidozo.battle.human_player.parse_action", lambda *a, **k: None)
    from nidozo.battle.human_player import resolve_pending

    bus = EventBus()
    q = bus.subscribe()
    player = _make_human_player(bus, battle_id=2, store=None, timeout=5.0)

    task = asyncio.create_task(player.choose_move(_FakeBattle()))
    await asyncio.sleep(0)  # let choose_move register its pending future
    assert resolve_pending(2, "p1", '{"action_type":"move","identifier":"???"}')
    order = await task

    assert order.message == "randmove"
    turn = next(e for e in _drain(q) if e["type"] == "turn")
    assert "invalid input" in turn["action"]


@pytest.mark.asyncio
async def test_choose_move_success_returns_parsed_order(monkeypatch):
    monkeypatch.setattr("nidozo.battle.human_player.serialize_battle", lambda b, *a, **k: {})
    monkeypatch.setattr(
        "nidozo.battle.human_player.parse_action",
        lambda *a, **k: _FakeOrder("move tackle"),
    )
    from nidozo.battle.human_player import resolve_pending

    bus = EventBus()
    q = bus.subscribe()
    store = MagicMock()
    player = _make_human_player(bus, battle_id=3, store=store, timeout=5.0)

    task = asyncio.create_task(player.choose_move(_FakeBattle()))
    await asyncio.sleep(0)
    assert resolve_pending(3, "p1", '{"action_type":"move","identifier":"tackle"}')
    order = await task

    assert order.message == "move tackle"
    # The successful turn was logged with parse_success=True.
    store.log_turn.assert_called_once()
    assert store.log_turn.call_args.kwargs["parse_success"] is True
    turn = next(e for e in _drain(q) if e["type"] == "turn")
    assert turn["action"] == "move tackle"


@pytest.mark.asyncio
async def test_choose_move_recharge_shortcut(monkeypatch):
    """A lone forced 'recharge' move skips the human prompt entirely."""
    monkeypatch.setattr("nidozo.battle.human_player.serialize_battle", lambda b, *a, **k: {})
    bus = EventBus()
    q = bus.subscribe()
    player = _make_human_player(bus, battle_id=4)

    battle = _FakeBattle()
    battle.available_moves = [type("M", (), {"id": "recharge"})()]
    order = await player.choose_move(battle)

    assert order.message == "recharge"
    # No human_action_required is emitted on a forced recharge turn.
    assert not any(e["type"] == "human_action_required" for e in _drain(q))


@pytest.mark.asyncio
async def test_terminate_cancels_pending(monkeypatch):
    from nidozo.battle.human_player import _pending, register_pending

    bus = EventBus()
    player = _make_human_player(bus, battle_id=9)

    stopped = {"called": False}

    class _FakePS:
        async def stop_listening(self):
            stopped["called"] = True

    player.ps_client = _FakePS()  # type: ignore[attr-defined]

    fut = register_pending(9, "p1")
    await player.terminate()

    assert fut.cancelled()
    assert (9, "p1") not in _pending
    assert stopped["called"]


# ---------------------------------------------------------------------------
# _log_turn
# ---------------------------------------------------------------------------

def test_log_turn_writes_to_store():
    bus = EventBus()
    store = MagicMock()
    player = _make_human_player(bus, battle_id=11, store=store)
    player._log_turn(1, "move 1", True, "raw", state_json="{}")
    store.log_turn.assert_called_once()
    assert store.log_turn.call_args.kwargs["player_role"] == "p1"


def test_log_turn_swallows_store_errors():
    bus = EventBus()
    store = MagicMock()
    store.log_turn.side_effect = RuntimeError("db down")
    player = _make_human_player(bus, battle_id=12, store=store)
    # Must not raise despite the store error.
    player._log_turn(1, "move 1", True, "raw")


def test_log_turn_noop_without_store():
    bus = EventBus()
    player = _make_human_player(bus, battle_id=13, store=None)
    player._log_turn(1, "move 1", True, "raw")  # no store → early return, no raise
