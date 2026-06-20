"""REST endpoint definitions for the Nidozo API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from nidozo import __version__
from nidozo.api.helpers import _model_name
from nidozo.api.models import (
    HumanActionRequest,
    StartBattleRequest,
    StartBattleResponse,
    StartSeasonRequest,
    StartSeasonResponse,
    StartTournamentRequest,
    StartTournamentResponse,
)
from nidozo.api.orchestration import run_battles, run_bracket_tournament, run_season, run_tournament
from nidozo.db.store import BattleStore

logger = logging.getLogger(__name__)

_SHOWDOWN_HOST = os.environ.get("NIDOZO_SHOWDOWN_HOST", "localhost")
_SHOWDOWN_PORT = int(os.environ.get("NIDOZO_SHOWDOWN_PORT", "8000"))


def create_router(
    store: BattleStore,
    bus: Any,
    active_tasks: dict[int, asyncio.Task[None]],
) -> APIRouter:
    """Build and return the main APIRouter with all REST endpoints registered."""
    router = APIRouter()

    # -------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------

    @router.get("/healthz", response_model=None)
    def healthz() -> dict[str, Any] | JSONResponse:
        """Liveness + readiness probe.

        Returns HTTP 200 with status "ok" when both the database and Showdown
        server are reachable.  Returns HTTP 503 with status "degraded" if
        either dependency is down.
        """
        checks: dict[str, str] = {}

        try:
            store._conn.execute("SELECT 1")
            checks["db"] = "ok"
        except Exception as exc:
            logger.error("Health check: DB unreachable: %s", exc)
            checks["db"] = "unreachable"

        try:
            with socket.create_connection((_SHOWDOWN_HOST, _SHOWDOWN_PORT), timeout=1.0):
                pass
            checks["showdown"] = "ok"
        except OSError:
            checks["showdown"] = "unreachable"

        overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
        payload = {"status": overall, "version": __version__, **checks}

        if overall != "ok":
            return JSONResponse(status_code=503, content=payload)
        return payload

    # -------------------------------------------------------------------
    # Leaderboard & battles
    # -------------------------------------------------------------------

    @router.get("/api/stats/global")
    def get_global_stats() -> dict[str, Any]:
        """Aggregate stats across all battles and models."""
        return store.get_global_stats()

    @router.get("/api/stats/personalities")
    def get_personality_stats() -> list[dict[str, Any]]:
        """Win/loss/tie counts per personality slug across all completed battles."""
        return store.get_personality_stats()

    @router.get("/api/stats/cost")
    def get_cost_stats() -> dict[str, Any]:
        """Token usage + estimated USD cost per model and overall (#225).

        Costs use the editable price table in ``data/model_prices.json``;
        models not listed (local LM Studio, ``random``) are counted as free.
        """
        from nidozo.llm.pricing import estimate_cost

        rows = store.get_token_usage_by_model()
        by_model: list[dict[str, Any]] = []
        total_prompt = total_completion = 0
        total_cost = 0.0
        for r in rows:
            pt = int(r["prompt_tokens"])
            ct = int(r["completion_tokens"])
            cost = estimate_cost(r["model_name"], pt, ct)
            total_prompt += pt
            total_completion += ct
            total_cost += cost
            by_model.append({**r, "est_cost_usd": round(cost, 4)})
        return {
            "by_model": by_model,
            "total": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "est_cost_usd": round(total_cost, 4),
            },
        }

    @router.get("/api/personalities")
    def list_personalities() -> list[dict[str, Any]]:
        """Return all available play-style personas for the battle form."""
        from nidozo.battle.personalities import PERSONALITIES
        return [
            {
                "slug":         p.slug,
                "display_name": p.display_name,
                "description":  p.description,
                "emoji":        p.emoji,
            }
            for p in PERSONALITIES.values()
        ]

    @router.get("/api/leaderboard")
    def get_leaderboard(grouped: bool = True, tier: str | None = None) -> list[dict[str, Any]]:
        return store.leaderboard(grouped=grouped, tier=tier)

    @router.get("/api/leaderboard/matchups")
    def get_matchup_matrix(tier: str | None = None) -> list[dict[str, Any]]:
        """Head-to-head win/loss/tie counts for every model pair.

        Each entry is model A's record vs model B (A's perspective).
        Pass ``tier`` to restrict to battles of a specific tier.
        """
        return store.matchup_matrix(tier=tier)

    @router.get("/api/battles")
    def get_battles(limit: int = 20) -> list[dict[str, Any]]:
        return store.recent_battles(limit=limit)

    @router.get("/api/battles/{battle_id}")
    def get_battle(battle_id: int) -> dict[str, Any]:
        battle = store.get_battle(battle_id)
        if not battle:
            raise HTTPException(status_code=404, detail="Battle not found")
        return battle

    @router.post("/api/battles/{battle_id}/cancel")
    async def cancel_battle(battle_id: int) -> dict[str, Any]:
        task = active_tasks.get(battle_id)
        if task and not task.done():
            task.cancel()

        cancelled = store.cancel_battle(battle_id)
        if not cancelled:
            battle = store.get_battle(battle_id)
            if not battle:
                raise HTTPException(status_code=404, detail="Battle not found")
            return {"ok": False, "message": f"Battle already {battle['status']}"}

        await bus.publish({"type": "battle_cancelled", "battle_id": battle_id})
        return {"ok": True, "message": "Battle cancelled"}

    @router.post("/api/battles/{battle_id}/human-action")
    async def submit_human_action(battle_id: int, body: HumanActionRequest) -> dict[str, Any]:
        """Deliver the human player's chosen action for the pending turn.

        The browser sends this when the human clicks a move or switch button.
        The future registered by StreamingHumanPlayer.choose_move is resolved,
        which unblocks the poke-env turn and sends the action to Showdown.
        """
        from nidozo.battle.human_player import resolve_pending

        action_json = json.dumps({
            "action_type": body.action_type,
            "identifier": body.identifier,
        })
        resolved = resolve_pending(battle_id, body.player_role, action_json)
        if not resolved:
            raise HTTPException(
                status_code=409,
                detail="No pending human action for this battle and player role",
            )
        return {"ok": True}

    @router.get("/api/lmstudio/models")
    async def get_lmstudio_models() -> list[str]:
        """Proxy to LM Studio's /v1/models."""
        base_url = os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": "Bearer lm-studio"},
                )
                resp.raise_for_status()
                data = resp.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception as exc:
            logger.debug("LM Studio not reachable at %s: %s", base_url, exc)
            return []

    @router.get("/api/battles/{battle_id}/replay")
    def get_replay(battle_id: int) -> dict[str, Any]:
        """Full turn-by-turn merged state for battle replay."""
        battle = store.get_battle(battle_id)
        if not battle:
            raise HTTPException(status_code=404, detail="Battle not found")

        turns_raw = store.get_turns_with_state(battle_id)

        turns_by_num: dict[int, dict[str, Any]] = {}
        for row in turns_raw:
            n = row["turn_number"]
            if n not in turns_by_num:
                turns_by_num[n] = {}
            state = json.loads(row["state_json"]) if row["state_json"] else None
            turns_by_num[n][row["player_role"]] = {
                "state":         state,
                "action":        row["action_chosen"],
                "parse_success": bool(row["parse_success"]),
                "fallback_reason": row.get("fallback_reason"),
                "coach_advice":  row.get("coach_advice"),
            }

        turns = [
            {"turn": n, **turns_by_num[n]}
            for n in sorted(turns_by_num.keys())
        ]
        return {"battle": battle, "turns": turns}

    @router.get("/api/battles/{battle_id}/turns")
    def get_turns(battle_id: int) -> list[dict[str, Any]]:
        return store.get_turns_basic(battle_id)

    @router.get("/api/battles/{battle_id}/analysis")
    def get_analysis(battle_id: int) -> dict[str, Any]:
        from nidozo.analysis import analyze_battle
        from nidozo.analysis.analyzer import _load_species_data

        turns = store.get_turns_with_state(battle_id)
        p1_team, p2_team = store.get_battle_teams(battle_id)
        p1_ids: list[str] | None = p1_team.get("pokemon") if p1_team else None
        p2_ids: list[str] | None = p2_team.get("pokemon") if p2_team else None
        sd = _load_species_data() if (p1_ids or p2_ids) else None
        result = analyze_battle(turns, p1_team_ids=p1_ids, p2_team_ids=p2_ids, species_data=sd)
        # Attach pre-generated narrative if available
        battle_row = store.get_battle(battle_id)
        if battle_row and battle_row.get("narrative"):
            result["narrative"] = battle_row["narrative"]
        return result

    @router.get("/api/models/{model_id}/lessons")
    def get_model_lessons(model_id: int, limit: int = 10) -> list[dict[str, Any]]:
        return store.get_lessons(model_id, limit=limit)

    @router.get("/api/models/{model_id}/stats")
    def get_model_stats(model_id: int) -> dict[str, Any]:
        stats = store.get_model_stats(model_id)
        if stats is None:
            raise HTTPException(status_code=404, detail="Model not found")
        return stats

    @router.get("/api/models/{model_id}/matchups")
    def get_model_matchups(model_id: int) -> list[dict[str, Any]]:
        """Head-to-head record for this model against each opponent it has faced."""
        return store.get_model_matchups(model_id)

    @router.get("/api/models/{model_id}/badges")
    def get_model_badges(model_id: int) -> list[dict[str, Any]]:
        """All badges earned by this model, oldest first."""
        return store.get_model_badges(model_id)

    # -------------------------------------------------------------------
    # Tournaments
    # -------------------------------------------------------------------

    @router.get("/api/tournaments")
    def list_tournaments(limit: int = 20) -> list[dict[str, Any]]:
        return store.list_tournaments(limit=limit)

    @router.get("/api/tournaments/{tournament_id}")
    def get_tournament(tournament_id: int) -> dict[str, Any]:
        t = store.get_tournament(tournament_id)
        if not t:
            raise HTTPException(status_code=404, detail="Tournament not found")
        # Parse bracket_state JSON so the client receives an object, not a string
        if t.get("bracket_state") and isinstance(t["bracket_state"], str):
            try:
                t["bracket_state"] = json.loads(t["bracket_state"])
            except json.JSONDecodeError:
                t["bracket_state"] = None
        return t

    @router.get("/api/tournaments/{tournament_id}/standings")
    def get_tournament_standings(tournament_id: int) -> list[dict[str, Any]]:
        if not store.get_tournament(tournament_id):
            raise HTTPException(status_code=404, detail="Tournament not found")
        return store.get_tournament_standings(tournament_id)

    @router.get("/api/tournaments/{tournament_id}/battles")
    def get_tournament_battles(tournament_id: int) -> list[dict[str, Any]]:
        if not store.get_tournament(tournament_id):
            raise HTTPException(status_code=404, detail="Tournament not found")
        return store.get_tournament_battles(tournament_id)

    @router.post("/api/tournaments/{tournament_id}/cancel")
    async def cancel_tournament(tournament_id: int) -> dict[str, Any]:
        t = store.get_tournament(tournament_id)
        if not t:
            raise HTTPException(status_code=404, detail="Tournament not found")
        cancelled = store.cancel_tournament(tournament_id)
        if cancelled:
            await bus.publish({
                "type": "tournament_cancelled",
                "tournament_id": tournament_id,
                "battles_completed": None,
            })
        status = t["status"] if not cancelled else "cancelled"
        return {"tournament_id": tournament_id, "cancelled": cancelled, "status": status}

    # -------------------------------------------------------------------
    # Party presets
    # -------------------------------------------------------------------

    @router.get("/api/presets")
    def list_presets() -> list[dict[str, Any]]:
        """All trainer-archetype party presets with metadata and Pokémon list."""
        from nidozo.battle.presets import list_presets as _list_presets
        return _list_presets()

    # -------------------------------------------------------------------
    # Tiers & teams
    # -------------------------------------------------------------------

    @router.get("/api/tiers")
    def list_tiers() -> list[dict[str, Any]]:
        from nidozo.battle.team_builder import all_species as _all_species
        from nidozo.battle.team_builder import load_movesets
        from nidozo.battle.tiers import _TIER_POOLS, TIER_DISPLAY, TIER_TO_FORMAT

        ms = load_movesets()
        all_s = _all_species(ms)
        result: list[dict[str, Any]] = []
        for tier_id, display in TIER_DISPLAY.items():
            if tier_id == "random":
                continue
            pool = _TIER_POOLS.get(tier_id)
            count = len(pool & all_s) if pool is not None else len(all_s)
            result.append({
                "id": tier_id,
                "name": display,
                "showdown_format": TIER_TO_FORMAT.get(tier_id, "gen9nationaldexag"),
                "pokemon_count": count,
            })
        return result

    @router.get("/api/tiers/{tier_id}/pokemon")
    def get_tier_pokemon(tier_id: str) -> list[dict[str, Any]]:
        from nidozo.battle.team_builder import all_species as _all_species
        from nidozo.battle.team_builder import get_pool_info, load_movesets
        from nidozo.battle.tiers import get_pool, is_valid_tier

        if not is_valid_tier(tier_id) or tier_id == "random":
            raise HTTPException(status_code=404, detail=f"Unknown tier: {tier_id!r}")
        ms = load_movesets()
        pool_ids = get_pool(tier_id, _all_species(ms))
        return get_pool_info(pool_ids, ms)

    @router.get("/api/models/{model_id}/teams")
    def get_model_teams(model_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return store.get_teams_for_model(model_id, limit=limit)

    @router.get("/api/battles/{battle_id}/teams")
    def get_battle_teams(battle_id: int) -> dict[str, Any]:
        p1, p2 = store.get_battle_teams(battle_id)
        return {"p1": p1, "p2": p2}

    # -------------------------------------------------------------------
    # Start a single battle
    # -------------------------------------------------------------------

    @router.post("/api/battles/start", response_model=StartBattleResponse)
    async def start_battle(
        req: StartBattleRequest,
        background_tasks: BackgroundTasks,
    ) -> StartBattleResponse:
        from nidozo.battle.tiers import resolve_format

        # tier / prompt_version / providers are validated by the Pydantic model
        # (Literal types → 422 on bad input), so no manual checks are needed here.
        use_preset = bool(req.p1_preset or req.p2_preset)
        showdown_format = (
            "gen9nationaldexag" if use_preset
            else resolve_format(req.tier, doubles=req.doubles, team_size=req.team_size)
        )
        effective_prompt = "v3" if (req.tier != "random" and req.draft) else req.prompt_version

        battle_ids = []
        for i in range(req.n_battles):
            p1_model_name = _model_name(req.p1_provider, req.p1_model or req.model)
            p2_model_name = _model_name(req.p2_provider, req.p2_model or req.model)
            p1_id = store.get_or_create_model(req.p1_provider, p1_model_name, effective_prompt)
            p2_id = store.get_or_create_model(req.p2_provider, p2_model_name, effective_prompt)
            # Placeholder tag is transient — overwritten with the real Showdown
            # tag once the battle connects (see update_battle_tag). It only has
            # to satisfy the battles.battle_tag UNIQUE constraint at insert time.
            # The index alone collides across separate start calls with the same
            # providers, so include a uuid to guarantee global uniqueness.
            placeholder_tag = (
                f"pending-{req.p1_provider}-{req.p2_provider}-{i}-{uuid.uuid4().hex[:12]}"
            )
            bid = store.create_battle(
                placeholder_tag,
                showdown_format,
                p1_id,
                p2_id,
                p1_personality=req.p1_personality,
                p2_personality=req.p2_personality,
                team_size=req.team_size,
            )
            battle_ids.append(bid)

        background_tasks.add_task(run_battles, req, battle_ids, store, bus, active_tasks)
        return StartBattleResponse(
            battle_ids=battle_ids,
            message=f"Started {req.n_battles} battle(s). Connect to /ws/battles to watch.",
        )

    # -------------------------------------------------------------------
    # Start a tournament
    # -------------------------------------------------------------------

    @router.post("/api/tournament/start", response_model=StartTournamentResponse)
    async def start_tournament(
        req: StartTournamentRequest,
        background_tasks: BackgroundTasks,
    ) -> StartTournamentResponse:
        import itertools

        from nidozo.battle.tiers import resolve_format

        # players (min_length=2), tier, tournament_format, prompt_version and
        # providers are all validated by the Pydantic model (Literal / Field
        # constraints → 422 on bad input), so no manual checks are needed here.
        if any(p.provider == "human" for p in req.players):
            raise HTTPException(status_code=422, detail="Tournaments cannot include the 'human' provider")
        showdown_format = resolve_format(req.tier, doubles=req.doubles, team_size=req.team_size)
        effective_prompt = "v3" if (req.tier != "random" and req.draft) else req.prompt_version

        player_specs: list[dict[str, Any]] = [
            {
                "provider":       p.provider,
                "model_name":     _model_name(p.provider, p.model),
                "coach_provider": p.coach_provider,
                "coach_model":    p.coach_model,
                "personality":    p.personality,
                "preset":         p.preset,
            }
            for p in req.players
        ]

        # --- Bracket formats (single_elim / double_elim) ---
        if req.tournament_format in ("single_elim", "double_elim"):
            n = len(player_specs)
            estimated_total = n - 1 if req.tournament_format == "single_elim" else 2 * n - 1

            tournament_id = store.create_tournament(
                players=player_specs,
                rounds=1,
                prompt_version=effective_prompt,
                total_battles=estimated_total,
                tier=req.tier,
                tournament_format=req.tournament_format,
            )

            background_tasks.add_task(
                run_bracket_tournament,
                req, tournament_id, player_specs, store, bus, active_tasks,
            )

            return StartTournamentResponse(
                tournament_id=tournament_id,
                battle_ids=[],
                total_battles=estimated_total,
                message=(
                    f"{req.tournament_format.replace('_', ' ').title()} tournament started: "
                    f"{n} players, ~{estimated_total} battles."
                ),
            )

        # --- Round-robin ---
        pairs = list(itertools.combinations(range(len(req.players)), 2))
        total = len(pairs) * req.rounds * 2

        tournament_id = store.create_tournament(
            players=player_specs,
            rounds=req.rounds,
            prompt_version=effective_prompt,
            total_battles=total,
            tier=req.tier,
            tournament_format="round_robin",
        )

        battle_ids = []
        battle_num = 0
        for (i, j) in pairs:
            for _ in range(req.rounds):
                for (a_idx, b_idx) in [(i, j), (j, i)]:
                    a = player_specs[a_idx]
                    b = player_specs[b_idx]
                    a_id = store.get_or_create_model(a["provider"], a["model_name"], effective_prompt)
                    b_id = store.get_or_create_model(b["provider"], b["model_name"], effective_prompt)
                    bid = store.create_battle(
                        f"tournament-{tournament_id}-{battle_num}",
                        showdown_format,
                        a_id, b_id,
                        tournament_id=tournament_id,
                        p1_personality=a.get("personality"),
                        p2_personality=b.get("personality"),
                        team_size=req.team_size,
                    )
                    battle_ids.append(bid)
                    battle_num += 1

        background_tasks.add_task(
            run_tournament, req, tournament_id, battle_ids, player_specs, store, bus, active_tasks
        )

        return StartTournamentResponse(
            tournament_id=tournament_id,
            battle_ids=battle_ids,
            total_battles=total,
            message=(
                f"Round-robin tournament started: {len(req.players)} players, "
                f"{req.rounds} round(s), {total} battles."
            ),
        )

    # -------------------------------------------------------------------
    # Seasons
    # -------------------------------------------------------------------

    @router.post("/api/seasons/start", response_model=StartSeasonResponse)
    async def start_season(
        req: StartSeasonRequest,
        background_tasks: BackgroundTasks,
    ) -> StartSeasonResponse:
        import itertools

        from nidozo.battle.tiers import resolve_format

        # players (min_length=2), tier, prompt_version and providers are validated
        # by the Pydantic model (Literal / Field constraints → 422), so no manual
        # checks are needed here.
        if any(p.provider == "human" for p in req.players):
            raise HTTPException(status_code=422, detail="Seasons cannot include the 'human' provider")
        showdown_format = resolve_format(req.tier, doubles=req.doubles, team_size=req.team_size)
        effective_prompt = "v3" if (req.tier != "random" and req.draft) else req.prompt_version

        player_specs: list[dict[str, Any]] = [
            {
                "provider":       p.provider,
                "model_name":     _model_name(p.provider, p.model),
                "coach_provider": p.coach_provider,
                "coach_model":    p.coach_model,
                "personality":    p.personality,
                "preset":         p.preset,
            }
            for p in req.players
        ]

        pairs = list(itertools.combinations(range(len(req.players)), 2))
        total = len(pairs) * req.rounds * 2

        season_id = store.create_season(
            name=req.name,
            tier=req.tier,
            fmt=showdown_format,
            participants=player_specs,
            rounds=req.rounds,
            prompt_version=effective_prompt,
            total_battles=total,
        )

        battle_ids: list[int] = []
        battle_num = 0
        for (i, j) in pairs:
            for _ in range(req.rounds):
                for (a_idx, b_idx) in [(i, j), (j, i)]:
                    a = player_specs[a_idx]
                    b = player_specs[b_idx]
                    a_id = store.get_or_create_model(a["provider"], a["model_name"], effective_prompt)
                    b_id = store.get_or_create_model(b["provider"], b["model_name"], effective_prompt)
                    bid = store.create_battle(
                        f"season-{season_id}-{battle_num}",
                        showdown_format,
                        a_id, b_id,
                        season_id=season_id,
                        p1_personality=a.get("personality"),
                        p2_personality=b.get("personality"),
                        team_size=req.team_size,
                    )
                    battle_ids.append(bid)
                    battle_num += 1

        background_tasks.add_task(
            run_season, req, season_id, battle_ids, player_specs, store, bus, active_tasks
        )

        return StartSeasonResponse(
            season_id=season_id,
            battle_ids=battle_ids,
            total_battles=total,
            message=(
                f"Season '{req.name}' started: {len(req.players)} players, "
                f"{req.rounds} round(s), {total} battles."
            ),
        )

    @router.get("/api/seasons")
    def list_seasons(limit: int = 20) -> list[dict[str, Any]]:
        return store.list_seasons(limit=limit)

    @router.get("/api/seasons/{season_id}")
    def get_season(season_id: int) -> dict[str, Any]:
        s = store.get_season(season_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Season not found")
        return s

    @router.get("/api/seasons/{season_id}/standings")
    def get_season_standings(season_id: int) -> list[dict[str, Any]]:
        if store.get_season(season_id) is None:
            raise HTTPException(status_code=404, detail="Season not found")
        return store.get_season_standings(season_id)

    @router.get("/api/seasons/{season_id}/battles")
    def get_season_battles(season_id: int) -> list[dict[str, Any]]:
        if store.get_season(season_id) is None:
            raise HTTPException(status_code=404, detail="Season not found")
        return store.get_season_battles(season_id)

    @router.post("/api/seasons/{season_id}/cancel")
    async def cancel_season(season_id: int) -> dict[str, Any]:
        if store.get_season(season_id) is None:
            raise HTTPException(status_code=404, detail="Season not found")
        cancelled = store.cancel_season(season_id)
        if not cancelled:
            raise HTTPException(
                status_code=409, detail="Season is already finished or cannot be cancelled"
            )
        await bus.publish({
            "type": "season_cancelled",
            "season_id": season_id,
            "battles_completed": None,
        })
        return {"ok": True, "season_id": season_id}

    return router
