"""
Tests for the battle state serializer.

These tests enforce the hidden-information contract:
  - Own Pokémon expose full detail.
  - Opponent Pokémon expose ONLY legitimately revealed information.

We use mocks to construct precise battle states without needing a live server.
"""

from unittest.mock import MagicMock, PropertyMock, patch

from nidozo.battle.serializer import (
    _serialize_opponent_pokemon,
    _serialize_own_pokemon,
    serialize_battle,
)

# ---------------------------------------------------------------------------
# Helpers to build mock Pokémon and Move objects
# ---------------------------------------------------------------------------

def _mock_move(id_: str, base_power: int = 80, pp: int = 15, max_pp: int = 15) -> MagicMock:
    move = MagicMock()
    move.id = id_
    move.type.name = "NORMAL"
    move.category.name = "PHYSICAL"
    move.base_power = base_power
    move.accuracy = 1.0
    move.current_pp = pp
    move.max_pp = max_pp
    move.priority = 0
    return move


def _mock_own_pokemon(
    species: str = "pikachu",
    hp_fraction: float = 1.0,
    status=None,
    moves: dict | None = None,
    item: str | None = "lightball",
    ability: str = "static",
) -> MagicMock:
    mon = MagicMock()
    mon.species = species
    mon.level = 50
    mon.types = [MagicMock(name="ELECTRIC")]
    type(mon).types = PropertyMock(return_value=[MagicMock(name="ELECTRIC")])
    mon.current_hp_fraction = hp_fraction
    mon.fainted = hp_fraction == 0.0
    mon.status = status
    mon.boosts = {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0, "accuracy": 0, "evasion": 0}
    mon.item = item
    mon.ability = ability
    mon.base_stats = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}
    mon.moves = moves or {"thunderbolt": _mock_move("thunderbolt")}
    mon.effects = {}
    mon.active = True
    return mon


def _mock_opponent_pokemon(
    species: str = "charmander",
    hp_fraction: float = 0.75,
    status=None,
    revealed_moves: dict | None = None,
) -> MagicMock:
    """Opponent mon: moves dict contains only moves actually used in battle."""
    mon = MagicMock()
    mon.species = species
    mon.level = 50
    type(mon).types = PropertyMock(return_value=[MagicMock(name="FIRE")])
    mon.current_hp_fraction = hp_fraction
    mon.fainted = False
    mon.status = status
    mon.boosts = {}
    mon.item = None
    mon.ability = None
    mon.base_stats = {"hp": 39, "atk": 52, "def": 43, "spa": 60, "spd": 50, "spe": 65}
    # poke-env only populates moves for opponent when they've been used
    mon.moves = revealed_moves or {}
    mon.active = True
    return mon


# ---------------------------------------------------------------------------
# Own Pokémon serialization
# ---------------------------------------------------------------------------

def test_own_pokemon_includes_moves_with_pp() -> None:
    move = _mock_move("thunderbolt", pp=10, max_pp=15)
    mon = _mock_own_pokemon(moves={"thunderbolt": move})
    result = _serialize_own_pokemon(mon)
    assert "moves" in result
    assert "thunderbolt" in result["moves"]
    assert result["moves"]["thunderbolt"]["pp"] == 10
    assert result["moves"]["thunderbolt"]["max_pp"] == 15


def test_own_pokemon_includes_item_and_ability() -> None:
    mon = _mock_own_pokemon(item="choiceband", ability="intimidate")
    result = _serialize_own_pokemon(mon)
    assert result["item"] == "choiceband"
    assert result["ability"] == "intimidate"


def test_own_pokemon_includes_exact_hp_fraction() -> None:
    mon = _mock_own_pokemon(hp_fraction=0.333)
    result = _serialize_own_pokemon(mon)
    assert result["hp_fraction"] == 0.333


def test_own_pokemon_includes_base_stats() -> None:
    mon = _mock_own_pokemon()
    result = _serialize_own_pokemon(mon)
    assert "base_stats" in result
    assert result["base_stats"]["spe"] == 90


def test_own_pokemon_none_returns_none() -> None:
    assert _serialize_own_pokemon(None) is None


# ---------------------------------------------------------------------------
# Opponent Pokémon — hidden-information enforcement
# ---------------------------------------------------------------------------

def test_opponent_pokemon_has_no_pp_on_moves() -> None:
    """Revealed moves must NOT include PP (we don't track opponent PP)."""
    used_move = _mock_move("flamethrower")
    mon = _mock_opponent_pokemon(revealed_moves={"flamethrower": used_move})
    result = _serialize_opponent_pokemon(mon)
    assert "flamethrower" in result["revealed_moves"]
    assert "pp" not in result["revealed_moves"]["flamethrower"]
    assert "max_pp" not in result["revealed_moves"]["flamethrower"]


def test_opponent_pokemon_no_unrevealed_moves() -> None:
    """If the opponent has used zero moves, revealed_moves must be empty."""
    mon = _mock_opponent_pokemon(revealed_moves={})
    result = _serialize_opponent_pokemon(mon)
    assert result["revealed_moves"] == {}


def test_opponent_pokemon_has_no_exact_hp() -> None:
    """Opponent HP must be a fraction (%), not an exact integer."""
    mon = _mock_opponent_pokemon(hp_fraction=0.6)
    result = _serialize_opponent_pokemon(mon)
    assert "hp_fraction" in result
    assert isinstance(result["hp_fraction"], float)
    # Must NOT have current_hp (exact value)
    assert "current_hp" not in result


def test_opponent_pokemon_includes_base_stats() -> None:
    """Base stats are Pokédex-public knowledge (not hidden battle info), so they
    are included in the opponent serialization for tooltip display in the UI."""
    mon = _mock_opponent_pokemon()
    result = _serialize_opponent_pokemon(mon)
    assert "base_stats" in result
    assert result["base_stats"]["hp"] == 39
    assert result["base_stats"]["spe"] == 65


def test_opponent_pokemon_unrevealed_item_is_none() -> None:
    """Item must not appear unless poke-env has registered it as revealed."""
    mon = _mock_opponent_pokemon()
    mon.item = None
    result = _serialize_opponent_pokemon(mon)
    assert result["item"] is None


def test_opponent_pokemon_revealed_item_appears() -> None:
    mon = _mock_opponent_pokemon()
    mon.item = "choicescarf"
    result = _serialize_opponent_pokemon(mon)
    assert result["item"] == "choicescarf"


def test_opponent_pokemon_none_returns_none() -> None:
    assert _serialize_opponent_pokemon(None) is None


# ---------------------------------------------------------------------------
# Full battle serialization smoke test
# ---------------------------------------------------------------------------

def test_serialize_battle_structure() -> None:
    """serialize_battle returns all expected top-level keys."""
    battle = MagicMock()
    battle.turn = 3
    battle.format = "gen3randombattle"
    battle.weather = {}
    battle.fields = {}
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    own = _mock_own_pokemon()
    opp = _mock_opponent_pokemon()
    opp.damage_multiplier.return_value = 1.0
    own.damage_multiplier.return_value = 1.0
    battle.active_pokemon = own
    battle.opponent_active_pokemon = opp
    battle.team = {"pikachu": _mock_own_pokemon()}
    battle.opponent_team = {"charmander": _mock_opponent_pokemon()}
    battle.available_moves = []
    battle.available_switches = []
    battle.force_switch = False

    result = serialize_battle(battle)

    expected_keys = {
        "turn", "format", "weather", "fields",
        "my_side_conditions", "opponent_side_conditions",
        "my_active", "my_team",
        "opponent_active", "opponent_team",
        "opponent_team_size_seen",
        "available_moves", "available_switches", "force_switch",
        "can_tera",
        "heuristics",
        "opponent_threat_map",
        "recent_events",
    }
    assert expected_keys == set(result.keys())
    assert result["turn"] == 3


def _mock_render_battle() -> MagicMock:
    """Battle mock whose decision-context accessors raise if touched.

    Used to prove the light serializer never computes heuristics / legal actions.
    """
    battle = MagicMock()
    battle.turn = 7
    battle.format = "gen3randombattle"
    battle.weather = {}
    battle.fields = {}
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    own = _mock_own_pokemon()
    opp = _mock_opponent_pokemon()
    battle.active_pokemon = own
    battle.opponent_active_pokemon = opp
    battle.team = {"pikachu": _mock_own_pokemon()}
    battle.opponent_team = {"charmander": _mock_opponent_pokemon()}
    battle.force_switch = False
    return battle


def test_serialize_battle_light_omits_decision_context() -> None:
    """light=True returns render fields but blanks heuristics / legal actions."""
    battle = _mock_render_battle()
    # Accessing these would compute the heavy/stale advisory — they must NOT be
    # touched in light mode.
    type(battle).available_moves = PropertyMock(side_effect=AssertionError("touched available_moves"))
    type(battle).available_switches = PropertyMock(side_effect=AssertionError("touched available_switches"))

    result = serialize_battle(battle, light=True)

    # Same top-level shape as the full serializer (stable contract for the UI)…
    expected_keys = {
        "turn", "format", "weather", "fields",
        "my_side_conditions", "opponent_side_conditions",
        "my_active", "my_team",
        "opponent_active", "opponent_team",
        "opponent_team_size_seen",
        "available_moves", "available_switches", "force_switch",
        "can_tera",
        "heuristics", "opponent_threat_map", "recent_events",
    }
    assert expected_keys == set(result.keys())
    # …but decision context is empty.
    assert result["heuristics"] is None
    assert result["opponent_threat_map"] == []
    assert result["available_moves"] == []
    assert result["available_switches"] == []
    # Render fields are still populated.
    assert result["turn"] == 7
    assert result["my_active"] is not None
    assert result["opponent_active"] is not None


def test_serialize_battle_light_does_not_call_heuristics() -> None:
    """light=True must not invoke score_actions (the expensive path)."""
    battle = _mock_render_battle()
    with patch("nidozo.battle.serializer.score_actions") as mock_score:
        serialize_battle(battle, light=True)
    mock_score.assert_not_called()


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

# --- _serialize_own_pokemon None ---

def test_serialize_own_pokemon_none_returns_none() -> None:
    assert _serialize_own_pokemon(None) is None


# --- _serialize_opponent_pokemon unknown item/ability ---

def test_opponent_unknown_item_becomes_none() -> None:
    """poke-env sentinel 'unknown' for item should be replaced with None."""
    mon = _mock_opponent_pokemon()
    mon.item = "unknown"
    result = _serialize_opponent_pokemon(mon)
    assert result["item"] is None


def test_opponent_unknown_ability_becomes_none() -> None:
    """poke-env sentinel 'unknown' for ability should be replaced with None."""
    mon = _mock_opponent_pokemon()
    mon.ability = "unknown"
    result = _serialize_opponent_pokemon(mon)
    assert result["ability"] is None


# --- _serialize_move fallback for pseudo-moves (KeyError/AttributeError) ---

def test_serialize_move_fallback_for_pseudo_move() -> None:
    """Pseudo-moves (e.g. 'recharge') that lack full data get a safe fallback."""
    from nidozo.battle.serializer import _serialize_move

    move = MagicMock()
    move.id = "recharge"
    # type.name raises KeyError (like a real recharge move in poke-env)
    type(move).type = PropertyMock(side_effect=KeyError("no type"))

    result = _serialize_move(move)
    assert result["id"] == "recharge"
    assert result["type"] == "NORMAL"
    assert result["category"] == "STATUS"
    assert result["base_power"] == 0
    assert result["priority"] == 0


# --- _serialize_move_basic fallback ---

def test_serialize_move_basic_fallback_for_pseudo_move() -> None:
    """Opponent pseudo-moves also get the safe fallback."""
    from nidozo.battle.serializer import _serialize_move_basic

    move = MagicMock()
    move.id = "recharge"
    type(move).type = PropertyMock(side_effect=KeyError("no type"))

    result = _serialize_move_basic(move)
    assert result["id"] == "recharge"
    assert result["type"] == "NORMAL"


# --- _safe_priority with KeyError ---

def test_safe_priority_returns_zero_on_keyerror() -> None:
    """_safe_priority returns 0 when priority raises KeyError."""
    from nidozo.battle.serializer import _safe_priority

    move = MagicMock()
    type(move).priority = PropertyMock(side_effect=KeyError("no priority"))

    result = _safe_priority(move)
    assert result == 0


# --- _serialize_weather with empty weather dict ---

def test_serialize_weather_empty_returns_none() -> None:
    """_serialize_weather returns None when weather dict is empty."""
    from nidozo.battle.serializer import _serialize_weather

    battle = MagicMock()
    battle.weather = {}
    result = _serialize_weather(battle)
    assert result is None


def test_serialize_weather_non_empty_returns_name() -> None:
    """Lines 175-176: _serialize_weather returns the weather key's .name when non-empty."""
    from nidozo.battle.serializer import _serialize_weather

    battle = MagicMock()
    weather_key = MagicMock()
    weather_key.name = "RAINDANCE"
    battle.weather = {weather_key: 5}

    result = _serialize_weather(battle)
    assert result == "RAINDANCE"


# ===========================================================================
# v5 additions — actual_stats, last_move on own and opponent Pokémon
# ===========================================================================

def _mock_move_for_last(id_: str = "thunderbolt", type_name: str = "ELECTRIC",
                        category_name: str = "SPECIAL", base_power: int = 95) -> MagicMock:
    m = MagicMock()
    m.id = id_
    t = MagicMock()
    t.name = type_name
    m.type = t
    cat = MagicMock()
    cat.name = category_name
    m.category = cat
    m.base_power = base_power
    m.accuracy = 1.0
    m.priority = 0
    return m


class TestOwnPokemonActualStats:
    """_serialize_own_pokemon exposes actual_stats (new in v5)."""

    def test_actual_stats_included_when_stats_populated(self) -> None:
        mon = _mock_own_pokemon()
        mon.stats = {"hp": 251, "atk": 85, "def": 57, "spa": 85, "spd": 73, "spe": 162}
        mon.last_move = None
        result = _serialize_own_pokemon(mon)
        assert result is not None
        assert result["actual_stats"] == {"hp": 251, "atk": 85, "def": 57, "spa": 85, "spd": 73, "spe": 162}

    def test_actual_stats_none_when_all_stats_none(self) -> None:
        mon = _mock_own_pokemon()
        mon.stats = {"hp": None, "atk": None, "def": None, "spa": None, "spd": None, "spe": None}
        mon.last_move = None
        result = _serialize_own_pokemon(mon)
        assert result is not None
        assert result["actual_stats"] is None

    def test_actual_stats_partial_when_some_stats_none(self) -> None:
        """Partial stats dict: only non-None values are kept."""
        mon = _mock_own_pokemon()
        mon.stats = {"hp": 200, "atk": None, "def": 80, "spa": None, "spd": 70, "spe": 120}
        mon.last_move = None
        result = _serialize_own_pokemon(mon)
        assert result is not None
        assert result["actual_stats"] == {"hp": 200, "def": 80, "spd": 70, "spe": 120}

    def test_actual_stats_key_always_present(self) -> None:
        """actual_stats key is always in the output dict (may be None)."""
        mon = _mock_own_pokemon()
        mon.stats = {}
        mon.last_move = None
        result = _serialize_own_pokemon(mon)
        assert result is not None
        assert "actual_stats" in result


class TestOwnPokemonLastMove:
    """_serialize_own_pokemon exposes last_move (new in v5)."""

    def test_last_move_included_when_used(self) -> None:
        mon = _mock_own_pokemon()
        mon.stats = {}
        mon.last_move = _mock_move_for_last("thunderbolt", "ELECTRIC", "SPECIAL", 95)
        result = _serialize_own_pokemon(mon)
        assert result is not None
        lm = result["last_move"]
        assert lm is not None
        assert lm["id"] == "thunderbolt"
        assert lm["type"] == "ELECTRIC"
        assert lm["base_power"] == 95

    def test_last_move_none_when_no_move_used(self) -> None:
        mon = _mock_own_pokemon()
        mon.stats = {}
        mon.last_move = None
        result = _serialize_own_pokemon(mon)
        assert result is not None
        assert result["last_move"] is None

    def test_last_move_key_always_present(self) -> None:
        """last_move key is always in the output (may be None)."""
        mon = _mock_own_pokemon()
        mon.stats = {}
        mon.last_move = None
        result = _serialize_own_pokemon(mon)
        assert result is not None
        assert "last_move" in result


class TestOpponentPokemonLastMove:
    """_serialize_opponent_pokemon exposes last_move (new in v5)."""

    def test_opponent_last_move_included_when_used(self) -> None:
        mon = _mock_opponent_pokemon()
        mon.last_move = _mock_move_for_last("earthquake", "GROUND", "PHYSICAL", 100)
        result = _serialize_opponent_pokemon(mon)
        assert result is not None
        lm = result["last_move"]
        assert lm is not None
        assert lm["id"] == "earthquake"
        assert lm["type"] == "GROUND"
        assert lm["base_power"] == 100
        # Opponent last_move should NOT include PP (hidden info)
        assert "pp" not in lm

    def test_opponent_last_move_none_when_not_used(self) -> None:
        mon = _mock_opponent_pokemon()
        mon.last_move = None
        result = _serialize_opponent_pokemon(mon)
        assert result is not None
        assert result["last_move"] is None

    def test_opponent_last_move_key_always_present(self) -> None:
        mon = _mock_opponent_pokemon()
        mon.last_move = None
        result = _serialize_opponent_pokemon(mon)
        assert result is not None
        assert "last_move" in result


class TestPromptBuilderV5:
    """PromptBuilder v5 template renders without error and contains expected content."""

    def _make_state(self) -> dict:
        """Minimal battle state with all v5 fields present."""
        return {
            "turn": 5,
            "format": "gen3randombattle",
            "weather": None,
            "fields": [],
            "my_side_conditions": [],
            "opponent_side_conditions": [],
            "recent_events": [],
            "my_active": {
                "species": "pikachu",
                "types": ["ELECTRIC"],
                "hp_fraction": 0.45,
                "status": None,
                "item": None,
                "ability": "static",
                "actual_stats": {"hp": 251, "atk": 85, "def": 57, "spa": 85, "spd": 73, "spe": 162},
                "boosts": {},
                "last_move": {"id": "thunderbolt", "type": "ELECTRIC", "category": "SPECIAL",
                              "base_power": 95, "accuracy": 1.0, "priority": 0},
                "moves": {
                    "thunderbolt": {
                        "id": "thunderbolt", "type": "ELECTRIC", "category": "SPECIAL",
                        "base_power": 95, "accuracy": 1.0, "pp": 15, "max_pp": 24, "priority": 0,
                    },
                },
                "effects": [],
            },
            "my_team": [],
            "opponent_active": {
                "species": "golem",
                "types": ["ROCK", "GROUND"],
                "hp_fraction": 0.6,
                "status": None,
                "item": None,
                "ability": None,
                "boosts": {},
                "last_move": {"id": "earthquake", "type": "GROUND", "category": "PHYSICAL",
                              "base_power": 100, "accuracy": 1.0, "priority": 0},
                "revealed_moves": {
                    "earthquake": {"id": "earthquake", "type": "GROUND", "category": "PHYSICAL",
                                   "base_power": 100, "accuracy": 1.0, "priority": 0},
                },
                "moves_revealed": 1,
            },
            "opponent_team": [],
            "opponent_team_size_seen": 1,
            "available_moves": [
                {"id": "thunderbolt", "type": "ELECTRIC", "category": "SPECIAL",
                 "base_power": 95, "accuracy": 1.0, "pp": 15, "max_pp": 24, "priority": 0},
            ],
            "available_switches": ["blastoise"],
            "force_switch": False,
            "heuristics": {
                "battle_context": {
                    "speed": {"you_move_first": True, "speed_tie": False,
                              "own_speed_estimate": 162, "opp_speed_estimate": 45,
                              "note": "You move FIRST (est. 162 vs 45)"},
                    "active_matchup": "disadvantaged",
                    "phase": "early",
                    "own_remaining": 5,
                    "opp_remaining": 5,
                    "weather": None,
                    "weather_note": None,
                    "own_status_impact": None,
                    "opp_status": None,
                    "opp_status_impact": None,
                    "ko_risk_note": "⚠ KO RISK: earthquake estimated ~120% — consider switching",
                },
                "move_scores": [
                    {"move_id": "thunderbolt", "type_multiplier": 0.0,
                     "effectiveness_label": "immune (0×)", "estimated_damage_pct": "0%",
                     "accuracy_adjusted_pct": "0%", "priority": 0, "is_status": False,
                     "low_pp": False, "notes": ["immune — will deal no damage"]},
                ],
                "switch_scores": [
                    {"species": "blastoise", "hp_fraction": 1.0,
                     "switch_quality": 3, "quality_label": "excellent switch",
                     "defensive_vs_opp": "neutral", "speed_vs_opp": "faster (98 vs ~45)",
                     "notes": ["Healthy HP (100%)"]},
                ],
            },
            "opponent_threat_map": [],
        }

    def test_v5_renders_without_error(self) -> None:
        """PromptBuilder v5 build_messages() completes without raising."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        msgs = pb.build_messages(self._make_state())
        assert len(msgs) == 2

    def test_v5_system_contains_decision_framework(self) -> None:
        """v5 system prompt includes the Decision Framework section."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        system = pb.build_system()["content"]
        assert "Decision Framework" in system

    def test_v5_system_contains_ko_guidance(self) -> None:
        """v5 system prompt explains KO risk and the survival check."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        system = pb.build_system()["content"]
        assert "KO" in system or "survival" in system.lower()

    def test_v5_turn_shows_ko_risk_note(self) -> None:
        """When ko_risk_note is set, it appears in the rendered turn prompt."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        msgs = pb.build_messages(self._make_state())
        turn_content = msgs[1]["content"]
        assert "KO RISK" in turn_content

    def test_v5_turn_shows_actual_stats(self) -> None:
        """Actual stats (Spe, Atk, SpA) appear in the turn prompt."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        msgs = pb.build_messages(self._make_state())
        turn_content = msgs[1]["content"]
        assert "Spe" in turn_content
        assert "162" in turn_content   # pikachu's speed stat

    def test_v5_turn_shows_opponent_last_move(self) -> None:
        """Opponent's last move appears in the opponent active section."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        msgs = pb.build_messages(self._make_state())
        turn_content = msgs[1]["content"]
        assert "Earthquake" in turn_content or "earthquake" in turn_content

    def test_v5_turn_shows_switch_defense_label(self) -> None:
        """Switch advisory includes the defensive_vs_opp label."""
        from nidozo.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder(version="v5")
        msgs = pb.build_messages(self._make_state())
        turn_content = msgs[1]["content"]
        assert "Defense vs current opponent" in turn_content
