"""StreamingPlayer — wraps any Player subclass and pushes battle events to the EventBus.

Zero-lag state updates (OP-01)
------------------------------
Pokémon Showdown delivers each ``>battle-…`` update as one WebSocket frame, which
poke-env turns into a single ``_handle_battle_message`` call. Crucially, the
turn-resolution frame (``|move|``, ``|-damage|``, …, ``|turn|N|``) and the next
``|request|`` arrive as *separate* frames. poke-env only invokes ``choose_move``
when the request frame is parsed — so emitting battle state purely from
``choose_move`` leaves the UI stale for the gap between the two frames, and never
reflects intermediate changes (faints, forced switches) until the next decision.

We close this by hooking ``_handle_battle_message``: we let poke-env parse the
frame as usual (``super()`` — so all the fragile protocol handling stays in the
library), then, if that frame changed visible state but did **not** result in a
``choose_move`` call, we publish a render-only ``state_update`` immediately. The
battlefield therefore refreshes the instant Showdown resolves a turn, regardless
of whose turn comes next.

The emit is render-only (``serialize_battle(light=True)``): it carries HP, status,
active/bench Pokémon, weather and fields, but not the heuristic advisory or legal
actions — those are both expensive and stale until poke-env parses the request.
The frontend preserves the last full turn's advisory when it merges these in.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING, Any

from poke_env.battle import AbstractBattle
from poke_env.player.battle_order import BattleOrder
from poke_env.ps_client import PSClient

from nidozo.battle.bots import RandomBot
from nidozo.battle.llm_player import LLMPlayer
from nidozo.battle.serializer import serialize_battle

if TYPE_CHECKING:
    from nidozo.api.events import EventBus

# Seconds to wait for a challenge to be accepted before assuming team rejection.
# Normally this takes under 2 seconds; 60s is generous enough to survive a slow
# server while still catching the infinite-hang from a Showdown team rejection.
_CHALLENGE_TIMEOUT_SECS: float = 60.0


# Showdown protocol message types that change what the battlefield renders.
# A frame containing any of these warrants an immediate render-only state_update.
# Cosmetic / control messages (|c| chat, |inactive|, |upkeep|, |-anim|, …) are
# intentionally excluded so we don't emit on no-op frames.
_STATE_CHANGE_MESSAGES = frozenset({
    # major actions
    "turn", "switch", "drag", "replace", "detailschange", "faint",
    # hp / status
    "-damage", "-heal", "-sethp", "-status", "-curestatus", "-cureteam",
    # stat stages
    "-boost", "-unboost", "-setboost", "-clearboost", "-clearallboost",
    "-clearnegativeboost", "-clearpositiveboost", "-copyboost", "-swapboost",
    "-invertboost",
    # field / weather / side
    "-weather", "-fieldstart", "-fieldend", "-sidestart", "-sideend",
    # items / abilities / forme
    "-item", "-enditem", "-ability", "-endability",
    "-transform", "-formechange", "-mega", "-primal", "-terastallize",
})


def _frame_changes_state(split_messages: list[list[str]]) -> bool:
    """Return True if a parsed battle frame contains a render-affecting message.

    ``split_messages`` is poke-env's representation of one WebSocket frame: a
    list of pipe-split protocol lines, e.g. ``['', 'turn', '6']``. The leading
    line (``['>battle-…']``) and blank lines are ignored.
    """
    return any(
        len(m) > 1 and m[1] in _STATE_CHANGE_MESSAGES
        for m in split_messages
    )


def _state_event(
    battle: AbstractBattle, player_role: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Lightweight render-only snapshot.

    Emitted (a) the instant Showdown resolves a turn — before the next request
    arrives — and (b) at the start of choose_move, so the frontend refreshes HP
    bars and active Pokémon without waiting for the LLM round-trip.
    """
    return {
        "type": "state_update",
        "battle_tag": battle.battle_tag,
        "turn": battle.turn,
        "player_role": player_role,
        "state": state,
    }


def _turn_event(
    battle: AbstractBattle, action: str, player_role: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Full turn record emitted at the END of choose_move, after action is decided."""
    return {
        "type": "turn",
        "battle_tag": battle.battle_tag,
        "turn": battle.turn,
        "player_role": player_role,
        "action": action,
        "state": state,
    }


class _StreamingMixin:
    """Shared zero-lag streaming behaviour for the LLM player and the random bot.

    Must precede the poke-env ``Player`` subclass in the MRO so that this
    ``_handle_battle_message`` override is found first and ``super()`` resolves
    to the library implementation.
    """

    # Set by _init_streaming; declared for type-checkers.
    _bus: EventBus
    _player_role: str
    _battles: dict[str, AbstractBattle]

    # Attributes provided by the poke-env Player subclass lower in the MRO.
    # Declared here so mypy can resolve them on the mixin without a concrete base.
    ps_client: PSClient
    logger: logging.Logger
    _format: str
    _battle_semaphore: asyncio.Semaphore
    _battle_count_queue: asyncio.Queue[Any]
    get_next_team: Callable[[], str | None]

    def _init_streaming(self, event_bus: EventBus, player_role: str) -> None:
        self._bus = event_bus
        self._player_role = player_role
        # Tracks whether choose_move ran during the current frame, so the
        # post-parse hook doesn't double-emit a state_update.
        self._chose_during_frame = False
        # Battle tags for which we've already emitted showdown_room (OP-02).
        self._announced_rooms: set[str] = set()

    async def terminate(self) -> None:
        """Close this player's Showdown websocket so its listen loop ends.

        poke-env drives each battle on the player's own ``ps_client.listen()``
        task, which is independent of the orchestration coroutine that awaits
        ``battle_against``. Cancelling the runner therefore leaves that listen
        loop alive: the player keeps answering ``|request|`` messages and the
        Showdown sim plays on, advancing turns and emitting events long after a
        cancel (issue #155).

        ``stop_listening`` bridges to poke-env's own loop and closes the socket,
        ending that task — so the sim halts (the next decision never happens).
        It also reclaims the connection after a battle finishes normally (no
        leak across sequential battles). Safe to call when already closed.

        Closing mid-battle can race a ``|request|`` poke-env's listen loop is
        already handling, so it may log a benign ``ConnectionClosed`` for the
        unsent move; the battle is stopped regardless. (Forfeiting first was
        tried — it doesn't preempt that already-queued request, so it removes
        no noise while widening the window for one extra turn.)
        """
        await self.ps_client.stop_listening()  # type: ignore[no-untyped-call]

    async def _emit_state_update(self, battle: AbstractBattle) -> None:
        """Publish a render-only snapshot of the current battle state."""
        await self._bus.publish(
            _state_event(battle, self._player_role, serialize_battle(battle, light=True))
        )

    async def _send_challenges(
        self,
        opponent: str,
        n_challenges: int,
        to_wait: asyncio.Event | None = None,
    ) -> None:
        """Override _send_challenges to add a per-challenge timeout.

        poke-env's default implementation blocks on ``_battle_semaphore.acquire()``
        forever when Showdown rejects the team (popup message) because no battle
        ever starts to release the semaphore. Adding a 60-second timeout detects
        this condition and raises so the battle is marked failed instead of hanging.

        This runs on POKE_LOOP (via ``handle_threaded_coroutines``), so asyncio
        primitives and ``self._bus.publish`` (which uses ``put_nowait``) are safe.
        """
        await self.ps_client.logged_in.wait()
        self.logger.info("Event logged in received in send challenge")

        if to_wait is not None:
            await to_wait.wait()

        start_time = perf_counter()

        for _ in range(n_challenges):
            await self.ps_client.challenge(opponent, self._format, self.get_next_team())
            try:
                await asyncio.wait_for(
                    self._battle_semaphore.acquire(),
                    timeout=_CHALLENGE_TIMEOUT_SECS,
                )
            except TimeoutError as exc:
                battle_id: int | None = getattr(self, "_battle_id", None)
                self.logger.error(
                    "Challenge timed out after %.0fs (battle_id=%s) — "
                    "Showdown likely rejected the team. Check server logs for a "
                    "'|popup|Your team was rejected' message.",
                    _CHALLENGE_TIMEOUT_SECS,
                    battle_id,
                )
                await self._bus.publish({
                    "type": "error",
                    "battle_id": battle_id,
                    "message": (
                        f"Battle challenge timed out after {_CHALLENGE_TIMEOUT_SECS:.0f}s — "
                        "Showdown rejected the team. See server logs for details."
                    ),
                })
                raise RuntimeError(
                    f"Battle challenge timed out after {_CHALLENGE_TIMEOUT_SECS:.0f}s — "
                    "Showdown likely rejected the submitted team. "
                    "Check the server logs for a '|popup|Your team was rejected' message."
                ) from exc

        await self._battle_count_queue.join()
        self.logger.info(
            "Challenges (%d battles) finished in %fs",
            n_challenges,
            perf_counter() - start_time,
        )

    async def _handle_battle_message(self, split_messages: list[list[str]]) -> None:
        # Emit showdown_room once per battle on the first frame so the browser
        # can open the spectator-proxy socket as soon as the room is known (OP-02).
        try:
            raw_tag = split_messages[0][0]
            if raw_tag.startswith(">"):
                battle_tag = raw_tag[1:]
                if battle_tag not in self._announced_rooms:
                    self._announced_rooms.add(battle_tag)
                    await self._bus.publish({
                        "type": "showdown_room",
                        "battle_id": getattr(self, "_battle_id", None),
                        "room": battle_tag,
                    })
        except (IndexError, AttributeError):
            pass

        # Let poke-env parse the frame (mutating the battle, possibly invoking
        # choose_move on a request). Delegating keeps us robust to library
        # internals changing between versions.
        self._chose_during_frame = False
        await super()._handle_battle_message(split_messages)  # type: ignore[misc]

        # If choose_move already ran for this frame, it has emitted a fresh
        # (full) state_update — nothing to add.
        if self._chose_during_frame:
            return
        # Only emit when the frame actually changed something visible.
        if not _frame_changes_state(split_messages):
            return
        try:
            battle_tag = split_messages[0][0][1:]  # strip leading '>'
            battle = self._battles.get(battle_tag)
        except (IndexError, AttributeError):
            return
        if battle is None or battle.finished:
            return
        await self._emit_state_update(battle)


class StreamingLLMPlayer(_StreamingMixin, LLMPlayer):
    """LLMPlayer that pushes state_update / thinking / turn events to the EventBus."""

    def __init__(
        self,
        event_bus: EventBus,
        player_role: str = "p1",
        lessons: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        # coach is forwarded to LLMPlayer via **kwargs
        super().__init__(
            player_role=player_role,
            on_thinking=self._emit_thinking,
            lessons=lessons,
            **kwargs,
        )
        self._init_streaming(event_bus, player_role)

    async def _emit_thinking(self, event: dict[str, Any]) -> None:
        await self._bus.publish(event)

    async def choose_move(
        self, battle: AbstractBattle, *, state: dict[str, Any] | None = None
    ) -> BattleOrder:
        # The request has just been parsed, so available_moves / heuristics are
        # fresh here — emit a FULL snapshot the UI can use for the advisory. This
        # one serialization is shared with the base player below.
        if state is None:
            state = serialize_battle(battle)

        # Emit immediately so the UI refreshes before LLM think-time, and flag
        # that this frame produced a choose_move so the post-parse hook skips.
        await self._bus.publish(_state_event(battle, self._player_role, state))
        self._chose_during_frame = True

        # Hand the already-computed snapshot to the base player so it doesn't
        # re-run the full serialization + heuristics for the prompt/DB state.
        order = await super().choose_move(battle, state=state)
        action_label = getattr(order, "message", str(order))
        await self._bus.publish(_turn_event(battle, action_label, self._player_role, state))
        return order


class StreamingRandomBot(_StreamingMixin, RandomBot):
    """RandomBot that pushes state_update / turn events to the EventBus."""

    def __init__(self, event_bus: EventBus, player_role: str = "p1", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._init_streaming(event_bus, player_role)

    async def choose_move(self, battle: AbstractBattle) -> BattleOrder:  # type: ignore[override]
        state = serialize_battle(battle)
        await self._bus.publish(_state_event(battle, self._player_role, state))
        self._chose_during_frame = True
        order = self.choose_random_move(battle)
        action_label = getattr(order, "message", str(order))
        await self._bus.publish(_turn_event(battle, action_label, self._player_role, state))
        return order
