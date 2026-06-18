"""Tests for v8 heuristic additions — entry hazard chip damage and removal signaling."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from nidozo.battle.heuristics import (
    _hazard_switch_notes,
    _is_grounded,
    _type_effectiveness_vs,
)

# ---------------------------------------------------------------------------
# Helpers — mirrors the mock pattern from test_heuristics.py
# ---------------------------------------------------------------------------


def _mock_type(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _mock_pokemon(
    species: str = "pikachu",
    types: tuple[str, ...] = ("ELECTRIC",),
    ability: str | None = None,
    moves: dict | None = None,
    hp_fraction: float = 1.0,
) -> MagicMock:
    mon = MagicMock()
    mon.species = species
    type(mon).types = PropertyMock(return_value=[_mock_type(t) for t in types])
    mon.ability = ability
    mon.base_stats = {"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80}
    mon.boosts = {}
    mon.stats = {}
    mon.current_hp_fraction = hp_fraction
    mon.fainted = hp_fraction <= 0
    mon.moves = moves or {}
    mon.status = None
    mon.damage_multiplier.return_value = 1.0
    return mon


def _mock_battle(side_conditions: dict | None = None) -> MagicMock:
    battle = MagicMock()
    battle.side_conditions = side_conditions or {}
    battle.opponent_side_conditions = {}
    battle.available_moves = []
    battle.available_switches = []
    battle.active_pokemon = None
    battle.opponent_active_pokemon = None
    battle.weather = {}
    battle.team = {}
    battle.opponent_team = {}
    return battle


def _side_condition(name: str) -> MagicMock:
    """Create a mock SideCondition key that compares by identity/name."""
    sc = MagicMock()
    sc.name = name
    return sc


# ---------------------------------------------------------------------------
# _is_grounded
# ---------------------------------------------------------------------------


def test_is_grounded_normal_type() -> None:
    mon = _mock_pokemon(types=("NORMAL",))
    assert _is_grounded(mon) is True


def test_is_grounded_flying_type() -> None:
    mon = _mock_pokemon(types=("NORMAL", "FLYING"))
    assert _is_grounded(mon) is False


def test_is_grounded_pure_flying() -> None:
    mon = _mock_pokemon(types=("FLYING",))
    assert _is_grounded(mon) is False


def test_is_grounded_levitate_ability() -> None:
    mon = _mock_pokemon(types=("GHOST",), ability="levitate")
    assert _is_grounded(mon) is False


def test_is_grounded_non_levitate_ability() -> None:
    mon = _mock_pokemon(types=("POISON",), ability="aftermath")
    assert _is_grounded(mon) is True


def test_is_grounded_water_type() -> None:
    mon = _mock_pokemon(types=("WATER",))
    assert _is_grounded(mon) is True


# ---------------------------------------------------------------------------
# _type_effectiveness_vs — uses real poke-env type chart
# ---------------------------------------------------------------------------


def test_rock_neutral_vs_water() -> None:
    mon = _mock_pokemon(types=("WATER",))
    mult = _type_effectiveness_vs("ROCK", mon)
    assert mult == pytest.approx(1.0)


def test_rock_2x_vs_fire() -> None:
    mon = _mock_pokemon(types=("FIRE",))
    mult = _type_effectiveness_vs("ROCK", mon)
    assert mult == pytest.approx(2.0)


def test_rock_4x_vs_fire_flying() -> None:
    # Fire/Flying (Charizard) — 4× Rock
    mon = _mock_pokemon(types=("FIRE", "FLYING"))
    mult = _type_effectiveness_vs("ROCK", mon)
    assert mult == pytest.approx(4.0)


def test_rock_0_5x_vs_steel() -> None:
    mon = _mock_pokemon(types=("STEEL",))
    mult = _type_effectiveness_vs("ROCK", mon)
    assert mult == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _hazard_switch_notes — no hazards
# ---------------------------------------------------------------------------


def test_no_hazards_returns_empty() -> None:
    mon = _mock_pokemon(types=("WATER",))
    battle = _mock_battle(side_conditions={})
    notes = _hazard_switch_notes(mon, battle)
    assert notes == []


# ---------------------------------------------------------------------------
# _hazard_switch_notes — Stealth Rock
# ---------------------------------------------------------------------------


def test_stealth_rock_neutral_note() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("WATER",))
    battle = _mock_battle(side_conditions={SideCondition.STEALTH_ROCK: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Stealth Rock" in n for n in notes)
    # Water is neutral to Rock → 12% chip
    assert any("12%" in n for n in notes)


def test_stealth_rock_2x_weak_note() -> None:
    from poke_env.battle import SideCondition

    # Fire-type is 2× weak to Rock
    mon = _mock_pokemon(types=("FIRE",))
    battle = _mock_battle(side_conditions={SideCondition.STEALTH_ROCK: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("25%" in n for n in notes)
    assert any("2×" in n or "2x" in n.lower() or "weak" in n.lower() for n in notes)


def test_stealth_rock_4x_weak_note() -> None:
    from poke_env.battle import SideCondition

    # Fire/Flying is 4× weak to Rock
    mon = _mock_pokemon(types=("FIRE", "FLYING"))
    battle = _mock_battle(side_conditions={SideCondition.STEALTH_ROCK: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("50%" in n for n in notes)
    assert any("4×" in n or "4x" in n.lower() for n in notes)


def test_stealth_rock_resistant_note() -> None:
    from poke_env.battle import SideCondition

    # Steel is 0.5× to Rock
    mon = _mock_pokemon(types=("STEEL",))
    battle = _mock_battle(side_conditions={SideCondition.STEALTH_ROCK: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Stealth Rock" in n for n in notes)
    assert any("6%" in n for n in notes)


# ---------------------------------------------------------------------------
# _hazard_switch_notes — Spikes
# ---------------------------------------------------------------------------


def test_spikes_1_layer_grounded() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL",))
    battle = _mock_battle(side_conditions={SideCondition.SPIKES: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Spikes" in n and "12%" in n for n in notes)


def test_spikes_2_layers_grounded() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL",))
    battle = _mock_battle(side_conditions={SideCondition.SPIKES: 2})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Spikes" in n and "17%" in n for n in notes)


def test_spikes_3_layers_grounded() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL",))
    battle = _mock_battle(side_conditions={SideCondition.SPIKES: 3})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Spikes" in n and "25%" in n for n in notes)


def test_spikes_no_effect_on_flying() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL", "FLYING"))
    battle = _mock_battle(side_conditions={SideCondition.SPIKES: 3})
    notes = _hazard_switch_notes(mon, battle)
    assert not any("Spikes" in n and "%" in n for n in notes)


# ---------------------------------------------------------------------------
# _hazard_switch_notes — Toxic Spikes
# ---------------------------------------------------------------------------


def test_toxic_spikes_1_layer_inflicts_poison() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("WATER",))
    battle = _mock_battle(side_conditions={SideCondition.TOXIC_SPIKES: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Toxic Spikes" in n and "Poison" in n and "Badly" not in n for n in notes)


def test_toxic_spikes_2_layers_inflicts_badly_poisoned() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("WATER",))
    battle = _mock_battle(side_conditions={SideCondition.TOXIC_SPIKES: 2})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Badly Poisoned" in n for n in notes)


def test_toxic_spikes_absorbed_by_poison_type() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("POISON",))
    battle = _mock_battle(side_conditions={SideCondition.TOXIC_SPIKES: 2})
    notes = _hazard_switch_notes(mon, battle)
    assert any("absorb" in n.lower() for n in notes)
    assert not any("Badly Poisoned" in n for n in notes)


def test_toxic_spikes_no_effect_on_steel() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("STEEL",))
    battle = _mock_battle(side_conditions={SideCondition.TOXIC_SPIKES: 2})
    notes = _hazard_switch_notes(mon, battle)
    # Steel shouldn't get poisoned or absorb Toxic Spikes
    assert not any("Badly Poisoned" in n or "absorb" in n.lower() for n in notes)


def test_toxic_spikes_no_effect_on_flying() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL", "FLYING"))
    battle = _mock_battle(side_conditions={SideCondition.TOXIC_SPIKES: 2})
    notes = _hazard_switch_notes(mon, battle)
    assert not any("Toxic Spikes" in n for n in notes)


# ---------------------------------------------------------------------------
# _hazard_switch_notes — Sticky Web
# ---------------------------------------------------------------------------


def test_sticky_web_grounded() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL",))
    battle = _mock_battle(side_conditions={SideCondition.STICKY_WEB: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert any("Sticky Web" in n for n in notes)


def test_sticky_web_no_effect_on_flying() -> None:
    from poke_env.battle import SideCondition

    mon = _mock_pokemon(types=("NORMAL", "FLYING"))
    battle = _mock_battle(side_conditions={SideCondition.STICKY_WEB: 1})
    notes = _hazard_switch_notes(mon, battle)
    assert not any("Sticky Web" in n for n in notes)


# ---------------------------------------------------------------------------
# Hazard removal signaling in score_actions
# ---------------------------------------------------------------------------


def _mock_move_simple(id_: str, category_name: str = "STATUS") -> MagicMock:
    from poke_env.battle.move_category import MoveCategory

    m = MagicMock()
    m.id = id_
    m.base_power = 0
    m.category = MoveCategory.STATUS
    m.type = _mock_type("NORMAL")
    m.priority = 0
    m.accuracy = True
    m.current_pp = 8
    m.max_pp = 8
    return m


def test_hazard_removal_note_when_hazards_active() -> None:
    from poke_env.battle import SideCondition

    from nidozo.battle.heuristics import score_actions

    spin = _mock_move_simple("rapidspin", "PHYSICAL")
    incoming = _mock_pokemon(types=("WATER",), moves={"rapidspin": spin})

    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0

    battle = _mock_battle(side_conditions={SideCondition.STEALTH_ROCK: 1})
    battle.active_pokemon = own
    battle.opponent_active_pokemon = opp
    battle.available_moves = []
    battle.available_switches = [incoming]
    battle.team = {own.species: own}
    battle.opponent_team = {opp.species: opp}

    result = score_actions(battle)
    ss = result["switch_scores"][0]
    assert any("clear hazard" in n.lower() or "Rapid Spin" in n for n in ss["notes"])


def test_no_hazard_removal_note_without_hazards() -> None:
    from nidozo.battle.heuristics import score_actions

    spin = _mock_move_simple("rapidspin")
    incoming = _mock_pokemon(types=("WATER",), moves={"rapidspin": spin})

    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0

    battle = _mock_battle(side_conditions={})  # no hazards
    battle.active_pokemon = own
    battle.opponent_active_pokemon = opp
    battle.available_moves = []
    battle.available_switches = [incoming]
    battle.team = {own.species: own}
    battle.opponent_team = {opp.species: opp}

    result = score_actions(battle)
    ss = result["switch_scores"][0]
    assert not any("clear hazard" in n.lower() for n in ss["notes"])


# ---------------------------------------------------------------------------
# Prompt builder — v8 loads correctly
# ---------------------------------------------------------------------------


def test_prompt_builder_loads_v8() -> None:
    from nidozo.llm.prompt_builder import PromptBuilder

    pb = PromptBuilder("v8")
    msg = pb.build_system()
    content = msg["content"]
    assert "Entry hazards" in content
    assert "Stealth Rock" in content
