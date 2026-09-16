"""Structured JSON logging configuration for Nidozo.

Call configure_logging() once at process startup. All loggers in the nidozo
package — and uvicorn's access/error loggers — will emit newline-delimited
JSON records instead of the default human-readable format.

Environment variables:
    LOG_LEVEL   DEBUG / INFO / WARNING / ERROR  (default: INFO)
    LOG_FILE    Absolute path; when set, logs are written there in addition
                to stdout.  Useful for post-mortem debugging without a
                log aggregator.

Example output (one record, pretty-printed for readability):
    {
        "ts":       "2026-06-06T15:42:01.123456Z",
        "level":    "INFO",
        "logger":   "nidozo.battle.llm_player",
        "message":  "[p1] turn 4 chose: thunderbolt (2.3s)",
        "battle_id": 12,
        "player":   "p1"
    }

At DEBUG level the httpx / httpcore loggers are also enabled, which exposes
every HTTP request and response made to LM Studio (or any OpenAI-compatible
backend) — headers, status codes, and timing included.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

try:
    import json as _json
    _json_dumps = _json.dumps
except ImportError:  # pragma: no cover
    raise

_CONFIGURED = False

# Standard LogRecord attributes that are NOT user-supplied extras.
# Anything in record.__dict__ that isn't in this set gets forwarded as a
# structured field so logger.info("msg", extra={"battle_id": 7}) just works.
_STANDARD_ATTRS: frozenset[str] = frozenset({
    "name", "msg", "args", "created", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message",
    "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName", "exc_info", "exc_text",
    "taskName",
})


class _RedactTokenFilter(logging.Filter):
    """Strip ``?token=`` values out of anything on its way to a log sink (#276).

    Browsers now carry the WebSocket credential in ``Sec-WebSocket-Protocol``, but
    non-browser clients still use the query parameter — and uvicorn's access log
    records the full request line, query string included. Writing the shared
    secret to stdout or ``LOG_FILE`` hands it to anyone who can read the logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_token_query(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_redact_arg(a) for a in record.args)
        return True


def _redact_arg(value: Any) -> Any:
    return _redact_token_query(value) if isinstance(value, str) else value


def _redact_token_query(text: str) -> str:
    return _TOKEN_QUERY_RE.sub("token=REDACTED", text)


# The value of a `token=` parameter, wherever it appears — in a query string
# (`?token=…`, `&token=…`) or formatted into a message. Runs to the next
# separator so the rest of the request line survives for debugging.
_TOKEN_QUERY_RE = re.compile(r"token=[^&\s\"',]*", re.IGNORECASE)


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record on a single line.

    Any keys passed via ``extra=`` are forwarded as top-level fields so
    callers can attach structured context without string-formatting it into
    the message.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Ensure record.message is populated (Formatter.format() does this).
        record.message = record.getMessage()

        payload: dict[str, Any] = {
            "ts":      datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.message,
        }

        # Forward any extra= fields as top-level structured keys.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return _json_dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    """Wire up JSON logging for nidozo, uvicorn, and (at DEBUG) httpx.

    Safe to call multiple times — only takes effect on the first call.

    Args:
        level: Log level string ("DEBUG", "INFO", "WARNING", "ERROR").
               Falls back to the LOG_LEVEL environment variable, then "INFO".
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return
    _CONFIGURED = True

    effective_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, effective_level, logging.INFO)

    handler: logging.Handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    # Redact on the handler too: handler filters see records from *every* logger
    # that reaches this sink, including third-party ones (#276).
    handler.addFilter(_RedactTokenFilter())

    root = logging.getLogger()
    root.setLevel(numeric)
    root.handlers.clear()
    root.addHandler(handler)

    # Optional file sink — LOG_FILE path gets the same JSON records.
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(_JsonFormatter())
        fh.addFilter(_RedactTokenFilter())
        root.addHandler(fh)

    # Uvicorn splits output across three loggers; align them to root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True

    # Also redact at the originating loggers, so records logged here stay clean
    # for handlers attached later (pytest's caplog, a log shipper) rather than
    # only for the sinks configured above (#276).
    for name in ("uvicorn.access", "nidozo.api"):
        logging.getLogger(name).addFilter(_RedactTokenFilter())

    # httpx / httpcore: expose raw LM Studio HTTP traffic at DEBUG.
    # At INFO+ these are silenced so they don't flood production logs.
    http_level = logging.DEBUG if numeric <= logging.DEBUG else logging.WARNING
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(http_level)
