"""Origin policy for the WebSocket endpoints (#280).

WebSockets are exempt from the same-origin policy, and CORS middleware never
sees the handshake — so without an explicit check, any page a user visits can
open a socket to this API and read the live battle stream (a cross-site
WebSocket hijack). The ``Origin`` header is the only thing that distinguishes a
page the operator served from a page that merely happens to share the browser;
browsers always send it on a WebSocket handshake, and a page cannot forge it.

The policy is deliberately narrow:

* **No ``Origin``** → allowed. Every browser sends one, so this is a non-browser
  client (``curl``, a script, the integration suite). Such a client cannot be
  made to connect by a page the user visited, so there is no hijack to prevent —
  and rejecting it would break every scripted client for no gain.
* **Same origin as this app** → allowed, with no configuration. The SPA is
  served by this same service, so this is the normal case.
* **``Origin: null``** → rejected. That is a sandboxed iframe or a ``file://``
  page, never our own app.
* **Anything else** → rejected unless listed in ``NIDOZO_ALLOWED_ORIGINS``,
  which extends the built-in list (the Vite dev server, which proxies ``/ws``
  to the API from a different origin).

The same-origin test compares *host:port*, not the scheme: a deployment behind
TLS termination has the browser sending ``https://host`` while the app sees a
plain-HTTP request, and the origin's authority is what actually identifies the
page.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.websockets import WebSocket

# Origins allowed to talk to the API from a different origin. Kept here rather
# than in middleware.py so the HTTP CORS policy and the WebSocket origin policy
# have one source of truth — a cross-origin caller is a cross-origin caller.
CORS_ORIGINS: list[str] = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:5001",  # serve.py production default
]


def _parse_origins(raw: str) -> list[str]:
    """Split a comma-separated origin list, ignoring blanks."""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def allowed_origins() -> list[str]:
    """Every origin permitted to open a WebSocket.

    The built-in list plus whatever ``NIDOZO_ALLOWED_ORIGINS`` adds, read per
    call so a test or a deployment can change it without reimporting.
    """
    return CORS_ORIGINS + _parse_origins(os.environ.get("NIDOZO_ALLOWED_ORIGINS", ""))


def _normalize(origin: str) -> str:
    """Lowercase and drop a trailing slash, so trivial spellings still match."""
    return origin.strip().rstrip("/").lower()


def _authority(origin: str) -> str:
    """The ``host:port`` of an origin, or "" if it isn't a usable absolute URL."""
    try:
        return urlsplit(origin.strip()).netloc.lower()
    except ValueError:  # pragma: no cover — urlsplit raises on malformed IPv6
        return ""


def origin_allowed(ws: WebSocket, allowed: Sequence[str] | None = None) -> bool:
    """Return True if this WebSocket handshake may proceed (#280).

    Args:
        ws: the incoming handshake.
        allowed: explicit origin allowlist; defaults to the configured one
            (built-ins plus ``NIDOZO_ALLOWED_ORIGINS``).
    """
    # `sec-websocket-origin` is the pre-standard spelling some old clients still
    # send; browsers use `origin`.
    raw = ws.headers.get("origin") or ws.headers.get("sec-websocket-origin")
    if raw is None:
        # Not a browser: nothing to hijack, and rejecting would break scripts.
        return True

    origin = _normalize(raw)
    if not origin or origin == "null":
        # A sandboxed iframe or a local file — never our own page.
        return False

    if origin in {_normalize(o) for o in (allowed if allowed is not None else allowed_origins())}:
        return True

    # Same-origin: the browser connected to this app's own host, so the SPA it
    # loaded came from here.
    host = ws.headers.get("host")
    if not host:
        return False
    return _authority(raw) == host.strip().lower()
