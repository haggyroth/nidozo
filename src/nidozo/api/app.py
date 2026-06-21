"""FastAPI application factory — assembles routes, WebSocket, middleware, and lifespan."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from nidozo import __version__
from nidozo.api.auth import add_auth, get_api_token
from nidozo.api.events import EventBus
from nidozo.api.lifespan import create_lifespan
from nidozo.api.logging_config import configure_logging
from nidozo.api.middleware import add_cors
from nidozo.api.ratelimit import add_rate_limit, get_rate_limit
from nidozo.api.routes import create_router
from nidozo.api.ws import create_ws_router
from nidozo.api.ws_showdown import create_showdown_ws_router
from nidozo.db.store import BattleStore

_DB_PATH = Path(os.environ.get("NIDOZO_DB") or os.environ.get("NIMZO_DB", "nidozo.db"))
_FRONTEND_DIST = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
_SHOWDOWN_HOST = os.environ.get("NIDOZO_SHOWDOWN_HOST", "localhost")
_SHOWDOWN_PORT = int(os.environ.get("NIDOZO_SHOWDOWN_PORT", "8000"))


def create_app(db_path: Path = _DB_PATH) -> FastAPI:
    configure_logging()
    bus = EventBus()
    store = BattleStore(db_path)
    active_tasks: dict[int, asyncio.Task[None]] = {}

    app = FastAPI(
        title="Nidozo",
        version=__version__,
        lifespan=create_lifespan(store, active_tasks, db_path),
    )
    app.state.store = store
    app.state.active_tasks = active_tasks

    # Optional shared-secret auth (#212). Enabled only when NIDOZO_API_TOKEN is
    # set; otherwise a no-op with a startup warning. Read here (not at import)
    # so tests and deployments can set the env before create_app runs.
    api_token = get_api_token()

    add_cors(app)
    add_auth(app, api_token)
    add_rate_limit(app, get_rate_limit())
    app.include_router(create_router(store, bus, active_tasks))
    app.include_router(create_ws_router(bus, auth_token=api_token))
    # OP-02 (#84): spectator-stream proxy for the Showdown battle-scene renderer.
    # Display-only and entirely separate from the /ws/battles JSON bus.
    app.include_router(create_showdown_ws_router(
        showdown_host=_SHOWDOWN_HOST, showdown_port=_SHOWDOWN_PORT, auth_token=api_token,
    ))

    if _FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="static")

    return app
