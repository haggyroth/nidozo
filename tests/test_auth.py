"""Tests for optional shared-secret API authentication (#212)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nidozo.api.app import create_app

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
