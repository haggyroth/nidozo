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

At DEBUG level the httpx / httpcore loggers are also enabled, so every request
to LM Studio (or any OpenAI-compatible backend) shows its method, URL, status,
and timing. Those loggers currently omit request headers — but that is their
choice, not our guarantee: a version bump that started rendering headers would
otherwise write ``Authorization: Bearer sk-…`` into stdout and ``LOG_FILE``.
So credentials are scrubbed on the way out, by value (this process's own API
keys) and by shape (``Bearer …``, ``x-api-key`` / ``api_key`` values, ``sk-…``
keys), alongside the ``?token=`` already handled by the filter (#283).
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


class _RedactSecretsFilter(logging.Filter):
    """Strip credentials out of anything on its way to a log sink (#276, #283).

    Two layers, because the two leaks have different shapes:

    * ``?token=`` in a request line — uvicorn's access log records the full
      request line, query string included (#276).
    * HTTP credentials — ``Authorization: Bearer …``, ``x-api-key``, raw
      ``sk-…`` keys, and this process's own API keys wherever they appear. The
      httpx/httpcore loggers are turned up to DEBUG, and #283 is the notice
      that keeping secrets out of their output currently depends on their repr
      not choosing to include headers.

    Rewriting ``record.msg``/``record.args`` keeps the record itself clean, so
    a handler attached later (pytest's caplog, a log shipper) is covered too.
    :meth:`_JsonFormatter.format` scrubs the rendered output as well, which is
    what catches a secret that arrived inside a non-string argument — a dict of
    headers, a request object — since only the rendering knows its text.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_secrets(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_redact_arg(a) for a in record.args)
        return True


def _redact_arg(value: Any) -> Any:
    return _redact_secrets(value) if isinstance(value, str) else value


def _redact_token_query(text: str) -> str:
    return _TOKEN_QUERY_RE.sub("token=REDACTED", text)


def _redact_secrets(text: str) -> str:
    """Mask every credential *text* carries, leaving the rest readable."""
    # Own credentials first: matching by value covers every rendering, however
    # a future library chooses to print a request. Plain replace — a secret can
    # contain regex metacharacters, and escaping it here buys nothing.
    for secret in _known_secrets():
        text = text.replace(secret, _MASK)
    text = _BEARER_RE.sub(rf"\1{_MASK}", text)
    text = _API_KEY_HEADER_RE.sub(rf"\1{_MASK}", text)
    text = _API_KEY_VALUE_RE.sub(f"sk-{_MASK}", text)
    return _redact_token_query(text)


def _known_secrets() -> list[str]:
    """The process's own credentials, read per call so a late-set env still counts."""
    return [
        value
        for name in _SECRET_ENV_VARS
        if len(value := os.environ.get(name, "")) >= _MIN_SECRET_LEN
    ]


# The value of a `token=` parameter, wherever it appears — in a query string
# (`?token=…`, `&token=…`) or formatted into a message. Runs to the next
# separator so the rest of the request line survives for debugging.
_TOKEN_QUERY_RE = re.compile(r"token=[^&\s\"',]*", re.IGNORECASE)

_MASK = "[REDACTED]"

# Credentials we hold: never worth logging, and never guessable from a shape.
_SECRET_ENV_VARS: tuple[str, ...] = (
    "NIDOZO_API_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LM_STUDIO_API_KEY",
)
# Below this a "secret" is a placeholder like "1" or "local", and masking every
# occurrence of it would shred unrelated log text.
_MIN_SECRET_LEN = 8

# Shape-based backstop for credentials this process does not hold — someone
# else's key, or one from a config file we never read. Each keeps the name and
# masks only the value, so a log line still says which header was sent.
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)([^\s\"',;}]+)")
_API_KEY_HEADER_RE = re.compile(
    r"(?i)(\b(?:x-api-key|api[-_]?key|api[-_]?token)\b['\"]?\s*[:=]\s*['\"]?)([^\s\"',;}]+)"
)
# OpenAI (`sk-`, `sk-proj-`) and Anthropic (`sk-ant-`) keys identify themselves.
_API_KEY_VALUE_RE = re.compile(r"\bsk-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_\-]{8,}")


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
            # Scrubbed here as well as in the filter: a secret can reach a
            # record inside a non-string argument (a dict of headers, a request
            # object), and only this rendering knows its text (#283).
            "message": _redact_secrets(record.message),
        }

        # Forward any extra= fields as top-level structured keys.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _redact_secrets(value) if isinstance(value, str) else value

        if record.exc_info:
            payload["exc"] = _redact_secrets(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = _redact_secrets(self.formatStack(record.stack_info))

        return _json_dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    """Wire up JSON logging for nidozo, uvicorn, and (at DEBUG) httpx.

    Every sink gets the credential redactor described in the module docstring.

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
    # that reaches this sink, including third-party ones (#276) — the httpx
    # DEBUG records of #283 included.
    handler.addFilter(_RedactSecretsFilter())

    root = logging.getLogger()
    root.setLevel(numeric)
    root.handlers.clear()
    root.addHandler(handler)

    # Optional file sink — LOG_FILE path gets the same JSON records.
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(_JsonFormatter())
        fh.addFilter(_RedactSecretsFilter())
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
        logging.getLogger(name).addFilter(_RedactSecretsFilter())

    # httpx / httpcore: expose raw LM Studio HTTP traffic at DEBUG. They log
    # method + URL today and no headers, but that is the library's choice
    # rather than ours: the redactor above is what keeps a future version's
    # headers out of the logs (#283). Muzzling these loggers instead would
    # cost the debugging they exist for. At INFO+ they are silenced so they
    # don't flood production logs.
    http_level = logging.DEBUG if numeric <= logging.DEBUG else logging.WARNING
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(http_level)
