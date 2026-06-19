"""Tests for the Showdown spectator-stream proxy (OP-02, #84).

The upstream Showdown connection is injected as a scripted fake, so these run
with no live Showdown server.  They cover room validation, the guest handshake
sequence, verbatim frame relay, and that login frames are not leaked to the
browser.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nidozo.api.ws_showdown import create_showdown_ws_router, is_valid_room


class FakeUpstream:
    """Scripted stand-in for a Showdown WebSocket connection.

    Seeds a ``|challstr|`` on construction; replies to ``/trn`` with a NAMED
    ``|updateuser|`` and to ``/join`` by emitting the supplied battle frames.
    ``recv`` blocks once the script is exhausted, mimicking a live battle.
    """

    def __init__(self, frames_after_join: list[str]) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._frames_after_join = frames_after_join
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self._inbox.put_nowait("|challstr|4|TESTCHALLSTR")

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if message.startswith("|/trn "):
            await self._inbox.put("|updateuser| NidozoSpecAbc|1|170|{}")
        elif message.startswith("|/join "):
            for frame in self._frames_after_join:
                await self._inbox.put(frame)

    async def recv(self) -> str:
        return await self._inbox.get()

    async def close(self) -> None:
        self.closed = True


def _make_app(frames_after_join: list[str], created: list[FakeUpstream]) -> FastAPI:
    async def fake_connect(uri: str) -> FakeUpstream:
        fake = FakeUpstream(frames_after_join)
        created.append(fake)
        return fake

    app = FastAPI()
    app.include_router(create_showdown_ws_router(connect_upstream=fake_connect))
    return app


# ---------------------------------------------------------------------------
# Room validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "room,ok",
    [
        ("battle-gen3randombattle-17", True),
        ("battle-gen3ou-1234-abcdef", True),
        ("lobby", False),
        ("battle-../etc", False),
        ("global", False),
        ("battle-Gen3OU-1", False),   # uppercase rejected
        ("", False),
    ],
)
def test_is_valid_room(room: str, ok: bool) -> None:
    assert is_valid_room(room) is ok


def test_invalid_room_is_rejected() -> None:
    """A non-battle room id is closed before any upstream connection is made."""
    created: list[FakeUpstream] = []
    app = _make_app([], created)
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/showdown/lobby") as ws:
            ws.receive_text()

    # Upstream was never dialed for a rejected room.
    assert created == []


# ---------------------------------------------------------------------------
# Handshake + relay
# ---------------------------------------------------------------------------

def test_handshake_then_relays_battle_frames() -> None:
    """After guest login + join, post-join frames are relayed verbatim."""
    frames = [
        ">battle-gen3randombattle-1\n|init|battle\n|title|A vs B",
        "|turn|1",
    ]
    created: list[FakeUpstream] = []
    app = _make_app(frames, created)
    client = TestClient(app)

    with client.websocket_connect("/ws/showdown/battle-gen3randombattle-1") as ws:
        assert ws.receive_text() == frames[0]
        assert ws.receive_text() == frames[1]

    # The upstream saw exactly the guest-login + join sequence.
    fake = created[0]
    trn = [m for m in fake.sent if m.startswith("|/trn ")]
    join = [m for m in fake.sent if m.startswith("|/join ")]
    assert trn and trn[0].endswith(",0,")          # empty assertion
    assert join == ["|/join battle-gen3randombattle-1"]


def test_login_frames_are_not_leaked_to_browser() -> None:
    """The browser must only receive post-join battle frames, never login frames."""
    frames = ["|turn|1"]
    created: list[FakeUpstream] = []
    app = _make_app(frames, created)
    client = TestClient(app)

    with client.websocket_connect("/ws/showdown/battle-gen3ou-9") as ws:
        first = ws.receive_text()

    # The very first thing the browser sees is the battle frame, not |challstr|
    # or |updateuser|.
    assert first == "|turn|1"
    assert not first.startswith("|challstr|")
    assert not first.startswith("|updateuser|")


# ---------------------------------------------------------------------------
# Idle keepalive, login timeout, and teardown branches
# ---------------------------------------------------------------------------

from nidozo.api import ws_showdown as _wsmod  # noqa: E402


def _make_app_with(factory, created: list) -> FastAPI:
    async def fake_connect(uri: str):
        fake = factory()
        created.append(fake)
        return fake

    app = FastAPI()
    app.include_router(create_showdown_ws_router(connect_upstream=fake_connect))
    return app


class _SilentAfterJoinUpstream(FakeUpstream):
    """Completes login + join but then never sends another frame (idle battle)."""

    def __init__(self) -> None:
        super().__init__(frames_after_join=[])


def test_idle_connection_gets_keepalive_ping(monkeypatch) -> None:
    """When upstream goes quiet, the proxy sends |ping to keep the browser alive."""
    monkeypatch.setattr(_wsmod, "_IDLE_PING_SECS", 0.05)
    created: list = []
    app = _make_app_with(_SilentAfterJoinUpstream, created)
    client = TestClient(app)

    with client.websocket_connect("/ws/showdown/battle-gen3ou-1") as ws:
        assert ws.receive_text() == "|ping"


class _NeverNamedUpstream:
    """Sends |challstr| but never the NAMED |updateuser|, so login never completes."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self._inbox.put_nowait("|challstr|4|TESTCHALLSTR")

    async def send(self, message: str) -> None:
        self.sent.append(message)
        # Deliberately never enqueue an |updateuser| reply.

    async def recv(self) -> str:
        return await self._inbox.get()

    async def close(self) -> None:
        self.closed = True


def test_login_timeout_closes_cleanly(monkeypatch) -> None:
    """If the guest handshake never completes, the proxy times out and closes."""
    monkeypatch.setattr(_wsmod, "_LOGIN_TIMEOUT_SECS", 0.1)
    created: list = []
    app = _make_app_with(_NeverNamedUpstream, created)
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/showdown/battle-gen3ou-2") as ws:
            ws.receive_text()

    # Upstream was dialed and torn down despite the failed login.
    assert created and created[0].closed


class _RaisingTeardownUpstream(FakeUpstream):
    """Relays one frame, then raises on the best-effort /leave + close teardown."""

    def __init__(self) -> None:
        super().__init__(frames_after_join=["|turn|1"])

    async def send(self, message: str) -> None:
        if message.startswith("|/leave"):
            raise RuntimeError("boom on leave")
        await super().send(message)

    async def close(self) -> None:
        raise RuntimeError("boom on close")


def test_teardown_swallows_best_effort_errors() -> None:
    """Errors while sending /leave or closing upstream must not crash the proxy."""
    created: list = []
    app = _make_app_with(_RaisingTeardownUpstream, created)
    client = TestClient(app)

    # The relayed frame still arrives; the raising teardown is swallowed on exit.
    with client.websocket_connect("/ws/showdown/battle-gen3ou-3") as ws:
        assert ws.receive_text() == "|turn|1"
