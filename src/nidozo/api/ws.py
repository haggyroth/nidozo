"""WebSocket endpoint for the live battle stream."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nidozo.api.auth import ws_authorized

# WebSocket close code for an unauthorized connection (1008 = policy violation).
_CLOSE_POLICY_VIOLATION = 1008


def create_ws_router(bus: Any, auth_token: str | None = None) -> APIRouter:
    """Return a router containing the /ws/battles WebSocket endpoint.

    When *auth_token* is set, the client must supply a matching ``?token=``
    query parameter (browsers can't set headers on a WS handshake).
    """
    router = APIRouter()

    @router.websocket("/ws/battles")
    async def battle_stream(ws: WebSocket) -> None:
        if not ws_authorized(ws, auth_token):
            await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="unauthorized")
            return
        await ws.accept()
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
