"""EventBus — asyncio broadcast queue for live battle state streaming."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

# Maximum events buffered per subscriber before oldest are dropped.
_QUEUE_MAX = 256

# Maximum structural events buffered for replay to late-joining subscribers.
# Sized to hold a full draft (2×draft_start + 12×draft_pick + 2×draft_complete)
# plus a tournament bracket and battle_start — roughly 30–40 events typical.
_REPLAY_MAX = 100

# Only structural / setup events are kept in the replay buffer.  Per-turn
# events (turn, state_update, thinking, coach_thinking) are intentionally
# excluded: replaying them would insert duplicate entries into the action log.
_REPLAY_TYPES: frozenset[str] = frozenset({
    "battle_start",
    "draft_start",
    "draft_pick",
    "draft_complete",
    "battle_end",
    "battle_cancelled",
    "error",
    "tournament_start",
    "tournament_bracket",
    "tournament_battle_start",
    "tournament_battle_end",
    "tournament_result",
    "season_start",
    "season_result",
    # OP-02: lets late-joining WS clients learn the Showdown room id for spectating.
    "showdown_room",
})


class EventBus:
    """Publish battle events; each subscriber gets its own bounded queue.

    **Replay buffer** — subscribers that connect after a battle has started
    (the common case for P1's draft phase) receive an immediate replay of
    structural events since the most recent ``battle_start``.  This ensures
    the draft overlay appears for both players even when the WebSocket
    connection races the background task.  The buffer is cleared on each
    ``battle_start`` so stale events from previous battles are never replayed.

    Only events in ``_REPLAY_TYPES`` are buffered; high-frequency per-turn
    events (``turn``, ``state_update``, etc.) are excluded to prevent
    duplicates in the action log on reconnect.

    If a subscriber's queue is full (e.g. a slow or dead WebSocket), the
    oldest buffered event is silently dropped to make room for the new one.
    This prevents a stuck client from growing memory without bound.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[dict[str, Any]]] = []
        self._replay: deque[dict[str, Any]] = deque(maxlen=_REPLAY_MAX)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        # Replay buffered events before registering so the new subscriber
        # sees a consistent snapshot of the current battle's setup state.
        # Because subscribe() and publish() both run on the asyncio event loop
        # (single-threaded), there is no race between the replay and incoming
        # publishes — the caller will receive the replayed events first, then
        # any events published after this subscribe() call returns.
        for event in self._replay:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                break  # Queue can't hold more; subscriber will catch up via live events
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    async def publish(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")

        # A new battle_start means previous battle's events are stale — clear
        # so late subscribers only see the current battle's setup events.
        if event_type == "battle_start":
            self._replay.clear()

        if event_type in _REPLAY_TYPES:
            self._replay.append(event)

        for q in list(self._queues):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest to make room
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # race between full-check and put; give up rather than block
