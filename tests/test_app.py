"""Tests for the FastAPI application factory — startup, lifespan, middleware, routes."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create(tmp_path: Path):
    from nidozo.api.app import create_app
    return create_app(db_path=tmp_path / "test.db")


# ---------------------------------------------------------------------------
# App startup — basic smoke tests
# ---------------------------------------------------------------------------


def test_create_app_returns_fastapi_instance(tmp_path: Path) -> None:
    from fastapi import FastAPI
    app = _create(tmp_path)
    assert isinstance(app, FastAPI)


def test_create_app_exposes_store_on_state(tmp_path: Path) -> None:
    from nidozo.db.store import BattleStore
    app = _create(tmp_path)
    assert isinstance(app.state.store, BattleStore)


def test_create_app_version_matches_package(tmp_path: Path) -> None:
    from nidozo import __version__
    app = _create(tmp_path)
    assert app.version == __version__


# ---------------------------------------------------------------------------
# Lifespan — startup and clean shutdown (no active tasks)
# ---------------------------------------------------------------------------


def test_lifespan_startup_and_shutdown_no_tasks(tmp_path: Path) -> None:
    """App starts up and shuts down cleanly when there are no active battle tasks."""
    app = _create(tmp_path)
    # starlette's TestClient runs the full lifespan when used as a context manager
    with TestClient(app) as client:
        resp = client.get("/healthz")
        # 200 (ok) or 503 (degraded if Showdown unreachable) — both mean the app is alive
        assert resp.status_code in (200, 503)
    # If lifespan cleanup raised, TestClient.__exit__ would re-raise — reaching here means clean


def test_lifespan_store_closed_on_shutdown(tmp_path: Path) -> None:
    """store.close() is called during lifespan shutdown."""
    app = _create(tmp_path)
    store = app.state.store

    with TestClient(app):
        pass  # trigger shutdown

    # After shutdown the connection should be closed — a query should fail
    with pytest.raises(sqlite3.ProgrammingError):
        store._conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# Lifespan — shutdown with active tasks
# ---------------------------------------------------------------------------


def test_lifespan_cancels_active_tasks_on_shutdown(tmp_path: Path) -> None:
    """Tasks in _active_tasks are cancelled and awaited during lifespan shutdown."""
    cancelled: list[bool] = []

    async def _run_scenario() -> None:
        from nidozo.api.app import create_app as _ca

        inner_app = _ca(db_path=tmp_path / "inner.db")

        async def _never_ending() -> None:
            try:
                await asyncio.sleep(9999)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        # _active_tasks is shared between the lifespan closure and app.state.
        task = asyncio.create_task(_never_ending())
        # Yield to the event loop so the task starts and reaches its first
        # await point (asyncio.sleep(9999)) before we cancel it.  Without
        # this, CancelledError is thrown *before* the try block executes and
        # the except handler never runs.
        await asyncio.sleep(0)
        inner_app.state.active_tasks[999] = task

        async with inner_app.router.lifespan_context(inner_app):
            pass  # triggers shutdown on __aexit__

    asyncio.run(_run_scenario())
    assert cancelled, "Expected the active task to be cancelled on shutdown"


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------


def test_cors_allows_vite_dev_server(tmp_path: Path) -> None:
    """CORS allows requests from the Vite dev server origin."""
    app = _create(tmp_path)
    with TestClient(app) as client:
        resp = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.status_code in (200, 204)
    assert "access-control-allow-origin" in resp.headers


def test_cors_allows_serve_py_origin(tmp_path: Path) -> None:
    """CORS allows requests from the serve.py production default origin."""
    app = _create(tmp_path)
    with TestClient(app) as client:
        resp = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:5001",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.status_code in (200, 204)
    assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# Routes registered
# ---------------------------------------------------------------------------


def test_ws_route_registered(tmp_path: Path) -> None:
    """The /ws/battles WebSocket route is present in the app."""
    app = _create(tmp_path)
    # Resolve by endpoint name rather than scanning app.routes for a `.path`:
    # Starlette ≥1.2 wraps included routers in an `_IncludedRouter` (no flat
    # `.path`), so a path scan misses sub-router routes. url_path_for walks the
    # router tree and raises if the route is missing, so it asserts presence.
    assert app.url_path_for("battle_stream") == "/ws/battles"


def test_healthz_route_registered(tmp_path: Path) -> None:
    app = _create(tmp_path)
    assert app.url_path_for("healthz") == "/healthz"
