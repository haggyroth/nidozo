"""WebSocket endpoint for the live battle stream."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nidozo.api.auth import ws_auth_subprotocol, ws_authorized
from nidozo.api.origin import origin_allowed

# WebSocket close code for an unauthorized connection (1008 = policy violation).
_CLOSE_POLICY_VIOLATION = 1008


def create_ws_router(bus: Any, auth_token: str | None = None) -> APIRouter:
    """Return a router containing the /ws/battles WebSocket endpoint.

    When *auth_token* is set, the client must supply it via the
    ``Sec-WebSocket-Protocol`` handshake header (``nidozo-auth.<base64url>``,
    which browsers can set) or the legacy ``?token=`` query parameter.
    """
    router = APIRouter()

    @router.websocket("/ws/battles")
    async def battle_stream(ws: WebSocket) -> None:
        # Origin first: it is the only guard for a browser session whose token
        # the user has already entered (#280).
        if not origin_allowed(ws):
            await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="cross-origin")
            return
        if not ws_authorized(ws, auth_token):
            await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="unauthorized")
            return
        # Echo the credential subprotocol — a browser aborts the handshake if the
        # server accepts without selecting one of the protocols it offered.
        await ws.accept(subprotocol=ws_auth_subprotocol(ws))
        q = bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    await ws.send_text(json.dumps(event))
                except TimeoutError:
                    await ws.send_text(json.dumps({"type": "ping"}))
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    return router
