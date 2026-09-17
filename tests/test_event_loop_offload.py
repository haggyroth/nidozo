"""Blocking store I/O stays off the event loop (#282).

``BattleStore`` is synchronous sqlite3: every call blocks until the query (and
its ``commit()``) returns.  Run on the event-loop thread, one write stalls the
WebSocket stream, every other request, and all in-flight battles for the whole
duration — so handlers and the decision loop must hand the work to a thread.

Two kinds of assertion here:

* **Mechanism** — no store query ever executes on the loop thread.  Every store
  access funnels through ``BattleStore._get_conn()``, so one spy there sees all
  of a handler's database work, including calls the handler makes through
  methods this change did not touch.
* **Consequence** — a deliberately slow store write does not stop the loop from
  scheduling anything else.  This is the user-visible half: it reproduces the
  stall itself, so a fix that merely moved the call to another *place* on the
  loop would still fail.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from nidozo.db.store import BattleStore

# Long enough that a blocked loop cannot possibly fake responsiveness, short
# enough that the suite stays quick.
_SLOW_WRITE_SECS = 0.3
_HEARTBEAT_SECS = 0.01


@pytest.fixture
def app(tmp_path: Path):
    """A fresh app backed by a temp SQLite database."""
    from nidozo.api.app import create_app
    return create_app(db_path=tmp_path / "test.db")


@pytest.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired straight to the ASGI app — same loop as the test."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def no_runners() -> Iterator[None]:
    """Stub the runners so a start call never dials Pokémon Showdown (absent in CI)."""

    async def _noop(*args: Any, **kwargs: Any) -> None:
        pass

    with patch("nidozo.api.routes.run_battles", side_effect=_noop), \
         patch("nidozo.api.routes.run_tournament", side_effect=_noop), \
         patch("nidozo.api.routes.run_bracket_tournament", side_effect=_noop), \
         patch("nidozo.api.routes.run_season", side_effect=_noop), \
         patch("nidozo.api.routes.run_experiment", side_effect=_noop):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ThreadRecorder:
    """Records the thread id of every store query made while installed."""

    def __init__(self, store: BattleStore) -> None:
        self._store = store
        self._real = store._get_conn
        self.threads: list[int] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def spy() -> Any:
            self.threads.append(threading.get_ident())
            return self._real()

        monkeypatch.setattr(self._store, "_get_conn", spy)


def _loop_thread() -> int:
    """The thread the calling coroutine runs on — the one that must stay free."""
    return threading.get_ident()


_START_PAYLOADS: dict[str, tuple[str, dict[str, Any], str]] = {
    "battle": (
        "/api/battles/start",
        {"p1_provider": "random", "p2_provider": "random", "n_battles": 1},
        "battle_ids",
    ),
    "tournament": (
        "/api/tournament/start",
        {"players": [{"provider": "random"}, {"provider": "random"}], "rounds": 1},
        "tournament_id",
    ),
    "season": (
        "/api/seasons/start",
        {"name": "Offload", "players": [{"provider": "random"}, {"provider": "random"}]},
        "season_id",
    ),
    "experiment": (
        "/api/experiments/start",
        {
            "name": "offload",
            "variant_a": {"provider": "openai", "model": "gpt-4o", "prompt_version": "v9"},
            "variant_b": {"provider": "openai", "model": "gpt-4o", "prompt_version": "v8"},
            "n_battles": 2,
            "tier": "random",
        },
        "experiment_id",
    ),
}

_CANCEL_PATHS: dict[str, str] = {
    "battle": "/api/battles/{id}/cancel",
    "tournament": "/api/tournaments/{id}/cancel",
    "season": "/api/seasons/{id}/cancel",
    "experiment": "/api/experiments/{id}/cancel",
}


async def _start(client: AsyncClient, kind: str) -> int:
    """Create a real row of *kind* via its start endpoint and return its id."""
    path, payload, id_field = _START_PAYLOADS[kind]
    resp = await client.post(path, json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    entity_id = data[id_field]
    if kind == "battle":
        entity_id = entity_id[0]
    assert isinstance(entity_id, int)
    return entity_id


async def _heartbeat_ticks(coro: Any) -> tuple[Any, int]:
    """Await *coro* while counting how often the loop gets to schedule us.

    A blocked event loop cannot run the heartbeat at all, so the tick count is a
    direct measure of whether the store work ran elsewhere.
    """
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(_HEARTBEAT_SECS)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        result = await coro
    finally:
        beat.cancel()
    return result, ticks


# ---------------------------------------------------------------------------
# Consequence: the loop keeps running through a slow store write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_loop_keeps_ticking_during_a_slow_cancel(app, client, no_runners) -> None:
    """A cancel whose store write takes 300ms must not freeze the process."""
    battle_id = await _start(client, "battle")
    store = app.state.store
    real_cancel = store.cancel_battle

    def slow_cancel(bid: int) -> bool:
        time.sleep(_SLOW_WRITE_SECS)
        return real_cancel(bid)

    with patch.object(store, "cancel_battle", slow_cancel):
        resp, ticks = await _heartbeat_ticks(client.post(f"/api/battles/{battle_id}/cancel"))

    assert resp.status_code == 200, resp.text
    assert ticks >= 5, f"the event loop was stalled by the store write ({ticks} heartbeats)"


@pytest.mark.asyncio
async def test_the_loop_keeps_ticking_while_a_battle_start_writes_rows(
    app, client, no_runners
) -> None:
    """Same for the sync start handler: its INSERTs belong in the threadpool."""
    store = app.state.store
    real_create = store.create_battle

    def slow_create(*args: Any, **kwargs: Any) -> int:
        time.sleep(_SLOW_WRITE_SECS)
        return real_create(*args, **kwargs)

    with patch.object(store, "create_battle", slow_create):
        resp, ticks = await _heartbeat_ticks(
            client.post("/api/battles/start", json=_START_PAYLOADS["battle"][1])
        )

    assert resp.status_code == 200, resp.text
    assert ticks >= 5, f"the event loop was stalled by the store write ({ticks} heartbeats)"


# ---------------------------------------------------------------------------
# Mechanism: no store query runs on the loop thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_CANCEL_PATHS))
async def test_cancel_endpoints_do_their_store_io_off_the_loop_thread(
    app, client, no_runners, monkeypatch, kind: str
) -> None:
    """Every store call a cancel handler makes — read and write — is off-loop."""
    entity_id = await _start(client, kind)
    recorder = _ThreadRecorder(app.state.store)
    recorder.install(monkeypatch)

    resp = await client.post(_CANCEL_PATHS[kind].format(id=entity_id))

    assert resp.status_code == 200, resp.text
    assert recorder.threads, "the handler never reached the store — test is vacuous"
    assert _loop_thread() not in recorder.threads, (
        f"{kind} cancel ran a store query on the event-loop thread"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_START_PAYLOADS))
async def test_start_endpoints_do_their_store_io_off_the_loop_thread(
    app, client, no_runners, monkeypatch, kind: str
) -> None:
    """The start handlers write rows through the threadpool, not the loop."""
    recorder = _ThreadRecorder(app.state.store)
    recorder.install(monkeypatch)

    entity_id = await _start(client, kind)

    assert entity_id >= 1
    assert recorder.threads, "the handler never reached the store — test is vacuous"
    assert _loop_thread() not in recorder.threads, (
        f"{kind} start ran a store query on the event-loop thread"
    )


# ---------------------------------------------------------------------------
# The decision loop: turn logging
# ---------------------------------------------------------------------------

def _seeded_store(tmp_path: Path) -> tuple[BattleStore, int]:
    """A store with one pending battle, so a turn row can legally be written."""
    store = BattleStore(db_path=tmp_path / "players.db")
    p1 = store.get_or_create_model("random", "r1", "v9")
    p2 = store.get_or_create_model("random", "r2", "v9")
    battle_id = store.create_battle("battle-gen9randombattle-offload", "gen9randombattle", p1, p2)
    return store, battle_id


@pytest.mark.asyncio
async def test_llm_player_turn_log_is_written_off_the_loop_thread(tmp_path, monkeypatch) -> None:
    """log_turn carries the state snapshot and raw response — the heaviest write."""
    from tests.test_llm_player import _make_player

    store, battle_id = _seeded_store(tmp_path)
    try:
        player = _make_player(AsyncMock())
        player._store = store
        player._battle_id = battle_id

        recorder = _ThreadRecorder(store)
        recorder.install(monkeypatch)
        await player._log_turn(1, "move thunderbolt", True, "raw response", state_json="{}")

        assert _loop_thread() not in recorder.threads
        # ...and the row really landed, so the assertion above is not vacuous.
        turns = store.get_turns_basic(battle_id)
        assert [(t["turn_number"], t["action_chosen"]) for t in turns] == [(1, "move thunderbolt")]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_human_player_turn_log_is_written_off_the_loop_thread(tmp_path, monkeypatch) -> None:
    """The human's turn log runs on the same loop that is awaiting their move."""
    from tests.test_human_player import _make_human_player

    store, battle_id = _seeded_store(tmp_path)
    try:
        player = _make_human_player(MagicMock(), battle_id=battle_id, store=store)

        recorder = _ThreadRecorder(store)
        recorder.install(monkeypatch)
        await player._log_turn(1, "move 1", True, "raw", state_json="{}")

        assert _loop_thread() not in recorder.threads
        turns = store.get_turns_basic(battle_id)
        assert [(t["turn_number"], t["action_chosen"]) for t in turns] == [(1, "move 1")]
    finally:
        store.close()
