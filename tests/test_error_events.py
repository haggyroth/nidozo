"""Failure detail stays in the server log, not on the event bus (#281).

Every battle runner publishes an ``error`` event that fans out to *every*
connected browser. An exception's own text is written for whoever debugs it —
it names files, endpoints, prompt versions — so only messages authored for the
user may be published.
"""

from __future__ import annotations

import pytest

from nidozo.errors import GENERIC_ERROR_MESSAGE, UserFacingError, public_error_message


class _Rejected(UserFacingError):
    """A product-defined condition, raised by our own code."""


# A message with everything an exception leaks in practice: a host, a URL, a
# filesystem path, and a library's own wording.
_LEAKY = (
    "HTTPConnectionPool(host='enterprise-e.local', port=1234): Max retries exceeded "
    "with url: /v1/chat/completions (Caused by ConnectTimeoutError) "
    "[key=sk-live-abc123] — see data/natdex_movesets.json"
)


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------

def test_a_user_facing_error_keeps_its_message() -> None:
    assert public_error_message(_Rejected("That team is not legal for OU.")) == (
        "That team is not legal for OU."
    )


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError(_LEAKY),
        ValueError("Unknown tier: 'ou2'. Valid tiers: ['ou', 'uu', 'lc']"),
        KeyError("No moveset defined for species 'roty'"),
        TimeoutError("Showdown guest login/join timed out"),
        OSError("[Errno 61] Connection refused"),
    ],
)
def test_everything_else_is_replaced_with_a_generic_message(exc: Exception) -> None:
    message = public_error_message(exc)
    assert message == GENERIC_ERROR_MESSAGE
    for leak in ("enterprise-e", "sk-live", "natdex_movesets", "roty", "Errno", "ou2"):
        assert leak not in message


def test_a_user_facing_subclass_is_recognised() -> None:
    assert isinstance(_Rejected("x"), UserFacingError)
    assert public_error_message(_Rejected("x")) == "x"


def test_cancellation_is_not_special_cased_into_a_leak() -> None:
    """CancelledError inherits BaseException — public_error_message must still hold."""
    assert public_error_message(BaseException(_LEAKY)) == GENERIC_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# Through the runner: the event a browser actually receives
# ---------------------------------------------------------------------------

async def _error_event(tmp_path, monkeypatch, exc: Exception) -> dict:
    """Drive run_battles with a player that raises *exc*, and return the error event."""
    from nidozo.api import orchestration
    from nidozo.api.models import StartBattleRequest
    from nidozo.db.store import BattleStore
    from tests.test_orchestration import _FakeBus, _RaisingPlayer

    store = BattleStore(tmp_path / "err.db")
    try:
        p1 = store.get_or_create_model("random", "random", "v9")
        p2 = store.get_or_create_model("random", "random2", "v9")
        bid = store.create_battle("e", "gen9randombattle", p1, p2)

        monkeypatch.setattr(
            orchestration, "_build_streaming_player", lambda *a, **k: _RaisingPlayer(exc)
        )
        req = StartBattleRequest(
            p1_provider="random", p2_provider="random", tier="random", n_battles=1
        )
        bus = _FakeBus()
        await orchestration.run_battles(req, [bid], store, bus, {})

        events = [e for e in bus.events if e["type"] == "error"]
        assert len(events) == 1, bus.events
        return events[0]
    finally:
        store.close()


async def test_a_backend_failure_is_not_broadcast_verbatim(tmp_path, monkeypatch) -> None:
    event = await _error_event(tmp_path, monkeypatch, RuntimeError(_LEAKY))

    assert event["message"] == GENERIC_ERROR_MESSAGE
    assert "enterprise-e" not in str(event)
    assert "sk-live" not in str(event)
    # The event still identifies which battle failed, so a log can be found.
    assert event["battle_id"] is not None


async def test_a_user_facing_failure_is_broadcast_verbatim(tmp_path, monkeypatch) -> None:
    """The negative control for the test above — the useful message survives."""
    event = await _error_event(
        tmp_path, monkeypatch, _Rejected("Showdown rejected the submitted team.")
    )
    assert event["message"] == "Showdown rejected the submitted team."


async def test_the_full_exception_still_reaches_the_log(tmp_path, monkeypatch, caplog) -> None:
    """Hiding the detail from the browser may not hide it from the operator."""
    import logging

    with caplog.at_level(logging.ERROR, logger="nidozo.api.orchestration"):
        await _error_event(tmp_path, monkeypatch, RuntimeError(_LEAKY))

    assert any("enterprise-e.local" in r.getMessage() for r in caplog.records)
    # A traceback, not just the message — that is now the only copy.
    assert any(r.exc_info is not None for r in caplog.records)
