"""Spectator-stream proxy for the Showdown battle-scene renderer (OP-02, #84).

This endpoint connects to the local Pokémon Showdown server as a **guest
spectator**, joins one battle room, and relays the raw Showdown text protocol
to a single browser client.  The browser renders it with a vendored copy of
Showdown's own ``Battle`` class (see ``docs/op-02-architecture.md``).

Design principles
-----------------
* **Dumb proxy.** It performs guest auth + ``/join`` + verbatim relay only.  It
  does *not* parse battle protocol, so it stays robust to Showdown protocol
  changes.  Frames are forwarded exactly as received (including the leading
  ``>ROOMID`` line); the frontend strips that and feeds ``|...`` lines to the
  renderer.
* **Read-only.** A spectator never sends moves, so nothing is forwarded
  browser → upstream except connection teardown.
* **Sandboxed target.** The ``room`` path param is validated against a strict
  ``battle-*`` pattern so this can never be used to join arbitrary rooms or as
  an SSRF pivot — it only ever talks to the configured Showdown host.
* **Additive.** This is entirely separate from the ``/ws/battles`` JSON bus.
  ELO, logging, analysis, and the existing visualizer are untouched.

The upstream connection is injectable (``connect_upstream``) so the proxy is
fully unit-testable against a scripted fake — no live Showdown server required.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Only well-formed battle room ids may be proxied.  Showdown battle rooms look
# like ``battle-gen9randombattle-17`` or ``battle-gen9nationaldex-1234-abcdef``.
_ROOM_RE = re.compile(r"^battle-[a-z0-9-]+$")

# Seconds to wait for the guest login + room join to complete before giving up.
_LOGIN_TIMEOUT_SECS: float = 15.0
# Idle period after which we send a keepalive ping to the browser.
_IDLE_PING_SECS: float = 25.0
# WebSocket close code for an invalid room id (1008 = policy violation).
_CLOSE_POLICY_VIOLATION = 1008


class UpstreamWS(Protocol):
    """Minimal async WebSocket surface the proxy needs from the upstream conn.

    Satisfied by ``websockets`` client connections and by test fakes alike.
    """

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


# Factory: given a ws:// URI, return a connected upstream.  Injected for tests.
ConnectUpstream = Callable[[str], Awaitable[UpstreamWS]]


async def _default_connect(uri: str) -> UpstreamWS:
    """Connect to the upstream Showdown server using the ``websockets`` lib."""
    import websockets

    # ``await websockets.connect(...)`` returns a live connection object that
    # exposes send / recv / close — matching the UpstreamWS protocol.
    return await websockets.connect(uri, max_size=None)


def _guest_name() -> str:
    """A unique-ish guest name so concurrent spectators don't collide."""
    return f"NidozoSpec{secrets.token_hex(3)}"


async def _login_and_join(upstream: UpstreamWS, room: str, username: str) -> None:
    """Perform the guest handshake and join ``room``.

    Sequence (verified against a ``--no-security`` server, see research §1):
        recv ``|challstr|`` → send ``|/trn NAME,0,`` (empty assertion)
        recv ``|updateuser| NAME|1|...`` (NAMED=1) → send ``|/join ROOM``

    Raises:
        TimeoutError: if the handshake does not complete in time.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + _LOGIN_TIMEOUT_SECS
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Showdown guest login/join timed out")
        raw = await asyncio.wait_for(upstream.recv(), timeout=remaining)
        text = raw.decode() if isinstance(raw, bytes) else raw
        for line in text.split("\n"):
            if line.startswith("|challstr|"):
                # Empty assertion: only valid on a --no-security dev server.
                await upstream.send(f"|/trn {username},0,")
            elif line.startswith("|updateuser|"):
                parts = line.split("|")
                # |updateuser| NAME|NAMED|AVATAR|SETTINGS  → parts[3] == NAMED
                if len(parts) > 3 and parts[3] == "1":
                    await upstream.send(f"|/join {room}")
                    return


async def _relay_upstream_to_client(upstream: UpstreamWS, client: WebSocket) -> None:
    """Forward every upstream frame to the browser, with idle keepalive pings."""
    while True:
        try:
            raw = await asyncio.wait_for(upstream.recv(), timeout=_IDLE_PING_SECS)
        except TimeoutError:
            # Keep intermediaries from dropping an idle connection.
            await client.send_text("|ping")
            continue
        text = raw.decode() if isinstance(raw, bytes) else raw
        await client.send_text(text)


async def _drain_client(client: WebSocket) -> None:
    """Consume (and ignore) browser→server frames so we detect disconnects.

    A spectator is read-only; we only watch this side to know when the browser
    has gone away so we can tear down the upstream connection.
    """
    while True:
        await client.receive_text()


def create_showdown_ws_router(
    showdown_host: str = "localhost",
    showdown_port: int = 8000,
    connect_upstream: ConnectUpstream | None = None,
    auth_token: str | None = None,
) -> APIRouter:
    """Return a router exposing ``GET (ws) /ws/showdown/{room}``.

    Args:
        showdown_host: Host of the local Showdown server.
        showdown_port: Port of the local Showdown server.
        connect_upstream: Optional upstream-connection factory (injected in
            tests).  Defaults to a real ``websockets`` connection.
        auth_token: When set, the client must supply a matching ``?token=``
            query parameter.
    """
    from nidozo.api.auth import ws_authorized

    router = APIRouter()
    connect = connect_upstream or _default_connect
    uri = f"ws://{showdown_host}:{showdown_port}/showdown/websocket"

    @router.websocket("/ws/showdown/{room}")
    async def showdown_stream(ws: WebSocket, room: str) -> None:
        if not _ROOM_RE.match(room):
            # Reject before accepting so a bad client gets a clean failure.
            await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="invalid room id")
            return

        if not ws_authorized(ws, auth_token):
            await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="unauthorized")
            return

        await ws.accept()

        upstream: UpstreamWS | None = None
        try:
            upstream = await connect(uri)
            await asyncio.wait_for(
                _login_and_join(upstream, room, _guest_name()),
                timeout=_LOGIN_TIMEOUT_SECS,
            )

            # Race the relay against the browser disconnect watcher; whichever
            # finishes first ends the session and cancels the other.
            relay = asyncio.create_task(_relay_upstream_to_client(upstream, ws))
            drain = asyncio.create_task(_drain_client(ws))
            done, pending = await asyncio.wait(
                {relay, drain}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # Surface any non-cancellation error from the finished task(s).
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect | TimeoutError):
                    raise exc

        except WebSocketDisconnect:
            pass
        except TimeoutError:
            logger.warning("Showdown spectate timed out for room %s", room)
        except Exception as exc:  # noqa: BLE001 — proxy must never crash the app
            logger.error("Showdown proxy error for room %s: %s", room, exc)
        finally:
            if upstream is not None:
                try:
                    await upstream.send(f"|/leave {room}")
                except Exception:  # noqa: BLE001 — best-effort, may already be closed
                    pass
                try:
                    await upstream.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 — may already be closed
                pass

    return router


# Re-exported so callers can keep their own copy of the validator if needed.
__all__ = ["create_showdown_ws_router", "UpstreamWS", "ConnectUpstream"]


def is_valid_room(room: str) -> bool:
    """Return True if ``room`` is a well-formed Showdown battle room id."""
    return bool(_ROOM_RE.match(room))
