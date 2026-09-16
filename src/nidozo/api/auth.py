"""Optional shared-secret authentication for the Nidozo API.

When ``NIDOZO_API_TOKEN`` is set, every ``/api/*`` HTTP route and both
WebSocket endpoints require the token. When it is unset, the app refuses to
start (fail-closed) unless ``NIDOZO_ALLOW_INSECURE=1`` explicitly opts into an
open, loopback-only instance.

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


def allow_insecure() -> bool:
    """Return True when the operator has explicitly opted out of the token gate.

    Only consulted when ``NIDOZO_API_TOKEN`` is unset. Accepts common truthy
    spellings so a blank or typo'd value can never silently disable the
    fail-closed guard.
    """
    return os.environ.get("NIDOZO_ALLOW_INSECURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def enforce_auth_policy(token: str | None) -> None:
    """Fail closed when no token is configured, unless explicitly opted out.

    Raises ``RuntimeError`` when authentication would be disabled without an
    explicit ``NIDOZO_ALLOW_INSECURE=1`` opt-in, so an exposed instance can
    never silently start open. Called before any resources are opened.
    """
    if token is not None:
        return
    if allow_insecure():
        logger.warning(
            "API authentication is DISABLED (NIDOZO_ALLOW_INSECURE=1). "
            "Do NOT expose this server beyond localhost: the battle-start "
            "endpoints spend real LLM API credits, and all data is readable."
        )
        return
    raise RuntimeError(
        "NIDOZO_API_TOKEN is not set — refusing to start with authentication "
        "disabled. Set NIDOZO_API_TOKEN, or set NIDOZO_ALLOW_INSECURE=1 to "
        "explicitly opt into an open (loopback-only) instance."
    )


def add_auth(app: FastAPI, token: str | None) -> None:
    """Install the token-gate middleware on *app* (no-op when *token* is None)."""
    if not token:
        # enforce_auth_policy() has already validated this path (opt-in).
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
