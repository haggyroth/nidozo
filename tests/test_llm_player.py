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
from poke_env.battle import DoubleBattle

from nidozo.battle.llm_player import LLMPlayer, _status_label, _status_verb

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


# ---------------------------------------------------------------------------
# Module-level helpers: _status_verb / _status_label
# ---------------------------------------------------------------------------


def test_status_verb_known_codes() -> None:
    assert _status_verb("BRN") == "burned"
    assert _status_verb("PAR") == "paralyzed"
    assert _status_verb("SLP") == "put to sleep"
    assert _status_verb("PSN") == "poisoned"
    assert _status_verb("TOX") == "badly poisoned"
    assert _status_verb("FRZ") == "frozen"


def test_status_verb_unknown_falls_back() -> None:
    assert _status_verb("XYZ") == "inflicted with XYZ"


def test_status_verb_case_insensitive() -> None:
    assert _status_verb("brn") == "burned"


def test_status_label_known_codes() -> None:
    assert _status_label("BRN") == "burn"
    assert _status_label("PAR") == "paralysis"
    assert _status_label("SLP") == "sleep"
    assert _status_label("PSN") == "poison"
    assert _status_label("TOX") == "toxic poison"
    assert _status_label("FRZ") == "freeze"


def test_status_label_unknown_falls_back_lowercase() -> None:
    assert _status_label("CONFUSION") == "confusion"


def test_status_label_case_insensitive() -> None:
    assert _status_label("brn") == "burn"


# ---------------------------------------------------------------------------
# _action_display
# ---------------------------------------------------------------------------


def test_action_display_none_returns_none(mock_backend) -> None:
    player = _make_player(mock_backend)
    assert player._action_display(None) is None


def test_action_display_empty_string_returns_none(mock_backend) -> None:
    player = _make_player(mock_backend)
    assert player._action_display("") is None


def test_action_display_valid_json_returns_formatted(mock_backend) -> None:
    player = _make_player(mock_backend)
    result = player._action_display('{"action_type":"move","identifier":"thunderbolt"}')
    assert result == "move thunderbolt"


def test_action_display_json_missing_identifier_returns_none(mock_backend) -> None:
    player = _make_player(mock_backend)
    assert player._action_display('{"action_type":"move"}') is None


def test_action_display_json_missing_action_type_returns_none(mock_backend) -> None:
    player = _make_player(mock_backend)
    assert player._action_display('{"identifier":"thunderbolt"}') is None


def test_action_display_invalid_json_returns_none(mock_backend) -> None:
    player = _make_player(mock_backend)
    assert player._action_display("not json at all") is None


def test_action_display_json_array_not_dict_returns_none(mock_backend) -> None:
    player = _make_player(mock_backend)
    assert player._action_display('["move", "thunderbolt"]') is None


# ---------------------------------------------------------------------------
# _build_recent_events — singles path
# ---------------------------------------------------------------------------


def _make_singles_battle(turn: int = 3) -> MagicMock:
    """Minimal singles AbstractBattle mock (not a DoubleBattle)."""
    battle = MagicMock(spec=["turn", "active_pokemon", "opponent_active_pokemon",
                              "team", "opponent_team", "force_switch"])
    battle.turn = turn
    battle.active_pokemon = None
    battle.opponent_active_pokemon = None
    battle.team = {}
    battle.opponent_team = {}
    # spec doesn't include DoubleBattle attributes, so isinstance(battle, DoubleBattle) → False
    return battle


def test_build_recent_events_returns_empty_on_turn_1(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=1)
    result = player._build_recent_events(battle, {})
    assert result == []


def test_build_recent_events_returns_empty_when_no_prev_hp(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=5)
    player._prev_hp = {}  # no snapshot yet
    result = player._build_recent_events(battle, {})
    assert result == []


def test_build_recent_events_records_hp_damage(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=4)
    player._prev_hp = {"pikachu": 1.0}
    player._prev_snapshot = {}

    state = {"my_active": {"species": "pikachu", "hp_fraction": 0.5}}
    result = player._build_recent_events(battle, state)

    assert result
    assert any("50%" in line or "took" in line for entry in result for line in entry["lines"])


def test_build_recent_events_records_hp_recovery(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=4)
    player._prev_hp = {"pikachu": 0.4}
    player._prev_snapshot = {}

    state = {"my_active": {"species": "pikachu", "hp_fraction": 0.8}}
    result = player._build_recent_events(battle, state)

    assert any("recovered" in line for entry in result for line in entry["lines"])


def test_build_recent_events_ignores_tiny_hp_change(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=4)
    player._prev_hp = {"pikachu": 0.800}
    player._prev_snapshot = {}

    state = {"my_active": {"species": "pikachu", "hp_fraction": 0.802}}
    result = player._build_recent_events(battle, state)
    # Change < 1% → no HP line emitted, so recent_events stays empty.
    assert result == []


def test_build_recent_events_records_status_applied(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=4)
    player._prev_hp = {"pikachu": 0.9}
    player._prev_snapshot = {"pikachu": {"status": None, "item": None, "ability": None}}

    mon = MagicMock()
    mon.status = MagicMock()
    mon.status.name = "BRN"
    mon.item = None
    battle.active_pokemon = mon

    state = {"my_active": {"species": "pikachu", "hp_fraction": 0.9}}
    result = player._build_recent_events(battle, state)
    assert any("burned" in line for entry in result for line in entry["lines"])


def test_build_recent_events_records_opponent_move(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=4)
    player._prev_hp = {"pikachu": 1.0}
    player._prev_snapshot = {}

    opp = MagicMock()
    opp_move = MagicMock()
    opp_move.id = "fire_blast"
    opp_move.priority = 0
    opp.last_move = opp_move
    battle.opponent_active_pokemon = opp

    state = {"my_active": {"species": "pikachu", "hp_fraction": 0.5}}
    result = player._build_recent_events(battle, state)
    assert any("Fire Blast" in line for entry in result for line in entry["lines"])


def test_build_recent_events_records_opponent_item_revealed(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_singles_battle(turn=4)
    player._prev_hp = {"opp_blastoise": 0.6}
    player._prev_snapshot = {
        "opp_blastoise": {"status": None, "item": None, "ability": None}
    }

    opp = MagicMock()
    opp.status = None
    opp.item = "leftovers"
    opp.ability = None
    opp.last_move = None  # not testing move display here
    battle.opponent_active_pokemon = opp

    state = {
        "opponent_active": {"species": "blastoise", "hp_fraction": 0.55},
    }
    result = player._build_recent_events(battle, state)
    assert any("Leftovers" in line for entry in result for line in entry["lines"])


def test_build_recent_events_trims_to_max_recent(mock_backend) -> None:
    player = _make_player(mock_backend)
    # Pre-seed with 3 events (the max).
    existing = [{"turn": i, "lines": ["event"]} for i in range(3)]
    player._recent_events = list(existing)
    player._prev_hp = {"pikachu": 1.0}
    player._prev_snapshot = {}

    battle = _make_singles_battle(turn=10)
    state = {"my_active": {"species": "pikachu", "hp_fraction": 0.5}}
    result = player._build_recent_events(battle, state)
    # Must not grow beyond _MAX_RECENT_EVENTS (3).
    assert len(result) <= 3


# ---------------------------------------------------------------------------
# _build_recent_events_doubles
# ---------------------------------------------------------------------------


def _make_doubles_battle(turn: int = 4) -> MagicMock:
    """Mock that passes isinstance(..., DoubleBattle)."""
    battle = MagicMock(spec=DoubleBattle)
    battle.turn = turn
    return battle


def test_build_recent_events_routes_doubles_to_doubles_helper(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_doubles_battle()
    player._prev_hp = {"pikachu": 1.0}  # non-empty so the branch isn't short-circuited
    player._recent_events = []

    state = {
        "my_active": [{"species": "pikachu", "hp_fraction": 0.5}],
        "opponent_active": [],
    }
    result = player._build_recent_events(battle, state)
    # The doubles helper should fire and record the HP delta.
    assert any("pikachu" in line.lower() for entry in result for line in entry["lines"])


def test_build_recent_events_doubles_records_both_sides(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_doubles_battle()
    player._prev_hp = {"pikachu": 1.0, "opp_blastoise": 0.8}
    player._recent_events = []

    state = {
        "my_active": [{"species": "pikachu", "hp_fraction": 0.4}],
        "opponent_active": [{"species": "blastoise", "hp_fraction": 0.3}],
    }
    result = player._build_recent_events(battle, state)
    all_lines = [line for entry in result for line in entry["lines"]]
    assert any("Pikachu" in line for line in all_lines)
    assert any("Blastoise" in line for line in all_lines)


def test_build_recent_events_doubles_ignores_tiny_changes(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_doubles_battle()
    player._prev_hp = {"pikachu": 0.500}
    player._recent_events = []

    state = {
        "my_active": [{"species": "pikachu", "hp_fraction": 0.501}],
        "opponent_active": [],
    }
    result = player._build_recent_events(battle, state)
    assert result == []


def test_build_recent_events_doubles_includes_last_action(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_doubles_battle()
    player._prev_hp = {"pikachu": 1.0}
    player._recent_events = []
    player._last_action_display = "move thunderbolt"

    state = {
        "my_active": [{"species": "pikachu", "hp_fraction": 0.4}],
        "opponent_active": [],
    }
    result = player._build_recent_events(battle, state)
    all_lines = [line for entry in result for line in entry["lines"]]
    assert any("thunderbolt" in line for line in all_lines)


def test_build_recent_events_doubles_skips_none_slots(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = _make_doubles_battle()
    player._prev_hp = {}
    player._recent_events = []

    state = {
        "my_active": [None, {"species": "charizard", "hp_fraction": 0.5}],
        "opponent_active": [None],
    }
    # Should not raise on None slots.
    result = player._build_recent_events(battle, state)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _update_hp_snapshot
# ---------------------------------------------------------------------------


def test_update_hp_snapshot_captures_team_and_opponent(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = MagicMock()

    own = MagicMock()
    own.species = "pikachu"
    own.current_hp_fraction = 0.75
    own.status = None
    own.item = "lightball"
    own.ability = "staticability"

    opp = MagicMock()
    opp.species = "blastoise"
    opp.current_hp_fraction = 0.5
    opp.status = None
    opp.item = None
    opp.ability = None

    battle.team = {"pikachu": own}
    battle.opponent_team = {"blastoise": opp}

    player._update_hp_snapshot(battle)

    assert player._prev_hp["pikachu"] == pytest.approx(0.75)
    assert player._prev_hp["opp_blastoise"] == pytest.approx(0.5)
    assert player._prev_snapshot["pikachu"]["item"] == "lightball"


def test_update_hp_snapshot_swallows_exception(mock_backend) -> None:
    player = _make_player(mock_backend)
    battle = MagicMock()
    battle.team = MagicMock(side_effect=AttributeError("no team"))

    # Should not raise.
    player._update_hp_snapshot(battle)
