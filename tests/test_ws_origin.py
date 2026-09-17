"""WebSocket handshakes must come from a page we serve (#280).

WebSockets are exempt from the same-origin policy and CORS middleware never sees
the handshake, so without this check any page a user visits can open a socket to
this API and read the live battle stream.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nidozo.api.origin import allowed_origins, origin_allowed
from nidozo.api.ws import create_ws_router
from nidozo.api.ws_showdown import create_showdown_ws_router

_OUR_ORIGIN = "http://localhost:5001"


class _FakeWS:
    """Only the header lookup the origin policy uses."""

    def __init__(self, **headers: str) -> None:
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


def _bus_app(**kwargs: Any) -> FastAPI:
    bus = type(
        "Bus",
        (),
        {"subscribe": lambda self: asyncio.Queue(), "unsubscribe": lambda self, q: None},
    )()
    app = FastAPI()
    app.include_router(create_ws_router(bus, **kwargs))
    return app


def _showdown_app(**kwargs: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(create_showdown_ws_router(**kwargs))
    return app


class _FakeUpstream:
    """A scripted Showdown guest connection, recording whether it was opened.

    ``recv`` replays a working login handshake and one battle frame, then blocks,
    so a handshake that is wrongly allowed stays connected instead of erroring
    out — a rejection test would otherwise pass on the *proxy's* failure rather
    than on the policy's.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._script = ["|challstr|1", "|updateuser|NidozoSpec|1||", "|init|battle"]

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._script:
            return self._script.pop(0)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


class _UpstreamRecorder:
    """Factory handed to the router, so a test can see if a connection was made."""

    def __init__(self) -> None:
        self.opened: list[_FakeUpstream] = []

    async def connect(self, uri: str) -> _FakeUpstream:
        upstream = _FakeUpstream()
        self.opened.append(upstream)
        return upstream


def _client_host(client: TestClient) -> str:
    """The Host the test client puts on its handshakes (``testserver``)."""
    return client.base_url.host


# ---------------------------------------------------------------------------
# The policy itself
# ---------------------------------------------------------------------------

def test_no_origin_header_is_allowed() -> None:
    """A non-browser client cannot be hijacked — and rejecting it breaks scripts."""
    assert origin_allowed(_FakeWS(host="localhost:5001")) is True


def test_same_origin_as_the_app_is_allowed() -> None:
    assert origin_allowed(_FakeWS(origin=_OUR_ORIGIN, host="localhost:5001")) is True


def test_same_origin_survives_tls_termination() -> None:
    """The browser sees https; the app sees http. Same authority, same page."""
    assert origin_allowed(_FakeWS(origin="https://nidozo.example", host="nidozo.example")) is True


def test_listed_cross_origin_is_allowed() -> None:
    """The Vite dev server proxies /ws to the API from a different origin."""
    assert origin_allowed(_FakeWS(origin="http://localhost:5173", host="localhost:5173")) is True


def test_an_unlisted_cross_origin_is_rejected() -> None:
    assert origin_allowed(_FakeWS(origin="http://evil.example", host="localhost:5001")) is False


def test_a_null_origin_is_rejected() -> None:
    """A sandboxed iframe or a file:// page — never our own app."""
    assert origin_allowed(_FakeWS(origin="null", host="localhost:5001")) is False


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5001/", "HTTP://LOCALHOST:5001", "http://localhost:5001"],
)
def test_origin_matching_ignores_case_and_a_trailing_slash(origin: str) -> None:
    assert origin_allowed(_FakeWS(origin=origin, host="localhost:5001")) is True


def test_the_legacy_sec_websocket_origin_header_is_honoured() -> None:
    assert origin_allowed(_FakeWS(sec_websocket_origin="http://evil.example", host="x")) is False


def test_a_missing_host_header_cannot_grant_same_origin() -> None:
    assert origin_allowed(_FakeWS(origin=_OUR_ORIGIN)) is True  # listed origin, not the fallback
    assert origin_allowed(_FakeWS(origin="http://evil.example")) is False


def test_env_var_extends_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIDOZO_ALLOWED_ORIGINS", "https://arena.example, https://other.example")
    assert "https://arena.example" in allowed_origins()
    assert origin_allowed(_FakeWS(origin="https://arena.example", host="localhost:5001")) is True
    # Blank entries and stray whitespace must not become an empty-string match.
    monkeypatch.setenv("NIDOZO_ALLOWED_ORIGINS", " , ")
    assert origin_allowed(_FakeWS(origin="http://evil.example", host="localhost:5001")) is False


def test_an_explicit_allowlist_overrides_the_configured_one() -> None:
    assert origin_allowed(_FakeWS(origin="http://x.example", host="h"), allowed=[]) is False
    assert origin_allowed(_FakeWS(origin="http://x.example", host="h"), allowed=["http://x.example"])


# ---------------------------------------------------------------------------
# Wired into both endpoints
# ---------------------------------------------------------------------------

def test_ws_battles_rejects_a_cross_origin_browser() -> None:
    with TestClient(_bus_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/battles", headers={"Origin": "http://evil.example"}
            ) as ws:
                ws.receive_text()


def test_ws_battles_accepts_its_own_origin() -> None:
    with TestClient(_bus_app()) as client:
        with client.websocket_connect(
            "/ws/battles", headers={"Origin": f"http://{_client_host(client)}"}
        ) as ws:
            assert ws is not None


def test_ws_battles_still_accepts_a_client_that_sends_no_origin() -> None:
    with TestClient(_bus_app()) as client:
        with client.websocket_connect("/ws/battles") as ws:
            assert ws is not None


def test_ws_showdown_rejects_a_cross_origin_browser() -> None:
    """Refused before the upstream connection is even attempted."""
    recorder = _UpstreamRecorder()
    with TestClient(_showdown_app(connect_upstream=recorder.connect)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/showdown/battle-gen9ou-1", headers={"Origin": "http://evil.example"}
            ) as ws:
                ws.receive_text()

    assert recorder.opened == [], "a cross-origin page reached the Showdown server"


def test_ws_showdown_accepts_its_own_origin() -> None:
    """The positive control for the test above: same handshake, our origin."""
    recorder = _UpstreamRecorder()
    with TestClient(_showdown_app(connect_upstream=recorder.connect)) as client:
        with client.websocket_connect(
            "/ws/showdown/battle-gen9ou-1", headers={"Origin": f"http://{_client_host(client)}"}
        ) as ws:
            assert ws.receive_text() == "|init|battle"

    assert len(recorder.opened) == 1
    assert any(s.startswith("|/join battle-gen9ou-1") for s in recorder.opened[0].sent)


def test_origin_is_checked_before_the_token() -> None:
    """A cross-origin handshake with a *valid* token is still refused (#276/#280).

    The Origin check is the defence that survives a leaked token, so it must not
    be short-circuited by authentication succeeding.
    """
    valid = base64.urlsafe_b64encode(b"s3cret").decode().rstrip("=")
    protocols = {"Sec-WebSocket-Protocol": f"nidozo-auth.{valid}"}
    with TestClient(_bus_app(auth_token="s3cret")) as client:
        # The same credentials, same-origin, do connect — so the rejection below
        # is the Origin policy and not a bad token.
        with client.websocket_connect(
            "/ws/battles", headers={"Origin": f"http://{_client_host(client)}", **protocols}
        ) as ws:
            assert ws is not None

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/battles", headers={"Origin": "http://evil.example", **protocols}
            ) as ws:
                ws.receive_text()
