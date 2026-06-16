"""Tests for LLMPlayer.choose_move — the core decision loop.

All tests run without a live Showdown server:
  - poke_env.player.Player.__init__ is patched to skip network setup
  - AbstractBattle is replaced with a lightweight MagicMock
  - serialize_battle and parse_action are patched at import sites
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nidozo.battle.llm_player import LLMPlayer

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_battle():
    """Minimal battle mock for choose_move tests."""
    battle = MagicMock()
    battle.turn = 3
    battle.battle_tag = "gen3randombattle-test"
    battle.format = "gen3randombattle"
    battle.weather = {}
    battle.fields = []
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    battle.active_pokemon = None
    battle.opponent_active_pokemon = None
    battle.team = {}
    battle.opponent_team = {}
    battle.force_switch = False
    # Two regular moves by default
    m1, m2 = MagicMock(), MagicMock()
    m1.id, m2.id = "thunderbolt", "surf"
    battle.available_moves = [m1, m2]
    battle.available_switches = []
    return battle


@pytest.fixture
def fake_order():
    order = MagicMock()
    order.message = "/choose move thunderbolt"
    return order


@pytest.fixture
def mock_backend():
    backend = AsyncMock()
    backend.complete = AsyncMock(
        return_value='{"reasoning":"test","action_type":"move","identifier":"thunderbolt"}'
    )
    return backend


def _make_player(backend, **kwargs) -> LLMPlayer:
    """Instantiate LLMPlayer without connecting to Showdown."""
    with patch("poke_env.player.Player.__init__", return_value=None):
        player = LLMPlayer(backend=backend, **kwargs)
    player.choose_random_move = MagicMock()
    player.create_order = MagicMock()
    # Short-circuit the prompt builder so tests don't need real templates
    player._prompt_builder.build_messages = MagicMock(
        return_value=[{"role": "user", "content": "Choose your move."}]
    )
    return player


# ---------------------------------------------------------------------------
# choose_move — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_returns_parsed_order(mock_backend, mock_battle, fake_order) -> None:
    """Successful backend response → parse_action result is returned."""
    player = _make_player(mock_backend)

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle)

    assert result is fake_order
    mock_backend.complete.assert_called_once()
    player.choose_random_move.assert_not_called()


@pytest.mark.asyncio
async def test_choose_move_logs_success_to_store(mock_backend, mock_battle, fake_order, tmp_path) -> None:
    """Successful turn is written to BattleStore with parse_success=True."""
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "test.db")
    m_id = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("test", "gen3randombattle", m_id, m_id)

    player = _make_player(mock_backend, store=store, battle_id=bid, player_role="p1")

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        await player.choose_move(mock_battle)

    turns = store.get_turns_with_state(bid)
    assert len(turns) == 1
    assert turns[0]["player_role"] == "p1"
    assert turns[0]["parse_success"] == 1
    assert turns[0]["action_chosen"] == fake_order.message


# ---------------------------------------------------------------------------
# choose_move — shared serialization (issue #136)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_reuses_passed_state_no_reserialize(
    mock_backend, mock_battle, fake_order
) -> None:
    """When a pre-serialized snapshot is passed in, choose_move must not call
    serialize_battle again (the streaming subclass already paid that cost)."""
    player = _make_player(mock_backend)
    passed = {"my_active": {"species": "pikachu"}}

    with patch("nidozo.battle.llm_player.serialize_battle") as mock_ser, \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle, state=passed)

    assert result is fake_order
    mock_ser.assert_not_called()
    # The caller's snapshot is left pristine — recent_events is added to a copy.
    assert "recent_events" not in passed


@pytest.mark.asyncio
async def test_choose_move_serializes_once_when_no_state(
    mock_backend, mock_battle, fake_order
) -> None:
    """Without a passed snapshot (non-streaming path), choose_move serializes
    exactly once."""
    player = _make_player(mock_backend)

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}) as mock_ser, \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        await player.choose_move(mock_battle)

    mock_ser.assert_called_once()


# ---------------------------------------------------------------------------
# choose_move — recharge short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_recharge_skips_llm(mock_backend, mock_battle) -> None:
    """Single recharge move → LLM is never called; create_order is used directly."""
    recharge = MagicMock()
    recharge.id = "recharge"
    mock_battle.available_moves = [recharge]

    player = _make_player(mock_backend)
    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}):
        await player.choose_move(mock_battle)

    mock_backend.complete.assert_not_called()
    player.create_order.assert_called_once_with(recharge)


# ---------------------------------------------------------------------------
# choose_move — empty response / retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_retries_on_empty_response(mock_backend, mock_battle, fake_order) -> None:
    """First attempt returns '' → one retry → succeeds."""
    mock_backend.complete = AsyncMock(side_effect=[
        "",
        '{"action_type":"move","identifier":"surf","reasoning":"ok"}',
    ])

    player = _make_player(mock_backend)

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle)

    assert result is fake_order
    assert mock_backend.complete.call_count == 2


@pytest.mark.asyncio
async def test_choose_move_empty_both_attempts_falls_back(mock_backend, mock_battle) -> None:
    """Both attempts return '' → choose_random_move, logged with parse_success=False."""
    mock_backend.complete = AsyncMock(return_value="")
    random_order = MagicMock()

    player = _make_player(mock_backend)
    player.choose_random_move.return_value = random_order

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}):
        result = await player.choose_move(mock_battle)

    assert result is random_order
    assert mock_backend.complete.call_count == 2
    player.choose_random_move.assert_called_once_with(mock_battle)


# ---------------------------------------------------------------------------
# choose_move — backend exceptions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_backend_error_first_then_succeeds(mock_battle, fake_order) -> None:
    """Backend raises on attempt 1; succeeds on attempt 2."""
    backend = AsyncMock()
    backend.complete = AsyncMock(side_effect=[
        RuntimeError("timeout"),
        '{"action_type":"move","identifier":"thunderbolt","reasoning":"ok"}',
    ])

    player = _make_player(backend)

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle)

    assert result is fake_order
    assert backend.complete.call_count == 2


@pytest.mark.asyncio
async def test_choose_move_backend_error_both_attempts_falls_back(mock_battle) -> None:
    """Backend raises both times → choose_random_move."""
    backend = AsyncMock()
    backend.complete = AsyncMock(side_effect=RuntimeError("down"))
    random_order = MagicMock()

    player = _make_player(backend)
    player.choose_random_move.return_value = random_order

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}):
        result = await player.choose_move(mock_battle)

    assert result is random_order
    player.choose_random_move.assert_called_once_with(mock_battle)


# ---------------------------------------------------------------------------
# choose_move — parse failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_parse_failure_falls_back(mock_backend, mock_battle) -> None:
    """parse_action returns None → choose_random_move."""
    random_order = MagicMock()

    player = _make_player(mock_backend)
    player.choose_random_move.return_value = random_order

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=None):
        result = await player.choose_move(mock_battle)

    assert result is random_order
    player.choose_random_move.assert_called_once_with(mock_battle)


@pytest.mark.asyncio
async def test_choose_move_parse_failure_logged(mock_backend, mock_battle, tmp_path) -> None:
    """Parse failure is written to the store with parse_success=False."""
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "test.db")
    m_id = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("test", "gen3randombattle", m_id, m_id)

    player = _make_player(mock_backend, store=store, battle_id=bid, player_role="p2")
    player.choose_random_move.return_value = MagicMock()

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=None):
        await player.choose_move(mock_battle)

    turns = store.get_turns_with_state(bid)
    assert len(turns) == 1
    assert turns[0]["parse_success"] == 0


# ---------------------------------------------------------------------------
# choose_move — per-turn timeout (#160) + distinct fallback reasons (#161)
# ---------------------------------------------------------------------------

def _store_player(backend, tmp_path, **kwargs):
    """A player wired to a real store so we can read back the logged turn."""
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "fr.db")
    m_id = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("fr", "gen3randombattle", m_id, m_id)
    player = _make_player(backend, store=store, battle_id=bid, player_role="p1", **kwargs)
    player.choose_random_move.return_value = MagicMock()
    return player, store, bid


_UNSET = object()


async def _reason_after_choose(player, store, bid, battle, *, parse=_UNSET) -> str | None:
    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}):
        if parse is _UNSET:
            await player.choose_move(battle)
        else:
            with patch("nidozo.battle.llm_player.parse_action", return_value=parse):
                await player.choose_move(battle)
    row = store.get_turns_with_state(bid)[0]
    assert row["parse_success"] == 0
    return row["fallback_reason"]


@pytest.mark.asyncio
async def test_choose_move_timeout_falls_back_with_reason(mock_battle, tmp_path) -> None:
    """A backend that exceeds the per-turn timeout falls back as 'backend_timeout'."""
    backend = AsyncMock()

    async def _slow(_messages):
        await asyncio.sleep(5)
        return "{}"

    backend.complete = _slow
    player, store, bid = _store_player(backend, tmp_path, turn_timeout=0.01)
    reason = await _reason_after_choose(player, store, bid, mock_battle)
    assert reason == "backend_timeout"
    player.choose_random_move.assert_called_once_with(mock_battle)
    store.close()


@pytest.mark.asyncio
async def test_choose_move_backend_error_reason(mock_battle, tmp_path) -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(side_effect=RuntimeError("down"))
    player, store, bid = _store_player(backend, tmp_path)
    assert await _reason_after_choose(player, store, bid, mock_battle) == "backend_error"
    store.close()


@pytest.mark.asyncio
async def test_choose_move_empty_response_reason(mock_battle, tmp_path) -> None:
    backend = AsyncMock()
    backend.complete = AsyncMock(return_value="")
    player, store, bid = _store_player(backend, tmp_path)
    assert await _reason_after_choose(player, store, bid, mock_battle) == "empty_response"
    store.close()


@pytest.mark.asyncio
async def test_choose_move_parse_failure_reason(mock_backend, mock_battle, tmp_path) -> None:
    player, store, bid = _store_player(mock_backend, tmp_path)
    reason = await _reason_after_choose(player, store, bid, mock_battle, parse=None)
    assert reason == "parse_failure"
    store.close()


@pytest.mark.asyncio
async def test_turn_timeout_can_be_disabled(mock_battle, fake_order) -> None:
    """turn_timeout=0 disables the deadline — complete is awaited without wait_for."""
    backend = AsyncMock()
    backend.complete = AsyncMock(
        return_value='{"action_type":"move","identifier":"surf","reasoning":"ok"}'
    )
    player = _make_player(backend, turn_timeout=0)
    assert player._turn_timeout is None
    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle)
    assert result is fake_order


# ---------------------------------------------------------------------------
# choose_move — on_thinking callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_choose_move_thinking_callback_fired(mock_backend, mock_battle, fake_order) -> None:
    """on_thinking callback is awaited with the correct event dict each turn."""
    thinking_cb = AsyncMock()

    player = _make_player(mock_backend, on_thinking=thinking_cb, player_role="p2")

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        await player.choose_move(mock_battle)

    thinking_cb.assert_called_once()
    event = thinking_cb.call_args[0][0]
    assert event["type"] == "thinking"
    assert event["player_role"] == "p2"
    assert event["turn"] == mock_battle.turn


@pytest.mark.asyncio
async def test_choose_move_thinking_callback_exception_is_swallowed(mock_backend, mock_battle, fake_order) -> None:
    """A crashing on_thinking callback does not abort the turn."""
    bad_cb = AsyncMock(side_effect=RuntimeError("boom"))

    player = _make_player(mock_backend, on_thinking=bad_cb)

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle)

    # Despite the callback raising, we still got a valid order
    assert result is fake_order


# ---------------------------------------------------------------------------
# _log_turn — no-op without store
# ---------------------------------------------------------------------------

def test_log_turn_no_op_without_store(mock_backend) -> None:
    """_log_turn is silent when no store is configured."""
    player = _make_player(mock_backend)
    # Should not raise even without a store
    player._log_turn(1, "/choose move thunderbolt", True, "response", "{}")


# ---------------------------------------------------------------------------
# StreamingLLMPlayer — publishes state_update then turn event to the EventBus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_llm_player_publishes_state_update_then_turn(mock_battle, fake_order) -> None:
    """StreamingLLMPlayer emits state_update immediately, then turn after action is decided."""
    from nidozo.api.events import EventBus
    from nidozo.battle.streaming_player import StreamingLLMPlayer

    bus = EventBus()
    queue = bus.subscribe()
    backend = AsyncMock()
    backend.complete = AsyncMock(
        return_value='{"action_type":"move","identifier":"thunderbolt","reasoning":"ok"}'
    )

    with patch("poke_env.player.Player.__init__", return_value=None):
        player = StreamingLLMPlayer(event_bus=bus, player_role="p1", backend=backend)

    player.choose_random_move = MagicMock()
    player.create_order = MagicMock()
    player._prompt_builder.build_messages = MagicMock(
        return_value=[{"role": "user", "content": "Choose."}]
    )

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.serializer.serialize_battle", return_value={}), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        result = await player.choose_move(mock_battle)

    assert result is fake_order
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    types = [e["type"] for e in events]
    # state_update must appear before turn
    assert "state_update" in types
    assert "turn" in types
    assert types.index("state_update") < types.index("turn")

    # state_update: no action field, has state
    su = next(e for e in events if e["type"] == "state_update")
    assert su["player_role"] == "p1"
    assert su["turn"] == mock_battle.turn
    assert "action" not in su
    assert "state" in su

    # turn: has action and state
    turn = next(e for e in events if e["type"] == "turn")
    assert turn["player_role"] == "p1"
    assert turn["turn"] == mock_battle.turn
    assert turn["action"] == fake_order.message
    assert "state" in turn


@pytest.mark.asyncio
async def test_streaming_llm_player_event_order(mock_battle, fake_order) -> None:
    """Full event order: state_update → thinking → turn (not interleaved)."""
    from nidozo.api.events import EventBus
    from nidozo.battle.streaming_player import StreamingLLMPlayer

    bus = EventBus()
    queue = bus.subscribe()
    backend = AsyncMock()
    backend.complete = AsyncMock(
        return_value='{"action_type":"move","identifier":"thunderbolt","reasoning":"ok"}'
    )

    with patch("poke_env.player.Player.__init__", return_value=None):
        player = StreamingLLMPlayer(event_bus=bus, player_role="p2", backend=backend)

    player.choose_random_move = MagicMock()
    player.create_order = MagicMock()
    player._prompt_builder.build_messages = MagicMock(
        return_value=[{"role": "user", "content": "Choose."}]
    )

    with patch("nidozo.battle.llm_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={}), \
         patch("nidozo.battle.llm_player.parse_action", return_value=fake_order):
        await player.choose_move(mock_battle)

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    types = [e["type"] for e in events]
    # state_update fires before thinking fires before turn
    assert "state_update" in types
    assert "thinking" in types
    assert "turn" in types
    assert types.index("state_update") < types.index("thinking")
    assert types.index("thinking") < types.index("turn")


# ---------------------------------------------------------------------------
# StreamingRandomBot — publishes state_update + turn from a random bot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_random_bot_publishes_state_update_and_turn(mock_battle) -> None:
    """StreamingRandomBot.choose_move emits state_update then turn."""
    from nidozo.api.events import EventBus
    from nidozo.battle.streaming_player import StreamingRandomBot

    bus = EventBus()
    queue = bus.subscribe()

    with patch("poke_env.player.Player.__init__", return_value=None):
        bot = StreamingRandomBot(event_bus=bus, player_role="p2")

    random_order = MagicMock()
    random_order.message = "/choose move surf"
    bot.choose_random_move = MagicMock(return_value=random_order)

    with patch("nidozo.battle.streaming_player.serialize_battle", return_value={}):
        result = await bot.choose_move(mock_battle)

    assert result is random_order
    bot.choose_random_move.assert_called_once_with(mock_battle)

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    types = [e["type"] for e in events]
    assert "state_update" in types
    assert "turn" in types
    assert types.index("state_update") < types.index("turn")

    su = next(e for e in events if e["type"] == "state_update")
    assert su["player_role"] == "p2"
    assert "action" not in su

    turn = next(e for e in events if e["type"] == "turn")
    assert turn["player_role"] == "p2"
    assert turn["action"] == random_order.message


# ---------------------------------------------------------------------------
# OP-01: zero-lag turn hook — _frame_changes_state (pure function)
# ---------------------------------------------------------------------------

def test_frame_changes_state_detects_turn() -> None:
    from nidozo.battle.streaming_player import _frame_changes_state

    frame = [[">battle-x"], ["", "turn", "6"]]
    assert _frame_changes_state(frame) is True


def test_frame_changes_state_detects_damage_and_faint() -> None:
    from nidozo.battle.streaming_player import _frame_changes_state

    assert _frame_changes_state([[">battle-x"], ["", "-damage", "p2a: Y", "50/100"]]) is True
    assert _frame_changes_state([[">battle-x"], ["", "faint", "p1a: X"]]) is True
    assert _frame_changes_state([[">battle-x"], ["", "switch", "p2a: Z", "Zapdos", "100/100"]]) is True


def test_frame_changes_state_ignores_cosmetic_only() -> None:
    from nidozo.battle.streaming_player import _frame_changes_state

    # chat, upkeep, inactivity timer, blank lines — nothing the UI renders
    frame = [
        [">battle-x"],
        ["", "upkeep"],
        ["", "c", "user", "gg"],
        ["", "inactive", "30 sec left"],
        [""],
    ]
    assert _frame_changes_state(frame) is False


def test_frame_changes_state_empty() -> None:
    from nidozo.battle.streaming_player import _frame_changes_state

    assert _frame_changes_state([[">battle-x"]]) is False


# ---------------------------------------------------------------------------
# OP-01: zero-lag turn hook — _handle_battle_message emit behaviour
# ---------------------------------------------------------------------------

def _drain(queue) -> list:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def _make_streaming_player(bus, *, role="p1"):
    """Build a StreamingLLMPlayer without a live server and stub a battle.

    Pre-announces the battle room so state_update tests are not cluttered with
    showdown_room events — that event is covered by test_streaming_player.py.
    """
    from nidozo.battle.streaming_player import StreamingLLMPlayer

    with patch("poke_env.player.Player.__init__", return_value=None):
        player = StreamingLLMPlayer(event_bus=bus, player_role=role, backend=AsyncMock())

    battle = MagicMock()
    battle.battle_tag = "gen3randombattle-1"
    battle.turn = 6
    battle.finished = False
    player._battles = {"battle-gen3randombattle-1": battle}
    player._announced_rooms.add("battle-gen3randombattle-1")
    return player, battle


@pytest.mark.asyncio
async def test_hook_emits_state_update_on_turn_frame_without_request() -> None:
    """A resolution frame (turn, no request) emits a single render-only state_update."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    queue = bus.subscribe()
    player, _battle = _make_streaming_player(bus)

    frame = [
        [">battle-gen3randombattle-1"],
        ["", "move", "p1a: X", "Tackle", "p2a: Y"],
        ["", "-damage", "p2a: Y", "50/100"],
        ["", "turn", "6"],
    ]

    # super() parses the frame but does NOT call choose_move (no request).
    async def fake_super(_split):
        return None

    with patch("poke_env.player.Player._handle_battle_message", side_effect=fake_super), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={"turn": 6}):
        await player._handle_battle_message(frame)

    events = _drain(queue)
    assert [e["type"] for e in events] == ["state_update"]
    assert events[0]["player_role"] == "p1"
    assert events[0]["turn"] == 6
    assert "action" not in events[0]


@pytest.mark.asyncio
async def test_hook_skips_when_choose_move_ran() -> None:
    """If choose_move ran during the frame (request present), the hook adds nothing."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    queue = bus.subscribe()
    player, _battle = _make_streaming_player(bus)

    frame = [[">battle-gen3randombattle-1"], ["", "request", "{}"]]

    # Simulate poke-env invoking choose_move (which sets the guard flag).
    async def fake_super(_split):
        player._chose_during_frame = True

    with patch("poke_env.player.Player._handle_battle_message", side_effect=fake_super), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={}):
        await player._handle_battle_message(frame)

    # Hook emitted nothing extra (choose_move's own emits are mocked away).
    assert _drain(queue) == []


@pytest.mark.asyncio
async def test_hook_skips_cosmetic_only_frame() -> None:
    """A frame with no render-affecting messages produces no state_update."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    queue = bus.subscribe()
    player, _battle = _make_streaming_player(bus)

    frame = [[">battle-gen3randombattle-1"], ["", "upkeep"], ["", "c", "u", "gg"]]

    async def fake_super(_split):
        return None

    with patch("poke_env.player.Player._handle_battle_message", side_effect=fake_super), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={}):
        await player._handle_battle_message(frame)

    assert _drain(queue) == []


@pytest.mark.asyncio
async def test_hook_skips_when_battle_finished() -> None:
    """No state_update after the battle is over (e.g. a faint+win frame)."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    queue = bus.subscribe()
    player, battle = _make_streaming_player(bus)
    battle.finished = True

    frame = [
        [">battle-gen3randombattle-1"],
        ["", "faint", "p2a: Y"],
        ["", "win", "p1"],
    ]

    async def fake_super(_split):
        return None

    with patch("poke_env.player.Player._handle_battle_message", side_effect=fake_super), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={}):
        await player._handle_battle_message(frame)

    assert _drain(queue) == []


@pytest.mark.asyncio
async def test_hook_uses_light_serialization() -> None:
    """The post-parse emit calls serialize_battle with light=True (render-only)."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    bus.subscribe()
    player, _battle = _make_streaming_player(bus)

    frame = [[">battle-gen3randombattle-1"], ["", "turn", "6"]]

    async def fake_super(_split):
        return None

    with patch("poke_env.player.Player._handle_battle_message", side_effect=fake_super), \
         patch("nidozo.battle.streaming_player.serialize_battle", return_value={}) as mock_ser:
        await player._handle_battle_message(frame)

    mock_ser.assert_called_once()
    assert mock_ser.call_args.kwargs.get("light") is True


# ---------------------------------------------------------------------------
# RandomBot — structural sanity check (no network needed)
# ---------------------------------------------------------------------------

def test_random_bot_is_subclass_of_random_player() -> None:
    """RandomBot is a RandomPlayer subclass — no logic to test, just the inheritance."""
    from poke_env.player import RandomPlayer

    from nidozo.battle.bots import RandomBot

    assert issubclass(RandomBot, RandomPlayer)


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

def test_log_turn_swallows_store_exception(mock_backend) -> None:
    """_log_turn silently swallows exceptions from store.log_turn()."""
    mock_store = MagicMock()
    mock_store.log_turn.side_effect = RuntimeError("DB locked")

    player = _make_player(mock_backend)
    player._store = mock_store
    player._battle_id = 1

    # Should not raise
    player._log_turn(5, "/choose move thunderbolt", True, "response", "{}")


# ---------------------------------------------------------------------------
# draft._parse_pick_response — pure function tests
# ---------------------------------------------------------------------------

def test_parse_pick_response_valid_json() -> None:
    """Happy path: valid JSON with correct pick and reasoning."""
    from nidozo.battle.draft import _parse_pick_response

    response = '{"pick": "Pikachu", "reasoning": "fast and electric"}'
    result = _parse_pick_response(response, {"Pikachu", "Charmander"})
    assert result == ("Pikachu", "fast and electric")


def test_parse_pick_response_normalized_match() -> None:
    """Pick normalizes to match a species despite casing/punctuation differences."""
    from nidozo.battle.draft import _parse_pick_response

    # 'mr. mime' normalized to 'mrmime' matches 'Mr. Mime' normalized to 'mrmime'
    response = '{"pick": "mr. mime", "reasoning": "psychic wall"}'
    result = _parse_pick_response(response, {"Mr. Mime", "Alakazam"})
    assert result is not None
    assert result[0] == "Mr. Mime"


def test_parse_pick_response_markdown_fences_stripped() -> None:
    """JSON wrapped in markdown code fences is parsed correctly."""
    from nidozo.battle.draft import _parse_pick_response

    response = "```json\n{\"pick\": \"Gengar\", \"reasoning\": \"ghost\"}\n```"
    result = _parse_pick_response(response, {"Gengar", "Haunter"})
    assert result == ("Gengar", "ghost")


def test_parse_pick_response_json_extracted_from_prose() -> None:
    """JSON embedded in prose is extracted via regex fallback."""
    from nidozo.battle.draft import _parse_pick_response

    response = 'Sure! Here is my pick: {"pick": "Snorlax", "reasoning": "big and bulky"} Thanks!'
    result = _parse_pick_response(response, {"Snorlax", "Blissey"})
    assert result == ("Snorlax", "big and bulky")


def test_parse_pick_response_invalid_json_no_json_in_text() -> None:
    """Non-JSON with no embedded object → returns None."""
    from nidozo.battle.draft import _parse_pick_response

    result = _parse_pick_response("I choose Pikachu!", {"Pikachu", "Charmander"})
    assert result is None


def test_parse_pick_response_pick_not_in_pool() -> None:
    """Pick name is valid JSON but species is not in available set → None."""
    from nidozo.battle.draft import _parse_pick_response

    response = '{"pick": "Mewtwo", "reasoning": "legendary"}'
    result = _parse_pick_response(response, {"Pikachu", "Charmander"})
    assert result is None


def test_parse_pick_response_empty_pick_field() -> None:
    """Empty pick field → None."""
    from nidozo.battle.draft import _parse_pick_response

    response = '{"pick": "", "reasoning": "no idea"}'
    result = _parse_pick_response(response, {"Pikachu"})
    assert result is None


def test_parse_pick_response_nested_json_both_fail() -> None:
    """Malformed inner JSON inside prose → returns None."""
    from nidozo.battle.draft import _parse_pick_response

    # Has braces but not valid JSON
    response = 'Here: {not valid json}'
    result = _parse_pick_response(response, {"Pikachu"})
    assert result is None


# ---------------------------------------------------------------------------
# draft.run_draft — async integration tests with full mocking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_draft_happy_path(tmp_path) -> None:
    """run_draft completes successfully: 6 picks, saves team + draft session."""
    from nidozo.battle.draft import run_draft
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "draft.db")
    model_id = store.get_or_create_model("test-model", "anthropic", "v1")

    # Build a pool of 10 Pokémon so there's always something left to pick
    pool_info = [
        {"species_id": f"pokemon{i}", "species": f"Pokemon{i}", "types": ["normal"]}
        for i in range(10)
    ]

    # Backend always returns a valid pick in order
    pick_counter = {"n": 0}

    async def _fake_complete(messages):
        idx = pick_counter["n"]
        pick_counter["n"] += 1
        return f'{{"pick": "Pokemon{idx}", "reasoning": "reason{idx}"}}'

    backend = AsyncMock()
    backend.complete.side_effect = _fake_complete

    with patch("nidozo.battle.draft.load_movesets", return_value={f"pokemon{i}": {} for i in range(10)}), \
         patch("nidozo.battle.draft.get_pool", return_value=[f"pokemon{i}" for i in range(10)]), \
         patch("nidozo.battle.draft.get_pool_info", return_value=pool_info), \
         patch("nidozo.battle.draft.build_team_string", return_value="Pikachu\n"), \
         patch("nidozo.battle.draft._build_draft_messages", return_value=[]), \
         patch("pathlib.Path.read_text", return_value="system prompt"):
        result = await run_draft(
            backend=backend,
            model_id=model_id,
            tier="ou",
            store=store,
            bus=None,
            player_role="p1",
        )

    assert len(result.picked) == 6
    assert result.tier == "ou"
    assert result.model_id == model_id


@pytest.mark.asyncio
async def test_run_draft_fallback_when_all_retries_fail(tmp_path) -> None:
    """run_draft falls back to first pool entry when all retry attempts fail."""
    from nidozo.battle.draft import run_draft
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "draft_fallback.db")
    model_id = store.get_or_create_model("test-model", "anthropic", "v1")

    pool_info = [
        {"species_id": f"pokemon{i}", "species": f"Pokemon{i}", "types": ["normal"]}
        for i in range(10)
    ]

    # Backend always returns gibberish so parse fails → fallback
    backend = AsyncMock()
    backend.complete.return_value = "not valid json at all"

    with patch("nidozo.battle.draft.load_movesets", return_value={f"pokemon{i}": {} for i in range(10)}), \
         patch("nidozo.battle.draft.get_pool", return_value=[f"pokemon{i}" for i in range(10)]), \
         patch("nidozo.battle.draft.get_pool_info", return_value=pool_info), \
         patch("nidozo.battle.draft.build_team_string", return_value="Pikachu\n"), \
         patch("nidozo.battle.draft._build_draft_messages", return_value=[]), \
         patch("pathlib.Path.read_text", return_value="system prompt"):
        result = await run_draft(
            backend=backend,
            model_id=model_id,
            tier="ou",
            store=store,
        )

    # All 6 picks should be the fallback (first remaining species each time)
    assert len(result.picked) == 6


@pytest.mark.asyncio
async def test_run_draft_backend_exception_triggers_fallback(tmp_path) -> None:
    """run_draft handles backend exceptions and falls back to first pool entry."""
    from nidozo.battle.draft import run_draft
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "draft_exc.db")
    model_id = store.get_or_create_model("test-model", "anthropic", "v1")

    pool_info = [
        {"species_id": f"pokemon{i}", "species": f"Pokemon{i}", "types": ["normal"]}
        for i in range(10)
    ]

    backend = AsyncMock()
    backend.complete.side_effect = RuntimeError("network error")

    with patch("nidozo.battle.draft.load_movesets", return_value={f"pokemon{i}": {} for i in range(10)}), \
         patch("nidozo.battle.draft.get_pool", return_value=[f"pokemon{i}" for i in range(10)]), \
         patch("nidozo.battle.draft.get_pool_info", return_value=pool_info), \
         patch("nidozo.battle.draft.build_team_string", return_value="Pikachu\n"), \
         patch("nidozo.battle.draft._build_draft_messages", return_value=[]), \
         patch("pathlib.Path.read_text", return_value="system prompt"):
        result = await run_draft(
            backend=backend,
            model_id=model_id,
            tier="ou",
            store=store,
        )

    assert len(result.picked) == 6


@pytest.mark.asyncio
async def test_run_draft_emits_bus_events(tmp_path) -> None:
    """run_draft publishes draft_pick and draft_complete events to bus."""
    from nidozo.battle.draft import run_draft
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "draft_bus.db")
    model_id = store.get_or_create_model("test-model", "anthropic", "v1")

    pool_info = [
        {"species_id": f"pokemon{i}", "species": f"Pokemon{i}", "types": ["normal"]}
        for i in range(10)
    ]

    pick_counter = {"n": 0}

    async def _fake_complete(messages):
        idx = pick_counter["n"]
        pick_counter["n"] += 1
        return f'{{"pick": "Pokemon{idx}", "reasoning": "reason{idx}"}}'

    backend = AsyncMock()
    backend.complete.side_effect = _fake_complete

    bus = AsyncMock()
    bus.publish = AsyncMock()

    with patch("nidozo.battle.draft.load_movesets", return_value={f"pokemon{i}": {} for i in range(10)}), \
         patch("nidozo.battle.draft.get_pool", return_value=[f"pokemon{i}" for i in range(10)]), \
         patch("nidozo.battle.draft.get_pool_info", return_value=pool_info), \
         patch("nidozo.battle.draft.build_team_string", return_value="Pikachu\n"), \
         patch("nidozo.battle.draft._build_draft_messages", return_value=[]), \
         patch("pathlib.Path.read_text", return_value="system prompt"):
        await run_draft(
            backend=backend,
            model_id=model_id,
            tier="ou",
            store=store,
            bus=bus,
            player_role="p2",
        )

    # 6 draft_pick events + 1 draft_complete event = 7 total
    assert bus.publish.call_count == 7
    calls = [c.args[0] for c in bus.publish.call_args_list]
    pick_events = [c for c in calls if c["type"] == "draft_pick"]
    complete_events = [c for c in calls if c["type"] == "draft_complete"]
    assert len(pick_events) == 6
    assert len(complete_events) == 1
