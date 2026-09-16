"""Optional in-process rate limiting for the mutating API routes (#233, #277).

Complements the shared-secret auth (#212): even a valid client (or an open
instance during local dev) shouldn't be able to hammer the endpoints that spend
real LLM credits. When ``NIDOZO_RATE_LIMIT_PER_MIN`` is a positive integer, the
battle/tournament/season/experiment *start* endpoints are limited per client IP
with a simple fixed-window counter (no external store — fine for a single
instance).

When ``NIDOZO_API_TOKEN`` is set the instance is reachable by others, so an unset
limit defaults to `DEFAULT_LIMIT_PER_MIN` rather than to "no limit"; an open
instance (local dev) stays unlimited unless asked, so the dev loop is unchanged.

Client identity
---------------
``request.client.host`` is the *peer* address, which is the reverse proxy's when
the API sits behind one — every client then shares a single bucket, turning a
per-IP limit into a global quota that one client can exhaust for everyone. Set
``NIDOZO_TRUSTED_PROXIES`` (comma-separated IPs/CIDRs, e.g. ``172.16.0.0/12``)
to the addresses of the proxies in front of this instance; the client key then
becomes the rightmost ``X-Forwarded-For`` hop that is *not* a trusted proxy,
which is the address of whoever actually connected.

The header is read **only** when the direct peer is a trusted proxy. Trust
nothing by default: an unset ``NIDOZO_TRUSTED_PROXIES`` means ``X-Forwarded-For``
is ignored entirely, because a directly-reachable client can put anything in it.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)

# POST routes that kick off (potentially many) real, credit-spending battles.
_LIMITED_PATHS: tuple[str, ...] = (
    "/api/battles/start",
    "/api/tournament/start",
    "/api/seasons/start",
    "/api/experiments/start",
)

_WINDOW_SECS = 60.0

# Limit applied when the instance is authenticated but no limit was configured:
# an exposed instance gets a bound by default (#277). Generous enough that a
# human clicking through the UI never sees it.
DEFAULT_LIMIT_PER_MIN = 60

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def get_rate_limit(authenticated: bool = False) -> int:
    """Requests/minute allowed on the limited routes; 0 disables it.

    An explicit ``NIDOZO_RATE_LIMIT_PER_MIN`` decides: a positive integer is the
    limit, and ``0``/``-1``/garbage/non-positive means unlimited — that is how an
    operator says "I know what I'm doing, leave it off". With the variable
    *unset*, an authenticated (network-exposed) instance gets
    `DEFAULT_LIMIT_PER_MIN` while an open local-dev instance stays unlimited.
    """
    raw = os.environ.get("NIDOZO_RATE_LIMIT_PER_MIN", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return 0
        return value if value > 0 else 0
    return DEFAULT_LIMIT_PER_MIN if authenticated else 0


def get_trusted_proxies() -> tuple[_IPNetwork, ...]:
    """Parse ``NIDOZO_TRUSTED_PROXIES`` — comma-separated IPs and CIDRs."""
    raw = os.environ.get("NIDOZO_TRUSTED_PROXIES", "").strip()
    if not raw:
        return ()
    networks: list[_IPNetwork] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            # strict=False tolerates a host address written with a prefix
            # (e.g. "10.0.0.1/8"), which operators do write.
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(
                "NIDOZO_TRUSTED_PROXIES: ignoring unparseable entry %r — expected an "
                "IP address or CIDR block.", entry,
            )
    return tuple(networks)


def client_ip(request: Request, trusted: Sequence[_IPNetwork] = ()) -> str:
    """The address to bucket this request under.

    Returns the peer address unless the peer is a trusted proxy, in which case
    the rightmost non-trusted ``X-Forwarded-For`` hop is returned. Falls back to
    the peer when the header is absent or every hop is itself a trusted proxy.
    """
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted(peer, trusted):
        return peer

    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
    hops = [h for h in hops if h]
    for hop in reversed(hops):
        # A hop that isn't a parseable IP can't be one of ours, so it counts as
        # the client — and it is only reached at all when a proxy appended a
        # genuine address to its right, since the proxy always appends.
        if not _is_trusted(hop, trusted):
            return hop
    return hops[0] if hops else peer


def _is_trusted(host: str, trusted: Sequence[_IPNetwork]) -> bool:
    if not trusted:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in network for network in trusted)


def add_rate_limit(app: FastAPI, per_min: int) -> None:
    """Install the fixed-window limiter middleware (no-op when *per_min* <= 0)."""
    if per_min <= 0:
        return

    trusted = get_trusted_proxies()
    logger.info(
        "Rate limiting ENABLED — %d req/min per IP on start endpoints (trusted proxies: %s).",
        per_min,
        ", ".join(str(n) for n in trusted) if trusted else "none — X-Forwarded-For ignored",
    )
    # ip -> (window_start_monotonic, count). Mutated only from the event loop
    # (middleware has no await before the read/modify/write), so no lock needed.
    buckets: dict[str, tuple[float, int]] = {}
    # Window-start of the last sweep; buckets are otherwise never removed, which
    # on an internet-exposed instance grew one entry per source IP forever (#277).
    last_prune = time.monotonic()

    @app.middleware("http")
    async def _limit(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        nonlocal last_prune
        if request.method == "POST" and request.url.path in _LIMITED_PATHS:
            ip = client_ip(request, trusted)
            now = time.monotonic()
            if now - last_prune >= _WINDOW_SECS:
                # Sweep at most once per window; every entry it drops has expired
                # and would be reset on lookup anyway.
                _prune(buckets, now)
                last_prune = now
            start, count = buckets.get(ip, (now, 0))
            if now - start >= _WINDOW_SECS:
                start, count = now, 0  # window rolled over
            if count >= per_min:
                retry = max(1, int(_WINDOW_SECS - (now - start)))
                return JSONResponse(
                    {"detail": "Rate limit exceeded — slow down."},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
            buckets[ip] = (start, count + 1)
        return await call_next(request)


def _prune(buckets: dict[str, tuple[float, int]], now: float) -> None:
    """Drop buckets whose window has expired."""
    for ip in [ip for ip, (start, _) in buckets.items() if now - start >= _WINDOW_SECS]:
        del buckets[ip]
