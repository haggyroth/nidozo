"""Tests for optional API rate limiting (#233) and client-IP derivation (#277)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from nidozo.api import ratelimit
from nidozo.api.ratelimit import (
    DEFAULT_LIMIT_PER_MIN,
    _prune,
    add_rate_limit,
    client_ip,
    get_rate_limit,
    get_trusted_proxies,
)


def _limited_client(per_min: int, *, peer: str = "testclient") -> TestClient:
    app = FastAPI()
    add_rate_limit(app, per_min)

    @app.post("/api/battles/start")
    def start() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/other")
    def other() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, client=(peer, 51234))


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


def test_unset_limit_defaults_on_for_an_authenticated_instance(monkeypatch) -> None:
    """An exposed instance gets a bound without the operator configuring one (#277)."""
    monkeypatch.delenv("NIDOZO_RATE_LIMIT_PER_MIN", raising=False)
    assert get_rate_limit(authenticated=True) == DEFAULT_LIMIT_PER_MIN > 0
    # Local dev, and an explicit opt-out, stay unlimited.
    assert get_rate_limit(authenticated=False) == 0
    monkeypatch.setenv("NIDOZO_RATE_LIMIT_PER_MIN", "0")
    assert get_rate_limit(authenticated=True) == 0


# ---------------------------------------------------------------------------
# Client IP derivation (#277)
# ---------------------------------------------------------------------------


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/battles/start",
        "headers": headers,
        "client": (peer, 51234),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    })


def test_trusted_proxies_parsing(monkeypatch) -> None:
    monkeypatch.delenv("NIDOZO_TRUSTED_PROXIES", raising=False)
    assert get_trusted_proxies() == ()          # trust nothing by default

    monkeypatch.setenv("NIDOZO_TRUSTED_PROXIES", " 10.0.0.0/8 , 192.168.1.7 , junk ,")
    networks = get_trusted_proxies()
    assert len(networks) == 2                    # the junk entry is skipped, not fatal
    assert str(networks[0]) == "10.0.0.0/8"
    assert str(networks[1]) == "192.168.1.7/32"


def test_forwarded_header_is_ignored_from_an_untrusted_peer(monkeypatch) -> None:
    """A directly-reachable client must not be able to pick its own bucket."""
    monkeypatch.delenv("NIDOZO_TRUSTED_PROXIES", raising=False)
    assert client_ip(_request("203.0.113.9", "1.1.1.1"), get_trusted_proxies()) == "203.0.113.9"


def test_forwarded_header_is_honoured_from_a_trusted_proxy(monkeypatch) -> None:
    monkeypatch.setenv("NIDOZO_TRUSTED_PROXIES", "10.0.0.0/8")
    trusted = get_trusted_proxies()
    assert client_ip(_request("10.0.0.5", "198.51.100.4"), trusted) == "198.51.100.4"


def test_rightmost_untrusted_hop_is_the_client(monkeypatch) -> None:
    """Client → proxy A → proxy B: the leftmost entry is client-controlled, so
    the address that matters is the rightmost one that isn't one of our proxies."""
    monkeypatch.setenv("NIDOZO_TRUSTED_PROXIES", "10.0.0.0/8")
    trusted = get_trusted_proxies()
    # Spoofed prefix, then the real client, then the proxy that saw it.
    assert client_ip(_request("10.0.0.6", "6.6.6.6, 198.51.100.4, 10.0.0.5"), trusted) == "198.51.100.4"
    # Every hop is one of ours → fall back to the peer rather than to a spoof.
    assert client_ip(_request("10.0.0.6", "10.0.0.5, 10.0.0.7"), trusted) == "10.0.0.5"


def test_forwarded_header_absent_from_a_trusted_proxy_falls_back_to_peer(monkeypatch) -> None:
    monkeypatch.setenv("NIDOZO_TRUSTED_PROXIES", "10.0.0.0/8")
    assert client_ip(_request("10.0.0.5"), get_trusted_proxies()) == "10.0.0.5"


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


def test_header_cannot_reset_a_bucket_for_an_untrusted_peer(monkeypatch) -> None:
    """The pre-#277 bypass: send a fresh X-Forwarded-For and start over."""
    monkeypatch.delenv("NIDOZO_TRUSTED_PROXIES", raising=False)
    client = _limited_client(1)
    assert client.post("/api/battles/start", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.post("/api/battles/start", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_limit_is_per_client_behind_a_trusted_proxy(monkeypatch) -> None:
    """All clients behind one proxy must not share a single bucket (#277)."""
    monkeypatch.setenv("NIDOZO_TRUSTED_PROXIES", "10.0.0.0/8")
    client = _limited_client(1, peer="10.0.0.5")

    # Client A spends their allowance…
    assert client.post("/api/battles/start", headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 200
    assert client.post("/api/battles/start", headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 429
    # …which must not touch client B's.
    assert client.post("/api/battles/start", headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 200


# ---------------------------------------------------------------------------
# Bucket pruning (#277)
# ---------------------------------------------------------------------------


def test_prune_drops_only_expired_buckets() -> None:
    now = time.monotonic()
    buckets = {
        "stale": (now - ratelimit._WINDOW_SECS - 1, 3),
        "fresh": (now - 1, 3),
    }
    _prune(buckets, now)
    assert list(buckets) == ["fresh"]


def test_middleware_sweeps_expired_buckets(monkeypatch) -> None:
    """The sweep is what keeps an exposed instance from accumulating one bucket
    per source IP forever — it must actually run as the window rolls over."""
    clock = [10_000.0]
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    sweeps: list[float] = []
    monkeypatch.setattr(ratelimit, "_prune", lambda buckets, now: sweeps.append(now))

    client = _limited_client(5)          # last_prune starts at the fake clock
    assert client.post("/api/battles/start").status_code == 200
    assert sweeps == []                  # inside the window: nothing to sweep

    clock[0] += ratelimit._WINDOW_SECS + 1
    assert client.post("/api/battles/start").status_code == 200
    assert sweeps == [clock[0]]


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


def test_authenticated_app_limits_without_being_asked(tmp_path, monkeypatch) -> None:
    """With a token set the instance is exposed, so it must not be unlimited just
    because NIDOZO_RATE_LIMIT_PER_MIN was left unset (#277)."""
    from nidozo.api import routes
    from nidozo.api.app import create_app

    monkeypatch.setenv("NIDOZO_API_TOKEN", "s3cret-token")
    monkeypatch.delenv("NIDOZO_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.setattr(ratelimit, "DEFAULT_LIMIT_PER_MIN", 2)

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(routes, "run_battles", _noop)

    client = TestClient(create_app(db_path=tmp_path / "rl-auth.db"))
    headers = {"Authorization": "Bearer s3cret-token"}
    payload = {"p1_provider": "random", "p2_provider": "random", "tier": "random"}
    assert client.post("/api/battles/start", json=payload, headers=headers).status_code == 200
    assert client.post("/api/battles/start", json=payload, headers=headers).status_code == 200
    assert client.post("/api/battles/start", json=payload, headers=headers).status_code == 429
