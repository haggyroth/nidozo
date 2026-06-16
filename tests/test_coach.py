"""Tests for CoachAgent and the coach integration in LLMPlayer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nidozo.llm.coach import CoachAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend(response: str | None = "Switch to Swampert for the Electric immunity.") -> MagicMock:
    backend = MagicMock()
    backend.complete = AsyncMock(return_value=response)
    return backend


def _minimal_state() -> dict:
    return {
        "turn": 5,
        "format": "gen3randombattle",
        "weather": None,
        "fields": [],
        "my_side_conditions": [],
        "opponent_side_conditions": [],
        "my_active": {
            "species": "pikachu", "types": ["ELECTRIC"],
            "hp_fraction": 0.8, "status": None, "boosts": {},
            "item": None, "ability": "static",
            "moves": {
                "thunderbolt": {"id": "thunderbolt", "type": "ELECTRIC",
                                "category": "SPECIAL", "base_power": 90, "pp": 12, "max_pp": 16},
            },
        },
        "opponent_active": {
            "species": "golem", "types": ["ROCK", "GROUND"],
            "hp_fraction": 0.9, "status": None, "boosts": {},
            "item": None, "ability": None,
            "revealed_moves": {},
        },
        "my_team": [],
        "opponent_team": [],
        "available_moves": [{"id": "thunderbolt", "type": "ELECTRIC", "category": "SPECIAL",
                             "base_power": 90, "pp": 12, "max_pp": 16}],
        "available_switches": [],
        "force_switch": False,
        "heuristics": {
            "move_scores": [],
            "switch_scores": [],
            "battle_context": {},
        },
    }


# ---------------------------------------------------------------------------
# CoachAgent unit tests
# ---------------------------------------------------------------------------

class TestCoachAgent:
    @pytest.mark.asyncio
    async def test_analyze_returns_backend_response(self) -> None:
        backend = _make_backend("Stay in — Thunderbolt is immune, use Surf instead.")
        coach = CoachAgent(backend=backend)
        advice = await coach.analyze(_minimal_state())
        assert advice == "Stay in — Thunderbolt is immune, use Surf instead."
        backend.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_strips_whitespace(self) -> None:
        backend = _make_backend("  Switch now.  ")
        coach = CoachAgent(backend=backend)
        advice = await coach.analyze(_minimal_state())
        assert advice == "Switch now."

    @pytest.mark.asyncio
    async def test_analyze_returns_none_on_empty_response(self) -> None:
        backend = _make_backend("")
        coach = CoachAgent(backend=backend)
        advice = await coach.analyze(_minimal_state())
        assert advice is None

    @pytest.mark.asyncio
    async def test_analyze_returns_none_on_backend_error(self) -> None:
        backend = MagicMock()
        backend.complete = AsyncMock(side_effect=RuntimeError("timeout"))
        coach = CoachAgent(backend=backend)
        advice = await coach.analyze(_minimal_state())
        assert advice is None

    @pytest.mark.asyncio
    async def test_analyze_includes_system_and_user_messages(self) -> None:
        backend = _make_backend("Advice here.")
        coach = CoachAgent(backend=backend)
        await coach.analyze(_minimal_state())
        messages = backend.complete.call_args[0][0]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_system_prompt_mentions_coach_role(self) -> None:
        backend = _make_backend("ok")
        coach = CoachAgent(backend=backend)
        await coach.analyze(_minimal_state())
        system_content = backend.complete.call_args[0][0][0]["content"]
        assert "coach" in system_content.lower()
        assert "analysis" in system_content.lower() or "advise" in system_content.lower()

    @pytest.mark.asyncio
    async def test_turn_message_includes_species(self) -> None:
        backend = _make_backend("ok")
        coach = CoachAgent(backend=backend)
        await coach.analyze(_minimal_state())
        turn_content = backend.complete.call_args[0][0][1]["content"]
        assert "pikachu" in turn_content.lower() or "Pikachu" in turn_content


# ---------------------------------------------------------------------------
# PromptBuilder coach injection tests
# ---------------------------------------------------------------------------

class TestPromptBuilderCoachInjection:
    def test_no_coach_returns_two_messages(self) -> None:
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v2")
        msgs = pb.build_messages(_minimal_state(), coach_advice=None)
        assert len(msgs) == 2

    def test_coach_advice_appended_to_user_message(self) -> None:
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v2")
        advice = "Switch to Swampert for the Electric immunity."
        msgs = pb.build_messages(_minimal_state(), coach_advice=advice)
        assert len(msgs) == 2
        user_content = msgs[1]["content"]
        assert "COACH ANALYSIS" in user_content
        assert advice in user_content

    def test_coach_advice_comes_after_battle_state(self) -> None:
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v2")
        advice = "Stay in and attack."
        msgs = pb.build_messages(_minimal_state(), coach_advice=advice)
        user_content = msgs[1]["content"]
        # Battle state content should appear before coach block
        assert user_content.index("HEURISTIC") < user_content.index("COACH ANALYSIS")

    def test_coach_advice_ends_with_decision_prompt(self) -> None:
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v2")
        msgs = pb.build_messages(_minimal_state(), coach_advice="Do something.")
        user_content = msgs[1]["content"]
        assert "chosen action" in user_content.lower()


# ---------------------------------------------------------------------------
# LLMPlayer with coach — integration
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_battle() -> MagicMock:
    battle = MagicMock()
    battle.turn = 4
    battle.battle_tag = "gen3randombattle-coach-test"
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
    m1 = MagicMock()
    m1.id = "surf"
    battle.available_moves = [m1]
    battle.available_switches = []
    return battle


@pytest.fixture
def player_backend() -> MagicMock:
    backend = MagicMock()
    backend.complete = AsyncMock(
        return_value='{"reasoning":"surf","action_type":"move","identifier":"surf"}'
    )
    return backend


@pytest.mark.asyncio
async def test_player_calls_coach_before_acting(mock_battle: MagicMock, player_backend: MagicMock) -> None:
    """Coach.analyze() should be called once before backend.complete()."""
    from nidozo.battle.llm_player import LLMPlayer

    coach = MagicMock()
    coach.analyze = AsyncMock(return_value="Use Surf — it's super effective.")

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value=_minimal_state()),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = player_backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = MagicMock(
            return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        )
        player._store = None
        player._battle_id = None
        player._player_role = "p1"
        player._on_thinking = None
        player._lessons = []
        player._coach = coach
        player._turn_timeout = None

        await player.choose_move(mock_battle)

    coach.analyze.assert_called_once()


@pytest.mark.asyncio
async def test_player_injects_coach_advice_into_prompt(mock_battle: MagicMock, player_backend: MagicMock) -> None:
    """build_messages must receive the coach_advice from the coach."""
    from nidozo.battle.llm_player import LLMPlayer

    coach = MagicMock()
    coach.analyze = AsyncMock(return_value="Switch to Blastoise.")

    build_messages_mock = MagicMock(
        return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value=_minimal_state()),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = player_backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = build_messages_mock
        player._store = None
        player._battle_id = None
        player._player_role = "p1"
        player._on_thinking = None
        player._lessons = []
        player._coach = coach
        player._turn_timeout = None

        await player.choose_move(mock_battle)

    call_kwargs = build_messages_mock.call_args[1]
    assert call_kwargs.get("coach_advice") == "Switch to Blastoise."


@pytest.mark.asyncio
async def test_player_acts_normally_when_no_coach(mock_battle: MagicMock, player_backend: MagicMock) -> None:
    """Without a coach, build_messages is called with coach_advice=None."""
    from nidozo.battle.llm_player import LLMPlayer

    build_messages_mock = MagicMock(
        return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value=_minimal_state()),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = player_backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = build_messages_mock
        player._store = None
        player._battle_id = None
        player._player_role = "p1"
        player._on_thinking = None
        player._lessons = []
        player._coach = None
        player._turn_timeout = None

        await player.choose_move(mock_battle)

    call_kwargs = build_messages_mock.call_args[1]
    assert call_kwargs.get("coach_advice") is None


@pytest.mark.asyncio
async def test_thinking_events_include_agent_field(mock_battle: MagicMock, player_backend: MagicMock) -> None:
    """Thinking events should carry agent='coach' then agent='player'."""
    from nidozo.battle.llm_player import LLMPlayer

    coach = MagicMock()
    coach.analyze = AsyncMock(return_value="Go for it.")
    emitted: list[dict] = []

    async def capture(event: dict) -> None:
        emitted.append(event)

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value=_minimal_state()),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = player_backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = MagicMock(
            return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        )
        player._store = None
        player._battle_id = None
        player._player_role = "p1"
        player._on_thinking = capture
        player._lessons = []
        player._coach = coach
        player._turn_timeout = None

        await player.choose_move(mock_battle)

    agents = [e["agent"] for e in emitted]
    assert "coach" in agents
    assert "player" in agents
    assert agents.index("coach") < agents.index("player")


@pytest.mark.asyncio
async def test_player_acts_even_if_coach_fails(mock_battle: MagicMock, player_backend: MagicMock) -> None:
    """If coach.analyze() returns None, player acts normally with coach_advice=None."""
    from nidozo.battle.llm_player import LLMPlayer

    coach = MagicMock()
    coach.analyze = AsyncMock(return_value=None)  # coach silently fails

    build_messages_mock = MagicMock(
        return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value=_minimal_state()),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = player_backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = build_messages_mock
        player._store = None
        player._battle_id = None
        player._player_role = "p1"
        player._on_thinking = None
        player._lessons = []
        player._coach = coach
        player._turn_timeout = None

        await player.choose_move(mock_battle)

    call_kwargs = build_messages_mock.call_args[1]
    assert call_kwargs.get("coach_advice") is None


@pytest.mark.asyncio
async def test_coach_advice_stored_in_db(mock_battle: MagicMock, player_backend: MagicMock) -> None:
    """coach_advice must be passed to store.log_turn()."""
    from nidozo.battle.llm_player import LLMPlayer

    coach = MagicMock()
    coach.analyze = AsyncMock(return_value="Attack now.")
    store = MagicMock()
    store.log_turn = MagicMock()

    with (
        patch("nidozo.battle.llm_player.serialize_battle", return_value=_minimal_state()),
        patch("poke_env.player.Player.__init__", return_value=None),
        patch("nidozo.battle.llm_player.parse_action", return_value=MagicMock()),
    ):
        player = LLMPlayer.__new__(LLMPlayer)
        player._backend = player_backend
        player._prompt_builder = MagicMock()
        player._prompt_builder.version = "v2"
        player._prompt_builder.build_messages = MagicMock(
            return_value=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        )
        player._store = store
        player._battle_id = 42
        player._player_role = "p1"
        player._on_thinking = None
        player._lessons = []
        player._coach = coach
        player._turn_timeout = None

        await player.choose_move(mock_battle)

    store.log_turn.assert_called_once()
    kwargs = store.log_turn.call_args[1]
    assert kwargs.get("coach_advice") == "Attack now."


class TestSchemaV7Migration:
    def test_v7_adds_coach_advice_column(self) -> None:
        """Migrating from v6 adds coach_advice TEXT to turns."""
        import sqlite3

        from nidozo.db.schema import migrate

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)

        # Verify coach_advice column exists
        cols = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(turns)").fetchall()
        ]
        assert "coach_advice" in cols

    def test_v7_coach_advice_nullable(self) -> None:
        """coach_advice should default to NULL for existing rows."""
        import sqlite3

        from nidozo.db.schema import migrate

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)

        # Insert a minimal model + battle + turn row without coach_advice
        conn.execute("INSERT INTO models (id, provider, model_name, prompt_version) VALUES (1,'random','random','v2')")
        conn.execute("INSERT INTO elo_ratings (model_id, rating, games) VALUES (1,1000,0)")
        conn.execute(
            "INSERT INTO battles (id, battle_tag, format, p1_model_id, p2_model_id) VALUES (1,'tag','gen3randombattle',1,1)"
        )
        conn.execute(
            "INSERT INTO turns (battle_id, turn_number, player_role, prompt_version, parse_success) VALUES (1,1,'p1','v2',1)"
        )
        row = conn.execute("SELECT coach_advice FROM turns WHERE battle_id=1").fetchone()
        assert row["coach_advice"] is None
