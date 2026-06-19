"""Unit tests for _StreamingMixin (OP-02 Stage 1).

Tests the showdown_room emission logic without a live poke-env / Showdown
server by injecting a minimal fake base class and a scripted EventBus.
"""

from __future__ import annotations

import pytest

from nidozo.api.events import EventBus
from nidozo.battle.streaming_player import _StreamingMixin


class _FakeBase:
    """Minimal stand-in for poke-env's Player._handle_battle_message."""

    async def _handle_battle_message(self, split_messages: list[list[str]]) -> None:
        pass


class _TestPlayer(_StreamingMixin, _FakeBase):
    """Concrete mixin under test — no poke-env dependencies needed."""

    def __init__(self, bus: EventBus, player_role: str = "p1", battle_id: int | None = None) -> None:
        self._init_streaming(bus, player_role)
        self._battles: dict = {}
        self._battle_id = battle_id


def _frame(room: str) -> list[list[str]]:
    """Minimal split_messages representation of a Showdown battle frame."""
    return [[f">{room}"], ["", "turn", "1"]]


@pytest.mark.asyncio
async def test_showdown_room_emitted_on_first_frame() -> None:
    """_StreamingMixin emits showdown_room once when a new battle room is seen."""
    bus = EventBus()
    q = bus.subscribe()
    player = _TestPlayer(bus, player_role="p1", battle_id=7)

    await player._handle_battle_message(_frame("battle-gen3randombattle-7"))

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    room_events = [e for e in events if e["type"] == "showdown_room"]
    assert len(room_events) == 1
    assert room_events[0]["room"] == "battle-gen3randombattle-7"
    assert room_events[0]["battle_id"] == 7


@pytest.mark.asyncio
async def test_showdown_room_emitted_only_once_per_battle() -> None:
    """Repeated frames for the same room do not produce duplicate showdown_room events."""
    bus = EventBus()
    q = bus.subscribe()
    player = _TestPlayer(bus)

    frame = _frame("battle-gen3randombattle-42")
    await player._handle_battle_message(frame)
    await player._handle_battle_message(frame)
    await player._handle_battle_message(frame)

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    room_events = [e for e in events if e["type"] == "showdown_room"]
    assert len(room_events) == 1


@pytest.mark.asyncio
async def test_showdown_room_emitted_per_distinct_room() -> None:
    """Each new room tag gets its own showdown_room event (back-to-back battles)."""
    bus = EventBus()
    q = bus.subscribe()
    player = _TestPlayer(bus)

    await player._handle_battle_message(_frame("battle-gen3randombattle-1"))
    await player._handle_battle_message(_frame("battle-gen3randombattle-2"))

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    rooms = [e["room"] for e in events if e["type"] == "showdown_room"]
    assert rooms == ["battle-gen3randombattle-1", "battle-gen3randombattle-2"]


@pytest.mark.asyncio
async def test_showdown_room_battle_id_none_for_random_bot() -> None:
    """When no _battle_id is set (e.g. RandomBot), battle_id field is None."""
    bus = EventBus()
    q = bus.subscribe()
    player = _TestPlayer(bus)  # no battle_id

    await player._handle_battle_message(_frame("battle-gen3randombattle-99"))

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    room_events = [e for e in events if e["type"] == "showdown_room"]
    assert room_events[0]["battle_id"] is None


@pytest.mark.asyncio
async def test_showdown_room_not_emitted_for_frameless_message() -> None:
    """Frames that lack a leading >room line (global messages) don't emit showdown_room."""
    bus = EventBus()
    q = bus.subscribe()
    player = _TestPlayer(bus)

    # Global frame: no leading '>room' line
    await player._handle_battle_message([["", "challstr", "4|FAKE"]])

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    assert not any(e["type"] == "showdown_room" for e in events)


# ---------------------------------------------------------------------------
# _send_challenges — team-rejection timeout (challenge never accepted)
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import logging  # noqa: E402

from nidozo.battle import streaming_player  # noqa: E402


class _FakePSClient:
    def __init__(self) -> None:
        self.logged_in = asyncio.Event()
        self.logged_in.set()
        self.challenges: list[tuple] = []

    async def challenge(self, opponent: str, fmt: str, team: object) -> None:
        self.challenges.append((opponent, fmt, team))


class _ChallengePlayer(_StreamingMixin, _FakeBase):
    """Mixin wired with just enough surface to drive _send_challenges."""

    def __init__(self, bus: EventBus, battle_id: int | None = 5) -> None:
        self._init_streaming(bus, "p1")
        self.ps_client = _FakePSClient()  # type: ignore[assignment]
        self._format = "gen9randombattle"
        self._battle_semaphore = asyncio.Semaphore(0)  # never released → timeout
        self._battle_count_queue = asyncio.Queue()
        self._battle_id = battle_id
        self.logger = logging.getLogger("test.challenge")

    def get_next_team(self) -> str | None:
        return None


@pytest.mark.asyncio
async def test_send_challenges_times_out_and_reports_team_rejection(monkeypatch) -> None:
    """A challenge that is never accepted (team rejected) raises and emits an error."""
    monkeypatch.setattr(streaming_player, "_CHALLENGE_TIMEOUT_SECS", 0.05)
    bus = EventBus()
    q = bus.subscribe()
    player = _ChallengePlayer(bus, battle_id=5)

    with pytest.raises(RuntimeError, match="team"):
        await player._send_challenges("opponent", 1)

    # A challenge was actually sent before the wait timed out.
    assert player.ps_client.challenges == [("opponent", "gen9randombattle", None)]

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["battle_id"] == 5
    assert "rejected" in errors[0]["message"].lower()


@pytest.mark.asyncio
async def test_handle_battle_message_tolerates_malformed_frame() -> None:
    """A frame with an empty leading line must not raise or emit showdown_room."""
    bus = EventBus()
    q = bus.subscribe()
    player = _TestPlayer(bus)

    # split_messages[0][0] raises IndexError — the guard should swallow it.
    await player._handle_battle_message([[]])

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert not any(e["type"] == "showdown_room" for e in events)
