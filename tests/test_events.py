"""Tests for EventBus — subscribe, publish, unsubscribe, and overflow."""

from __future__ import annotations

import asyncio

import pytest

from nidozo.api.events import _QUEUE_MAX, EventBus

# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_returns_queue() -> None:
    """subscribe() returns an asyncio.Queue."""
    bus = EventBus()
    q = bus.subscribe()
    assert isinstance(q, asyncio.Queue)


@pytest.mark.asyncio
async def test_subscribe_registers_queue() -> None:
    """Subscribed queue appears in the internal list."""
    bus = EventBus()
    q = bus.subscribe()
    assert q in bus._queues


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue() -> None:
    """unsubscribe() removes the queue from the internal list."""
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    assert q not in bus._queues


@pytest.mark.asyncio
async def test_unsubscribe_missing_queue_no_error() -> None:
    """Unsubscribing a queue that was never registered raises no error."""
    bus = EventBus()
    phantom: asyncio.Queue[dict] = asyncio.Queue()
    bus.unsubscribe(phantom)  # should not raise ValueError


# ---------------------------------------------------------------------------
# publish — delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_delivers_to_single_subscriber() -> None:
    """Published event lands in the subscriber's queue."""
    bus = EventBus()
    q = bus.subscribe()
    await bus.publish({"type": "battle_start"})
    assert q.get_nowait() == {"type": "battle_start"}


@pytest.mark.asyncio
async def test_publish_delivers_to_multiple_subscribers() -> None:
    """All active subscribers receive the event."""
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    await bus.publish({"type": "turn", "n": 1})
    assert q1.get_nowait() == {"type": "turn", "n": 1}
    assert q2.get_nowait() == {"type": "turn", "n": 1}


@pytest.mark.asyncio
async def test_publish_not_delivered_after_unsubscribe() -> None:
    """After unsubscribing, no further events reach that queue."""
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    await bus.publish({"type": "late_event"})
    assert q.empty()


# ---------------------------------------------------------------------------
# publish — overflow (drop-oldest)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_drops_oldest_when_queue_full() -> None:
    """When the queue is full, the oldest event is dropped to make room."""
    bus = EventBus()
    q = bus.subscribe()

    # Fill the queue to capacity with sentinel events
    for i in range(_QUEUE_MAX):
        q.put_nowait({"i": i})

    assert q.full()
    await bus.publish({"type": "newest"})

    # First item ({"i": 0}) should have been dropped
    first = q.get_nowait()
    assert first == {"i": 1}

    # Drain to the last item — it should be our newest event
    items = [first]
    while not q.empty():
        items.append(q.get_nowait())

    assert items[-1] == {"type": "newest"}
    assert len(items) == _QUEUE_MAX  # total count unchanged


@pytest.mark.asyncio
async def test_publish_queue_full_race_silently_ignored() -> None:
    """If put_nowait raises QueueFull after the full-check (race), it is swallowed."""
    bus = EventBus()
    q = bus.subscribe()

    # Simulate the race: queue appears not-full at the check but is full at put_nowait
    call_count = 0

    def patched_full() -> bool:
        nonlocal call_count
        call_count += 1
        # Report not-full on the first call so we skip the drop path,
        # then let QueueFull be raised by put_nowait naturally.
        return False

    q.full = patched_full  # type: ignore[method-assign]

    # Fill it so put_nowait will raise QueueFull
    for _ in range(_QUEUE_MAX):
        q.put_nowait({})

    # Should not raise even though put_nowait will hit QueueFull
    await bus.publish({"type": "race"})
