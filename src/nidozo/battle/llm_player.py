"""LLMPlayer — a poke-env Player driven by a ModelBackend each turn."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from poke_env.battle import AbstractBattle
from poke_env.player import Player
from poke_env.player.battle_order import BattleOrder

from nidozo.battle.action_parser import parse_action
from nidozo.battle.serializer import serialize_battle
from nidozo.llm.backend import ModelBackend
from nidozo.llm.prompt_builder import PromptBuilder

if TYPE_CHECKING:
    from nidozo.db.store import BattleStore
    from nidozo.llm.coach import CoachAgent

logger = logging.getLogger(__name__)

# Callback type: async fn(event_dict) — injected by StreamingLLMPlayer
ThinkingCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None

# Block appended to the player's user turn message when coach advice is present.
_COACH_BLOCK = "\n\n--- COACH ANALYSIS ---\n{advice}\n---\n\nWith this analysis in mind, what is your chosen action?"


_MAX_RECENT_EVENTS = 3  # turns of history to surface in the prompt

# Per-turn deadline for the LLM decision call. Without it, a hung or very slow
# backend (a stuck local model, a network stall) blocks the turn for up to the
# SDK default (~10 min). Override with NIDOZO_TURN_TIMEOUT (seconds); set to 0
# to disable. Kept generous so slow-but-working local models aren't cut off.
_DEFAULT_TURN_TIMEOUT: float = float(os.environ.get("NIDOZO_TURN_TIMEOUT", "90"))


class LLMPlayer(Player):
    """A Pokémon Showdown player whose moves are chosen by an LLM.

    Args:
        backend: Any object satisfying the ModelBackend protocol.
        prompt_version: Prompt template version to use. All production callers
            pass "v5" (the current default); "v1" is the fallback only.
        store: Optional BattleStore for turn logging.
        battle_id: DB battle id — required when store is provided.
        player_role: "p1" or "p2" — required when store is provided.
        coach: Optional CoachAgent that provides pre-move strategic advice.
        **kwargs: Passed through to poke-env's Player base class.
    """

    def __init__(
        self,
        backend: ModelBackend,
        prompt_version: str = "v1",
        store: BattleStore | None = None,
        battle_id: int | None = None,
        player_role: str = "p1",
        on_thinking: ThinkingCallback = None,
        lessons: list[str] | None = None,
        coach: CoachAgent | None = None,
        turn_timeout: float | None = _DEFAULT_TURN_TIMEOUT,
        personality: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._backend = backend
        # Per-turn LLM deadline (seconds); None or <=0 disables it.
        self._turn_timeout = turn_timeout if turn_timeout and turn_timeout > 0 else None
        self._prompt_builder = PromptBuilder(version=prompt_version)
        self._store = store
        self._battle_id = battle_id
        self._player_role = player_role
        self._on_thinking = on_thinking
        self._lessons = lessons or []
        self._coach = coach
        self._personality = personality
        # Battle-history tracking (for prompt v4 recent_events)
        self._prev_hp: dict[str, float] = {}       # species_key → hp_fraction
        self._recent_events: list[dict[str, Any]] = []  # rolling event log
        self._last_action_display: str | None = None    # human-readable last action

    async def choose_move(
        self, battle: AbstractBattle, *, state: dict[str, Any] | None = None
    ) -> BattleOrder:
        # Recharge turn (e.g. after Hyper Beam): only one forced pseudo-move, skip LLM
        moves = battle.available_moves
        if len(moves) == 1 and moves[0].id == "recharge":
            return self.create_order(moves[0])

        # serialize_battle runs the full heuristic engine (damage formulas, type
        # chart, switch scoring). The streaming subclass already computes one
        # snapshot for its WS events and passes it in, so we reuse it rather than
        # paying that cost twice per turn. Copy before mutating so the caller's
        # snapshot (used for the render-only state events) stays untouched.
        if state is None:
            state = serialize_battle(battle)
        else:
            state = dict(state)

        # Inject battle history (HP deltas since last turn) for prompt v4.
        # This is stateful and can't be derived from a single snapshot, so we
        # build it here and overwrite the empty list set by serialize_battle.
        state["recent_events"] = self._build_recent_events(battle, state)
        # Snapshot current HP for next turn's delta computation.
        self._update_hp_snapshot(battle)

        state_json = json.dumps(state)
        coach_advice: str | None = None

        # --- Coach phase (optional) ---
        if self._coach is not None:
            await self._notify_thinking(is_coach=True, turn=battle.turn)
            coach_advice = await self._coach.analyze(state)
            if coach_advice:
                logger.debug(
                    "Coach advice for %s turn %d (%d chars)",
                    self._player_role, battle.turn, len(coach_advice),
                )

        # --- Player phase ---
        await self._notify_thinking(is_coach=False, turn=battle.turn)

        messages = self._prompt_builder.build_messages(
            state,
            lessons=self._lessons or None,
            coach_advice=coach_advice,
            personality=self._personality,
        )
        response: str | None = None

        # Call the LLM with one retry. Each attempt is bounded by a per-turn
        # timeout so a hung backend can't stall the battle. The fallback reason
        # records *why* we fell back (backend_timeout / backend_error /
        # empty_response / parse_failure) so analysis can tell them apart.
        for attempt in range(2):
            try:
                if self._turn_timeout is not None:
                    response = await asyncio.wait_for(
                        self._backend.complete(messages), timeout=self._turn_timeout
                    )
                else:
                    response = await self._backend.complete(messages)
            except TimeoutError:
                logger.error(
                    "LLM backend timed out on turn %d (attempt %d, %.0fs)",
                    battle.turn, attempt + 1, self._turn_timeout,
                )
                if attempt == 1:
                    self._log_turn(battle.turn, None, False, None, state_json, coach_advice,
                                   fallback_reason="backend_timeout")
                    return self.choose_random_move(battle)
                continue
            except Exception as exc:
                logger.error("LLM backend error on turn %d (attempt %d): %s", battle.turn, attempt + 1, exc)
                if attempt == 1:
                    self._log_turn(battle.turn, None, False, None, state_json, coach_advice,
                                   fallback_reason="backend_error")
                    return self.choose_random_move(battle)
                continue

            if response:
                break

            if attempt == 0:
                logger.warning(
                    "Empty response from LLM on turn %d — retrying once.", battle.turn
                )

        if not response:
            logger.warning(
                "LLM returned empty response on turn %d after retry — falling back to random.",
                battle.turn,
            )
            self._log_turn(battle.turn, None, False, "", state_json, coach_advice,
                           fallback_reason="empty_response")
            return self.choose_random_move(battle)

        order = parse_action(response, battle, self)
        if order is None:
            logger.warning(
                "Action parse failed on turn %d — falling back to random move.\n"
                "Response was:\n%s",
                battle.turn,
                response,
            )
            self._log_turn(battle.turn, None, False, response, state_json, coach_advice,
                           fallback_reason="parse_failure")
            return self.choose_random_move(battle)

        action_label = getattr(order, "message", str(order))
        self._log_turn(battle.turn, action_label, True, response, state_json, coach_advice)
        # Store for next turn's battle history summary
        self._last_action_display = self._action_display(response)
        return order

    # ------------------------------------------------------------------
    # Battle history helpers (prompt v4)
    # ------------------------------------------------------------------

    def _action_display(self, response: str | None) -> str | None:
        """Extract a human-readable action string from the LLM response."""
        if not response:
            return None
        try:
            data = json.loads(response.strip())
            if isinstance(data, dict):
                atype = data.get("action_type", "")
                ident = data.get("identifier", "")
                if atype and ident:
                    return f"{atype} {ident}"
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return None

    def _build_recent_events(
        self,
        battle: AbstractBattle,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build a rolling list of the last N turns' HP events for the prompt."""
        prev_hp: dict[str, float] = getattr(self, "_prev_hp", {})
        recent_events: list[dict[str, Any]] = getattr(self, "_recent_events", [])
        if not hasattr(self, "_recent_events"):
            self._recent_events = recent_events
        if not prev_hp or battle.turn <= 1:
            return list(recent_events)

        lines: list[str] = []

        # What did I play last turn?
        last_action = getattr(self, "_last_action_display", None)
        if last_action:
            lines.append(f"Your action: {last_action}")

        # HP changes for my active Pokémon
        my_active = state.get("my_active")
        if my_active:
            key = my_active["species"]
            prev = prev_hp.get(key)
            curr = my_active["hp_fraction"]
            if prev is not None and abs(curr - prev) > 0.01:
                delta_pct = (curr - prev) * 100
                direction = f"took ~{abs(delta_pct):.0f}% damage" if delta_pct < 0 else f"recovered ~{delta_pct:.0f}% HP"
                lines.append(
                    f"Your {key.title()} {direction} (now {curr * 100:.0f}%)"
                )

        # What move did the opponent use last turn?
        opp_pokemon = battle.opponent_active_pokemon
        if opp_pokemon is not None:
            opp_last = opp_pokemon.last_move
            if opp_last is not None:
                move_name = opp_last.id.replace("_", " ").title()
                lines.append(f"Opponent used: {move_name}")

        # HP changes for opponent's active Pokémon
        opp_active = state.get("opponent_active")
        if opp_active:
            key = f"opp_{opp_active['species']}"
            prev = prev_hp.get(key)
            curr = opp_active["hp_fraction"]
            if prev is not None and abs(curr - prev) > 0.01:
                delta_pct = (curr - prev) * 100
                direction = f"took ~{abs(delta_pct):.0f}% damage" if delta_pct < 0 else f"recovered ~{delta_pct:.0f}% HP"
                lines.append(
                    f"Opponent's {opp_active['species'].title()} {direction} (now {curr * 100:.0f}%)"
                )

        if lines:
            recent_events.append({"turn": battle.turn - 1, "lines": lines})
            trimmed = recent_events[-_MAX_RECENT_EVENTS:]
            self._recent_events = trimmed
            return list(trimmed)

        return list(recent_events)

    def _update_hp_snapshot(self, battle: AbstractBattle) -> None:
        """Snapshot current HP fractions for all visible mons."""
        snap: dict[str, float] = {}
        try:
            if battle.active_pokemon:
                snap[battle.active_pokemon.species] = (
                    battle.active_pokemon.current_hp_fraction
                )
            for mon in battle.team.values():
                if not mon.active:
                    snap[mon.species] = mon.current_hp_fraction
            if battle.opponent_active_pokemon:
                snap[f"opp_{battle.opponent_active_pokemon.species}"] = (
                    battle.opponent_active_pokemon.current_hp_fraction
                )
            for mon in battle.opponent_team.values():
                if not mon.active:
                    snap[f"opp_{mon.species}"] = mon.current_hp_fraction
        except Exception:  # noqa: BLE001
            pass
        self._prev_hp = snap

    async def _notify_thinking(self, is_coach: bool, turn: int) -> None:
        """Fire a thinking event to any registered callback."""
        if self._on_thinking is None:
            return
        try:
            await self._on_thinking({
                "type": "thinking",
                "player_role": self._player_role,
                "turn": turn,
                "agent": "coach" if is_coach else "player",
            })
        except Exception:
            pass

    def _log_turn(
        self,
        turn_number: int,
        action_chosen: str | None,
        parse_success: bool,
        response: str | None,
        state_json: str | None = None,
        coach_advice: str | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        if self._store is None or self._battle_id is None:
            return
        try:
            self._store.log_turn(
                battle_id=self._battle_id,
                turn_number=turn_number,
                player_role=self._player_role,
                prompt_version=self._prompt_builder.version,
                action_chosen=action_chosen,
                parse_success=parse_success,
                llm_response=response,
                state_json=state_json,
                coach_advice=coach_advice,
                fallback_reason=fallback_reason,
            )
        except Exception as exc:
            logger.warning("Failed to log turn: %s", exc)
