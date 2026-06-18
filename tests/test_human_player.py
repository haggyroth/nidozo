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
