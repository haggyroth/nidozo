"""The API token must never reach a log sink (#276).

Non-browser WebSocket clients still authenticate with ``?token=``, and uvicorn's
access log records the full request line — query string included. These tests
drive a realistic access-log record through the installed filter and assert the
secret is gone while the rest of the line survives for debugging.
"""

from __future__ import annotations

import io
import logging

import pytest

from nidozo.api.logging_config import (
    _redact_token_query,
    _RedactTokenFilter,
    configure_logging,
)

_SECRET = "s3cret-token-value"


# ---------------------------------------------------------------------------
# The redactor itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/ws/battles?token=s3cret-token-value", "/ws/battles?token=REDACTED"),
        ("/ws/battles?x=1&token=s3cret-token-value", "/ws/battles?x=1&token=REDACTED"),
        ("/ws/battles?token=s3cret-token-value&x=1", "/ws/battles?token=REDACTED&x=1"),
        ("/ws/battles?TOKEN=s3cret-token-value", "/ws/battles?token=REDACTED"),
        ("no query string here", "no query string here"),
    ],
)
def test_redacts_the_token_and_keeps_the_rest_of_the_url(raw: str, expected: str) -> None:
    assert _redact_token_query(raw) == expected


def test_filter_leaves_a_record_without_a_token_untouched() -> None:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.5:41234", "GET", "/api/leaderboard", "1.1", 200),
        None,
    )
    assert _RedactTokenFilter().filter(record) is True
    assert record.getMessage() == '10.0.0.5:41234 - "GET /api/leaderboard HTTP/1.1" 200'


def test_filter_redacts_a_dict_args_record() -> None:
    # Assigning the dict after construction: LogRecord.__init__ probes args[0]
    # for a Mapping, which a keyless dict cannot answer.
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "handshake %(path)s", (), None)
    record.args = {"path": f"/ws/battles?token={_SECRET}"}
    _RedactTokenFilter().filter(record)
    assert _SECRET not in record.getMessage()
    assert "token=REDACTED" in record.getMessage()


# ---------------------------------------------------------------------------
# Wired into the real logging setup
#
# These assert on the record that reaches the sink configure_logging() actually
# installs, rather than on caplog: configure_logging() clears the root handlers,
# which would take pytest's capture handler with it.
# ---------------------------------------------------------------------------

def _sink(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Point the configured root handler at a buffer we can read."""
    configure_logging()
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler))
    buf = io.StringIO()
    monkeypatch.setattr(handler, "stream", buf)
    return buf


def test_uvicorn_access_log_never_writes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the record uvicorn actually emits, through the installed filter."""
    buf = _sink(monkeypatch)

    # Exactly the shape uvicorn's access logger builds for a WS handshake.
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "10.0.0.5:41234", "GET", f"/ws/battles?token={_SECRET}", "1.1", 101,
    )

    written = buf.getvalue()
    assert _SECRET not in written
    assert "token=REDACTED" in written
    # The request line is still there to debug with.
    assert "/ws/battles" in written


def test_nested_nidozo_api_loggers_are_redacted_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A record logged on a *child* logger (nidozo.api.ws) — the common case here.

    Logger-level filters don't apply to descendants, so this is what the
    handler-level filter is for.
    """
    buf = _sink(monkeypatch)

    logging.getLogger("nidozo.api.ws").info(
        "client connected: %s", f"/ws/battles?token={_SECRET}"
    )

    written = buf.getvalue()
    assert _SECRET not in written
    assert "token=REDACTED" in written


def test_the_redactor_is_installed_on_both_layers() -> None:
    """The sink and the originating loggers, so late-attached handlers stay clean."""
    configure_logging()
    root_handler_filters = [f for h in logging.getLogger().handlers for f in h.filters]
    assert any(isinstance(f, _RedactTokenFilter) for f in root_handler_filters)
    assert any(
        isinstance(f, _RedactTokenFilter)
        for f in logging.getLogger("uvicorn.access").filters
    )
