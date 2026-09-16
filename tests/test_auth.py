"""Tests for optional shared-secret API authentication (#212)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nidozo.api.app import create_app
from nidozo.api.auth import WS_SUBPROTOCOL_PREFIX, _decode_subprotocol_token, ws_auth_subprotocol

_TOKEN = "s3cret-token"


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NIDOZO_API_TOKEN", _TOKEN)
    app = create_app(db_path=tmp_path / "auth.db")
    return TestClient(app)


@pytest.fixture
def open_client(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    app = create_app(db_path=tmp_path / "open.db")
    return TestClient(app)


# ---------------------------------------------------------------------------
# HTTP gate
# ---------------------------------------------------------------------------

def test_healthz_is_always_open(auth_client) -> None:
    # /healthz returns 200 (deps up) or 503 (deps down) — never 401.
    resp = auth_client.get("/healthz")
    assert resp.status_code != 401


def test_api_route_rejected_without_token(auth_client) -> None:
    resp = auth_client.get("/api/leaderboard")
    assert resp.status_code == 401


def test_api_route_rejected_with_wrong_token(auth_client) -> None:
    resp = auth_client.get("/api/leaderboard", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_api_route_allowed_with_bearer_token(auth_client) -> None:
    resp = auth_client.get("/api/leaderboard", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 200


def test_api_route_allowed_with_x_api_token_header(auth_client) -> None:
    resp = auth_client.get("/api/leaderboard", headers={"X-API-Token": _TOKEN})
    assert resp.status_code == 200


def test_auth_disabled_allows_api_without_token(open_client) -> None:
    resp = open_client.get("/api/leaderboard")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# WebSocket gate
# ---------------------------------------------------------------------------

def test_ws_battles_rejected_without_token(auth_client) -> None:
    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect("/ws/battles") as ws:
            ws.receive_text()


def test_ws_battles_allowed_with_token(auth_client) -> None:
    # Connects and stays open; the first frame is a periodic ping (or an event).
    with auth_client.websocket_connect(f"/ws/battles?token={_TOKEN}") as ws:
        assert ws is not None  # handshake accepted, no immediate close


def test_ws_battles_open_when_auth_disabled(open_client) -> None:
    with open_client.websocket_connect("/ws/battles") as ws:
        assert ws is not None


def test_ws_showdown_rejected_without_token(auth_client) -> None:
    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect("/ws/showdown/battle-gen9ou-1") as ws:
            ws.receive_text()


# ---------------------------------------------------------------------------
# WebSocket gate — credential in Sec-WebSocket-Protocol (#276)
#
# The query parameter lands in uvicorn's access log, so browsers send the token
# as `nidozo-auth.<base64url(token)>` instead. Both must keep working.
# ---------------------------------------------------------------------------

def _subprotocol(token: str) -> str:
    encoded = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
    return f"{WS_SUBPROTOCOL_PREFIX}{encoded}"


def test_ws_battles_allowed_with_subprotocol_token(auth_client) -> None:
    entry = _subprotocol(_TOKEN)
    with auth_client.websocket_connect("/ws/battles", subprotocols=[entry]) as ws:
        # The server must *select* the offered protocol: a browser aborts the
        # handshake if it accepts without echoing one back.
        assert ws.accepted_subprotocol == entry


def test_ws_showdown_allowed_with_subprotocol_token(auth_client) -> None:
    entry = _subprotocol(_TOKEN)
    with auth_client.websocket_connect(
        "/ws/showdown/battle-gen9ou-1", subprotocols=[entry]
    ) as ws:
        assert ws.accepted_subprotocol == entry


def test_ws_battles_rejected_with_wrong_subprotocol_token(auth_client) -> None:
    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect(
            "/ws/battles", subprotocols=[_subprotocol("not-the-token")]
        ) as ws:
            ws.receive_text()


def test_unrelated_subprotocol_still_falls_back_to_the_query_token(auth_client) -> None:
    """A legacy/non-browser client offering some other protocol is not locked out."""
    with auth_client.websocket_connect(
        f"/ws/battles?token={_TOKEN}", subprotocols=["graphql-ws"]
    ) as ws:
        assert ws.accepted_subprotocol is None


def test_subprotocol_token_survives_an_unsigned_or_base64_variant_token() -> None:
    """The encoding has to round-trip characters that break RFC 6455's grammar."""
    token = "a+b/c=d?e&f"  # base64 + / = and query separators, all in one
    encoded = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
    assert "+" not in encoded and "/" not in encoded and "=" not in encoded
    assert _decode_subprotocol_token(f"{WS_SUBPROTOCOL_PREFIX}{encoded}") == token


@pytest.mark.parametrize(
    "entry",
    [
        f"{WS_SUBPROTOCOL_PREFIX}",           # prefix with nothing after it
        f"{WS_SUBPROTOCOL_PREFIX}!!!not-base64",
        f"{WS_SUBPROTOCOL_PREFIX}////",       # decodes to bytes that aren't UTF-8
    ],
)
def test_undecodable_subprotocol_yields_no_token(entry: str) -> None:
    """A hand-crafted entry must not crash the handshake or sneak a match through."""
    assert _decode_subprotocol_token(entry) is None


def test_a_subprotocol_that_is_not_ours_is_ignored() -> None:
    assert ws_auth_subprotocol(_FakeWS("chat, superchat")) is None
    assert ws_auth_subprotocol(_FakeWS("chat, nidozo-auth.abc, superchat")) == "nidozo-auth.abc"


class _FakeWS:
    """Just enough of a WebSocket for the header-reading helpers."""

    def __init__(self, offered: str) -> None:
        self.headers = {"sec-websocket-protocol": offered}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_get_api_token_treats_blank_as_unset(monkeypatch) -> None:
    from nidozo.api.auth import get_api_token

    monkeypatch.setenv("NIDOZO_API_TOKEN", "   ")
    assert get_api_token() is None
    monkeypatch.setenv("NIDOZO_API_TOKEN", "abc")
    assert get_api_token() == "abc"


def test_token_matches_is_constant_time_safe() -> None:
    from nidozo.api.auth import token_matches

    assert token_matches("abc", "abc") is True
    assert token_matches("abc", "abd") is False
    assert token_matches(None, "abc") is False
    assert token_matches("", "abc") is False


# ---------------------------------------------------------------------------
# Fail-closed startup (#273)
# ---------------------------------------------------------------------------

def test_create_app_fails_closed_when_no_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    monkeypatch.delenv("NIDOZO_ALLOW_INSECURE", raising=False)
    with pytest.raises(RuntimeError, match="NIDOZO_API_TOKEN"):
        create_app(db_path=tmp_path / "failclosed.db")


def test_create_app_allows_insecure_opt_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    monkeypatch.setenv("NIDOZO_ALLOW_INSECURE", "1")
    app = create_app(db_path=tmp_path / "insecure.db")
    assert app is not None
