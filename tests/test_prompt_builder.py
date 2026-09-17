"""Tests for PromptBuilder — template loading, rendering, and version handling."""

import re

import pytest

from nidozo.llm.prompt_builder import (
    _PROMPTS_ROOT,
    ALL_PROMPT_VERSIONS,
    DEFAULT_PROMPT_VERSION,
    DOUBLES_PROMPT_VERSION,
    DRAFT_PROMPT_VERSION,
    PromptBuilder,
    resolve_prompt_version,
)

_ALL_VERSIONS = [f"v{n}" for n in range(1, 10)]

# Doubles states are list-shaped (my_active is a list of slot dicts), which is
# what the singles templates cannot render. build_turn must reject a version
# without a doubles template before it attempts to render at all, so this state
# needs no other keys.
_DOUBLES_STATE: dict = {
    "is_doubles": True,
    "turn": 1,
    "my_active": [{"species": "garchomp", "slot": 0}],
    "available_moves": [[], []],
}

# Minimal battle state that satisfies all template variables
_MINIMAL_STATE: dict = {
    "turn": 1,
    "format": "gen3randombattle",
    "weather": None,
    "fields": [],
    "my_side_conditions": [],
    "opponent_side_conditions": [],
    "my_active": {
        "species": "pikachu",
        "level": 100,
        "types": ["ELECTRIC"],
        "hp_fraction": 1.0,
        "fainted": False,
        "status": None,
        "boosts": {},
        "item": "lightball",
        "ability": "static",
        "base_stats": {"atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        "actual_stats": None,
        "moves": {
            "thunderbolt": {
                "id": "thunderbolt",
                "type": "ELECTRIC",
                "category": "SPECIAL",
                "base_power": 90,
                "pp": 15,
                "max_pp": 15,
                "priority": 0,
            }
        },
        "effects": [],
        "last_move": None,
        "is_terastallized": False,
        "tera_type": None,
    },
    "my_team": [],
    "opponent_active": {
        "species": "charmander",
        "level": 100,
        "types": ["FIRE"],
        "hp_fraction": 0.75,
        "fainted": False,
        "status": None,
        "boosts": {},
        "item": None,
        "ability": None,
        "base_stats": {"atk": 52, "def": 43, "spa": 60, "spd": 50, "spe": 65},
        "revealed_moves": {},
        "moves_revealed": 0,
        "last_move": None,
        "is_terastallized": False,
        "tera_type": None,
    },
    "opponent_team": [],
    "available_moves": [
        {
            "id": "thunderbolt",
            "type": "ELECTRIC",
            "category": "SPECIAL",
            "base_power": 90,
            "accuracy": 1.0,
            "pp": 15,
            "max_pp": 15,
            "priority": 0,
        }
    ],
    "available_switches": [],
    "force_switch": False,
    # Added by #285: PromptBuilder() with no argument builds DEFAULT_PROMPT_VERSION,
    # and the v9 template reads these. They were missing, which is why the whole
    # fixture had quietly been rendering v1 — see
    # test_the_minimal_state_renders_on_every_version.
    "recent_events": [],
    "opponent_team_size_seen": 1,
    "opponent_threat_map": [],
    "can_tera": False,
    "heuristics": {
        # _battle_context pre-populates every optional key with None, so this is
        # the shape a real minimal state carries.
        "battle_context": {
            "speed": None,
            "active_matchup": None,
            "phase": None,
            "own_remaining": None,
            "opp_remaining": None,
            "weather": None,
            "weather_note": None,
            "own_status_impact": None,
            "opp_status": None,
            "opp_status_impact": None,
            "ko_risk_note": None,
            "tera_note": None,
        },
        "move_scores": [
            {
                "move_id": "thunderbolt",
                "type_multiplier": 1.0,
                "effectiveness_label": "neutral (1×)",
                "estimated_damage_pct": "~30%",
                "accuracy_adjusted_pct": "~30%",
                "priority": 0,
                "is_status": False,
                "low_pp": False,
                "notes": ["STAB"],
            }
        ],
        "switch_scores": [],
    },
}


def test_v1_loads_without_error() -> None:
    builder = PromptBuilder(version="v1")
    assert builder.version == "v1"


def test_build_system_returns_system_role() -> None:
    builder = PromptBuilder()
    msg = builder.build_system()
    assert msg["role"] == "system"
    assert len(msg["content"]) > 100


def test_system_prompt_contains_action_format() -> None:
    """The default system prompt states the JSON action contract (#285).

    This asserted on the v1 text protocol (``ACTION: move``) long after the
    templates moved to JSON — it kept passing only because ``PromptBuilder()``
    silently defaulted to v1, so it was testing a prompt no battle runs.
    """
    builder = PromptBuilder()
    system = builder.build_system()["content"]
    # The enum the model must choose from, then a worked example of each.
    assert '"action_type": "move", "switch", or "tera_move"' in system
    assert '"action_type":"move"' in system
    assert '"action_type":"switch"' in system
    assert '"action_type":"tera_move"' in system
    # And the v1 text protocol is gone, not merely accompanied.
    assert "ACTION: move" not in system


def test_build_turn_returns_user_role() -> None:
    builder = PromptBuilder()
    msg = builder.build_turn(_MINIMAL_STATE)
    assert msg["role"] == "user"


def test_turn_renders_species_and_turn_number() -> None:
    builder = PromptBuilder()
    content = builder.build_turn(_MINIMAL_STATE)["content"]
    assert "Turn 1" in content
    assert "Pikachu" in content
    assert "Charmander" in content


def test_turn_renders_move_name_and_bp() -> None:
    builder = PromptBuilder()
    content = builder.build_turn(_MINIMAL_STATE)["content"]
    assert "Thunderbolt" in content
    assert "90" in content


def test_turn_shows_no_revealed_moves_when_empty() -> None:
    builder = PromptBuilder()
    content = builder.build_turn(_MINIMAL_STATE)["content"]
    assert "No moves revealed yet" in content


def test_turn_shows_revealed_move_when_present() -> None:
    state = dict(_MINIMAL_STATE)
    state["opponent_active"] = dict(_MINIMAL_STATE["opponent_active"])
    state["opponent_active"]["revealed_moves"] = {
        "flamethrower": {
            "id": "flamethrower",
            "type": "FIRE",
            "category": "SPECIAL",
            "base_power": 90,
            "priority": 0,
        }
    }
    builder = PromptBuilder()
    content = builder.build_turn(state)["content"]
    assert "Flamethrower" in content
    assert "Revealed moves: none yet" not in content


def test_build_messages_returns_two_messages() -> None:
    builder = PromptBuilder()
    messages = builder.build_messages(_MINIMAL_STATE)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_unknown_version_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        PromptBuilder(version="v99")


def test_heuristic_advisory_section_rendered() -> None:
    builder = PromptBuilder()
    content = builder.build_turn(_MINIMAL_STATE)["content"]
    assert "HEURISTIC ADVISORY" in content
    assert "neutral (1×)" in content


def test_force_switch_text_appears() -> None:
    state = dict(_MINIMAL_STATE, force_switch=True, available_moves=[])
    builder = PromptBuilder()
    content = builder.build_turn(state)["content"]
    assert "must switch" in content


# ---------------------------------------------------------------------------
# Personality injection
# ---------------------------------------------------------------------------

def test_build_system_injects_personality_block() -> None:
    builder = PromptBuilder()
    content = builder.build_system(personality="aggressive")["content"]
    assert "All-out Attacker" in content
    assert "Attack" in content


def test_build_system_personality_appended_after_base() -> None:
    builder = PromptBuilder()
    base = builder.build_system()["content"]
    with_persona = builder.build_system(personality="defensive")["content"]
    assert with_persona.startswith(base)
    assert "Bulwark" in with_persona[len(base):]


def test_build_system_no_personality_unchanged() -> None:
    builder = PromptBuilder()
    base = builder.build_system()["content"]
    no_persona = builder.build_system(personality=None)["content"]
    assert base == no_persona


def test_build_system_unknown_personality_unchanged() -> None:
    builder = PromptBuilder()
    base = builder.build_system()["content"]
    unknown = builder.build_system(personality="does_not_exist")["content"]
    assert base == unknown


def test_personality_before_lessons_in_system() -> None:
    builder = PromptBuilder()
    content = builder.build_system(
        personality="momentum", lessons=["switch early"]
    )["content"]
    persona_pos = content.index("Tempo Player")
    lesson_pos  = content.index("Battle Memory")
    assert persona_pos < lesson_pos, "personality block should appear before lessons"


@pytest.mark.parametrize("slug", ["aggressive", "defensive", "balanced", "trickster", "momentum"])
def test_build_messages_with_each_personality(slug: str) -> None:
    builder = PromptBuilder()
    messages = builder.build_messages(_MINIMAL_STATE, personality=slug)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    # Each persona block starts with its own heading
    from nidozo.battle.personalities import get_personality
    persona = get_personality(slug)
    assert persona is not None
    assert persona.display_name in messages[0]["content"]


# ---------------------------------------------------------------------------
# Doubles capability (#302)
#
# A doubles state has a different shape from singles, so a version without a
# doubles template cannot render one. It must fail loudly and name the way out
# rather than render the singles template and surface a Jinja UndefinedError.
# ---------------------------------------------------------------------------

def test_version_list_covers_every_shipped_version() -> None:
    """A new prompts/v<N>/ directory must be added here (and so get tested)."""
    shipped = sorted(
        p.name for p in _PROMPTS_ROOT.iterdir()
        if p.is_dir() and re.fullmatch(r"v\d+", p.name)
    )
    assert shipped == _ALL_VERSIONS


def test_the_api_version_literal_covers_every_shipped_version() -> None:
    """The API must accept every version that exists — and refuse nothing else."""
    assert list(ALL_PROMPT_VERSIONS) == _ALL_VERSIONS


def test_the_default_prompt_version_is_the_newest_singles_template() -> None:
    """#285. ``PromptBuilder()`` must build the prompt production actually runs.

    It defaulted to ``"v1"`` while the API sent ``"v9"``, so anything
    constructed without an explicit version silently ran the oldest prompt in
    the repo — and, as the fixture below shows, took the tests with it. Adding
    v10 without moving this constant fails here.
    """
    singles = sorted(
        (p.name for p in _PROMPTS_ROOT.iterdir()
         if p.is_dir() and re.fullmatch(r"v\d+", p.name)
         and (p / "turn.txt.jinja").is_file()),
        key=lambda v: int(v[1:]),
    )
    assert DEFAULT_PROMPT_VERSION == singles[-1]


def test_the_default_prompt_version_is_loadable() -> None:
    assert PromptBuilder().version == DEFAULT_PROMPT_VERSION


def test_the_player_defaults_to_the_default_prompt_version() -> None:
    """The other half of the drift: LLMPlayer defaulted to v1 as well."""
    from unittest.mock import AsyncMock, patch

    from nidozo.battle.llm_player import LLMPlayer

    with patch("poke_env.player.Player.__init__", return_value=None):
        player = LLMPlayer(backend=AsyncMock())

    assert player._prompt_builder.version == DEFAULT_PROMPT_VERSION


def test_the_store_defaults_to_the_default_prompt_version() -> None:
    from nidozo.db.store import BattleStore

    store = BattleStore(":memory:")
    model_id = store.get_or_create_model("openai", "gpt-test")
    row = store._conn.execute(
        "SELECT prompt_version FROM models WHERE id=?", (model_id,)
    ).fetchone()
    assert row[0] == DEFAULT_PROMPT_VERSION


def test_the_schema_has_no_prompt_version_column_default() -> None:
    """#285. A column default is a stale version waiting to be stamped on a row.

    Every INSERT passes ``prompt_version`` explicitly, so the defaults were
    unreachable — 'v1'/'v2'/'v6' sat in the schema text naming versions nothing
    ran. Dropping them means a forgotten value fails at insert.
    """
    from nidozo.db.schema import _DDL_TABLES

    for line in _DDL_TABLES.splitlines():
        if "prompt_version" in line:
            assert "DEFAULT" not in line, line


@pytest.mark.parametrize("version", _ALL_VERSIONS)
def test_the_minimal_state_renders_on_every_version(version: str) -> None:
    """The fixture must keep pace with the templates, or it silently stops testing.

    ``_MINIMAL_STATE`` still satisfied v1 long after the default moved on, so
    every test built on it was exercising a prompt no battle runs — and the only
    symptom was the fixture quietly going stale. Rendering it through each
    version under ``StrictUndefined`` makes that drift fail here instead.
    """
    content = PromptBuilder(version).build_turn(_MINIMAL_STATE)["content"]
    assert "Turn 1" in content


@pytest.mark.parametrize("version", _ALL_VERSIONS)
def test_supports_doubles_matches_the_shipped_template(version: str) -> None:
    template = _PROMPTS_ROOT / version / "turn_doubles.txt.jinja"
    assert PromptBuilder(version).supports_doubles is template.is_file()


def test_doubles_prompt_version_is_loadable_and_supports_doubles() -> None:
    # Guards the constant itself: if the doubles template moves to another
    # version, this fails rather than silently degrading every doubles prompt.
    assert PromptBuilder(DOUBLES_PROMPT_VERSION).supports_doubles


def test_draft_prompt_version_is_loadable() -> None:
    assert PromptBuilder(DRAFT_PROMPT_VERSION).version == DRAFT_PROMPT_VERSION


def test_doubles_state_on_a_singles_version_raises_a_clear_error() -> None:
    builder = PromptBuilder("v9")
    assert not builder.supports_doubles
    with pytest.raises(ValueError) as exc:
        builder.build_turn(_DOUBLES_STATE)
    message = str(exc.value)
    assert "'v9' has no doubles template" in message
    assert DOUBLES_PROMPT_VERSION in message          # names the version that works
    assert "turn_doubles.txt.jinja" in message        # names the missing file
    assert "resolve_prompt_version" in message        # names the helper to use


@pytest.mark.parametrize("version", _ALL_VERSIONS)
def test_resolved_version_is_always_loadable(version: str) -> None:
    """Whatever resolve_prompt_version picks must exist on disk."""
    PromptBuilder(resolve_prompt_version(version, doubles=True))
    PromptBuilder(resolve_prompt_version(version, draft=True))


def test_resolve_prompt_version_honours_the_request_when_plain() -> None:
    assert resolve_prompt_version("v9") == "v9"


def test_resolve_prompt_version_pins_doubles() -> None:
    assert resolve_prompt_version("v9", doubles=True) == DOUBLES_PROMPT_VERSION


def test_resolve_prompt_version_pins_draft() -> None:
    assert resolve_prompt_version("v9", draft=True) == DRAFT_PROMPT_VERSION


def test_resolve_prompt_version_prefers_doubles_over_draft() -> None:
    """A doubles draft still has to render the doubles template."""
    assert resolve_prompt_version("v9", doubles=True, draft=True) == DOUBLES_PROMPT_VERSION
