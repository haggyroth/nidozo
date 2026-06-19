"""Optional shared-secret authentication for the Nidozo API.

When ``NIDOZO_API_TOKEN`` is set, every ``/api/*`` HTTP route and both
WebSocket endpoints require the token. When it is unset, authentication is
disabled (the historical local-dev behaviour) and a loud warning is logged at
startup.

Always left open, regardless of the token:
  * ``/healthz`` — so container/load-balancer health checks keep working.
  * the static SPA bundle (``/`` and assets) — so the page can load in order
    to let the user enter the token in the first place.

HTTP requests carry the token as ``Authorization: Bearer <token>`` (an
``X-API-Token`` header is also accepted). Browsers cannot set custom headers on
a WebSocket handshake, so the WS endpoints take the token as a ``?token=`` query
parameter instead.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response
from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


def get_api_token() -> str | None:
    """Return the configured API token, or None if auth is disabled.

    An empty or whitespace-only ``NIDOZO_API_TOKEN`` is treated as unset so a
    blank env var (common in compose files) doesn't enable a guessable token.
    """
    token = os.environ.get("NIDOZO_API_TOKEN", "").strip()
    return token or None


def _extract_token(request: Request) -> str | None:
    """Pull the token from the Authorization (Bearer) or X-API-Token header."""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-token")


def token_matches(provided: str | None, expected: str) -> bool:
    """Constant-time comparison guarding against timing attacks."""
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


def add_auth(app: FastAPI, token: str | None) -> None:
    """Install the token-gate middleware on *app* (no-op when *token* is None)."""
    if not token:
        logger.warning(
            "API authentication is DISABLED (NIDOZO_API_TOKEN not set). "
            "Do NOT expose this server beyond localhost: the battle-start "
            "endpoints spend real LLM API credits, and all data is readable."
        )
        return

    logger.info("API authentication ENABLED — token required on /api/* and WebSockets.")

    @app.middleware("http")
    async def _require_token(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Only the API surface is gated; /healthz and the static SPA stay open.
        if request.url.path.startswith("/api/"):
            if not token_matches(_extract_token(request), token):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


def ws_authorized(ws: WebSocket, token: str | None) -> bool:
    """Return True if a WebSocket connection may proceed.

    When *token* is None (auth disabled) every connection is allowed. Otherwise
    the ``?token=`` query parameter must match.
    """
    if not token:
        return True
    return token_matches(ws.query_params.get("token"), token)
