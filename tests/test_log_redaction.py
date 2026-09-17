"""Credentials must never reach a log sink (#276, #283).

Non-browser WebSocket clients still authenticate with ``?token=``, and uvicorn's
access log records the full request line — query string included. Separately,
the httpx / httpcore loggers are turned up to DEBUG and today happen to log only
method + URL: headers, and therefore ``Authorization: Bearer sk-…``, would reach
stdout and ``LOG_FILE`` the moment one of those libraries renders them (#283).

These tests drive realistic records — including one shaped like the future
httpx output that issue anticipates — through the installed filter and formatter
and assert the secret is gone while the rest of the line survives.
"""

from __future__ import annotations

import io
import logging

import pytest

from nidozo.api.logging_config import (
    _redact_secrets,
    _redact_token_query,
    _RedactSecretsFilter,
    configure_logging,
)

_SECRET = "s3cret-token-value"
_ANTHROPIC_KEY = "sk-ant-api03-Zx9Qw8Er7Ty6Ui5Op4"
_OPENAI_KEY = "sk-proj-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8"
_NIDOZO_TOKEN = "nid0zo-s3cret-4f2a9c1e"


@pytest.fixture
def no_ambient_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any real credentials from the environment for the shape tests.

    The redactor also masks this process's own keys by value, so a developer
    machine with ``ANTHROPIC_API_KEY`` exported must not change what these
    assertions mean.
    """
    for name in ("NIDOZO_API_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LM_STUDIO_API_KEY"):
        monkeypatch.delenv(name, raising=False)


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
    assert _RedactSecretsFilter().filter(record) is True
    assert record.getMessage() == '10.0.0.5:41234 - "GET /api/leaderboard HTTP/1.1" 200'


def test_filter_redacts_a_dict_args_record() -> None:
    # Assigning the dict after construction: LogRecord.__init__ probes args[0]
    # for a Mapping, which a keyless dict cannot answer.
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "handshake %(path)s", (), None)
    record.args = {"path": f"/ws/battles?token={_SECRET}"}
    _RedactSecretsFilter().filter(record)
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
    assert any(isinstance(f, _RedactSecretsFilter) for f in root_handler_filters)
    assert any(
        isinstance(f, _RedactSecretsFilter)
        for f in logging.getLogger("uvicorn.access").filters
    )


# ---------------------------------------------------------------------------
# HTTP credentials (#283)
#
# The httpx / httpcore loggers run at DEBUG. Their current output carries no
# headers, so nothing leaks today — but nothing in *our* code keeps it that way
# either. These are the shapes a version bump would produce.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Header line, as a URL request would print it.
        (
            f"Authorization: Bearer {_ANTHROPIC_KEY}",
            "Authorization: Bearer [REDACTED]",
        ),
        # A repr of a headers dict — quoted key, quoted value, trailing comma.
        (
            f"'authorization': 'Bearer {_ANTHROPIC_KEY}',",
            "'authorization': 'Bearer [REDACTED]',",
        ),
        (
            f"headers={{'x-api-key': '{_OPENAI_KEY}'}}",
            "headers={'x-api-key': '[REDACTED]'}",
        ),
        (f"api_key={_OPENAI_KEY}", "api_key=[REDACTED]"),
        (f"x-api-key: {_OPENAI_KEY}", "x-api-key: [REDACTED]"),
        # A key with no header around it — the value identifies itself.
        (
            f"backend rejected key {_ANTHROPIC_KEY} (401)",
            "backend rejected key sk-[REDACTED] (401)",
        ),
        # Nothing to redact.
        ("GET /api/leaderboard HTTP/1.1 200", "GET /api/leaderboard HTTP/1.1 200"),
    ],
)
def test_credential_values_are_redacted_and_the_line_survives(
    no_ambient_secrets, raw: str, expected: str
) -> None:
    assert _redact_secrets(raw) == expected


def test_a_custom_header_named_key_cannot_hide_behind_a_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The by-value pass is the guarantee: no shape rule can anticipate every header.

    ``X-Nidozo-Key`` matches none of the patterns, so this is purely the
    process's own credential being recognised wherever it turns up.
    """
    monkeypatch.setenv("NIDOZO_API_TOKEN", _NIDOZO_TOKEN)
    raw = f"handshake headers={{'X-Nidozo-Key': '{_NIDOZO_TOKEN}'}} retry=3"

    redacted = _redact_secrets(raw)

    assert _NIDOZO_TOKEN not in redacted
    assert "'X-Nidozo-Key': '[REDACTED]'" in redacted
    assert "retry=3" in redacted


def test_a_short_env_value_is_not_mistaken_for_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below the length floor the value is a placeholder, not a credential.

    Masking ``local`` on sight would shred unrelated log text, which is why the
    floor exists rather than a bare "is the env var set".
    """
    monkeypatch.setenv("LM_STUDIO_API_KEY", "local")
    assert _redact_secrets("using key local for LM Studio") == "using key local for LM Studio"


def test_a_future_httpx_debug_record_with_headers_does_not_leak_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#283 end to end: the record a bumped httpx would emit at DEBUG.

    The headers arrive as a non-string argument, so the filter's rewrite of
    ``msg``/``args`` cannot see inside them — the formatter's scrub of the
    rendered message is what has to catch it.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", _ANTHROPIC_KEY)
    buf = _sink(monkeypatch)
    httpx_logger = logging.getLogger("httpx")
    monkeypatch.setattr(httpx_logger, "level", logging.DEBUG)

    httpx_logger.debug(
        'HTTP Request: %s %s "%s %d %s" headers=%s',
        "POST",
        "https://api.anthropic.com/v1/messages",
        "HTTP/1.1",
        200,
        "OK",
        {"authorization": f"Bearer {_ANTHROPIC_KEY}", "x-api-key": _ANTHROPIC_KEY},
    )

    written = buf.getvalue()
    assert "HTTP Request" in written, "the DEBUG record never reached the sink"
    assert _ANTHROPIC_KEY not in written
    assert "Bearer [REDACTED]" in written
    # The request is still identifiable — the point of enabling this at all.
    assert "api.anthropic.com/v1/messages" in written


def test_a_traceback_does_not_carry_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracebacks are rendered by the formatter, not the filter — scrub there too."""
    monkeypatch.setenv("OPENAI_API_KEY", _OPENAI_KEY)
    buf = _sink(monkeypatch)

    try:
        raise RuntimeError(f"401 Unauthorized (key {_OPENAI_KEY})")
    except RuntimeError:
        logging.getLogger("nidozo.llm.openai").exception("completion failed")

    written = buf.getvalue()
    assert _OPENAI_KEY not in written
    assert "401 Unauthorized" in written
    assert "RuntimeError" in written


def test_extra_fields_are_scrubbed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret passed as a structured field must not ride out in the JSON."""
    monkeypatch.setenv("OPENAI_API_KEY", _OPENAI_KEY)
    buf = _sink(monkeypatch)

    logging.getLogger("nidozo.llm.openai").warning(
        "backend unreachable", extra={"endpoint": f"https://api.openai.com?key={_OPENAI_KEY}"}
    )

    written = buf.getvalue()
    assert _OPENAI_KEY not in written
    assert "backend unreachable" in written


def test_httpx_logging_is_still_enabled_at_debug_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """The #283 fix is redaction, not muting the loggers.

    Pinned deliberately: silencing httpx would also satisfy "no headers in the
    logs", while removing the traffic detail the DEBUG level exists to show.
    """
    from nidozo.api import logging_config

    monkeypatch.setattr(logging_config, "_CONFIGURED", False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setattr(logging.getLogger(), "level", logging.getLogger().level)
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.getLogger(name).level)

    configure_logging("DEBUG")

    assert logging.getLogger("httpx").level == logging.DEBUG
    assert logging.getLogger("httpcore").level == logging.DEBUG
