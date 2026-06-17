"""LLMPlayer — a poke-env Player driven by a ModelBackend each turn."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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

_STATUS_VERB: dict[str, str] = {
    "BRN": "burned",
    "PAR": "paralyzed",
    "SLP": "put to sleep",
    "PSN": "poisoned",
    "TOX": "badly poisoned",
    "FRZ": "frozen",
}
_STATUS_LABEL: dict[str, str] = {
    "BRN": "burn",
    "PAR": "paralysis",
    "SLP": "sleep",
    "PSN": "poison",
    "TOX": "toxic poison",
    "FRZ": "freeze",
}


def _status_verb(status: str) -> str:
    return _STATUS_VERB.get(status.upper(), f"inflicted with {status}")


def _status_label(status: str) -> str:
    return _STATUS_LABEL.get(status.upper(), status.lower())

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
            pass "v6" (the current default); "v1" is the fallback only.
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
        # Battle-history tracking (for prompt v4+ recent_events)
        self._prev_hp: dict[str, float] = {}       # species_key → hp_fraction
        # Richer snapshot for competitive event annotation (item/status/ability)
        self._prev_snapshot: dict[str, dict[str, Any]] = {}
        self._recent_events: list[dict[str, Any]] = []  # rolling event log
        self._last_action_display: str | None = None    # human-readable last action

    async def choose_move(
        self, battle: AbstractBattle, *, state: dict[str, Any] | None = None
    ) -> BattleOrder:
        # Recharge turn (e.g. after Hyper Beam): only one forced pseudo-move, skip LLM
        moves = battle.available_moves
        if len(moves) == 1 and moves[0].id == "recharge":
            return self.create_order(moves[0])

        logger.info(
            "[%s] deciding turn %d",
            self._player_role, battle.turn,
            extra={"player": self._player_role, "turn": battle.turn, "battle_id": self._battle_id},
        )

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
        _extra = {"player": self._player_role, "turn": battle.turn, "battle_id": self._battle_id}

        # Call the LLM with one retry. Each attempt is bounded by a per-turn
        # timeout so a hung backend can't stall the battle. The fallback reason
        # records *why* we fell back (backend_timeout / backend_error /
        # empty_response / parse_failure) so analysis can tell them apart.
        _t0 = time.monotonic()
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
                    "[%s] turn %d LLM timed out (attempt %d, %.0fs limit)",
                    self._player_role, battle.turn, attempt + 1, self._turn_timeout,
                    extra=_extra,
                )
                if attempt == 1:
                    self._log_turn(battle.turn, None, False, None, state_json, coach_advice,
                                   fallback_reason="backend_timeout")
                    return self.choose_random_move(battle)
                continue
            except Exception as exc:
                logger.error(
                    "[%s] turn %d LLM error (attempt %d): %s",
                    self._player_role, battle.turn, attempt + 1, exc,
                    extra=_extra,
                )
                if attempt == 1:
                    self._log_turn(battle.turn, None, False, None, state_json, coach_advice,
                                   fallback_reason="backend_error")
                    return self.choose_random_move(battle)
                continue

            if response:
                break

            if attempt == 0:
                logger.warning(
                    "[%s] turn %d empty LLM response — retrying once",
                    self._player_role, battle.turn,
                    extra=_extra,
                )

        _elapsed = time.monotonic() - _t0

        if not response:
            logger.warning(
                "[%s] turn %d empty LLM response after retry — falling back to random",
                self._player_role, battle.turn,
                extra=_extra,
            )
            self._log_turn(battle.turn, None, False, "", state_json, coach_advice,
                           fallback_reason="empty_response")
            return self.choose_random_move(battle)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[%s] turn %d raw LLM response:\n%s",
                self._player_role, battle.turn, response,
                extra=_extra,
            )

        order = parse_action(response, battle, self)
        if order is None:
            logger.warning(
                "[%s] turn %d parse failed — falling back to random (response: %r)",
                self._player_role, battle.turn, response[:200],
                extra=_extra,
            )
            self._log_turn(battle.turn, None, False, response, state_json, coach_advice,
                           fallback_reason="parse_failure")
            return self.choose_random_move(battle)

        action_label = getattr(order, "message", str(order))
        logger.info(
            "[%s] turn %d → %s (%.1fs)",
            self._player_role, battle.turn, action_label, _elapsed,
            extra={**_extra, "action": action_label, "elapsed_s": round(_elapsed, 2)},
        )
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

        prev_snap: dict[str, dict[str, Any]] = getattr(self, "_prev_snapshot", {})

        # What did I play last turn?
        last_action = getattr(self, "_last_action_display", None)
        if last_action:
            lines.append(f"Your action: {last_action}")

        # HP changes + state events for my active Pokémon
        my_active = state.get("my_active")
        if my_active:
            key = my_active["species"]
            prev_hp_val = prev_hp.get(key)
            curr = my_active["hp_fraction"]
            if prev_hp_val is not None and abs(curr - prev_hp_val) > 0.01:
                delta_pct = (curr - prev_hp_val) * 100
                direction = f"took ~{abs(delta_pct):.0f}% damage" if delta_pct < 0 else f"recovered ~{delta_pct:.0f}% HP"
                lines.append(f"Your {key.title()} {direction} (now {curr * 100:.0f}%)")
            # Status applied / cured
            if battle.active_pokemon and key in prev_snap:
                prev_s = prev_snap[key]
                mon = battle.active_pokemon
                curr_status = mon.status.name if mon.status else None
                if curr_status and not prev_s.get("status"):
                    lines.append(f"Your {key.title()} was {_status_verb(curr_status)}")
                elif not curr_status and prev_s.get("status"):
                    lines.append(f"Your {key.title()}'s {_status_label(prev_s['status'])} was cured")
                # Item consumed (had item, now None or changed)
                prev_item = prev_s.get("item")
                if prev_item and mon.item is None:
                    lines.append(
                        f"Your {key.title()}'s {prev_item.replace('_', ' ').title()} was consumed"
                    )

        # What move did the opponent use last turn? (with priority note)
        opp_pokemon = battle.opponent_active_pokemon
        if opp_pokemon is not None:
            opp_last = opp_pokemon.last_move
            if opp_last is not None:
                move_name = opp_last.id.replace("_", " ").title()
                prio = getattr(opp_last, "priority", 0) or 0
                prio_note = (
                    f" [priority {'+' if prio > 0 else ''}{prio}]" if prio != 0 else ""
                )
                lines.append(f"Opponent used: {move_name}{prio_note}")

        # HP changes + state events for opponent's active Pokémon
        opp_active = state.get("opponent_active")
        if opp_active:
            opp_species = opp_active["species"]
            key = f"opp_{opp_species}"
            prev_hp_val = prev_hp.get(key)
            curr = opp_active["hp_fraction"]
            if prev_hp_val is not None and abs(curr - prev_hp_val) > 0.01:
                delta_pct = (curr - prev_hp_val) * 100
                direction = f"took ~{abs(delta_pct):.0f}% damage" if delta_pct < 0 else f"recovered ~{delta_pct:.0f}% HP"
                lines.append(
                    f"Opponent's {opp_species.title()} {direction} (now {curr * 100:.0f}%)"
                )
            # Opponent status applied / cured; item / ability revealed
            if opp_pokemon is not None and key in prev_snap:
                prev_s = prev_snap[key]
                curr_status = opp_pokemon.status.name if opp_pokemon.status else None
                if curr_status and not prev_s.get("status"):
                    lines.append(
                        f"Opponent's {opp_species.title()} was {_status_verb(curr_status)}"
                    )
                elif not curr_status and prev_s.get("status"):
                    lines.append(
                        f"Opponent's {opp_species.title()}'s {_status_label(prev_s['status'])} was cured"
                    )
                # Item revealed (opponent couldn't see before, now can)
                if not prev_s.get("item") and opp_pokemon.item:
                    lines.append(
                        f"Opponent's item revealed: {opp_pokemon.item.replace('_', ' ').title()}"
                    )
                # Ability revealed
                if not prev_s.get("ability") and opp_pokemon.ability:
                    lines.append(
                        f"Opponent's ability revealed: {opp_pokemon.ability.replace('_', ' ').title()}"
                    )

        if lines:
            recent_events.append({"turn": battle.turn - 1, "lines": lines})
            trimmed = recent_events[-_MAX_RECENT_EVENTS:]
            self._recent_events = trimmed
            return list(trimmed)

        return list(recent_events)

    def _update_hp_snapshot(self, battle: AbstractBattle) -> None:
        """Snapshot current HP fractions and state (status/item/ability) for all visible mons."""
        hp_snap: dict[str, float] = {}
        state_snap: dict[str, dict[str, Any]] = {}
        try:
            if battle.active_pokemon:
                mon = battle.active_pokemon
                key = mon.species
                hp_snap[key] = mon.current_hp_fraction
                state_snap[key] = {
                    "status": mon.status.name if mon.status else None,
                    "item": mon.item,
                    "ability": mon.ability,
                }
            for mon in battle.team.values():
                if not mon.active:
                    key = mon.species
                    hp_snap[key] = mon.current_hp_fraction
                    state_snap[key] = {
                        "status": mon.status.name if mon.status else None,
                        "item": mon.item,
                        "ability": mon.ability,
                    }
            if battle.opponent_active_pokemon:
                mon = battle.opponent_active_pokemon
                key = f"opp_{mon.species}"
                hp_snap[key] = mon.current_hp_fraction
                state_snap[key] = {
                    "status": mon.status.name if mon.status else None,
                    "item": mon.item,
                    "ability": mon.ability,
                }
            for mon in battle.opponent_team.values():
                if not mon.active:
                    key = f"opp_{mon.species}"
                    hp_snap[key] = mon.current_hp_fraction
                    state_snap[key] = {
                        "status": mon.status.name if mon.status else None,
                        "item": mon.item,
                        "ability": mon.ability,
                    }
        except Exception:  # noqa: BLE001
            pass
        self._prev_hp = hp_snap
        self._prev_snapshot = state_snap

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
