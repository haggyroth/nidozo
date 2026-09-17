"""Error types whose messages are safe to show outside the server (#281).

The battle runner reports failures on the shared event bus, which fans out to
every connected browser. An exception's own text is written for whoever is
debugging — it names files, tiers, prompt versions, model endpoints, HTTP URLs —
and none of that belongs in a browser tab, least of all one belonging to a
different user.

So a message is published only when it was *written to be read*:
:class:`UserFacingError` marks exactly that. Everything else is replaced with a
generic line, and the exception itself (type, message, traceback) goes to the
log, where an operator can find it. The published event still carries the
``battle_id``, so a report of "battle 12 failed" can be matched to its trace.
"""

from __future__ import annotations


class UserFacingError(Exception):
    """An error whose message is intended for the user and safe to display.

    Raise this for a condition the user can act on and whose wording is part of
    the product — a challenge that Showdown refused, a team it rejected. The
    message reaches the browser verbatim, so it must read as something addressed
    to the person watching, not as a diagnostic.
    """


# Shown for every failure that is not a UserFacingError. Deliberately says where
# to look rather than what happened: the operator has the trace, the user does
# not need it.
GENERIC_ERROR_MESSAGE = "The battle failed to complete. Check the server logs for details."


def public_error_message(exc: BaseException) -> str:
    """The message to publish for *exc* — its own only if it is user-facing."""
    if isinstance(exc, UserFacingError):
        return str(exc)
    return GENERIC_ERROR_MESSAGE
