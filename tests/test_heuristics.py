"""Tests for the heuristic action scorer."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from nidozo.battle.heuristics import _effectiveness_label, score_actions

# ---------------------------------------------------------------------------
# Helpers — same mock pattern as test_serializer.py
# ---------------------------------------------------------------------------

def _mock_type(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _mock_move(
    id_: str,
    base_power: int = 90,
    category_name: str = "SPECIAL",
    type_name: str = "ELECTRIC",
    priority: int = 0,
) -> MagicMock:
    from poke_env.battle.move_category import MoveCategory
    m = MagicMock()
    m.id = id_
    m.base_power = base_power
    m.category = MoveCategory.SPECIAL if category_name == "SPECIAL" else (
        MoveCategory.PHYSICAL if category_name == "PHYSICAL" else MoveCategory.STATUS
    )
    m.type = _mock_type(type_name)
    m.priority = priority
    m.accuracy = 1.0
    m.current_pp = 15
    m.max_pp = 15
    return m


def _mock_pokemon(
    species: str = "pikachu",
    types=("ELECTRIC",),
    base_stats: dict | None = None,
    boosts: dict | None = None,
    stats: dict | None = None,
    status=None,
    hp_fraction: float = 1.0,
) -> MagicMock:
    mon = MagicMock()
    mon.species = species
    type(mon).types = PropertyMock(
        return_value=[_mock_type(t) for t in types]
    )
    mon.base_stats = base_stats or {"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80}
    mon.boosts = boosts or {}
    mon.stats = stats or {}
    mon.current_hp_fraction = hp_fraction
    mon.fainted = hp_fraction <= 0
    mon.moves = {}
    mon.status = status
    mon.damage_multiplier.return_value = 1.0   # safe default; override per-test as needed
    return mon


def _mock_battle(
    available_moves=None,
    available_switches=None,
    own: MagicMock | None = None,
    opp: MagicMock | None = None,
    weather: dict | None = None,
) -> MagicMock:
    battle = MagicMock()
    battle.available_moves = available_moves or []
    battle.available_switches = available_switches or []
    battle.active_pokemon = own
    battle.opponent_active_pokemon = opp
    battle.weather = weather or {}
    # Give team dicts so _remaining_count doesn't blow up
    battle.team = {own.species: own} if own is not None else {}
    battle.opponent_team = {opp.species: opp} if opp is not None else {}
    return battle


# ---------------------------------------------------------------------------
# _effectiveness_label
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mult,expected", [
    (0.0, "immune (0×)"),
    (0.25, "not very effective (0.25×)"),
    (0.5, "not very effective (0.5×)"),
    (1.0, "neutral (1×)"),
    (2.0, "super effective (2×)"),
    (4.0, "super effective (4×)"),
])
def test_effectiveness_label(mult, expected) -> None:
    assert _effectiveness_label(mult) == expected


# ---------------------------------------------------------------------------
# Move scoring
# ---------------------------------------------------------------------------

def test_score_actions_returns_move_and_switch_keys() -> None:
    battle = _mock_battle()
    result = score_actions(battle)
    assert "move_scores" in result
    assert "switch_scores" in result


def test_super_effective_move_labeled_correctly() -> None:
    move = _mock_move("thunderbolt", type_name="ELECTRIC")
    own = _mock_pokemon()
    opp = _mock_pokemon(types=("WATER",))
    # Water is 2× weak to Electric
    opp.damage_multiplier.return_value = 2.0

    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    result = score_actions(battle)

    ms = result["move_scores"][0]
    assert ms["type_multiplier"] == 2.0
    assert "super effective" in ms["effectiveness_label"]


def test_immune_move_shows_zero_damage() -> None:
    move = _mock_move("thunderbolt", type_name="ELECTRIC")
    own = _mock_pokemon()
    opp = _mock_pokemon(types=("GROUND",))
    opp.damage_multiplier.return_value = 0.0

    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    result = score_actions(battle)

    ms = result["move_scores"][0]
    assert ms["type_multiplier"] == 0.0
    assert ms["estimated_damage_pct"] == "0%"
    assert any("immune" in n for n in ms["notes"])


def test_priority_move_noted() -> None:
    move = _mock_move("quickattack", base_power=40, category_name="PHYSICAL",
                      type_name="NORMAL", priority=1)
    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0

    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    result = score_actions(battle)

    ms = result["move_scores"][0]
    assert ms["priority"] == 1
    assert any("priority" in n for n in ms["notes"])


def test_status_move_has_no_damage_estimate() -> None:
    move = _mock_move("thunderwave", base_power=0, category_name="STATUS")
    battle = _mock_battle(available_moves=[move], own=_mock_pokemon(), opp=_mock_pokemon())
    result = score_actions(battle)

    ms = result["move_scores"][0]
    assert ms["is_status"] is True
    assert ms["estimated_damage_pct"] is None


def test_no_crash_when_opp_is_none() -> None:
    move = _mock_move("tackle")
    battle = _mock_battle(available_moves=[move], own=_mock_pokemon(), opp=None)
    result = score_actions(battle)
    assert len(result["move_scores"]) == 1


# ---------------------------------------------------------------------------
# Switch scoring
# ---------------------------------------------------------------------------

def test_switch_score_notes_resistance() -> None:
    incoming = _mock_pokemon("blastoise", types=("WATER",))
    # incoming resists Fire (defensive check); Water is not SE vs Fire (offensive check)
    incoming.damage_multiplier.side_effect = lambda t: 0.5 if t.name == "FIRE" else 1.0

    opp = _mock_pokemon(types=("FIRE",))
    opp.moves = {}
    opp.damage_multiplier.return_value = 1.0  # Water neutral vs Fire for offensive check

    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
    result = score_actions(battle)

    ss = result["switch_scores"][0]
    assert ss["species"] == "blastoise"
    assert any("resist" in n.lower() for n in ss["notes"])


def test_switch_score_notes_weakness() -> None:
    incoming = _mock_pokemon("charizard", types=("FIRE", "FLYING"))
    # incoming is weak to Water
    incoming.damage_multiplier.side_effect = lambda t: 2.0 if t.name == "WATER" else 1.0

    opp = _mock_pokemon(types=("WATER",))
    opp.moves = {}
    opp.damage_multiplier.return_value = 1.0  # Fire neutral vs Water for offensive check

    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
    result = score_actions(battle)

    ss = result["switch_scores"][0]
    assert any("weak" in n.lower() for n in ss["notes"])


# ---------------------------------------------------------------------------
# battle_context — speed comparison
# ---------------------------------------------------------------------------

class TestBattleContext:
    def test_context_present_in_output(self) -> None:
        battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon())
        result = score_actions(battle)
        assert "battle_context" in result

    def test_faster_own_moves_first(self) -> None:
        own = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 130})
        opp = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80})
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["speed"]["you_move_first"] is True

    def test_slower_own_moves_second(self) -> None:
        own = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 50})
        opp = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 100})
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["speed"]["you_move_first"] is False

    def test_paralysis_reduces_own_effective_speed(self) -> None:
        """A paralyzed mon with 120 base speed should have effective speed 60 (Gen 9: 50%),
        which is less than an opponent at 80 base speed → moves second."""
        status_par = MagicMock()
        status_par.name = "PAR"
        own = _mock_pokemon(
            base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 120},
            status=status_par,
        )
        opp = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80})
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        # 120 * 0.50 = 60 < 80 → moves second (Gen 9 paralysis halves speed)
        assert ctx["speed"]["you_move_first"] is False

    def test_active_matchup_favorable(self) -> None:
        """Own STAB hits ×2, opponent STAB hits ×1 → favorable."""
        own = _mock_pokemon(types=("WATER",))
        opp = _mock_pokemon(types=("FIRE",))
        opp.damage_multiplier.return_value = 2.0   # Water super effective vs Fire
        own.damage_multiplier.return_value = 1.0   # Fire neutral vs Water
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["active_matchup"] == "favorable"

    def test_active_matchup_disadvantaged(self) -> None:
        """Opponent STAB hits ×2, own STAB hits ×1 → disadvantaged."""
        own = _mock_pokemon(types=("FIRE",))
        opp = _mock_pokemon(types=("WATER",))
        opp.damage_multiplier.return_value = 1.0   # Fire neutral vs Water
        own.damage_multiplier.return_value = 2.0   # Water super effective vs Fire
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["active_matchup"] == "disadvantaged"

    def test_own_status_impact_reported(self) -> None:
        status_brn = MagicMock()
        status_brn.name = "BRN"
        own = _mock_pokemon(status=status_brn)
        battle = _mock_battle(own=own, opp=_mock_pokemon())
        ctx = score_actions(battle)["battle_context"]
        assert "own_status_impact" in ctx
        assert "Burn" in ctx["own_status_impact"]

    def test_opp_status_reported(self) -> None:
        status_slp = MagicMock()
        status_slp.name = "SLP"
        opp = _mock_pokemon(status=status_slp)
        battle = _mock_battle(own=_mock_pokemon(), opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx.get("opp_status") == "SLP"

    def test_weather_note_rain(self) -> None:
        weather_key = MagicMock()
        weather_key.name = "RAINDANCE"
        battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon(),
                              weather={weather_key: 5})
        ctx = score_actions(battle)["battle_context"]
        assert ctx.get("weather") == "RAINDANCE"
        assert "Rain" in ctx.get("weather_note", "")

    def test_remaining_counts_present(self) -> None:
        own = _mock_pokemon()
        opp = _mock_pokemon()
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert "own_remaining" in ctx
        assert "opp_remaining" in ctx


# ---------------------------------------------------------------------------
# Move scoring — new fields and notes
# ---------------------------------------------------------------------------

class TestMoveScoringEnhancements:
    def test_result_has_accuracy_adjusted_pct(self) -> None:
        move = _mock_move("stoneedge", base_power=100, category_name="PHYSICAL",
                          type_name="ROCK", priority=0)
        move.accuracy = 80
        own = _mock_pokemon()
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert ms["accuracy_adjusted_pct"] is not None
        assert ms["accuracy_adjusted_pct"] != ms["estimated_damage_pct"]

    def test_low_pp_flagged(self) -> None:
        move = _mock_move("thunderbolt")
        move.current_pp = 1
        move.max_pp = 16
        own = _mock_pokemon()
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert ms["low_pp"] is True
        assert any("LOW PP" in n for n in ms["notes"])

    def test_full_pp_not_flagged(self) -> None:
        move = _mock_move("thunderbolt")
        move.current_pp = 16
        move.max_pp = 16
        own = _mock_pokemon()
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert ms["low_pp"] is False

    def test_speed_note_faster(self) -> None:
        move = _mock_move("thunderbolt", priority=0)
        own = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 150})
        opp = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80})
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("first" in n.lower() for n in ms["notes"])

    def test_speed_note_slower(self) -> None:
        move = _mock_move("thunderbolt", priority=0)
        own = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 50})
        opp = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 120})
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("second" in n.lower() or "opponent moves first" in n.lower() for n in ms["notes"])

    def test_burn_halves_physical_damage(self) -> None:
        """Burn should reduce estimated physical damage vs an unburned version."""
        move_phys = _mock_move("tackle", base_power=40, category_name="PHYSICAL", type_name="NORMAL")
        move_phys.accuracy = True
        status_brn = MagicMock()
        status_brn.name = "BRN"
        own_burned = _mock_pokemon(status=status_brn)
        own_clean  = _mock_pokemon()
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0

        battle_burned = _mock_battle(available_moves=[move_phys], own=own_burned, opp=opp)
        battle_clean  = _mock_battle(available_moves=[move_phys], own=own_clean,  opp=opp)
        pct_burned = score_actions(battle_burned)["move_scores"][0]["estimated_damage_pct"]
        pct_clean  = score_actions(battle_clean)["move_scores"][0]["estimated_damage_pct"]
        # Damage should be lower when burned
        assert int(pct_burned.strip("~%")) < int(pct_clean.strip("~%"))

    def test_burn_note_on_physical_move(self) -> None:
        status_brn = MagicMock()
        status_brn.name = "BRN"
        move = _mock_move("tackle", base_power=40, category_name="PHYSICAL", type_name="NORMAL")
        move.accuracy = True
        own = _mock_pokemon(status=status_brn)
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("burn" in n.lower() and "halve" in n.lower() for n in ms["notes"])

    def test_weather_modifier_applied_to_water_in_rain(self) -> None:
        """Water move in Rain should show higher damage than without Rain."""
        move = _mock_move("surf", base_power=95, category_name="SPECIAL", type_name="WATER")
        move.accuracy = True
        own = _mock_pokemon()
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0

        weather_key = MagicMock()
        weather_key.name = "RAINDANCE"

        battle_rain = _mock_battle(available_moves=[move], own=own, opp=opp,
                                   weather={weather_key: 5})
        battle_dry  = _mock_battle(available_moves=[move], own=own, opp=opp)

        pct_rain = int(score_actions(battle_rain)["move_scores"][0]["estimated_damage_pct"].strip("~%"))
        pct_dry  = int(score_actions(battle_dry)["move_scores"][0]["estimated_damage_pct"].strip("~%"))
        assert pct_rain > pct_dry

    def test_priority_note_present(self) -> None:
        move = _mock_move("extremespeed", base_power=80, category_name="PHYSICAL",
                          type_name="NORMAL", priority=2)
        own = _mock_pokemon()
        opp = _mock_pokemon()
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("priority" in n.lower() for n in ms["notes"])
        # Priority moves should NOT also have a "you move first/second" speed note
        assert not any("you move first" in n.lower() or "second" in n.lower() for n in ms["notes"])


# ---------------------------------------------------------------------------
# Status move annotation — new context
# ---------------------------------------------------------------------------

class TestStatusMoveAnnotation:
    def test_paralysis_move_already_statused(self) -> None:
        """Thunder Wave vs already-paralyzed opponent should warn about wasting it."""
        move = _mock_move("thunderwave", base_power=0, category_name="STATUS")
        own = _mock_pokemon()
        status_par = MagicMock()
        status_par.name = "PAR"
        opp = _mock_pokemon(status=status_par)
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("already" in n.lower() or "wasted" in n.lower() for n in ms["notes"])

    def test_sleep_move_fresh_opponent(self) -> None:
        """Spore vs healthy unstatused opponent — should note Sleep."""
        move = _mock_move("spore", base_power=0, category_name="STATUS")
        own = _mock_pokemon()
        opp = _mock_pokemon()
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("sleep" in n.lower() for n in ms["notes"])
        # No "wasted" warning for a fresh opponent
        assert not any("wasted" in n.lower() for n in ms["notes"])

    def test_boost_move_annotates_stage_gain(self) -> None:
        """Swords Dance should show atk stage change."""
        move = _mock_move("swordsdance", base_power=0, category_name="STATUS")
        own = _mock_pokemon(boosts={"atk": 0})
        opp = _mock_pokemon()
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        # Should mention atk and show the +2 stage change
        assert any("atk" in n.lower() for n in ms["notes"])

    def test_boost_move_at_max_stage(self) -> None:
        """Swords Dance when atk is already +6 should flag no effect."""
        move = _mock_move("swordsdance", base_power=0, category_name="STATUS")
        own = _mock_pokemon(boosts={"atk": 6})
        opp = _mock_pokemon()
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("max" in n.lower() or "+6" in n for n in ms["notes"])

    def test_heal_move_low_hp_high_value(self) -> None:
        """Recover at low HP should note high recovery value."""
        move = _mock_move("recover", base_power=0, category_name="STATUS")
        own = _mock_pokemon(hp_fraction=0.3)
        opp = _mock_pokemon()
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("high recovery" in n.lower() or "low" in n.lower() for n in ms["notes"])

    def test_heal_move_full_hp_low_value(self) -> None:
        """Recover at near-full HP should note limited value."""
        move = _mock_move("recover", base_power=0, category_name="STATUS")
        own = _mock_pokemon(hp_fraction=0.95)
        opp = _mock_pokemon()
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("limited" in n.lower() or "high" in n.lower() for n in ms["notes"])

    def test_stat_drop_move_annotates_opponent_stage(self) -> None:
        """Screech should show the opponent's defense stage change."""
        move = _mock_move("screech", base_power=0, category_name="STATUS")
        own = _mock_pokemon()
        opp = _mock_pokemon(boosts={"def": 0})
        battle = _mock_battle(available_moves=[move], own=own, opp=opp)
        ms = score_actions(battle)["move_scores"][0]
        assert any("def" in n.lower() for n in ms["notes"])


# ---------------------------------------------------------------------------
# Switch scoring — quality score and enhanced notes
# ---------------------------------------------------------------------------

class TestSwitchScoringEnhancements:
    def test_switch_quality_present(self) -> None:
        incoming = _mock_pokemon("blastoise", types=("WATER",))
        opp = _mock_pokemon(types=("FIRE",))
        opp.moves = {}
        battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert "switch_quality" in ss
        assert isinstance(ss["switch_quality"], int)
        assert -3 <= ss["switch_quality"] <= 3

    def test_quality_label_present(self) -> None:
        incoming = _mock_pokemon("blastoise", types=("WATER",))
        opp = _mock_pokemon(types=("FIRE",))
        opp.moves = {}
        battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert "quality_label" in ss

    def test_low_hp_incoming_penalized(self) -> None:
        """An incoming mon at 15% HP should have a lower switch_quality."""
        high_hp = _mock_pokemon("blastoise", hp_fraction=1.0)
        low_hp  = _mock_pokemon("blastoise", hp_fraction=0.15)
        opp = _mock_pokemon()
        opp.moves = {}
        battle_high = _mock_battle(available_switches=[high_hp], own=_mock_pokemon(), opp=opp)
        battle_low  = _mock_battle(available_switches=[low_hp],  own=_mock_pokemon(), opp=opp)
        sq_high = score_actions(battle_high)["switch_scores"][0]["switch_quality"]
        sq_low  = score_actions(battle_low)["switch_scores"][0]["switch_quality"]
        assert sq_low < sq_high

    def test_disadvantaged_active_raises_switch_value(self) -> None:
        """Switching out of a disadvantaged matchup should boost incoming quality."""
        incoming = _mock_pokemon("starmie", types=("WATER",))
        incoming.damage_multiplier.return_value = 1.0  # neutral defensive matchup

        own = _mock_pokemon("charizard", types=("FIRE",))
        opp = _mock_pokemon(types=("WATER",))
        # Own Fire types hit Water neutrally; opponent Water types hit own Fire SE
        own.damage_multiplier.return_value = 2.0   # Water super effective vs Fire (own takes SE hits)
        opp.damage_multiplier.return_value = 1.0   # Fire neutral vs Water

        opp.moves = {}
        battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert any("disadvantaged" in n.lower() or "high value" in n.lower() for n in ss["notes"])

    def test_immune_incoming_boosts_quality(self) -> None:
        """Incoming mon immune to opponent's threat type gets quality boost."""
        incoming = _mock_pokemon("gengar", types=("GHOST", "POISON"))
        # Immune to Normal
        incoming.damage_multiplier.side_effect = lambda t: 0.0 if t.name == "NORMAL" else 1.0
        opp = _mock_pokemon(types=("NORMAL",))
        opp.moves = {}
        opp.damage_multiplier.return_value = 1.0
        battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert any("immune" in n.lower() for n in ss["notes"])
        assert ss["switch_quality"] > 0


def test_no_crash_when_opp_is_none_for_switch() -> None:
    incoming = _mock_pokemon("pikachu")
    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=None)
    result = score_actions(battle)
    assert result["switch_scores"][0]["species"] == "pikachu"


# ---------------------------------------------------------------------------
# New coverage tests — missing lines in heuristics.py
# ---------------------------------------------------------------------------

def test_battle_context_phase_exception_silenced() -> None:
    """Lines 288-289: except Exception in phase calculation is silenced.

    Making battle.team.values() raise causes the phase block to be skipped,
    but build_heuristics (via score_actions) must still return without error.
    """
    own = _mock_pokemon()
    opp = _mock_pokemon()
    battle = _mock_battle(own=own, opp=opp)
    # Make team.values() raise so the phase-calculation except fires
    battle.team = MagicMock()
    battle.team.values.side_effect = RuntimeError("oops")
    # Should not raise — the except clause silences it
    result = score_actions(battle)
    assert "battle_context" in result
    # 'phase' should be falsy (None) because exception was caught before it was set
    assert not result.get("battle_context", {}).get("phase")


def test_score_move_priority_keyerror_silenced() -> None:
    """Lines 334-335: KeyError on move.priority is caught and defaults to 0."""

    move = _mock_move("recharge")
    # Override priority to raise KeyError
    type(move).priority = PropertyMock(side_effect=KeyError("no priority"))
    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    assert ms["priority"] == 0


def test_score_move_pp_attributeerror_silenced() -> None:
    """Lines 342-343: AttributeError in PP check is caught silently.

    We use a spec'd MagicMock so PropertyMock raises AttributeError on access.
    """
    from poke_env.battle.move_category import MoveCategory

    class _FakeMove:
        pass

    move = MagicMock(spec=_FakeMove)
    move.id = "tackle"
    move.base_power = 90
    move.category = MoveCategory.SPECIAL
    move.priority = 0
    move.max_pp = 10
    # Make current_pp raise AttributeError via PropertyMock on a spec'd mock
    type(move).current_pp = PropertyMock(side_effect=AttributeError("no pp"))
    move_type = _mock_type("NORMAL")
    type(move).type = PropertyMock(return_value=move_type)

    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    # Should not raise — the AttributeError is caught
    ms = score_actions(battle)["move_scores"][0]
    assert ms["low_pp"] is False


def test_score_move_type_attributeerror_silenced() -> None:
    """Lines 382-383: AttributeError on move.type.name defaults move_type_name to ''.

    When move.type.name raises AttributeError, the except sets move_type_name=''
    and weather modifier defaults to 1.0.
    """
    from poke_env.battle.move_category import MoveCategory

    class _FakeMove2:
        pass

    class _FakeType:
        """No 'name' attribute — accessing .name on a spec=_FakeType mock raises AttributeError."""
        pass

    move = MagicMock(spec=_FakeMove2)
    move.id = "tackle"
    move.base_power = 90
    move.category = MoveCategory.SPECIAL
    move.priority = 0
    move.max_pp = 15
    move.current_pp = 15
    move.accuracy = 1.0
    # MagicMock(spec=_FakeType) raises AttributeError on .name because _FakeType has no 'name'
    bad_type = MagicMock(spec=_FakeType)
    type(move).type = PropertyMock(return_value=bad_type)

    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    # Should not raise; weather modifier defaults to 1.0 with empty string
    ms = score_actions(battle)["move_scores"][0]
    assert "estimated_damage_pct" in ms


def test_score_move_accuracy_exception_defaults_to_one() -> None:
    """Lines 390-391: exception in accuracy evaluation defaults acc_frac to 1.0.

    Setting accuracy to a value that causes float() to raise ValueError
    triggers the except clause. With acc_frac=1.0, accuracy_adjusted_pct
    equals estimated_damage_pct.
    """
    move = _mock_move("tackle")
    # "not-a-number" causes float("not-a-number") to raise ValueError
    move.accuracy = "not-a-number"
    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    # With acc_frac=1.0, pct * 1.0 == pct, so both percentages should be the same
    assert ms["estimated_damage_pct"] is not None
    assert ms["accuracy_adjusted_pct"] is not None
    assert ms["estimated_damage_pct"] == ms["accuracy_adjusted_pct"]


def test_score_move_likely_ohko_noted() -> None:
    """Line 423: 'likely OHKO' note fires when pct >= 100."""
    # Use very high base_power and low opp stats to guarantee pct >= 100
    move = _mock_move("hyperbeam", base_power=250, category_name="SPECIAL", type_name="NORMAL")
    move.accuracy = True
    own = _mock_pokemon(
        stats={"spa": 400},
        base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 400, "spd": 80, "spe": 80},
    )
    opp = _mock_pokemon(
        base_stats={"hp": 1, "atk": 80, "def": 80, "spa": 80, "spd": 5, "spe": 80},
    )
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    assert any("OHKO" in n for n in ms["notes"])


def test_score_move_stab_noted() -> None:
    """Line 456: 'STAB' note fires when move.type is in own.types."""
    electric_type = _mock_type("ELECTRIC")
    move = _mock_move("thunderbolt", type_name="ELECTRIC")
    # Override move.type to be the same object as in own.types
    move.type = electric_type
    own = _mock_pokemon(types=("ELECTRIC",))
    # Override own.types to return exactly that type object
    type(own).types = PropertyMock(return_value=[electric_type])
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 2.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    assert any("STAB" in n for n in ms["notes"])


def test_annotate_status_move_unknown_id_generic_fallback() -> None:
    """Lines 476-477: unknown status move id → generic 'status move' note + return."""
    move = _mock_move("unknownmove99", base_power=0, category_name="STATUS")
    own = _mock_pokemon()
    opp = _mock_pokemon()
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    assert any(n == "status move" for n in ms["notes"])


def test_annotate_status_move_stat_drop_at_min_stage() -> None:
    """Line 513: opponent already at -6 notes 'no further effect'."""
    move = _mock_move("growl", base_power=0, category_name="STATUS")
    own = _mock_pokemon()
    opp = _mock_pokemon(boosts={"atk": -6})
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    assert any("-6" in n and "min" in n.lower() for n in ms["notes"])


def test_switch_fainted_cannot_be_sent_out() -> None:
    """Lines 564-566: fainted incoming → 'fainted' note and switch_quality=-3."""
    incoming = _mock_pokemon("pikachu", hp_fraction=0.0)
    incoming.fainted = True
    opp = _mock_pokemon()
    opp.moves = {}
    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
    ss = score_actions(battle)["switch_scores"][0]
    assert ss["switch_quality"] == -3
    assert any("fainted" in n.lower() for n in ss["notes"])


def test_switch_moderate_hp_noted() -> None:
    """Lines 574-575: incoming at moderate HP (0.25 <= hp < 0.5) notes 'Moderate HP'."""
    incoming = _mock_pokemon("pikachu", hp_fraction=0.4)
    incoming.fainted = False
    opp = _mock_pokemon()
    opp.moves = {}
    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
    ss = score_actions(battle)["switch_scores"][0]
    assert any("moderate" in n.lower() or "40%" in n for n in ss["notes"])


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

# --- _current_weather exception paths ---

def test_current_weather_stop_iteration() -> None:
    """_current_weather returns None when weather dict raises StopIteration."""
    from nidozo.battle.heuristics import _current_weather

    battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon())
    # iter({}) raises StopIteration on next()
    battle.weather = {}  # empty dict → StopIteration on next(iter({}))
    result = _current_weather(battle)
    assert result is None


def test_current_weather_attribute_error() -> None:
    """_current_weather returns None when weather attribute is missing."""
    from nidozo.battle.heuristics import _current_weather

    battle = MagicMock()
    type(battle).weather = PropertyMock(side_effect=AttributeError("no weather"))
    result = _current_weather(battle)
    assert result is None


def test_current_weather_happy_path() -> None:
    """_current_weather extracts the weather name when dict has one entry."""
    from nidozo.battle.heuristics import _current_weather

    weather_key = MagicMock()
    weather_key.name = "SANDSTORM"
    battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon(),
                          weather={weather_key: 3})
    result = _current_weather(battle)
    assert result == "SANDSTORM"


# --- _effective_speed exception path ---

def test_effective_speed_attribute_error_returns_fallback() -> None:
    """_effective_speed returns 80.0 when boosts.get raises TypeError."""
    from nidozo.battle.heuristics import _effective_speed

    mon = MagicMock()
    mon.stats = {}       # empty dict → raw is None → float(mon.base_stats.get(...))
    mon.base_stats = {"spe": 80}
    # Make boosts.get raise TypeError → triggers except block
    mon.boosts = None    # None.get("spe", 0) → AttributeError → caught
    result = _effective_speed(mon, is_own=True)
    assert result == pytest.approx(80.0)


# --- _active_matchup_quality ---

def test_active_matchup_quality_double_edged() -> None:
    """Both sides super effective → 'double-edged'."""
    from nidozo.battle.heuristics import _active_matchup_quality

    own = _mock_pokemon(types=("WATER",))
    opp = _mock_pokemon(types=("FIRE",))
    # own STAB (Water) hits opp (Fire) for 2×; opp STAB (Fire) hits own (Water) for... set to 2×
    opp.damage_multiplier.return_value = 2.0   # own Water → opp Fire: 2×
    own.damage_multiplier.return_value = 2.0   # opp Fire → own Water: 2×
    result = _active_matchup_quality(own, opp)
    assert result == "double-edged"


def test_active_matchup_quality_unknown_when_none() -> None:
    """Returns 'unknown' when either pokemon is None."""
    from nidozo.battle.heuristics import _active_matchup_quality

    assert _active_matchup_quality(None, _mock_pokemon()) == "unknown"
    assert _active_matchup_quality(_mock_pokemon(), None) == "unknown"


# --- Battle phase detection ---

def test_battle_phase_endgame_behind() -> None:
    """own_remaining==1, opp_remaining>1 → endgame_behind."""
    own = _mock_pokemon()
    opp1 = _mock_pokemon("opp1")
    opp2 = _mock_pokemon("opp2")
    battle = MagicMock()
    battle.available_moves = []
    battle.available_switches = []
    battle.active_pokemon = own
    battle.opponent_active_pokemon = opp1
    battle.weather = {}
    # own team: only 1 alive; opp team: 2 alive
    own.fainted = False
    opp1.fainted = False
    opp2.fainted = False
    battle.team = {"own": own}
    battle.opponent_team = {"opp1": opp1, "opp2": opp2}

    ctx = score_actions(battle)["battle_context"]
    assert ctx.get("phase") == "endgame_behind"


def test_battle_phase_endgame_ahead() -> None:
    """own_remaining>1, opp_remaining==1 → endgame_ahead."""
    own1 = _mock_pokemon("own1")
    own2 = _mock_pokemon("own2")
    opp = _mock_pokemon("opp")
    battle = MagicMock()
    battle.available_moves = []
    battle.available_switches = []
    battle.active_pokemon = own1
    battle.opponent_active_pokemon = opp
    battle.weather = {}
    own1.fainted = False
    own2.fainted = False
    opp.fainted = False
    battle.team = {"own1": own1, "own2": own2}
    battle.opponent_team = {"opp": opp}

    ctx = score_actions(battle)["battle_context"]
    assert ctx.get("phase") == "endgame_ahead"


def test_battle_phase_midgame() -> None:
    """3 own + 3 opp remaining (sum=6) in a 6-mon team → midgame.

    Threshold is len(battle.team) = 6; sum==6 triggers midgame.
    """
    # 12 mons total (6 own roster, 6 opp roster); mark half fainted so 3 per side remain.
    all_mons = [_mock_pokemon(f"m{i}") for i in range(12)]
    for i, m in enumerate(all_mons):
        m.fainted = i in (3, 4, 5, 9, 10, 11)  # 3 own + 3 opp fainted
    battle = MagicMock()
    battle.available_moves = []
    battle.available_switches = []
    battle.active_pokemon = all_mons[0]
    battle.opponent_active_pokemon = all_mons[6]
    battle.weather = {}
    battle.team = {m.species: m for m in all_mons[:6]}
    battle.opponent_team = {m.species: m for m in all_mons[6:]}

    ctx = score_actions(battle)["battle_context"]
    assert ctx.get("phase") == "midgame"


def test_battle_phase_early() -> None:
    """4 own + 4 opp (sum=8 > 6) → early."""
    mons = [_mock_pokemon(f"m{i}") for i in range(8)]
    for m in mons:
        m.fainted = False
    battle = MagicMock()
    battle.available_moves = []
    battle.available_switches = []
    battle.active_pokemon = mons[0]
    battle.opponent_active_pokemon = mons[4]
    battle.weather = {}
    battle.team = {m.species: m for m in mons[:4]}
    battle.opponent_team = {m.species: m for m in mons[4:]}

    ctx = score_actions(battle)["battle_context"]
    assert ctx.get("phase") == "early"


# --- Weather context notes ---

def test_weather_note_sandstorm() -> None:
    weather_key = MagicMock()
    weather_key.name = "SANDSTORM"
    battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon(),
                          weather={weather_key: 5})
    ctx = score_actions(battle)["battle_context"]
    assert "Sandstorm" in ctx.get("weather_note", "")


def test_weather_note_hail() -> None:
    weather_key = MagicMock()
    weather_key.name = "HAIL"
    battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon(),
                          weather={weather_key: 5})
    ctx = score_actions(battle)["battle_context"]
    assert "Hail" in ctx.get("weather_note", "")


def test_weather_note_sunnyday() -> None:
    weather_key = MagicMock()
    weather_key.name = "SUNNYDAY"
    battle = _mock_battle(own=_mock_pokemon(), opp=_mock_pokemon(),
                          weather={weather_key: 5})
    ctx = score_actions(battle)["battle_context"]
    assert "Sun" in ctx.get("weather_note", "")


# --- Low PP note ---

def test_low_pp_boundary() -> None:
    """Exactly 25% PP left still triggers low_pp warning."""
    move = _mock_move("thunderbolt")
    move.current_pp = 4
    move.max_pp = 16   # 4/16 = 0.25 → low_pp
    own = _mock_pokemon()
    opp = _mock_pokemon()
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_moves=[move], own=own, opp=opp)
    ms = score_actions(battle)["move_scores"][0]
    assert ms["low_pp"] is True
    assert any("LOW PP" in n for n in ms["notes"])


# --- Switch scoring — quality labels ---

def test_switch_quality_poor_label() -> None:
    """Very low HP (-2) and a weakness (-1) → switch_quality ≤ -2 → 'poor switch'."""
    incoming = _mock_pokemon("charizard", types=("FIRE",), hp_fraction=0.15)
    incoming.damage_multiplier.return_value = 2.0  # weak to opp's type
    opp = _mock_pokemon(types=("WATER",))
    opp.moves = {}
    opp.damage_multiplier.return_value = 1.0
    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
    ss = score_actions(battle)["switch_scores"][0]
    assert ss["quality_label"] in ("poor switch", "risky switch")


def test_switch_quality_excellent_label() -> None:
    """Multiple immunities / resists → high switch_quality → 'excellent switch'."""
    incoming = _mock_pokemon("gengar", types=("GHOST",))
    # immune to Normal, also resists Poison
    incoming.damage_multiplier.side_effect = lambda t: (
        0.0 if t.name in ("NORMAL", "FIGHTING") else
        0.5 if t.name == "POISON" else 1.0
    )
    opp = _mock_pokemon(types=("NORMAL",))
    opp.moves = {}
    opp.damage_multiplier.return_value = 2.0  # Ghost hits opp SE
    battle = _mock_battle(available_switches=[incoming], own=_mock_pokemon(), opp=opp)
    ss = score_actions(battle)["switch_scores"][0]
    # Should be high quality
    assert ss["switch_quality"] >= 1


# --- _is_primarily_physical with no moves ---

def test_is_primarily_physical_no_moves_falls_back_to_base_stats() -> None:
    """With no moves, _is_primarily_physical uses base_stats atk vs spa."""
    from nidozo.battle.heuristics import _is_primarily_physical

    mon = _mock_pokemon(base_stats={"hp": 80, "atk": 120, "def": 80, "spa": 60, "spd": 80, "spe": 80})
    mon.moves = {}   # no moves
    assert _is_primarily_physical(mon) is True

    mon_special = _mock_pokemon(base_stats={"hp": 80, "atk": 60, "def": 80, "spa": 120, "spd": 80, "spe": 80})
    mon_special.moves = {}
    assert _is_primarily_physical(mon_special) is False


# --- Switch score: burned physical attacker note ---

def test_switch_note_burned_physical_attacker() -> None:
    """Burned physical attacker generates a note suggesting switching."""
    from poke_env.battle.move_category import MoveCategory

    status_brn = MagicMock()
    status_brn.name = "BRN"

    # Set up own as burned with mostly physical moves
    phys_move = MagicMock()
    phys_move.category = MoveCategory.PHYSICAL
    phys_move.base_power = 80

    own = _mock_pokemon(status=status_brn)
    own.moves = {"tackle": phys_move}

    incoming = _mock_pokemon("blastoise")
    incoming.damage_multiplier.return_value = 1.0

    opp = _mock_pokemon(types=("NORMAL",))
    opp.moves = {}
    opp.damage_multiplier.return_value = 1.0

    battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
    ss = score_actions(battle)["switch_scores"][0]
    assert any("burned" in n.lower() or "burn" in n.lower() for n in ss["notes"])


# --- Paralysis on switch target ---

def test_switch_note_paralyzed_active() -> None:
    """Paralyzed active mon should note the speed penalty when switching."""
    status_par = MagicMock()
    status_par.name = "PAR"

    own = _mock_pokemon(status=status_par)
    incoming = _mock_pokemon("blastoise")
    incoming.damage_multiplier.return_value = 1.0

    opp = _mock_pokemon()
    opp.moves = {}
    opp.damage_multiplier.return_value = 1.0

    battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
    ss = score_actions(battle)["switch_scores"][0]
    assert any("paralyz" in n.lower() for n in ss["notes"])


# ===========================================================================
# v5 additions — KO risk, switch defensive labels, speed_vs_opp
# ===========================================================================

class TestKoRiskNote:
    """heuristics.battle_context.ko_risk_note (new in v5)."""

    def _make_last_move(self, base_power: int = 100, category_name: str = "PHYSICAL", type_name: str = "GROUND") -> MagicMock:
        from poke_env.battle.move_category import MoveCategory
        m = MagicMock()
        m.id = "earthquake"
        m.base_power = base_power
        m.category = MoveCategory.PHYSICAL if category_name == "PHYSICAL" else MoveCategory.SPECIAL
        m.type = MagicMock()
        m.type.name = type_name
        m.accuracy = 1.0
        return m

    def test_ko_risk_note_set_when_estimated_damage_exceeds_hp(self) -> None:
        """At low HP, a high-power super-effective last move produces a KO risk note."""
        own = _mock_pokemon(
            species="charizard",
            types=("FIRE", "FLYING"),
            base_stats={"hp": 78, "atk": 84, "def": 78, "spa": 109, "spd": 85, "spe": 100},
            hp_fraction=0.25,   # only 25% HP left
        )
        opp = _mock_pokemon(
            species="golem",
            types=("ROCK", "GROUND"),
            base_stats={"hp": 80, "atk": 110, "def": 130, "spa": 55, "spd": 65, "spe": 45},
        )
        # Opponent's last move: Rock Slide — 4× vs Charizard
        last_move = self._make_last_move(base_power=75, category_name="PHYSICAL", type_name="ROCK")
        opp.last_move = last_move
        opp.damage_multiplier.return_value = 1.0  # not used in context path

        # own.damage_multiplier simulates that ROCK is 4× vs FIRE/FLYING
        own.damage_multiplier.return_value = 4.0

        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["ko_risk_note"] is not None
        assert "KO" in ctx["ko_risk_note"].upper() or "risk" in ctx["ko_risk_note"].lower()

    def test_ko_risk_note_none_when_no_last_move(self) -> None:
        """No last move → ko_risk_note stays None."""
        own = _mock_pokemon(hp_fraction=0.3)
        opp = _mock_pokemon()
        opp.last_move = None
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["ko_risk_note"] is None

    def test_ko_risk_note_none_for_status_last_move(self) -> None:
        """A status last move (base_power=0) should not generate a KO risk note."""
        from poke_env.battle.move_category import MoveCategory
        own = _mock_pokemon(hp_fraction=0.1)  # even at 10% HP
        opp = _mock_pokemon()
        status_move = MagicMock()
        status_move.id = "toxic"
        status_move.base_power = 0
        status_move.category = MoveCategory.STATUS
        opp.last_move = status_move
        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["ko_risk_note"] is None

    def test_ko_risk_note_none_when_hp_is_safe(self) -> None:
        """At full HP with a weak last move, no risk note should fire."""
        own = _mock_pokemon(
            hp_fraction=1.0,
            base_stats={"hp": 180, "atk": 80, "def": 100, "spa": 80, "spd": 100, "spe": 80},
        )
        opp = _mock_pokemon(
            base_stats={"hp": 80, "atk": 40, "def": 80, "spa": 40, "spd": 80, "spe": 80},
        )
        weak_move = self._make_last_move(base_power=40, type_name="NORMAL")
        opp.last_move = weak_move
        own.damage_multiplier.return_value = 0.5   # resisted

        battle = _mock_battle(own=own, opp=opp)
        ctx = score_actions(battle)["battle_context"]
        assert ctx["ko_risk_note"] is None

    def test_ko_risk_note_key_always_present(self) -> None:
        """battle_context always has ko_risk_note key (even if None) so templates don't error."""
        battle = _mock_battle()
        ctx = score_actions(battle)["battle_context"]
        assert "ko_risk_note" in ctx


class TestSwitchDefensiveVsOpp:
    """switch_scores[*].defensive_vs_opp and speed_vs_opp (new in v5)."""

    def test_defensive_vs_opp_immune_when_target_immune(self) -> None:
        """When the incoming mon is immune to all opponent threat types, label is 'immune'."""
        own = _mock_pokemon()
        opp = _mock_pokemon(types=("GROUND",))
        opp.moves = {}

        incoming = _mock_pokemon(species="charizard", types=("FIRE", "FLYING"))
        incoming.fainted = False
        incoming.current_hp_fraction = 1.0
        # Charizard is immune to Ground
        incoming.damage_multiplier.return_value = 0.0

        battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert ss["defensive_vs_opp"] == "immune"

    def test_defensive_vs_opp_weak_when_target_weak(self) -> None:
        """When the incoming mon is weak to the opponent's type(s), label is 'weak'."""
        own = _mock_pokemon()
        opp = _mock_pokemon(types=("FIRE",))
        opp.moves = {}

        incoming = _mock_pokemon(species="exeggutor", types=("GRASS", "PSYCHIC"))
        incoming.fainted = False
        incoming.current_hp_fraction = 1.0
        # Exeggutor is weak to Fire
        incoming.damage_multiplier.return_value = 2.0

        battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert ss["defensive_vs_opp"] == "weak"

    def test_defensive_vs_opp_neutral_when_no_special_interaction(self) -> None:
        """When no immunity, resistance, or weakness exists, label is 'neutral'."""
        own = _mock_pokemon()
        opp = _mock_pokemon(types=("NORMAL",))
        opp.moves = {}

        incoming = _mock_pokemon(species="raticate", types=("NORMAL",))
        incoming.fainted = False
        incoming.current_hp_fraction = 1.0
        incoming.damage_multiplier.return_value = 1.0

        battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert ss["defensive_vs_opp"] == "neutral"

    def test_defensive_vs_opp_key_always_present(self) -> None:
        """Every switch score dict has defensive_vs_opp regardless of opponent state."""
        own = _mock_pokemon()
        incoming = _mock_pokemon(species="raichu")
        incoming.fainted = False
        incoming.current_hp_fraction = 1.0
        incoming.damage_multiplier.return_value = 1.0

        battle = _mock_battle(available_switches=[incoming], own=own, opp=None)
        ss = score_actions(battle)["switch_scores"][0]
        assert "defensive_vs_opp" in ss

    def test_speed_vs_opp_present_when_opp_available(self) -> None:
        """speed_vs_opp is a non-None string when opponent is known."""
        own = _mock_pokemon()
        opp = _mock_pokemon(base_stats={"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 50})
        opp.moves = {}
        opp.boosts = {}
        opp.status = None

        incoming = _mock_pokemon(
            species="jolteon",
            base_stats={"hp": 65, "atk": 65, "def": 60, "spa": 110, "spd": 95, "spe": 130},
        )
        incoming.fainted = False
        incoming.current_hp_fraction = 1.0
        incoming.damage_multiplier.return_value = 1.0
        incoming.boosts = {}
        incoming.status = None

        battle = _mock_battle(available_switches=[incoming], own=own, opp=opp)
        ss = score_actions(battle)["switch_scores"][0]
        assert ss["speed_vs_opp"] is not None
        assert "faster" in ss["speed_vs_opp"] or "slower" in ss["speed_vs_opp"] or "similar" in ss["speed_vs_opp"]
