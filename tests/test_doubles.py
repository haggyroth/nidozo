"""Tests for doubles (2v2) support: action parser, heuristics, serializer.

Covers target-field parsing, spread-move handling, partner-aware heuristics,
and the doubles state shape. Battle objects are mocked with ``spec=DoubleBattle``
so the production ``isinstance(battle, DoubleBattle)`` routing fires.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from poke_env.battle import DoubleBattle
from poke_env.battle.move import Target
from poke_env.battle.move_category import MoveCategory
from poke_env.player.battle_order import (
    DoubleBattleOrder,
    PassBattleOrder,
    SingleBattleOrder,
)

from nidozo.battle.action_parser import parse_action
from nidozo.battle.heuristics import _move_target_hint, score_doubles_actions
from nidozo.battle.serializer import serialize_battle

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_move(
    id_: str,
    *,
    target: Target = Target.NORMAL,
    category: MoveCategory = MoveCategory.PHYSICAL,
    base_power: int = 80,
) -> MagicMock:
    m = MagicMock()
    m.id = id_
    m.deduced_target = target
    m.category = category
    m.base_power = base_power
    m.priority = 0
    m.accuracy = 100
    m.current_pp = 16
    m.max_pp = 16
    m.type = MagicMock()
    m.type.name = "NORMAL"
    return m


def _mock_pokemon(species: str, *, hp: float = 1.0) -> MagicMock:
    p = MagicMock()
    p.species = species
    p.current_hp_fraction = hp
    p.fainted = hp <= 0.0
    p.base_stats = {"hp": 80, "atk": 80, "def": 80, "spa": 80, "spd": 80, "spe": 80}
    p.boosts = {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
    p.stats = {"hp": 250, "atk": 200, "def": 180, "spa": 180, "spd": 180, "spe": 200}
    p.status = None
    p.types = []
    p.moves = {}
    p.damage_multiplier = MagicMock(return_value=1.0)
    return p


def _make_doubles_battle(
    *,
    own=None,
    opp=None,
    moves=None,
    switches=None,
    force_switch=None,
    can_tera=None,
    targets=None,
) -> MagicMock:
    """Construct a spec'd DoubleBattle mock with the per-slot list shapes."""
    battle = MagicMock(spec=DoubleBattle)
    battle.turn = 3
    battle.format = "gen9randomdoublesbattle"
    battle.active_pokemon = own or [_mock_pokemon("garchomp"), _mock_pokemon("rotom")]
    battle.opponent_active_pokemon = opp or [
        _mock_pokemon("incineroar"),
        _mock_pokemon("pelipper"),
    ]
    battle.available_moves = moves or [[], []]
    battle.available_switches = switches or [[], []]
    battle.force_switch = force_switch or [False, False]
    battle.can_tera = can_tera or [False, False]
    # get_possible_showdown_targets returns a list of showdown target ints
    battle.get_possible_showdown_targets = MagicMock(
        return_value=targets if targets is not None else [1, 2]
    )
    battle.weather = {}
    return battle


def _make_player() -> MagicMock:
    """Player whose create_order echoes a real SingleBattleOrder."""
    player = MagicMock()

    def _create(order, *, terastallize=False, move_target=0, **_):
        return SingleBattleOrder(order, terastallize=terastallize, move_target=move_target)

    player.create_order.side_effect = _create
    return player


# ---------------------------------------------------------------------------
# Action parser — doubles JSON
# ---------------------------------------------------------------------------


def test_doubles_two_move_actions_with_targets() -> None:
    moves = [[_mock_move("earthquake")], [_mock_move("thunderbolt")]]
    battle = _make_doubles_battle(moves=moves, targets=[1, 2])
    player = _make_player()

    response = (
        '{"reasoning":"focus fire","actions":['
        '{"action_type":"move","identifier":"earthquake","target":"foe_1"},'
        '{"action_type":"move","identifier":"thunderbolt","target":"foe_2"}]}'
    )
    order = parse_action(response, battle, player)

    assert isinstance(order, DoubleBattleOrder)
    assert order.first_order.order.id == "earthquake"
    assert order.first_order.move_target == 1
    assert order.second_order.order.id == "thunderbolt"
    assert order.second_order.move_target == 2


def test_doubles_target_tokens_resolve() -> None:
    # slot 1 (index 1) ally is own slot 0 → showdown position -1
    moves = [[_mock_move("protect")], [_mock_move("helpinghand")]]
    battle = _make_doubles_battle(moves=moves, targets=[-1, -2, 1, 2])
    player = _make_player()

    response = (
        '{"actions":['
        '{"action_type":"move","identifier":"protect","target":"self"},'
        '{"action_type":"move","identifier":"helpinghand","target":"ally"}]}'
    )
    order = parse_action(response, battle, player)
    assert isinstance(order, DoubleBattleOrder)
    # self for slot 0 → -1; ally for slot 1 → -1 (slot 0)
    assert order.first_order.move_target == -1
    assert order.second_order.move_target == -1


def test_doubles_spread_move_uses_empty_target() -> None:
    spread = _mock_move("earthquake", target=Target.ALL_ADJACENT_FOES)
    moves = [[spread], [_mock_move("surf")]]
    # Spread moves return EMPTY_TARGET_POSITION (0) from showdown targets
    battle = _make_doubles_battle(moves=moves, targets=[0])
    player = _make_player()

    response = (
        '{"actions":['
        '{"action_type":"move","identifier":"earthquake"},'
        '{"action_type":"move","identifier":"surf","target":"foe_1"}]}'
    )
    order = parse_action(response, battle, player)
    assert isinstance(order, DoubleBattleOrder)
    assert order.first_order.move_target == 0


def test_doubles_switch_action() -> None:
    moves = [[_mock_move("earthquake")], []]
    switches = [[_mock_pokemon("dragonite")], [_mock_pokemon("kingdra")]]
    battle = _make_doubles_battle(moves=moves, switches=switches)
    player = _make_player()

    response = (
        '{"actions":['
        '{"action_type":"move","identifier":"earthquake","target":"foe_1"},'
        '{"action_type":"switch","identifier":"kingdra"}]}'
    )
    order = parse_action(response, battle, player)
    assert isinstance(order, DoubleBattleOrder)
    assert order.second_order.order.species == "kingdra"


def test_doubles_pass_for_empty_slot() -> None:
    # Only slot 0 active (slot 1 fainted → None)
    own = [_mock_pokemon("garchomp"), None]
    moves = [[_mock_move("earthquake")], []]
    battle = _make_doubles_battle(own=own, moves=moves)
    player = _make_player()

    response = (
        '{"actions":[{"action_type":"move","identifier":"earthquake","target":"foe_1"}]}'
    )
    order = parse_action(response, battle, player)
    assert isinstance(order, DoubleBattleOrder)
    assert order.first_order.order.id == "earthquake"
    assert isinstance(order.second_order, PassBattleOrder)


def test_doubles_invalid_target_falls_back_to_valid() -> None:
    # Requested foe_2 but only foe_1 (pos 1) is a legal target → fall back.
    moves = [[_mock_move("tackle")], [_mock_move("tackle")]]
    battle = _make_doubles_battle(moves=moves, targets=[1])
    player = _make_player()

    response = (
        '{"actions":['
        '{"action_type":"move","identifier":"tackle","target":"foe_2"},'
        '{"action_type":"move","identifier":"tackle","target":"foe_1"}]}'
    )
    order = parse_action(response, battle, player)
    assert isinstance(order, DoubleBattleOrder)
    # foe_2 (pos 2) not in [1] → falls back to the only valid foe target (1)
    assert order.first_order.move_target == 1


def test_doubles_unparseable_returns_none() -> None:
    battle = _make_doubles_battle()
    player = _make_player()
    assert parse_action("total garbage, no json", battle, player) is None


# ---------------------------------------------------------------------------
# Heuristics — targeting hints + partner scoring
# ---------------------------------------------------------------------------


def test_move_target_hint_spread_foes() -> None:
    move = _mock_move("rockslide", target=Target.ALL_ADJACENT_FOES)
    hint = _move_target_hint(move, has_ally=True)
    assert hint["targeting"] == "spread_foes"


def test_move_target_hint_hits_ally() -> None:
    move = _mock_move("earthquake", target=Target.ALL_ADJACENT)
    hint = _move_target_hint(move, has_ally=True)
    assert hint["targeting"] == "hits_ally_too"
    assert "ALSO HITS ALLY" in hint["target_note"]


def test_move_target_hint_choose_foe() -> None:
    move = _mock_move("thunderbolt", target=Target.NORMAL)
    hint = _move_target_hint(move, has_ally=True)
    assert hint["targeting"] == "choose_foe"


def test_score_doubles_actions_shape() -> None:
    moves = [
        [_mock_move("earthquake", target=Target.ALL_ADJACENT)],
        [_mock_move("thunderbolt", target=Target.NORMAL, category=MoveCategory.SPECIAL)],
    ]
    battle = _make_doubles_battle(moves=moves)
    result = score_doubles_actions(battle)

    assert "slot_0" in result and "slot_1" in result
    assert "battle_context" in result
    assert len(result["slot_0"]["move_scores"]) == 1
    assert len(result["slot_1"]["move_scores"]) == 1
    # Each move score carries doubles targeting metadata
    assert "targeting" in result["slot_0"]["move_scores"][0]
    ctx = result["battle_context"]
    assert ctx["foe_1_species"] == "incineroar"
    assert ctx["foe_2_species"] == "pelipper"


# ---------------------------------------------------------------------------
# Serializer — doubles state shape
# ---------------------------------------------------------------------------


def test_serialize_doubles_shape() -> None:
    own = [_mock_pokemon("garchomp"), _mock_pokemon("rotom")]
    opp = [_mock_pokemon("incineroar"), _mock_pokemon("pelipper")]
    moves = [[_mock_move("earthquake")], [_mock_move("thunderbolt")]]
    switches = [[_mock_pokemon("dragonite")], [_mock_pokemon("kingdra")]]
    battle = _make_doubles_battle(own=own, opp=opp, moves=moves, switches=switches)
    # team/opponent_team needed for threat map + bench
    battle.team = {"a": own[0], "b": own[1], "c": _mock_pokemon("ferrothorn")}
    battle.opponent_team = {"x": opp[0], "y": opp[1]}
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    battle.fields = {}

    state = serialize_battle(battle)

    assert state["is_doubles"] is True
    assert isinstance(state["my_active"], list) and len(state["my_active"]) == 2
    assert state["my_active"][0]["slot"] == 0
    assert isinstance(state["opponent_active"], list)
    assert isinstance(state["force_switch"], list)
    assert isinstance(state["available_moves"], list)
    assert isinstance(state["available_moves"][0], list)
    assert state["heuristics"] is not None


def test_serialize_doubles_light_omits_decision_context() -> None:
    battle = _make_doubles_battle()
    battle.team = {"a": battle.active_pokemon[0], "b": battle.active_pokemon[1]}
    battle.opponent_team = {"x": battle.opponent_active_pokemon[0]}
    battle.side_conditions = {}
    battle.opponent_side_conditions = {}
    battle.fields = {}

    state = serialize_battle(battle, light=True)
    assert state["is_doubles"] is True
    assert state["heuristics"] is None
    assert state["available_moves"] == [[], []]
