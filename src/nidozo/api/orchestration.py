"""Background battle and tournament runners."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

from nidozo.api.helpers import _build_backend, _build_streaming_player, _model_name
from nidozo.api.models import StartBattleRequest, StartSeasonRequest, StartTournamentRequest
from nidozo.battle.presets import PRESET_FORMAT, build_preset_team_string

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget post-battle tasks (lesson/narrative
# generation). The event loop keeps only a weak reference to a task, so without
# this a task can be garbage-collected before it finishes — silently dropping a
# multi-second LLM write. See the asyncio.create_task docs.
_background_tasks: set[asyncio.Task[None]] = set()


def _spawn_background(coro: Any) -> asyncio.Task[None]:
    """Schedule a fire-and-forget coroutine, holding a strong reference until it
    completes so it can't be GC'd mid-flight."""
    task: asyncio.Task[None] = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _showdown_cfg() -> Any:
    """Build a poke-env ServerConfiguration from environment variables.

    NIDOZO_SHOWDOWN_HOST defaults to 'localhost' (bare-metal dev).
    Set it to 'showdown' inside Docker Compose so containers talk by service name.
    """
    from poke_env.ps_client.server_configuration import ServerConfiguration

    host = os.environ.get("NIDOZO_SHOWDOWN_HOST", "localhost")
    port = os.environ.get("NIDOZO_SHOWDOWN_PORT", "8000")
    return ServerConfiguration(
        websocket_url=f"ws://{host}:{port}/showdown/websocket",
        authentication_url="https://play.pokemonshowdown.com/action.php?",
    )


async def _terminate_players(*players: Any) -> None:
    """Tear down each player's PS websocket, swallowing individual failures so
    one player's teardown error can't block the other's."""
    for player in players:
        if player is None:
            continue
        try:
            await player.terminate()
        except Exception:
            logger.debug("Error tearing down player %r", player, exc_info=True)


async def _battle_and_teardown(p1: Any, p2: Any) -> None:
    """Run one battle, then tear both players down — on success, error, OR
    cancellation.

    poke-env keeps each player's ``ps_client.listen()`` task running after
    ``battle_against`` is awaited; cancelling the runner raises here but leaves
    those loops alive, so the Showdown sim plays on (issue #155). Tearing the
    players down in a ``finally`` guarantees the sim stops when a battle is
    cancelled, and reclaims connections after a normal finish.
    """
    try:
        await p1.battle_against(p2, n_battles=1)
    finally:
        await _terminate_players(p1, p2)


@dataclass
class _BattleOutcome:
    """Result of one played battle, shared by every runner's bookkeeping."""

    winner: int | None       # 1=p1, 2=p2, None=tie
    total_turns: int
    real_tag: str            # the Showdown battle tag (or a synthetic fallback)
    new_badges: list[dict[str, Any]]


async def _play_battle(
    *,
    battle_id: int,
    p1: Any,
    p2: Any,
    store: Any,
    bus: Any,
    p1_label: str,
    p2_label: str,
    start_extra: dict[str, Any] | None = None,
    end_extra: dict[str, Any] | None = None,
) -> _BattleOutcome:
    """Run one fully-built battle and persist its result.

    Owns the lifecycle every runner shares: mark running → publish
    ``battle_start`` → play + teardown → derive winner/tag/turns → update the
    battle tag → ``finish_battle`` → publish ``battle_end`` → fan out
    ``badge_earned`` events. Returns the outcome so the caller can advance a
    bracket, publish standings, and spawn post-battle work.

    ``start_extra`` / ``end_extra`` carry the runner-specific event fields
    (tournament_id, season_id, match_id, personality, preset, …) merged into the
    ``battle_start`` / ``battle_end`` payloads. The winner is honest — ``None``
    is a genuine tie (Explosion, Destiny Bond, Struggle, …).
    """
    store.set_battle_status(battle_id, "running")
    await bus.publish({
        "type": "battle_start",
        "battle_id": battle_id,
        "p1": p1_label,
        "p2": p2_label,
        **(start_extra or {}),
    })

    await _battle_and_teardown(p1, p2)

    winner = 1 if p1.n_won_battles > 0 else (2 if p2.n_won_battles > 0 else None)
    real_tag = next(iter(p1.battles), f"battle-{battle_id}")
    battle_obj = p1.battles.get(real_tag)
    total_turns = battle_obj.turn if battle_obj else 0

    store.update_battle_tag(battle_id, real_tag)
    new_badges = store.finish_battle(battle_id, winner, total_turns)

    await bus.publish({
        "type": "battle_end",
        "battle_id": battle_id,
        "battle_tag": real_tag,
        "winner": winner,
        "total_turns": total_turns,
        **(end_extra or {}),
    })
    for badge in new_badges:
        await bus.publish({"type": "badge_earned", "battle_id": battle_id, **badge})

    return _BattleOutcome(winner, total_turns, real_tag, new_badges)


def _spawn_post_battle(
    *,
    store: Any,
    battle_id: int,
    outcome: _BattleOutcome,
    lessons_kwargs: dict[str, Any],
    narrative_kwargs: dict[str, Any],
) -> None:
    """Spawn the fire-and-forget post-battle lesson + narrative tasks.

    Kept separate from :func:`_play_battle` so each runner can slot its own
    standings / bracket-advance step in between (the order callers rely on).
    """
    turns = store.get_turns_basic(battle_id)
    _spawn_background(generate_and_store_lessons(
        store, battle_id, outcome.winner, outcome.total_turns, turns, **lessons_kwargs
    ))
    _spawn_background(generate_and_store_narrative(
        store, battle_id, outcome.winner, outcome.total_turns, **narrative_kwargs
    ))


def _bracket_advance_slot(
    winner: int | None, p1_seed: int, p2_seed: int
) -> int:
    """Choose which slot advances in a bracket match.

    A bracket must always advance one side, but a battle can legitimately tie
    (Explosion, Destiny Bond, Struggle, etc.).  When there is a real winner we
    use it; on a tie we apply a deterministic tiebreak — the better seed (the
    lower seed number) advances.  The recorded battle result stays honest
    (``winner=None`` for a tie); this only governs bracket routing.
    """
    if winner is not None:
        return winner
    return 1 if p1_seed < p2_seed else 2


def _random_preset_team(tier: str, team_size: int = 6) -> str:
    """Build a random team string of *team_size* Pokémon from the tier's pool.

    Used when a non-random tier battle is started with *draft=False* — the
    format requires a real team, but no draft was run.  Each player gets a
    fresh random selection so they aren't mirror teams.
    """
    from nidozo.battle.team_builder import all_species, build_team_string, load_movesets
    from nidozo.battle.tiers import get_pool

    movesets = load_movesets()
    pool = get_pool(tier, all_species(movesets))
    picks = random.sample(pool, min(team_size, len(pool)))
    return build_team_string(picks, movesets)


async def run_battles(
    req: StartBattleRequest,
    battle_ids: list[int],
    store: Any,
    bus: Any,
    active_tasks: dict[int, asyncio.Task[None]],
) -> None:
    """Run one or more battles sequentially as a background task."""
    from nidozo.battle.tiers import resolve_format

    use_preset = bool(req.p1_preset or req.p2_preset)
    doubles = bool(getattr(req, "doubles", False))
    team_size: int = getattr(req, "team_size", 6)
    has_human = req.p1_provider == "human" or req.p2_provider == "human"
    do_draft = (
        not has_human
        and req.tier != "random"
        and req.draft
        and not use_preset
    )
    showdown_format = (
        PRESET_FORMAT if use_preset
        else resolve_format(req.tier, doubles=doubles, team_size=team_size)
    )
    # Doubles requires the v7 prompt (it carries the 2v2 turn template); draft
    # uses v3; otherwise honour the request.
    effective_prompt = "v7" if doubles else ("v3" if do_draft else req.prompt_version)
    cfg = _showdown_cfg()

    for battle_id in battle_ids:
        task = asyncio.current_task()
        if task:
            active_tasks[battle_id] = task

        try:
            p1_model = req.p1_model or req.model
            p2_model = req.p2_model or req.model

            model_ids = store.get_player_model_ids(battle_id)
            p1_id, p2_id = model_ids if model_ids else (None, None)
            p1_lessons = (
                [r["content"] for r in store.get_lessons(p1_id)]
                if p1_id and req.p1_provider not in ("random", "human") else None
            )
            p2_lessons = (
                [r["content"] for r in store.get_lessons(p2_id)]
                if p2_id and req.p2_provider not in ("random", "human") else None
            )

            p1_team: str | None = None
            p2_team: str | None = None
            p1_team_id: int | None = None
            p2_team_id: int | None = None

            # Preset teams take priority over draft and random fallback.
            if req.p1_preset:
                p1_team = build_preset_team_string(req.p1_preset)
            if req.p2_preset:
                p2_team = build_preset_team_string(req.p2_preset)

            if do_draft and p1_id is not None and req.p1_provider != "random":
                await bus.publish({
                    "type": "draft_start",
                    "player_role": "p1",
                    "tier": req.tier,
                    "battle_id": battle_id,
                })
                p1_backend = _build_backend(req.p1_provider, p1_model)
                p1_draft = await run_draft_phase(p1_backend, p1_id, req.tier, store, bus, "p1", team_size=team_size, doubles=doubles)
                p1_team = p1_draft["team_string"]
                p1_team_id = p1_draft["team_id"]

            if do_draft and p2_id is not None and req.p2_provider != "random":
                await bus.publish({
                    "type": "draft_start",
                    "player_role": "p2",
                    "tier": req.tier,
                    "battle_id": battle_id,
                })
                p2_backend = _build_backend(req.p2_provider, p2_model)
                p2_draft = await run_draft_phase(p2_backend, p2_id, req.tier, store, bus, "p2", team_size=team_size, doubles=doubles)
                p2_team = p2_draft["team_string"]
                p2_team_id = p2_draft["team_id"]

            if do_draft:
                store.set_battle_teams(battle_id, p1_team_id, p2_team_id, req.tier)
            elif req.tier != "random" and not use_preset:
                # Non-random format requires a real team even when draft was
                # skipped and no preset was given.
                p1_team = p1_team or _random_preset_team(req.tier, team_size)
                p2_team = p2_team or _random_preset_team(req.tier, team_size)

            p1 = _build_streaming_player(
                req.p1_provider, p1_model, "p1",
                effective_prompt, store, battle_id, bus, cfg, showdown_format,
                lessons=p1_lessons,
                team=p1_team,
                coach_provider=req.p1_coach_provider,
                coach_model=req.p1_coach_model,
                personality=req.p1_personality,
            )
            p2 = _build_streaming_player(
                req.p2_provider, p2_model, "p2",
                effective_prompt, store, battle_id, bus, cfg, showdown_format,
                lessons=p2_lessons,
                team=p2_team,
                coach_provider=req.p2_coach_provider,
                coach_model=req.p2_coach_model,
                personality=req.p2_personality,
            )

            p1_label = f"{req.p1_provider}/{_model_name(req.p1_provider, p1_model)}"
            p2_label = f"{req.p2_provider}/{_model_name(req.p2_provider, p2_model)}"
            outcome = await _play_battle(
                battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
                p1_label=p1_label, p2_label=p2_label,
                start_extra={
                    "format": showdown_format,
                    "tier": req.tier,
                    "drafted": do_draft,
                    "p1_personality": req.p1_personality,
                    "p2_personality": req.p2_personality,
                    "p1_preset": req.p1_preset,
                    "p2_preset": req.p2_preset,
                },
            )

            _spawn_post_battle(
                store=store, battle_id=battle_id, outcome=outcome,
                lessons_kwargs={
                    "p1_provider": req.p1_provider, "p1_model": p1_model,
                    "p1_id": p1_id, "p1_opponent": p2_label,
                    "p2_provider": req.p2_provider, "p2_model": p2_model,
                    "p2_id": p2_id, "p2_opponent": p1_label,
                },
                narrative_kwargs={
                    "p1_label": p1_label, "p2_label": p2_label,
                    "p1_provider": req.p1_provider, "p1_model": p1_model,
                    "p2_provider": req.p2_provider, "p2_model": p2_model,
                },
            )

        except asyncio.CancelledError:
            logger.info("Battle %d cancelled", battle_id)
            store.cancel_battle(battle_id)
            # Every battle in this request shares one task, so cancelling the
            # current one tears down the whole run. Mark each still-queued battle
            # cancelled too — sync DB writes first so they land even if the
            # notifications below are interrupted — instead of stranding them as
            # 'pending' until the next restart. (The current battle's
            # battle_cancelled event is published by the cancel endpoint.)
            idx = battle_ids.index(battle_id)
            stranded = [
                bid for bid in battle_ids[idx + 1:] if store.cancel_battle(bid)
            ]
            for bid in stranded:
                await bus.publish({"type": "battle_cancelled", "battle_id": bid})
            raise
        except Exception as exc:
            logger.error("Battle %d failed: %s", battle_id, exc)
            store.set_battle_status(battle_id, "failed")
            await bus.publish({"type": "error", "battle_id": battle_id, "message": str(exc)})
        finally:
            active_tasks.pop(battle_id, None)


async def run_tournament(
    req: StartTournamentRequest,
    tournament_id: int,
    battle_ids: list[int],
    player_specs: list[dict[str, Any]],
    store: Any,
    bus: Any,
    active_tasks: dict[int, asyncio.Task[None]],
) -> None:
    """Run all battles in a tournament sequentially as a background task."""
    from nidozo.battle.tiers import resolve_format

    any_preset = any(ps.get("preset") for ps in player_specs)
    team_size: int = getattr(req, "team_size", 6)
    doubles: bool = getattr(req, "doubles", False)
    do_draft = req.tier != "random" and req.draft and not any_preset
    showdown_format = (
        PRESET_FORMAT if any_preset
        else resolve_format(req.tier, doubles=doubles, team_size=team_size)
    )
    effective_prompt = "v7" if doubles else ("v3" if do_draft else req.prompt_version)
    cfg = _showdown_cfg()

    total = len(battle_ids)
    await bus.publish({
        "type": "tournament_start",
        "tournament_id": tournament_id,
        "players": player_specs,
        "total_battles": total,
        "rounds": req.rounds,
        "tier": req.tier,
    })

    # Build lookups so each battle's p1/p2 can find their coach, personality, and preset.
    coach_lookup: dict[tuple[str, str], tuple[str | None, str | None]] = {
        (ps["provider"], ps["model_name"]): (
            ps.get("coach_provider"), ps.get("coach_model")
        )
        for ps in player_specs
    }
    personality_lookup: dict[tuple[str, str], str | None] = {
        (ps["provider"], ps["model_name"]): ps.get("personality")
        for ps in player_specs
    }
    preset_lookup: dict[tuple[str, str], str | None] = {
        (ps["provider"], ps["model_name"]): ps.get("preset")
        for ps in player_specs
    }

    for battle_num, battle_id in enumerate(battle_ids, start=1):
        # A cancel via the API only flips the DB status row; the runner must
        # notice and stop here, between battles, rather than playing on.
        t_row = store.get_tournament(tournament_id)
        if t_row and t_row["status"] != "running":
            logger.info(
                "Tournament %d no longer running (status=%s) — stopping runner "
                "before battle %d",
                tournament_id, t_row["status"], battle_num,
            )
            await bus.publish({
                "type": "tournament_cancelled",
                "tournament_id": tournament_id,
                "battles_completed": battle_num - 1,
            })
            return

        battle_row = store.get_battle(battle_id)
        if not battle_row or battle_row["status"] == "cancelled":
            continue

        task = asyncio.current_task()
        if task:
            active_tasks[battle_id] = task

        battle_info = store.get_battle_players(battle_id)
        if battle_info is None:
            logger.error(
                "Tournament %d: no player info for battle %d — skipping",
                tournament_id, battle_id,
            )
            continue

        p1_label = f"{battle_info['p1_provider']}/{battle_info['p1_model']}"
        p2_label = f"{battle_info['p2_provider']}/{battle_info['p2_model']}"

        await bus.publish({
            "type": "tournament_progress",
            "tournament_id": tournament_id,
            "battle_num": battle_num,
            "total_battles": total,
            "battle_id": battle_id,
            "p1": p1_label,
            "p2": p2_label,
        })

        try:
            model_ids = store.get_player_model_ids(battle_id)
            t_p1_id, t_p2_id = model_ids if model_ids else (None, None)
            t_p1_prov, t_p2_prov = battle_info["p1_provider"], battle_info["p2_provider"]
            t_p1_lessons = (
                [r["content"] for r in store.get_lessons(t_p1_id)]
                if t_p1_id and t_p1_prov != "random" else None
            )
            t_p2_lessons = (
                [r["content"] for r in store.get_lessons(t_p2_id)]
                if t_p2_id and t_p2_prov != "random" else None
            )

            t_p1_team: str | None = None
            t_p2_team: str | None = None
            t_p1_team_id: int | None = None
            t_p2_team_id: int | None = None

            t_p1_preset = preset_lookup.get((t_p1_prov, battle_info["p1_model"]))
            t_p2_preset = preset_lookup.get((t_p2_prov, battle_info["p2_model"]))
            if t_p1_preset:
                t_p1_team = build_preset_team_string(t_p1_preset)
            if t_p2_preset:
                t_p2_team = build_preset_team_string(t_p2_preset)

            if do_draft and t_p1_id is not None and t_p1_prov != "random":
                await bus.publish({
                    "type": "draft_start",
                    "player_role": "p1",
                    "tier": req.tier,
                    "battle_id": battle_id,
                    "tournament_id": tournament_id,
                })
                p1_backend = _build_backend(t_p1_prov, battle_info["p1_model"])
                p1_draft_r = await run_draft_phase(
                    p1_backend, t_p1_id, req.tier, store, bus, "p1", team_size=team_size, doubles=doubles
                )
                t_p1_team = p1_draft_r["team_string"]
                t_p1_team_id = p1_draft_r["team_id"]

            if do_draft and t_p2_id is not None and t_p2_prov != "random":
                await bus.publish({
                    "type": "draft_start",
                    "player_role": "p2",
                    "tier": req.tier,
                    "battle_id": battle_id,
                    "tournament_id": tournament_id,
                })
                p2_backend = _build_backend(t_p2_prov, battle_info["p2_model"])
                p2_draft_r = await run_draft_phase(
                    p2_backend, t_p2_id, req.tier, store, bus, "p2", team_size=team_size, doubles=doubles
                )
                t_p2_team = p2_draft_r["team_string"]
                t_p2_team_id = p2_draft_r["team_id"]

            if do_draft:
                store.set_battle_teams(battle_id, t_p1_team_id, t_p2_team_id, req.tier)
            elif req.tier != "random" and not any_preset:
                t_p1_team = t_p1_team or _random_preset_team(req.tier, team_size)
                t_p2_team = t_p2_team or _random_preset_team(req.tier, team_size)

            t_p1_coach_prov, t_p1_coach_model = coach_lookup.get(
                (t_p1_prov, battle_info["p1_model"]), (None, None)
            )
            t_p2_coach_prov, t_p2_coach_model = coach_lookup.get(
                (t_p2_prov, battle_info["p2_model"]), (None, None)
            )
            t_p1_personality = personality_lookup.get((t_p1_prov, battle_info["p1_model"]))
            t_p2_personality = personality_lookup.get((t_p2_prov, battle_info["p2_model"]))

            p1 = _build_streaming_player(
                t_p1_prov, battle_info["p1_model"], "p1",
                effective_prompt, store, battle_id, bus, cfg, showdown_format,
                lessons=t_p1_lessons,
                team=t_p1_team,
                coach_provider=t_p1_coach_prov,
                coach_model=t_p1_coach_model,
                personality=t_p1_personality,
            )
            p2 = _build_streaming_player(
                t_p2_prov, battle_info["p2_model"], "p2",
                effective_prompt, store, battle_id, bus, cfg, showdown_format,
                lessons=t_p2_lessons,
                team=t_p2_team,
                coach_provider=t_p2_coach_prov,
                coach_model=t_p2_coach_model,
                personality=t_p2_personality,
            )

            outcome = await _play_battle(
                battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
                p1_label=p1_label, p2_label=p2_label,
                start_extra={
                    "tournament_id": tournament_id,
                    "format": showdown_format,
                    "tier": req.tier,
                    "drafted": do_draft,
                    "p1_personality": t_p1_personality,
                    "p2_personality": t_p2_personality,
                    "p1_preset": t_p1_preset,
                    "p2_preset": t_p2_preset,
                },
                end_extra={"tournament_id": tournament_id},
            )

            standings = store.get_tournament_standings(tournament_id)
            await bus.publish({
                "type": "tournament_standings",
                "tournament_id": tournament_id,
                "standings": standings,
                "battle_num": battle_num,
                "total_battles": total,
            })

            _spawn_post_battle(
                store=store, battle_id=battle_id, outcome=outcome,
                lessons_kwargs={
                    "p1_provider": t_p1_prov, "p1_model": battle_info["p1_model"],
                    "p1_id": t_p1_id, "p1_opponent": p2_label,
                    "p2_provider": t_p2_prov, "p2_model": battle_info["p2_model"],
                    "p2_id": t_p2_id, "p2_opponent": p1_label,
                },
                narrative_kwargs={
                    "p1_label": p1_label, "p2_label": p2_label,
                    "p1_provider": t_p1_prov, "p1_model": battle_info["p1_model"],
                    "p2_provider": t_p2_prov, "p2_model": battle_info["p2_model"],
                },
            )

        except asyncio.CancelledError:
            logger.info("Tournament %d cancelled at battle %d", tournament_id, battle_id)
            store.cancel_battle(battle_id)
            store.finish_tournament(tournament_id, status="cancelled")
            await bus.publish({
                "type": "tournament_cancelled",
                "tournament_id": tournament_id,
                "battles_completed": battle_num - 1,
            })
            raise
        except Exception as exc:
            logger.error("Tournament %d battle %d failed: %s", tournament_id, battle_id, exc)
            store.set_battle_status(battle_id, "failed")
            await bus.publish({"type": "error", "battle_id": battle_id, "message": str(exc)})
        finally:
            active_tasks.pop(battle_id, None)

    # Only finalize as completed if the tournament wasn't cancelled (or otherwise
    # moved to a terminal status) while the loop was running — otherwise we'd
    # silently overwrite the user's cancel with 'completed'.
    final = store.get_tournament(tournament_id)
    if final and final["status"] != "running":
        return
    store.finish_tournament(tournament_id, status="completed")
    leaderboard = store.leaderboard()
    await bus.publish({
        "type": "tournament_end",
        "tournament_id": tournament_id,
        "leaderboard": leaderboard,
    })


async def run_draft_phase(
    backend: Any,
    model_id: int,
    tier: str,
    store: Any,
    bus: Any,
    player_role: str,
    team_size: int = 6,
    doubles: bool = False,
) -> dict[str, Any]:
    """Run the draft for one player and return {team_string, team_id}."""
    from nidozo.battle.draft import run_draft

    result = await run_draft(
        backend=backend,
        model_id=model_id,
        tier=tier,
        store=store,
        bus=bus,
        player_role=player_role,
        prompt_version="v3",
        team_size=team_size,
        doubles=doubles,
    )
    return {"team_string": result.team_string, "team_id": result.team_id}


async def generate_and_store_lessons(
    store: Any,
    battle_id: int,
    winner: int | None,
    total_turns: int,
    turns: list[dict[str, Any]],
    *,
    p1_provider: str,
    p1_model: str | None,
    p1_id: int | None,
    p1_opponent: str,
    p2_provider: str,
    p2_model: str | None,
    p2_id: int | None,
    p2_opponent: str,
) -> None:
    """Generate and persist post-battle lessons for both LLM players.

    Runs as a fire-and-forget asyncio task.  Errors are logged but never
    propagate — the battle pipeline must not be affected.
    """
    from nidozo.analysis import analyze_battle
    from nidozo.analysis.analyzer import _load_species_data
    from nidozo.llm.lesson_generator import generate_lesson

    turns_with_state = store.get_turns_with_state(battle_id)
    p1_team, p2_team = store.get_battle_teams(battle_id)
    p1_ids: list[str] | None = p1_team.get("pokemon") if p1_team else None
    p2_ids: list[str] | None = p2_team.get("pokemon") if p2_team else None
    sd = _load_species_data() if (p1_ids or p2_ids) else None
    try:
        # analyze_battle is pure CPU work over every turn (merge, type chart,
        # win-prob timeline, RNG inference). Run it in a worker thread so it
        # can't stall the event loop — which also drives the live WS stream and
        # the next battle in a tournament/season. (#207)
        analysis: dict[str, Any] | None = (
            await asyncio.to_thread(
                analyze_battle, turns_with_state,
                p1_team_ids=p1_ids, p2_team_ids=p2_ids, species_data=sd,
            )
            if turns_with_state else None
        )
    except Exception as exc:
        logger.warning(
            "Analysis failed for battle %d (lessons will proceed without it): %s",
            battle_id, exc,
        )
        analysis = None

    for provider, model, model_id, role, opponent in (
        (p1_provider, p1_model, p1_id, "p1", p1_opponent),
        (p2_provider, p2_model, p2_id, "p2", p2_opponent),
    ):
        if provider in ("random", "human") or model_id is None:
            continue
        try:
            lesson_backend = _build_backend(provider, model, json_mode=False)
            lesson = await generate_lesson(
                backend=lesson_backend,
                player_role=role,
                winner=winner,
                total_turns=total_turns,
                opponent_label=opponent,
                turns=turns,
                analysis=analysis,
            )
            if lesson:
                store.create_lesson(model_id, battle_id, lesson)
                logger.info(
                    "Lesson stored for model %d (battle %d, role=%s): %s…",
                    model_id, battle_id, role, lesson[:80],
                )
        except Exception as exc:
            logger.error(
                "Failed to generate/store lesson for model %d battle %d: %s",
                model_id, battle_id, exc,
            )


async def generate_and_store_narrative(
    store: Any,
    battle_id: int,
    winner: int | None,
    total_turns: int,
    *,
    p1_label: str,
    p2_label: str,
    p1_provider: str,
    p1_model: str | None,
    p2_provider: str,
    p2_model: str | None,
) -> None:
    """Generate and persist an LLM battle narrative for a completed battle.

    Runs as a fire-and-forget asyncio task.  Errors are logged but never
    propagate — the battle pipeline must not be affected.
    """
    from nidozo.analysis import analyze_battle
    from nidozo.analysis.analyzer import _load_species_data
    from nidozo.llm.narrator import generate_battle_narrative

    # Pick a backend — prefer p1; fall back to p2; skip if both random/human.
    if p1_provider not in ("random", "human") and p1_model:
        narrator_provider, narrator_model = p1_provider, p1_model
    elif p2_provider not in ("random", "human") and p2_model:
        narrator_provider, narrator_model = p2_provider, p2_model
    else:
        return  # no LLM available for narrative generation

    try:
        turns_with_state = store.get_turns_with_state(battle_id)
        if not turns_with_state:
            return

        p1_team, p2_team = store.get_battle_teams(battle_id)
        p1_ids: list[str] | None = p1_team.get("pokemon") if p1_team else None
        p2_ids: list[str] | None = p2_team.get("pokemon") if p2_team else None
        sd = _load_species_data() if (p1_ids or p2_ids) else None
        # CPU-bound — offload to a worker thread so it can't stall the loop. (#207)
        analysis = await asyncio.to_thread(
            analyze_battle, turns_with_state,
            p1_team_ids=p1_ids, p2_team_ids=p2_ids, species_data=sd,
        )

        backend = _build_backend(narrator_provider, narrator_model, json_mode=False)
        narrative = await generate_battle_narrative(
            backend=backend,
            analysis=analysis,
            p1_label=p1_label,
            p2_label=p2_label,
            winner=winner,
            total_turns=total_turns,
        )
        if narrative:
            store.set_battle_narrative(battle_id, narrative)
            logger.info(
                "Narrative stored for battle %d (%d chars)", battle_id, len(narrative)
            )
    except Exception as exc:
        logger.error("Failed to generate narrative for battle %d: %s", battle_id, exc)


async def run_bracket_tournament(
    req: StartTournamentRequest,
    tournament_id: int,
    player_specs: list[dict[str, Any]],
    store: Any,
    bus: Any,
    active_tasks: dict[int, asyncio.Task[None]],
) -> None:
    """Run a single-elim or double-elim tournament round by round.

    Unlike round-robin (where all battles are pre-created), bracket
    tournaments create battles lazily — we only know future matchups
    after earlier rounds resolve.
    """
    from nidozo.battle.tiers import resolve_format
    from nidozo.tournament.bracket import (
        build_bracket,
        get_pending_matches,
        record_result,
        resolve_seed,
    )

    any_preset = any(ps.get("preset") for ps in player_specs)
    team_size: int = getattr(req, "team_size", 6)
    doubles: bool = getattr(req, "doubles", False)
    do_draft = req.tier != "random" and req.draft and not any_preset
    showdown_format = (
        PRESET_FORMAT if any_preset
        else resolve_format(req.tier, doubles=doubles, team_size=team_size)
    )
    effective_prompt = "v7" if doubles else ("v3" if do_draft else req.prompt_version)
    cfg = _showdown_cfg()

    # Build initial bracket state
    bracket_state = build_bracket(player_specs, req.tournament_format)
    store.update_bracket_state(tournament_id, bracket_state)

    personality_lookup: dict[tuple[str, str], str | None] = {
        (ps["provider"], ps["model_name"]): ps.get("personality")
        for ps in player_specs
    }
    preset_lookup: dict[tuple[str, str], str | None] = {
        (ps["provider"], ps["model_name"]): ps.get("preset")
        for ps in player_specs
    }

    # Estimate total battles: SE = n-1, DE = 2n-2 or 2n-1 (approx)
    n = len(player_specs)
    if req.tournament_format == "single_elim":
        estimated_total = n - 1
    else:
        estimated_total = 2 * n - 1  # may vary with bracket reset

    await bus.publish({
        "type": "tournament_start",
        "tournament_id": tournament_id,
        "players": player_specs,
        "total_battles": estimated_total,
        "rounds": 0,
        "tier": req.tier,
        "tournament_format": req.tournament_format,
    })
    await bus.publish({
        "type": "bracket_update",
        "tournament_id": tournament_id,
        "bracket": bracket_state,
    })

    battle_num = 0
    cancelled = False
    match_failed = False

    try:
        while True:
            # A cancel via the API only flips the DB status row; stop the runner
            # here, between matches, rather than playing out the bracket.
            t_row = store.get_tournament(tournament_id)
            if t_row and t_row["status"] != "running":
                logger.info(
                    "Bracket tournament %d no longer running (status=%s) — "
                    "stopping runner at battle %d",
                    tournament_id, t_row["status"], battle_num,
                )
                cancelled = True
                break

            pending = get_pending_matches(bracket_state)
            if not pending:
                break

            for match in pending:
                match_id = match["match_id"]
                p1_seed  = match["p1_seed"]
                p2_seed  = match["p2_seed"]
                p1_info  = resolve_seed(bracket_state, p1_seed)
                p2_info  = resolve_seed(bracket_state, p2_seed)

                if p1_info is None or p2_info is None:
                    # Bracket invariant violated — a pending match has unresolvable
                    # seeds. Continuing would spin forever; abort the tournament.
                    logger.error(
                        "Bracket %d: match %s seed lookup failed (p1=%s p2=%s) — "
                        "aborting tournament",
                        tournament_id, match_id, p1_seed, p2_seed,
                    )
                    match_failed = True
                    break

                p1_prov  = p1_info["provider"]
                p1_model = p1_info["model_name"]
                p2_prov  = p2_info["provider"]
                p2_model = p2_info["model_name"]

                p1_label = f"{p1_prov}/{p1_model}"
                p2_label = f"{p2_prov}/{p2_model}"
                battle_num += 1

                # Create battle row
                p1_db_id = store.get_or_create_model(p1_prov, p1_model, effective_prompt)
                p2_db_id = store.get_or_create_model(p2_prov, p2_model, effective_prompt)
                battle_id = store.create_battle(
                    f"bracket-{tournament_id}-{match_id}",
                    showdown_format, p1_db_id, p2_db_id,
                    tournament_id=tournament_id,
                )
                match["battle_id"] = battle_id
                match["status"] = "running"
                store.update_bracket_state(tournament_id, bracket_state)

                task = asyncio.current_task()
                if task:
                    active_tasks[battle_id] = task

                await bus.publish({
                    "type": "tournament_progress",
                    "tournament_id": tournament_id,
                    "battle_num": battle_num,
                    "total_battles": estimated_total,
                    "battle_id": battle_id,
                    "p1": p1_label,
                    "p2": p2_label,
                    "match_id": match_id,
                })

                try:
                    t_p1_id = p1_db_id
                    t_p2_id = p2_db_id
                    t_p1_lessons = (
                        [r["content"] for r in store.get_lessons(t_p1_id)]
                        if p1_prov != "random" else None
                    )
                    t_p2_lessons = (
                        [r["content"] for r in store.get_lessons(t_p2_id)]
                        if p2_prov != "random" else None
                    )

                    b_p1_preset = preset_lookup.get((p1_prov, p1_model))
                    b_p2_preset = preset_lookup.get((p2_prov, p2_model))
                    p1 = _build_streaming_player(
                        p1_prov, p1_model, "p1",
                        effective_prompt, store, battle_id, bus, cfg, showdown_format,
                        lessons=t_p1_lessons,
                        team=build_preset_team_string(b_p1_preset) if b_p1_preset else None,
                        personality=personality_lookup.get((p1_prov, p1_model)),
                    )
                    p2 = _build_streaming_player(
                        p2_prov, p2_model, "p2",
                        effective_prompt, store, battle_id, bus, cfg, showdown_format,
                        lessons=t_p2_lessons,
                        team=build_preset_team_string(b_p2_preset) if b_p2_preset else None,
                        personality=personality_lookup.get((p2_prov, p2_model)),
                    )

                    outcome = await _play_battle(
                        battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
                        p1_label=p1_label, p2_label=p2_label,
                        start_extra={
                            "tournament_id": tournament_id,
                            "format": showdown_format,
                            "tier": req.tier,
                            "drafted": do_draft,
                            "match_id": match_id,
                            "p1_personality": personality_lookup.get((p1_prov, p1_model)),
                            "p2_personality": personality_lookup.get((p2_prov, p2_model)),
                            "p1_preset": b_p1_preset,
                            "p2_preset": b_p2_preset,
                        },
                        end_extra={"tournament_id": tournament_id, "match_id": match_id},
                    )
                    winner = outcome.winner

                    # A bracket must advance someone even on a tie; the recorded
                    # result stays honest (winner=None) and this only governs routing.
                    advance_slot = _bracket_advance_slot(winner, p1_seed, p2_seed)
                    if winner is None:
                        logger.warning(
                            "Bracket %d match %s ended in a tie; advancing better "
                            "seed (slot %d, seed %d) by tiebreak",
                            tournament_id, match_id, advance_slot,
                            p1_seed if advance_slot == 1 else p2_seed,
                        )

                    # Advance bracket
                    record_result(bracket_state, match_id, advance_slot, battle_id)
                    store.update_bracket_state(tournament_id, bracket_state)

                    await bus.publish({
                        "type": "bracket_update",
                        "tournament_id": tournament_id,
                        "bracket": bracket_state,
                    })

                    _spawn_post_battle(
                        store=store, battle_id=battle_id, outcome=outcome,
                        lessons_kwargs={
                            "p1_provider": p1_prov, "p1_model": p1_model,
                            "p1_id": t_p1_id, "p1_opponent": p2_label,
                            "p2_provider": p2_prov, "p2_model": p2_model,
                            "p2_id": t_p2_id, "p2_opponent": p1_label,
                        },
                        narrative_kwargs={
                            "p1_label": p1_label, "p2_label": p2_label,
                            "p1_provider": p1_prov, "p1_model": p1_model,
                            "p2_provider": p2_prov, "p2_model": p2_model,
                        },
                    )

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "Bracket %d match %s failed: %s", tournament_id, match_id, exc
                    )
                    # Update in-memory bracket state so the match isn't left as
                    # "running" — that would make get_pending_matches return nothing,
                    # and the while loop would break and call finish_tournament with
                    # champion_seed=None (silently completing a broken tournament).
                    match["status"] = "failed"
                    store.set_battle_status(battle_id, "failed")
                    store.update_bracket_state(tournament_id, bracket_state)
                    await bus.publish({
                        "type": "error", "battle_id": battle_id, "message": str(exc),
                    })
                    match_failed = True
                finally:
                    active_tasks.pop(battle_id, None)

                if match_failed:
                    break  # exit inner for-loop immediately

            if match_failed:
                break  # exit while True

            # Check if champion is known
            if bracket_state.get("champion_seed") is not None:
                break

    except asyncio.CancelledError:
        logger.info("Bracket tournament %d cancelled at battle %d", tournament_id, battle_num)
        store.finish_tournament(tournament_id, status="cancelled")
        await bus.publish({
            "type": "tournament_cancelled",
            "tournament_id": tournament_id,
            "battles_completed": battle_num,
        })
        cancelled = True
        raise

    if cancelled:
        # Reached here (rather than via the re-raising CancelledError handler)
        # only when the runner saw the tournament was cancelled out from under
        # it. The DB status is already 'cancelled'; just notify clients.
        await bus.publish({
            "type": "tournament_cancelled",
            "tournament_id": tournament_id,
            "battles_completed": battle_num,
        })
    elif match_failed:
        store.finish_tournament(tournament_id, status="failed")
        await bus.publish({
            "type": "tournament_failed",
            "tournament_id": tournament_id,
            "battles_completed": battle_num,
            "bracket": bracket_state,
        })
    else:
        store.finish_tournament(tournament_id, status="completed")
        champion_seed = bracket_state.get("champion_seed")
        champion_info = resolve_seed(bracket_state, champion_seed) if champion_seed else None
        leaderboard = store.leaderboard()
        await bus.publish({
            "type": "tournament_end",
            "tournament_id": tournament_id,
            "leaderboard": leaderboard,
            "bracket": bracket_state,
            "champion": champion_info,
        })


async def run_season(
    req: StartSeasonRequest,
    season_id: int,
    battle_ids: list[int],
    player_specs: list[dict[str, Any]],
    store: Any,
    bus: Any,
    active_tasks: dict[int, asyncio.Task[None]],
) -> None:
    """Run all battles in a season sequentially as a background task."""
    from nidozo.battle.tiers import resolve_format

    any_preset = any(ps.get("preset") for ps in player_specs)
    team_size: int = getattr(req, "team_size", 6)
    doubles: bool = getattr(req, "doubles", False)
    do_draft = req.tier != "random" and req.draft and not any_preset
    showdown_format = (
        PRESET_FORMAT if any_preset
        else resolve_format(req.tier, doubles=doubles, team_size=team_size)
    )
    effective_prompt = "v7" if doubles else ("v3" if do_draft else req.prompt_version)
    cfg = _showdown_cfg()

    total = len(battle_ids)
    store.set_season_running(season_id)
    await bus.publish({
        "type": "season_start",
        "season_id": season_id,
        "season_name": req.name,
        "players": player_specs,
        "total_battles": total,
        "rounds": req.rounds,
        "tier": req.tier,
    })

    coach_lookup: dict[tuple[str, str], tuple[str | None, str | None]] = {
        (ps["provider"], ps["model_name"]): (
            ps.get("coach_provider"), ps.get("coach_model")
        )
        for ps in player_specs
    }
    personality_lookup: dict[tuple[str, str], str | None] = {
        (ps["provider"], ps["model_name"]): ps.get("personality")
        for ps in player_specs
    }
    preset_lookup: dict[tuple[str, str], str | None] = {
        (ps["provider"], ps["model_name"]): ps.get("preset")
        for ps in player_specs
    }

    for battle_num, battle_id in enumerate(battle_ids, start=1):
        # A cancel via the API only flips the DB status row; the runner must
        # notice and stop here, between battles, rather than playing on.
        s_row = store.get_season(season_id)
        if s_row and s_row["status"] != "running":
            logger.info(
                "Season %d no longer running (status=%s) — stopping runner "
                "before battle %d",
                season_id, s_row["status"], battle_num,
            )
            await bus.publish({
                "type": "season_cancelled",
                "season_id": season_id,
                "battles_completed": battle_num - 1,
            })
            return

        battle_row = store.get_battle(battle_id)
        if not battle_row or battle_row["status"] == "cancelled":
            continue

        task = asyncio.current_task()
        if task:
            active_tasks[battle_id] = task

        battle_info = store.get_battle_players(battle_id)
        if battle_info is None:
            logger.error(
                "Season %d: no player info for battle %d — skipping", season_id, battle_id
            )
            continue

        p1_label = f"{battle_info['p1_provider']}/{battle_info['p1_model']}"
        p2_label = f"{battle_info['p2_provider']}/{battle_info['p2_model']}"

        await bus.publish({
            "type": "season_progress",
            "season_id": season_id,
            "battle_num": battle_num,
            "total_battles": total,
            "battle_id": battle_id,
            "p1": p1_label,
            "p2": p2_label,
        })

        try:
            model_ids = store.get_player_model_ids(battle_id)
            s_p1_id, s_p2_id = model_ids if model_ids else (None, None)
            s_p1_prov, s_p2_prov = battle_info["p1_provider"], battle_info["p2_provider"]
            s_p1_lessons = (
                [r["content"] for r in store.get_lessons(s_p1_id)]
                if s_p1_id and s_p1_prov != "random" else None
            )
            s_p2_lessons = (
                [r["content"] for r in store.get_lessons(s_p2_id)]
                if s_p2_id and s_p2_prov != "random" else None
            )

            s_p1_team: str | None = None
            s_p2_team: str | None = None
            s_p1_team_id: int | None = None
            s_p2_team_id: int | None = None

            s_p1_preset = preset_lookup.get((s_p1_prov, battle_info["p1_model"]))
            s_p2_preset = preset_lookup.get((s_p2_prov, battle_info["p2_model"]))
            if s_p1_preset:
                s_p1_team = build_preset_team_string(s_p1_preset)
            if s_p2_preset:
                s_p2_team = build_preset_team_string(s_p2_preset)

            if do_draft and s_p1_id is not None and s_p1_prov != "random":
                await bus.publish({
                    "type": "draft_start",
                    "player_role": "p1",
                    "tier": req.tier,
                    "battle_id": battle_id,
                    "season_id": season_id,
                })
                p1_backend = _build_backend(s_p1_prov, battle_info["p1_model"])
                p1_draft_r = await run_draft_phase(
                    p1_backend, s_p1_id, req.tier, store, bus, "p1", team_size=team_size, doubles=doubles
                )
                s_p1_team = p1_draft_r["team_string"]
                s_p1_team_id = p1_draft_r["team_id"]

            if do_draft and s_p2_id is not None and s_p2_prov != "random":
                await bus.publish({
                    "type": "draft_start",
                    "player_role": "p2",
                    "tier": req.tier,
                    "battle_id": battle_id,
                    "season_id": season_id,
                })
                p2_backend = _build_backend(s_p2_prov, battle_info["p2_model"])
                p2_draft_r = await run_draft_phase(
                    p2_backend, s_p2_id, req.tier, store, bus, "p2", team_size=team_size, doubles=doubles
                )
                s_p2_team = p2_draft_r["team_string"]
                s_p2_team_id = p2_draft_r["team_id"]

            if do_draft:
                store.set_battle_teams(battle_id, s_p1_team_id, s_p2_team_id, req.tier)
            elif req.tier != "random" and not any_preset:
                s_p1_team = s_p1_team or _random_preset_team(req.tier, team_size)
                s_p2_team = s_p2_team or _random_preset_team(req.tier, team_size)

            s_p1_coach_prov, s_p1_coach_model = coach_lookup.get(
                (s_p1_prov, battle_info["p1_model"]), (None, None)
            )
            s_p2_coach_prov, s_p2_coach_model = coach_lookup.get(
                (s_p2_prov, battle_info["p2_model"]), (None, None)
            )
            s_p1_personality = personality_lookup.get((s_p1_prov, battle_info["p1_model"]))
            s_p2_personality = personality_lookup.get((s_p2_prov, battle_info["p2_model"]))

            p1 = _build_streaming_player(
                s_p1_prov, battle_info["p1_model"], "p1",
                effective_prompt, store, battle_id, bus, cfg, showdown_format,
                lessons=s_p1_lessons,
                team=s_p1_team,
                coach_provider=s_p1_coach_prov,
                coach_model=s_p1_coach_model,
                personality=s_p1_personality,
            )
            p2 = _build_streaming_player(
                s_p2_prov, battle_info["p2_model"], "p2",
                effective_prompt, store, battle_id, bus, cfg, showdown_format,
                lessons=s_p2_lessons,
                team=s_p2_team,
                coach_provider=s_p2_coach_prov,
                coach_model=s_p2_coach_model,
                personality=s_p2_personality,
            )

            outcome = await _play_battle(
                battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
                p1_label=p1_label, p2_label=p2_label,
                start_extra={
                    "season_id": season_id,
                    "format": showdown_format,
                    "tier": req.tier,
                    "drafted": do_draft,
                    "p1_personality": s_p1_personality,
                    "p2_personality": s_p2_personality,
                    "p1_preset": s_p1_preset,
                    "p2_preset": s_p2_preset,
                },
                end_extra={"season_id": season_id},
            )

            standings = store.get_season_standings(season_id)
            await bus.publish({
                "type": "season_standings",
                "season_id": season_id,
                "standings": standings,
                "battle_num": battle_num,
                "total_battles": total,
            })

            _spawn_post_battle(
                store=store, battle_id=battle_id, outcome=outcome,
                lessons_kwargs={
                    "p1_provider": s_p1_prov, "p1_model": battle_info["p1_model"],
                    "p1_id": s_p1_id, "p1_opponent": p2_label,
                    "p2_provider": s_p2_prov, "p2_model": battle_info["p2_model"],
                    "p2_id": s_p2_id, "p2_opponent": p1_label,
                },
                narrative_kwargs={
                    "p1_label": p1_label, "p2_label": p2_label,
                    "p1_provider": s_p1_prov, "p1_model": battle_info["p1_model"],
                    "p2_provider": s_p2_prov, "p2_model": battle_info["p2_model"],
                },
            )

        except asyncio.CancelledError:
            logger.info("Season %d cancelled at battle %d", season_id, battle_id)
            store.cancel_battle(battle_id)
            store.finish_season(season_id, status="cancelled")
            await bus.publish({
                "type": "season_cancelled",
                "season_id": season_id,
                "battles_completed": battle_num - 1,
            })
            raise
        except Exception as exc:
            logger.error("Season %d battle %d failed: %s", season_id, battle_id, exc)
            store.set_battle_status(battle_id, "failed")
            await bus.publish({"type": "error", "battle_id": battle_id, "message": str(exc)})
        finally:
            active_tasks.pop(battle_id, None)

    # Only finalize as completed if the season wasn't cancelled (or otherwise
    # moved to a terminal status) while the loop was running — otherwise we'd
    # silently overwrite the user's cancel with 'completed'.
    final = store.get_season(season_id)
    if final and final["status"] != "running":
        return
    store.finish_season(season_id, status="completed")
    final_standings = store.get_season_standings(season_id)
    await bus.publish({
        "type": "season_end",
        "season_id": season_id,
        "season_name": req.name,
        "standings": final_standings,
    })


async def run_experiment(
    req: Any,
    experiment_id: int,
    battle_ids: list[int],
    variant_a: dict[str, Any],
    variant_b: dict[str, Any],
    a_model_id: int,
    b_model_id: int,
    store: Any,
    bus: Any,
    active_tasks: dict[int, asyncio.Task[None]],
) -> None:
    """Run a bake-off: a fixed N-battle head-to-head between two variants (#226).

    Sides alternate across the pre-created battles (handled at creation) to
    cancel first-move advantage. Each player uses its *own* variant's prompt
    version — the whole point of the comparison. Lessons and post-battle
    generation are deliberately skipped so neither variant evolves mid-run and
    the result reflects the prompt/model alone.
    """
    from nidozo.analysis.significance import bakeoff_result
    from nidozo.battle.tiers import resolve_format

    doubles: bool = getattr(req, "doubles", False)
    team_size: int = getattr(req, "team_size", 6)
    fmt = resolve_format(req.tier, doubles=doubles, team_size=team_size)
    cfg = _showdown_cfg()
    by_id = {a_model_id: variant_a, b_model_id: variant_b}

    store.set_experiment_running(experiment_id)
    await bus.publish({
        "type": "experiment_start",
        "experiment_id": experiment_id,
        "name": req.name,
        "variant_a": variant_a,
        "variant_b": variant_b,
        "total_battles": len(battle_ids),
        "tier": req.tier,
    })

    def _team_for(tier: str) -> str | None:
        # Random tier → Showdown auto-generates; non-random → a fresh random team.
        return None if tier == "random" else _random_preset_team(tier, team_size)

    for battle_num, battle_id in enumerate(battle_ids, start=1):
        e_row = store.get_experiment(experiment_id)
        if e_row and e_row["status"] != "running":
            await bus.publish({
                "type": "experiment_cancelled",
                "experiment_id": experiment_id,
                "battles_completed": battle_num - 1,
            })
            return

        battle_row = store.get_battle(battle_id)
        if not battle_row or battle_row["status"] == "cancelled":
            continue

        task = asyncio.current_task()
        if task:
            active_tasks[battle_id] = task

        model_ids = store.get_player_model_ids(battle_id)
        if not model_ids:
            continue
        p1_mid, p2_mid = model_ids
        v1 = by_id.get(p1_mid, variant_a)
        v2 = by_id.get(p2_mid, variant_b)
        p1_label = f"{v1['provider']}/{v1['model_name']}·{v1['prompt_version']}"
        p2_label = f"{v2['provider']}/{v2['model_name']}·{v2['prompt_version']}"

        await bus.publish({
            "type": "experiment_progress",
            "experiment_id": experiment_id,
            "battle_num": battle_num,
            "total_battles": len(battle_ids),
            "battle_id": battle_id,
            "p1": p1_label,
            "p2": p2_label,
        })

        try:
            p1 = _build_streaming_player(
                v1["provider"], v1["model_name"], "p1", v1["prompt_version"],
                store, battle_id, bus, cfg, fmt, team=_team_for(req.tier),
            )
            p2 = _build_streaming_player(
                v2["provider"], v2["model_name"], "p2", v2["prompt_version"],
                store, battle_id, bus, cfg, fmt, team=_team_for(req.tier),
            )

            await _play_battle(
                battle_id=battle_id, p1=p1, p2=p2, store=store, bus=bus,
                p1_label=p1_label, p2_label=p2_label,
                start_extra={"experiment_id": experiment_id, "format": fmt, "tier": req.tier},
                end_extra={"experiment_id": experiment_id},
            )

            counts = store.get_experiment_result(experiment_id)
            await bus.publish({
                "type": "experiment_progress",
                "experiment_id": experiment_id,
                "battle_num": battle_num,
                "total_battles": len(battle_ids),
                "result": bakeoff_result(**counts),
            })
        except asyncio.CancelledError:
            logger.info("Experiment %d cancelled at battle %d", experiment_id, battle_id)
            store.cancel_battle(battle_id)
            store.finish_experiment(experiment_id, status="cancelled")
            await bus.publish({
                "type": "experiment_cancelled",
                "experiment_id": experiment_id,
                "battles_completed": battle_num - 1,
            })
            raise
        except Exception as exc:
            logger.error("Experiment %d battle %d failed: %s", experiment_id, battle_id, exc)
            store.set_battle_status(battle_id, "failed")
            await bus.publish({"type": "error", "battle_id": battle_id, "message": str(exc)})
        finally:
            active_tasks.pop(battle_id, None)

    final = store.get_experiment(experiment_id)
    if final and final["status"] != "running":
        return
    store.finish_experiment(experiment_id, status="completed")
    await bus.publish({
        "type": "experiment_end",
        "experiment_id": experiment_id,
        "name": req.name,
        "result": bakeoff_result(**store.get_experiment_result(experiment_id)),
    })
