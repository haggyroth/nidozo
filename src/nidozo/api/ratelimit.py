"""Optional in-process rate limiting for the mutating API routes (#233).

Complements the shared-secret auth (#212): even a valid client (or an open
instance during local dev) shouldn't be able to hammer the endpoints that spend
real LLM credits. When ``NIDOZO_RATE_LIMIT_PER_MIN`` is a positive integer, the
battle/tournament/season/experiment *start* endpoints are limited per client IP
with a simple fixed-window counter (no external store — fine for a single
instance). Unset or ``0`` disables it, so local dev is unchanged.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable

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


def get_rate_limit() -> int:
    """Requests/minute allowed on the limited routes; 0 (default) disables it."""
    raw = os.environ.get("NIDOZO_RATE_LIMIT_PER_MIN", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def add_rate_limit(app: FastAPI, per_min: int) -> None:
    """Install the fixed-window limiter middleware (no-op when *per_min* <= 0)."""
    if per_min <= 0:
        return

    logger.info("Rate limiting ENABLED — %d req/min per IP on start endpoints.", per_min)
    # ip -> (window_start_monotonic, count). Mutated only from the event loop
    # (middleware has no await before the read/modify/write), so no lock needed.
    buckets: dict[str, tuple[float, int]] = {}

    @app.middleware("http")
    async def _limit(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method == "POST" and request.url.path in _LIMITED_PATHS:
            ip = request.client.host if request.client else "unknown"
            now = time.monotonic()
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
