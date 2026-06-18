"""HumanPlayer — a poke-env Player whose moves come from a human via the API.

When it is the human's turn, choose_move:
  1. Publishes a ``human_action_required`` event so the browser can show the
     move picker.
  2. Awaits an asyncio.Future that the ``POST /api/battles/{id}/human-action``
     endpoint resolves once the human clicks a button.
  3. Parses the submitted action string through the same parse_action path as
     the LLM player, falling back to random on timeout or parse failure.

Since poke-env and FastAPI share the same asyncio event loop (battle_against is
awaited directly in the FastAPI background task), standard asyncio.Future works
with no cross-loop bridging required.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from poke_env.battle import AbstractBattle, DoubleBattle
from poke_env.player import Player
from poke_env.player.battle_order import BattleOrder

from nidozo.battle.action_parser import parse_action
from nidozo.battle.serializer import serialize_battle
from nidozo.battle.streaming_player import _StreamingMixin, _state_event, _turn_event

if TYPE_CHECKING:
    from nidozo.db.store import BattleStore

logger = logging.getLogger(__name__)

# Seconds to wait for the human to choose before falling back to random.
# Override with NIDOZO_HUMAN_TIMEOUT env var.
_DEFAULT_HUMAN_TIMEOUT: float = float(os.environ.get("NIDOZO_HUMAN_TIMEOUT", "300"))

# Module-level registry: (battle_id, player_role) → Future[str]
# Created in choose_move (on the shared event loop); resolved by the API endpoint.
_pending: dict[tuple[int, str], asyncio.Future[str]] = {}


def register_pending(battle_id: int, player_role: str) -> asyncio.Future[str]:
    """Create and register a Future for the next human move decision."""
    key = (battle_id, player_role)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    fut: asyncio.Future[str] = loop.create_future()
    _pending[key] = fut
    return fut


def resolve_pending(battle_id: int, player_role: str, action: str) -> bool:
    """Resolve the pending Future with the human's chosen action.

    Returns True if a pending action was found and resolved, False if no
    decision was pending (e.g. the turn already timed out).
    """
    key = (battle_id, player_role)
    fut = _pending.pop(key, None)
    if fut is None or fut.done():
        return False
    fut.set_result(action)
    return True


def cancel_pending(battle_id: int, player_role: str) -> None:
    """Cancel any pending Future for this slot (called on battle teardown)."""
    key = (battle_id, player_role)
    fut = _pending.pop(key, None)
    if fut is not None and not fut.done():
        fut.cancel()


def has_pending(battle_id: int, player_role: str) -> bool:
    """Return True if the human has an unresolved pending decision."""
    key = (battle_id, player_role)
    fut = _pending.get(key)
    return fut is not None and not fut.done()


class HumanPlayer(Player):
    """Minimal base class — real logic lives in StreamingHumanPlayer."""

    def __init__(
        self,
        store: BattleStore | None = None,
        battle_id: int | None = None,
        player_role: str = "p1",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._store = store
        self._battle_id = battle_id
        self._player_role = player_role

    async def choose_move(self, battle: AbstractBattle) -> BattleOrder:
        # Bare fallback — StreamingHumanPlayer overrides this entirely.
        return self.choose_random_move(battle)

    def _log_turn(
        self,
        turn_number: int,
        action_chosen: str | None,
        parse_success: bool,
        response: str | None,
        state_json: str | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        if self._store is None or self._battle_id is None:
            return
        try:
            self._store.log_turn(
                battle_id=self._battle_id,
                turn_number=turn_number,
                player_role=self._player_role,
                prompt_version="human",
                action_chosen=action_chosen,
                parse_success=parse_success,
                llm_response=response,
                state_json=state_json,
                fallback_reason=fallback_reason,
            )
        except Exception as exc:
            logger.warning("Failed to log human turn: %s", exc)


class StreamingHumanPlayer(_StreamingMixin, HumanPlayer):
    """HumanPlayer that pushes state events to the EventBus and awaits human input."""

    def __init__(self, event_bus: Any, player_role: str = "p1", **kwargs: Any) -> None:
        super().__init__(player_role=player_role, **kwargs)
        self._init_streaming(event_bus, player_role)
        self._human_timeout = _DEFAULT_HUMAN_TIMEOUT

    async def choose_move(self, battle: AbstractBattle) -> BattleOrder:
        # Recharge shortcut (same as LLMPlayer)
        if not isinstance(battle, DoubleBattle):
            moves = battle.available_moves
            if len(moves) == 1 and moves[0].id == "recharge":
                return self.create_order(moves[0])

        state = serialize_battle(battle)
        state_json = json.dumps(state)

        # Emit full state_update immediately so the UI is fresh before the picker appears.
        await self._bus.publish(_state_event(battle, self._player_role, state))
        self._chose_during_frame = True

        # Signal the frontend: it's your turn.
        await self._bus.publish({
            "type": "human_action_required",
            "battle_id": self._battle_id,
            "battle_tag": battle.battle_tag,
            "player_role": self._player_role,
            "turn": battle.turn,
            "state": state,
        })

        fut = register_pending(self._battle_id, self._player_role)
        try:
            action = await asyncio.wait_for(fut, timeout=self._human_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[%s] turn %d human timed out (%.0fs) — falling back to random",
                self._player_role, battle.turn, self._human_timeout,
            )
            _pending.pop((self._battle_id, self._player_role), None)
            self._log_turn(
                battle.turn, None, False, None, state_json,
                fallback_reason="human_timeout",
            )
            order = self.choose_random_move(battle)
            action_label = getattr(order, "message", str(order))
            await self._bus.publish(
                _turn_event(battle, f"{action_label} [timeout fallback]", self._player_role, state)
            )
            return order

        order = parse_action(action, battle, self)
        if order is None:
            logger.warning(
                "[%s] turn %d human action parse failed (%r) — falling back to random",
                self._player_role, battle.turn, action[:80] if action else "",
            )
            self._log_turn(
                battle.turn, None, False, action, state_json,
                fallback_reason="parse_failure",
            )
            order = self.choose_random_move(battle)
            action_label = getattr(order, "message", str(order))
            await self._bus.publish(
                _turn_event(battle, f"{action_label} [invalid input]", self._player_role, state)
            )
            return order

        action_label = getattr(order, "message", str(order))
        self._log_turn(battle.turn, action_label, True, action, state_json)
        await self._bus.publish(
            _turn_event(battle, action_label, self._player_role, state)
        )
        return order

    async def terminate(self) -> None:
        """Cancel any pending human action future before closing the connection."""
        cancel_pending(self._battle_id, self._player_role)
        await super().terminate()
