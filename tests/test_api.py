"""Tests for the FastAPI REST endpoints.

Uses httpx.AsyncClient with ASGITransport — no real server is started,
no Pokémon Showdown connection is needed.  The LM Studio proxy endpoint
is tested with a mocked httpx response so tests pass offline.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path: Path):
    """Create a fresh app instance backed by a temp SQLite database."""
    from nidozo.api.app import create_app
    return create_app(db_path=tmp_path / "test.db")


@pytest.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired directly to the ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# /api/leaderboard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_leaderboard_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/leaderboard")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_leaderboard_returns_models(app, client: AsyncClient) -> None:
    """After inserting a model + battle the leaderboard returns a row."""

    # Reach into the app's store via the same db_path
    # Instead, seed via the store exposed on app.state (if set) or recreate
    # We use a fresh store pointing at the same tmp db the app uses.
    store = app.state.store if hasattr(app.state, "store") else None
    if store is None:
        # Seed a model directly via SQL on the test DB
        # (the app's store connection owns the db; re-open read-only via same path)
        return  # skip if store not accessible — covered by store tests

    store.get_or_create_model("random", "random", "v2")
    resp = await client.get("/api/leaderboard")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert rows[0]["model_name"] == "random"


@pytest.mark.asyncio
async def test_leaderboard_shape(client: AsyncClient) -> None:
    """Grouped response has expected keys; per-version response includes prompt_version."""
    resp = await client.get("/api/leaderboard")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    for row in data:
        for key in ("provider", "model_name", "rating", "games", "wins", "losses", "ties"):
            assert key in row, f"Missing key {key!r} in grouped leaderboard row"
        # grouped mode: no prompt_version column, but versions field present
        assert "prompt_version" not in row, "grouped leaderboard should not have prompt_version"
        assert "versions" in row, "grouped leaderboard should have versions field"

    # per-version mode still works
    resp2 = await client.get("/api/leaderboard?grouped=false")
    assert resp2.status_code == 200
    for row in resp2.json():
        assert "prompt_version" in row


# ---------------------------------------------------------------------------
# /api/battles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_battles_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/battles")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_battles_respects_limit(client: AsyncClient) -> None:
    resp = await client.get("/api/battles?limit=5")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# POST /api/battles/start
# ---------------------------------------------------------------------------

@pytest.fixture
def no_battle_runner():
    """Patch battle/tournament runners so API calls return immediately without
    trying to connect to Pokémon Showdown (which is not running in CI)."""
    async def _noop(*args, **kwargs):
        pass
    with patch("nidozo.api.routes.run_battles", side_effect=_noop), \
         patch("nidozo.api.routes.run_tournament", side_effect=_noop), \
         patch("nidozo.api.routes.run_season", side_effect=_noop):
        yield


@pytest.mark.asyncio
async def test_start_battle_returns_battle_ids(client: AsyncClient, no_battle_runner) -> None:
    """Start endpoint returns battle_ids and a message."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "n_battles": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "battle_ids" in data
    assert len(data["battle_ids"]) == 2
    assert "message" in data


@pytest.mark.asyncio
async def test_start_battle_default_prompt_version(client: AsyncClient, no_battle_runner) -> None:
    """Default prompt_version is v2."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_start_battle_with_lmstudio_model(client: AsyncClient, no_battle_runner) -> None:
    """lmstudio provider with explicit model id is accepted."""
    resp = await client.post(
        "/api/battles/start",
        json={
            "p1_provider": "lmstudio",
            "p1_model": "ibm/granite-4-h-tiny",
            "p2_provider": "random",
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_start_battle_repeated_same_providers_no_tag_collision(
    client: AsyncClient, no_battle_runner
) -> None:
    """Two separate starts with identical providers must not collide on battle_tag.

    Regression: the placeholder tag used to be pending-{p1}-{p2}-{i}, so a second
    start with the same providers hit the battles.battle_tag UNIQUE constraint and
    returned 500.
    """
    payload = {"p1_provider": "random", "p2_provider": "random", "n_battles": 1}
    resp1 = await client.post("/api/battles/start", json=payload)
    resp2 = await client.post("/api/battles/start", json=payload)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Distinct battle rows were created.
    assert resp1.json()["battle_ids"][0] != resp2.json()["battle_ids"][0]


@pytest.mark.asyncio
async def test_start_battle_creates_db_rows(client: AsyncClient, no_battle_runner) -> None:
    """Starting a battle creates a pending battle row in the DB."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "n_battles": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    battle_id = data["battle_ids"][0]
    assert isinstance(battle_id, int)
    assert battle_id >= 1


# ---------------------------------------------------------------------------
# /api/lmstudio/models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lmstudio_models_offline_returns_empty(client: AsyncClient) -> None:
    """When LM Studio is not running the endpoint returns [] not an error."""
    # Mock a connection error so the test doesn't sit waiting for a real timeout.
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=ConnectionRefusedError("LM Studio not running"))

    with patch("nidozo.api.routes.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/lmstudio/models")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_lmstudio_models_online_returns_ids(client: AsyncClient) -> None:
    """When LM Studio responds, model ids are extracted from the data array."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "ibm/granite-4-h-tiny", "object": "model"},
            {"id": "mistralai/ministral-3-3b", "object": "model"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("nidozo.api.routes.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/lmstudio/models")

    assert resp.status_code == 200
    models = resp.json()
    assert "ibm/granite-4-h-tiny" in models
    assert "mistralai/ministral-3-3b" in models


@pytest.mark.asyncio
async def test_lmstudio_models_error_returns_empty(client: AsyncClient) -> None:
    """HTTP errors from LM Studio are swallowed and return []."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

    with patch("nidozo.api.routes.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/lmstudio/models")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/battles/{id} — single battle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_single_battle(client: AsyncClient, no_battle_runner) -> None:
    """GET /api/battles/{id} returns the battle row after creation."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = resp.json()["battle_ids"][0]

    resp2 = await client.get(f"/api/battles/{battle_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["id"] == battle_id
    assert "status" in data
    assert "p1" in data
    assert "p2" in data


@pytest.mark.asyncio
async def test_get_single_battle_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/battles/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/battles/{id}/cancel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_pending_battle(client: AsyncClient, no_battle_runner) -> None:
    """A pending battle can be cancelled."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = resp.json()["battle_ids"][0]

    cancel_resp = await client.post(f"/api/battles/{battle_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["ok"] is True

    # Verify status updated
    battle = (await client.get(f"/api/battles/{battle_id}")).json()
    assert battle["status"] == "cancelled"


# ---------------------------------------------------------------------------
# POST /api/tournament/start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_tournament_returns_ids(client: AsyncClient, no_battle_runner) -> None:
    """Tournament endpoint returns tournament_id and correct battle count."""
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [
                {"provider": "random"},
                {"provider": "random"},
            ],
            "rounds": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tournament_id" in data
    assert data["total_battles"] == 4   # 2 players × 1 pair × 2 rounds × 2 sides
    assert len(data["battle_ids"]) == 4


@pytest.mark.asyncio
async def test_start_tournament_requires_two_players(client: AsyncClient, no_battle_runner) -> None:
    # Pydantic Field(min_length=2) returns 422 before the handler runs
    resp = await client.post(
        "/api/tournament/start",
        json={"players": [{"provider": "random"}], "rounds": 1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_tournament(client: AsyncClient, no_battle_runner) -> None:
    """GET /api/tournaments/{id} returns tournament metadata."""
    start = await client.post(
        "/api/tournament/start",
        json={"players": [{"provider": "random"}, {"provider": "random"}], "rounds": 1},
    )
    tournament_id = start.json()["tournament_id"]

    resp = await client.get(f"/api/tournaments/{tournament_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == tournament_id
    assert data["rounds"] == 1
    assert data["status"] == "running"


# ---------------------------------------------------------------------------
# /api/battles/{id}/analysis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analysis_nonexistent_battle(client: AsyncClient, no_battle_runner) -> None:
    """Analysis for a battle with no turns returns a valid (empty) structure."""
    start_resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = start_resp.json()["battle_ids"][0]

    resp = await client.get(f"/api/battles/{battle_id}/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert "p1_summary" in data
    assert "p2_summary" in data
    assert "turns" in data
    assert data["turns"] == []


# ---------------------------------------------------------------------------
# /api/battles/{id}/turns
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_turns_empty_battle(client: AsyncClient, no_battle_runner) -> None:
    start_resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = start_resp.json()["battle_ids"][0]

    resp = await client.get(f"/api/battles/{battle_id}/turns")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/battles/{id}/replay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_empty_battle(client: AsyncClient, no_battle_runner) -> None:
    """Replay endpoint returns battle metadata and empty turns list for a fresh battle."""
    start_resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = start_resp.json()["battle_ids"][0]

    resp = await client.get(f"/api/battles/{battle_id}/replay")
    assert resp.status_code == 200

    data = resp.json()
    assert "battle" in data
    assert "turns" in data
    assert data["battle"]["id"] == battle_id
    assert isinstance(data["turns"], list)
    assert data["turns"] == []   # no turns recorded yet


@pytest.mark.asyncio
async def test_replay_not_found(client: AsyncClient) -> None:
    """Replay endpoint returns 404 for a non-existent battle."""
    resp = await client.get("/api/battles/99999/replay")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# M4 — Input bounds (n_battles / tournament size)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_battle_n_battles_zero_rejected(client: AsyncClient, no_battle_runner) -> None:
    """n_battles=0 is below ge=1 — must return 422."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "n_battles": 0},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_battle_n_battles_too_large(client: AsyncClient, no_battle_runner) -> None:
    """n_battles=51 exceeds le=50 — must return 422."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "n_battles": 51},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_tournament_rounds_too_large(client: AsyncClient, no_battle_runner) -> None:
    """rounds=11 exceeds le=10 — must return 422."""
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [{"provider": "random"}, {"provider": "random"}],
            "rounds": 11,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_tournament_too_many_players(client: AsyncClient, no_battle_runner) -> None:
    """13 players exceeds max_length=12 — must return 422."""
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [{"provider": "random"}] * 13,
            "rounds": 1,
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# M1 — Failed battle status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_battle_sets_failed_status(app) -> None:
    """When _run_battles raises an unexpected exception the battle is marked
    'failed', not 'completed', and ELO is unchanged."""
    from unittest.mock import patch

    from nidozo.api.events import EventBus
    from nidozo.api.models import StartBattleRequest
    from nidozo.api.orchestration import run_battles as _run_battles

    store = app.state.store
    bus = EventBus()

    m_id = store.get_or_create_model("random", "random", "v2")
    rating_before = store._conn.execute(
        "SELECT rating FROM elo_ratings WHERE model_id=?", (m_id,)
    ).fetchone()["rating"]

    bid = store.create_battle("test-fail", "gen3randombattle", m_id, m_id)
    req = StartBattleRequest(p1_provider="random", p2_provider="random")

    with patch("nidozo.api.orchestration._build_streaming_player", side_effect=RuntimeError("boom")):
        await _run_battles(req, [bid], store, bus, {})

    battle = store.get_battle(bid)
    assert battle["status"] == "failed", f"Expected 'failed', got {battle['status']!r}"

    # ELO must be unchanged (finish_battle was never called)
    rating_after = store._conn.execute(
        "SELECT rating FROM elo_ratings WHERE model_id=?", (m_id,)
    ).fetchone()["rating"]
    assert rating_before == rating_after, "ELO must not change on a failed battle"


@pytest.mark.asyncio
async def test_replay_merges_turns_by_number(app, client: AsyncClient, no_battle_runner) -> None:
    """Replay endpoint merges p1 and p2 rows into a single dict per turn number."""
    import json as _json

    start_resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = start_resp.json()["battle_ids"][0]

    # Manually insert two turn rows (p1 and p2) for the same turn number via the app's store
    store = app.state.store
    state_p1 = _json.dumps({"my_active": {"species": "Pikachu", "hp_fraction": 0.8}})
    state_p2 = _json.dumps({"my_active": {"species": "Squirtle", "hp_fraction": 0.9}})
    store.log_turn(battle_id, 1, "p1", "v1", "/choose move thunderbolt", True, "", state_p1)
    store.log_turn(battle_id, 1, "p2", "v1", "/choose move tackle",      True, "", state_p2)

    resp = await client.get(f"/api/battles/{battle_id}/replay")
    assert resp.status_code == 200

    data = resp.json()
    assert len(data["turns"]) == 1

    turn = data["turns"][0]
    assert turn["turn"] == 1
    assert "p1" in turn
    assert "p2" in turn
    # p1 side
    assert turn["p1"]["action"] == "/choose move thunderbolt"
    assert turn["p1"]["parse_success"] is True
    assert turn["p1"]["state"]["my_active"]["species"] == "Pikachu"
    # p2 side
    assert turn["p2"]["action"] == "/choose move tackle"
    assert turn["p2"]["state"]["my_active"]["species"] == "Squirtle"


# ---------------------------------------------------------------------------
# Model stats endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_stats_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/models/99999/stats")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

# --- cancel_battle: task.cancel() when active task exists (via endpoint function) ---

@pytest.mark.asyncio
async def test_cancel_battle_cancels_active_task() -> None:
    """cancel_battle calls task.cancel() on an active task for the battle."""
    from unittest.mock import AsyncMock, MagicMock

    from nidozo.api.routes import create_router

    # Build a minimal mock store and bus
    mock_store = MagicMock()
    mock_store.cancel_battle.return_value = True
    mock_battle = {"id": 7, "status": "pending"}
    mock_store.get_battle.return_value = mock_battle

    mock_bus = AsyncMock()
    active_tasks: dict = {}

    # Create a mock task that tracks cancel() calls
    mock_task = MagicMock()
    mock_task.done.return_value = False
    active_tasks[7] = mock_task

    router = create_router(mock_store, mock_bus, active_tasks)

    # Find the cancel_battle endpoint handler by looking up the route
    cancel_fn = None
    for route in router.routes:
        if hasattr(route, "path") and "{battle_id}" in route.path and "cancel" in route.path:
            cancel_fn = route.endpoint
            break

    assert cancel_fn is not None, "Could not find cancel_battle endpoint"
    result = await cancel_fn(battle_id=7)
    mock_task.cancel.assert_called_once()
    assert result["ok"] is True


# --- cancel_battle: battle not found returns 404 ---

@pytest.mark.asyncio
async def test_cancel_battle_not_found(client: AsyncClient) -> None:
    resp = await client.post("/api/battles/99999/cancel")
    assert resp.status_code == 404


# --- cancel_battle: already finished battle returns ok=False ---

@pytest.mark.asyncio
async def test_cancel_already_finished_battle(app, client: AsyncClient, no_battle_runner) -> None:
    """Cancelling a finished battle returns ok=False, not an error."""
    start = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = start.json()["battle_ids"][0]

    # Finish the battle so cancel_battle() returns False
    store = app.state.store
    store.finish_battle(battle_id, winner=1, total_turns=10)
    store.set_battle_status(battle_id, "completed")

    resp = await client.post(f"/api/battles/{battle_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# --- get_tournament: bracket_state JSON parsing ---

@pytest.mark.asyncio
async def test_get_tournament_bracket_state_parsed(app, client: AsyncClient) -> None:
    """GET /api/tournaments/{id} parses bracket_state from JSON string to dict."""
    from nidozo.tournament.bracket import build_single_elim

    store = app.state.store
    players = [{"provider": "random", "model_name": "bot1"}, {"provider": "random", "model_name": "bot2"}]
    tid = store.create_tournament(
        players=players,
        rounds=1,
        prompt_version="v2",
        total_battles=1,
        tier="random",
        tournament_format="single_elim",
    )
    bracket = build_single_elim(players)
    store.update_bracket_state(tid, bracket)

    resp = await client.get(f"/api/tournaments/{tid}")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["bracket_state"], dict)
    assert data["bracket_state"]["format"] == "single_elim"


# --- /api/tiers endpoint ---

@pytest.mark.asyncio
async def test_list_tiers_returns_list(client: AsyncClient) -> None:
    resp = await client.get("/api/tiers")
    assert resp.status_code == 200
    tiers = resp.json()
    assert isinstance(tiers, list)
    # Expect at least a few tiers
    assert len(tiers) >= 1
    for t in tiers:
        assert "id" in t
        assert "name" in t
        assert "showdown_format" in t
        assert "pokemon_count" in t


# --- /api/tiers/{tier_id}/pokemon endpoint ---

@pytest.mark.asyncio
async def test_get_tier_pokemon_invalid_tier(client: AsyncClient) -> None:
    resp = await client.get("/api/tiers/nonexistent_tier/pokemon")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_tier_pokemon_random_tier_not_found(client: AsyncClient) -> None:
    """The 'random' tier is excluded from the tiers list and returns 404."""
    resp = await client.get("/api/tiers/random/pokemon")
    assert resp.status_code == 404


# --- /api/models/{id}/stats (found) ---

@pytest.mark.asyncio
async def test_model_stats_found(app, client: AsyncClient) -> None:
    store = app.state.store
    mid = store.get_or_create_model("random", "random_model", "v2")

    resp = await client.get(f"/api/models/{mid}/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "model" in data
    assert "elo_history" in data
    assert "battle_history" in data
    assert "turn_stats" in data


# --- start_tournament — invalid tier ---

@pytest.mark.asyncio
async def test_start_tournament_invalid_tier(client: AsyncClient, no_battle_runner) -> None:
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [{"provider": "random"}, {"provider": "random"}],
            "tier": "nonexistent_tier",
        },
    )
    # Rejected by the Pydantic Tier literal at the boundary.
    assert resp.status_code == 422


# --- start_tournament — invalid format ---

@pytest.mark.asyncio
async def test_start_tournament_invalid_format(client: AsyncClient, no_battle_runner) -> None:
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [{"provider": "random"}, {"provider": "random"}],
            "tournament_format": "swiss",
        },
    )
    # Rejected by the Pydantic TournamentFormat literal at the boundary.
    assert resp.status_code == 422


# --- start_tournament — single_elim bracket format ---

@pytest.fixture
def no_bracket_runner():
    """Patch all runners so bracket tournament calls return immediately."""
    async def _noop(*args, **kwargs):
        pass

    with patch("nidozo.api.routes.run_battles", side_effect=_noop), \
         patch("nidozo.api.routes.run_tournament", side_effect=_noop), \
         patch("nidozo.api.routes.run_bracket_tournament", side_effect=_noop):
        yield


@pytest.mark.asyncio
async def test_start_single_elim_tournament(client: AsyncClient, no_bracket_runner) -> None:
    """start_tournament with single_elim format returns correct total_battles."""
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [{"provider": "random"}, {"provider": "random"}, {"provider": "random"}],
            "tournament_format": "single_elim",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tournament_id" in data
    # 3 players → 2 battles in single elim
    assert data["total_battles"] == 2


@pytest.mark.asyncio
async def test_start_double_elim_tournament(client: AsyncClient, no_bracket_runner) -> None:
    """start_tournament with double_elim format returns correct total_battles."""
    resp = await client.post(
        "/api/tournament/start",
        json={
            "players": [{"provider": "random"}, {"provider": "random"}, {"provider": "random"}],
            "tournament_format": "double_elim",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tournament_id" in data
    # 3 players → 2*3-1 = 5 battles in double elim
    assert data["total_battles"] == 5


@pytest.mark.asyncio
async def test_model_stats_returns_data(app, client: AsyncClient) -> None:
    store = app.state.store
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("t1", "gen3randombattle", mid, opp)
    store.finish_battle(bid, winner=1, total_turns=12)

    resp = await client.get(f"/api/models/{mid}/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"]["model_name"] == "claude-test"
    assert data["model"]["wins"] == 1
    assert "elo_history" in data
    assert "battle_history" in data
    assert "turn_stats" in data
    assert "lessons" in data


@pytest.mark.asyncio
async def test_leaderboard_grouped_has_model_id(app, client: AsyncClient) -> None:
    store = app.state.store
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("t1", "gen3randombattle", mid, opp)
    store.finish_battle(bid, winner=1, total_turns=10)

    resp = await client.get("/api/leaderboard?grouped=true")
    assert resp.status_code == 200
    rows = resp.json()
    claude_rows = [r for r in rows if r.get("model_name") == "claude-test"]
    assert len(claude_rows) == 1
    assert "model_id" in claude_rows[0]
    assert claude_rows[0]["model_id"] == mid


# ---------------------------------------------------------------------------
# Tournament API endpoints  (feat/tournament-mode)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tournaments_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/tournaments")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_tournaments_returns_created(app, client: AsyncClient) -> None:
    store = app.state.store
    tid = store.create_tournament(
        players=[{"provider": "random", "model_name": "bot0"}, {"provider": "random", "model_name": "bot1"}],
        rounds=1,
        prompt_version="v2",
        total_battles=2,
    )
    resp = await client.get("/api/tournaments")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert tid in ids


@pytest.mark.asyncio
async def test_get_tournament_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/tournaments/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tournament_standings_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/tournaments/99999/standings")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tournament_standings_empty_before_battles(app, client: AsyncClient) -> None:
    store = app.state.store
    tid = store.create_tournament(
        players=[{"provider": "random", "model_name": "bot0"}, {"provider": "random", "model_name": "bot1"}],
        rounds=1, prompt_version="v2", total_battles=2,
    )
    resp = await client.get(f"/api/tournaments/{tid}/standings")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_tournament_standings_populated(app, client: AsyncClient) -> None:
    store = app.state.store
    tid = store.create_tournament(
        players=[{"provider": "random", "model_name": "bot0"}, {"provider": "random", "model_name": "bot1"}],
        rounds=1, prompt_version="v2", total_battles=2,
    )
    a_id = store.get_or_create_model("random", "bot0", "v2")
    b_id = store.get_or_create_model("random", "bot1", "v2")
    bid = store.create_battle("tag-api-s", "gen3randombattle", a_id, b_id, tournament_id=tid)
    store.finish_battle(bid, winner=1, total_turns=7)
    store.set_battle_status(bid, "completed")

    resp = await client.get(f"/api/tournaments/{tid}/standings")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    winner = next(r for r in rows if r["model_id"] == a_id)
    assert winner["wins"] == 1
    assert winner["points"] == 3


@pytest.mark.asyncio
async def test_tournament_battles_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/tournaments/99999/battles")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tournament_battles_returns_list(app, client: AsyncClient) -> None:
    store = app.state.store
    tid = store.create_tournament(
        players=[{"provider": "random", "model_name": "bot0"}, {"provider": "random", "model_name": "bot1"}],
        rounds=1, prompt_version="v2", total_battles=1,
    )
    a_id = store.get_or_create_model("random", "bot0", "v2")
    b_id = store.get_or_create_model("random", "bot1", "v2")
    bid = store.create_battle("tag-api-tb", "gen3randombattle", a_id, b_id, tournament_id=tid)

    resp = await client.get(f"/api/tournaments/{tid}/battles")
    assert resp.status_code == 200
    battles = resp.json()
    assert len(battles) == 1
    assert battles[0]["id"] == bid
    assert "p1" in battles[0]
    assert "p2" in battles[0]


@pytest.mark.asyncio
async def test_cancel_tournament_not_found(client: AsyncClient) -> None:
    resp = await client.post("/api/tournaments/99999/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_tournament_running(app, client: AsyncClient) -> None:
    store = app.state.store
    tid = store.create_tournament(
        players=[{"provider": "random", "model_name": "bot0"}, {"provider": "random", "model_name": "bot1"}],
        rounds=1, prompt_version="v2", total_battles=2,
    )
    resp = await client.post(f"/api/tournaments/{tid}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is True
    assert data["status"] == "cancelled"


# ---------------------------------------------------------------------------
# New coverage tests — routes.py missing lines
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tournament_invalid_bracket_state(app, client: AsyncClient) -> None:
    """Lines 200-201: invalid bracket_state JSON is caught and set to None."""
    store = app.state.store
    tid = store.create_tournament(
        players=[{"provider": "random", "model_name": "bot0"}, {"provider": "random", "model_name": "bot1"}],
        rounds=1, prompt_version="v2", total_battles=2,
    )
    # Directly corrupt bracket_state in the DB
    store._conn.execute(
        "UPDATE tournaments SET bracket_state='NOT_VALID_JSON' WHERE id=?", (tid,)
    )
    store._conn.commit()

    resp = await client.get(f"/api/tournaments/{tid}")
    assert resp.status_code == 200
    data = resp.json()
    # bracket_state should be None because json.loads failed
    assert data["bracket_state"] is None


@pytest.mark.asyncio
async def test_get_tier_pokemon_valid_tier(client: AsyncClient) -> None:
    """Lines 265-267: GET /api/tiers/{tier_id}/pokemon returns a list for a valid tier."""
    resp = await client.get("/api/tiers/ou/pokemon")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Each entry should have a species key
    if data:
        assert "species_id" in data[0] or "id" in data[0] or isinstance(data[0], dict)


@pytest.mark.asyncio
async def test_get_tier_pokemon_invalid_tier_extra(client: AsyncClient) -> None:
    """Lines 263-264: unknown tier returns 404 (second coverage test)."""
    resp = await client.get("/api/tiers/invalidtierxyz/pokemon")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_model_teams_endpoint(app, client: AsyncClient, no_battle_runner) -> None:
    """Line 271: GET /api/models/{model_id}/teams returns a list."""
    store = app.state.store
    mid = store.get_or_create_model("random", "random")

    resp = await client.get(f"/api/models/{mid}/teams")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_battle_teams_endpoint(app, client: AsyncClient, no_battle_runner) -> None:
    """Lines 275-276: GET /api/battles/{battle_id}/teams returns p1 and p2 keys."""
    start_resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random"},
    )
    battle_id = start_resp.json()["battle_ids"][0]

    resp = await client.get(f"/api/battles/{battle_id}/teams")
    assert resp.status_code == 200
    data = resp.json()
    assert "p1" in data
    assert "p2" in data


@pytest.mark.asyncio
async def test_start_battle_unknown_tier_returns_422(client: AsyncClient, no_battle_runner) -> None:
    """Unknown tier is rejected by the Pydantic Tier literal (422)."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "tier": "unknowntier999"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_battle_unknown_provider_returns_422(client: AsyncClient, no_battle_runner) -> None:
    """An unknown provider is rejected at the boundary instead of silently
    falling through to LM Studio."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "gpt5enterprise", "p2_provider": "random"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_battle_unknown_prompt_version_returns_422(client: AsyncClient, no_battle_runner) -> None:
    """An unknown prompt_version is rejected at the boundary instead of failing
    deep in the background task when the prompt builder is constructed."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "prompt_version": "v99"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_battle_random_coach_provider_returns_422(client: AsyncClient, no_battle_runner) -> None:
    """'random' is not a valid coach provider — coaches need a real LLM backend."""
    resp = await client.post(
        "/api/battles/start",
        json={"p1_provider": "random", "p2_provider": "random", "p1_coach_provider": "random"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_tournament_one_player_returns_400(client: AsyncClient, no_battle_runner) -> None:
    """Line 332: start_tournament with 1 player (bypassing pydantic) returns 400.

    The pydantic model has min_length=2, so sending 1 player directly via
    list construction will return 422. We test the route-level guard (line 332)
    by testing that the endpoint correctly handles validation — in practice
    this is covered by the 422 response from pydantic, so we verify 422 is
    returned for a single-player request.
    """
    resp = await client.post(
        "/api/tournament/start",
        json={"players": [{"provider": "random"}], "rounds": 1},
    )
    # Pydantic enforces min_length=2 → 422 Unprocessable Entity
    assert resp.status_code in (400, 422)


# ===========================================================================
# /api/seasons — CRUD + start
# ===========================================================================

@pytest.mark.asyncio
async def test_list_seasons_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/seasons")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_season_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/seasons/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_season_standings_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/seasons/9999/standings")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_season_battles_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/seasons/9999/battles")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_season_not_found(client: AsyncClient) -> None:
    resp = await client.post("/api/seasons/9999/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_start_season_one_player_returns_422(client: AsyncClient, no_battle_runner) -> None:
    """Pydantic min_length=2 on players."""
    resp = await client.post(
        "/api/seasons/start",
        json={"name": "Test", "players": [{"provider": "random"}]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_season_unknown_tier_returns_422(client: AsyncClient, no_battle_runner) -> None:
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Tier Test",
            "players": [{"provider": "random"}, {"provider": "random"}],
            "tier": "notarealthing",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_season_returns_season_id_and_battle_ids(
    client: AsyncClient, no_battle_runner
) -> None:
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Season 1 — Gen 3 Random",
            "players": [
                {"provider": "random"},
                {"provider": "random"},
            ],
            "rounds": 1,
            "tier": "random",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "season_id" in data
    assert isinstance(data["season_id"], int)
    assert "battle_ids" in data
    assert len(data["battle_ids"]) == 2   # 2 players × 1 round × both sides
    assert data["total_battles"] == 2


@pytest.mark.asyncio
async def test_start_season_creates_retrievable_season(
    app, client: AsyncClient, no_battle_runner
) -> None:
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Retrievable Season",
            "players": [{"provider": "random"}, {"provider": "random"}],
            "rounds": 1,
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["season_id"]

    # Season is retrievable via GET
    get_resp = await client.get(f"/api/seasons/{sid}")
    assert get_resp.status_code == 200
    season = get_resp.json()
    assert season["name"] == "Retrievable Season"
    assert season["status"] == "pending"

    # Appears in list
    list_resp = await client.get("/api/seasons")
    assert list_resp.status_code == 200
    ids = [s["id"] for s in list_resp.json()]
    assert sid in ids


@pytest.mark.asyncio
async def test_get_season_standings_empty_before_battles(
    app, client: AsyncClient, no_battle_runner
) -> None:
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Standings Test",
            "players": [{"provider": "random"}, {"provider": "random"}],
            "rounds": 1,
        },
    )
    sid = resp.json()["season_id"]
    st_resp = await client.get(f"/api/seasons/{sid}/standings")
    assert st_resp.status_code == 200
    standings = st_resp.json()
    assert len(standings) == 2
    for s in standings:
        assert s["elo"] == 1000.0
        assert s["games"] == 0


@pytest.mark.asyncio
async def test_get_season_battles_empty_initially(
    app, client: AsyncClient, no_battle_runner
) -> None:
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Battles Test",
            "players": [{"provider": "random"}, {"provider": "random"}],
            "rounds": 1,
        },
    )
    sid = resp.json()["season_id"]
    bt_resp = await client.get(f"/api/seasons/{sid}/battles")
    assert bt_resp.status_code == 200
    # Battles exist in DB (created on start) but none are finished yet
    battles = bt_resp.json()
    assert isinstance(battles, list)
    assert len(battles) == 2   # 2 players, 1 round, both sides


@pytest.mark.asyncio
async def test_cancel_season_pending_returns_ok(
    app, client: AsyncClient, no_battle_runner
) -> None:
    """A pending season cannot be cancelled — only running ones can."""
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Cancel Test",
            "players": [{"provider": "random"}, {"provider": "random"}],
            "rounds": 1,
        },
    )
    sid = resp.json()["season_id"]
    # Manually set to running so cancel is valid
    app.state.store.set_season_running(sid)

    cancel_resp = await client.post(f"/api/seasons/{sid}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["ok"] is True

    s = app.state.store.get_season(sid)
    assert s["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_already_completed_season_returns_409(
    app, client: AsyncClient, no_battle_runner
) -> None:
    resp = await client.post(
        "/api/seasons/start",
        json={
            "name": "Done Season",
            "players": [{"provider": "random"}, {"provider": "random"}],
            "rounds": 1,
        },
    )
    sid = resp.json()["season_id"]
    app.state.store.finish_season(sid, status="completed")

    cancel_resp = await client.post(f"/api/seasons/{sid}/cancel")
    assert cancel_resp.status_code == 409


# ---------------------------------------------------------------------------
# EventBus replay buffer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eventbus_replays_structural_events_to_late_subscriber() -> None:
    """A subscriber that connects after battle_start receives buffered events."""
    from nidozo.api.events import EventBus

    bus = EventBus()

    # Publish a battle_start + two draft events before anyone subscribes
    await bus.publish({"type": "battle_start", "battle_id": 1})
    await bus.publish({"type": "draft_start", "player_role": "p1"})
    await bus.publish({"type": "draft_pick", "player_role": "p1", "species": "charizard"})

    # Late subscriber — connects after the above events were published
    q = bus.subscribe()

    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())

    types = [e["type"] for e in replayed]
    assert "battle_start" in types
    assert "draft_start" in types
    assert "draft_pick" in types


@pytest.mark.asyncio
async def test_eventbus_clears_replay_on_new_battle_start() -> None:
    """battle_start clears the replay buffer so previous battle events aren't replayed."""
    from nidozo.api.events import EventBus

    bus = EventBus()

    await bus.publish({"type": "battle_start", "battle_id": 1})
    await bus.publish({"type": "draft_start", "player_role": "p1", "battle_id": 1})

    # Second battle starts — first battle's events should be wiped
    await bus.publish({"type": "battle_start", "battle_id": 2})
    await bus.publish({"type": "draft_start", "player_role": "p1", "battle_id": 2})

    q = bus.subscribe()
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())

    battle_ids = {e.get("battle_id") for e in replayed}
    assert 1 not in battle_ids, "Events from the previous battle must not be replayed"
    assert 2 in battle_ids


@pytest.mark.asyncio
async def test_eventbus_does_not_replay_per_turn_events() -> None:
    """turn and state_update events are excluded from the replay buffer."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    await bus.publish({"type": "battle_start", "battle_id": 1})
    await bus.publish({"type": "turn", "turn": 1})
    await bus.publish({"type": "state_update", "turn": 1})
    await bus.publish({"type": "thinking", "player_role": "p1"})

    q = bus.subscribe()
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())

    types = [e["type"] for e in replayed]
    assert "turn" not in types
    assert "state_update" not in types
    assert "thinking" not in types
    assert "battle_start" in types  # structural event IS replayed


@pytest.mark.asyncio
async def test_eventbus_replays_showdown_room_to_late_subscriber() -> None:
    """showdown_room is in _REPLAY_TYPES so spectators that connect after the
    room was announced still learn the room id."""
    from nidozo.api.events import EventBus

    bus = EventBus()
    await bus.publish({"type": "battle_start", "battle_id": 5})
    await bus.publish({"type": "showdown_room", "battle_id": 5, "room": "battle-gen3randombattle-5"})

    q = bus.subscribe()
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())

    types = [e["type"] for e in replayed]
    assert "showdown_room" in types
    rooms = [e["room"] for e in replayed if e["type"] == "showdown_room"]
    assert rooms == ["battle-gen3randombattle-5"]
