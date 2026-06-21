"""Tests for optional API rate limiting (#233)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nidozo.api.ratelimit import add_rate_limit, get_rate_limit


def _limited_client(per_min: int) -> TestClient:
    app = FastAPI()
    add_rate_limit(app, per_min)

    @app.post("/api/battles/start")
    def start() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/other")
    def other() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

def test_get_rate_limit_parsing(monkeypatch) -> None:
    monkeypatch.setenv("NIDOZO_RATE_LIMIT_PER_MIN", "5")
    assert get_rate_limit() == 5
    monkeypatch.setenv("NIDOZO_RATE_LIMIT_PER_MIN", "0")
    assert get_rate_limit() == 0
    monkeypatch.setenv("NIDOZO_RATE_LIMIT_PER_MIN", "-3")
    assert get_rate_limit() == 0
    monkeypatch.setenv("NIDOZO_RATE_LIMIT_PER_MIN", "abc")
    assert get_rate_limit() == 0
    monkeypatch.delenv("NIDOZO_RATE_LIMIT_PER_MIN", raising=False)
    assert get_rate_limit() == 0


# ---------------------------------------------------------------------------
# Middleware behaviour
# ---------------------------------------------------------------------------

def test_blocks_after_limit() -> None:
    client = _limited_client(2)
    assert client.post("/api/battles/start").status_code == 200
    assert client.post("/api/battles/start").status_code == 200
    resp = client.post("/api/battles/start")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_only_limits_start_paths() -> None:
    client = _limited_client(1)
    assert client.post("/api/battles/start").status_code == 200
    assert client.post("/api/battles/start").status_code == 429
    # A non-start path is never limited.
    assert client.post("/api/other").status_code == 200
    assert client.post("/api/other").status_code == 200


def test_disabled_allows_all() -> None:
    client = _limited_client(0)
    for _ in range(5):
        assert client.post("/api/battles/start").status_code == 200


# ---------------------------------------------------------------------------
# Wiring into the real app
# ---------------------------------------------------------------------------

def test_rate_limit_wired_into_app(tmp_path, monkeypatch) -> None:
    from nidozo.api import routes
    from nidozo.api.app import create_app

    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    monkeypatch.setenv("NIDOZO_RATE_LIMIT_PER_MIN", "2")

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    # Don't actually run battles (no Showdown in this test).
    monkeypatch.setattr(routes, "run_battles", _noop)

    client = TestClient(create_app(db_path=tmp_path / "rl.db"))
    payload = {"p1_provider": "random", "p2_provider": "random", "tier": "random"}
    assert client.post("/api/battles/start", json=payload).status_code == 200
    assert client.post("/api/battles/start", json=payload).status_code == 200
    assert client.post("/api/battles/start", json=payload).status_code == 429
