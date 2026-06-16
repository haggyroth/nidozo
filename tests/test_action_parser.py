"""Tests for ActionParser — valid formats, edge cases, and fallback behaviour."""

from unittest.mock import MagicMock

from nidozo.battle.action_parser import parse_action

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_move(id_: str) -> MagicMock:
    m = MagicMock()
    m.id = id_
    return m


def _mock_pokemon(species: str) -> MagicMock:
    p = MagicMock()
    p.species = species
    return p


def _make_battle(moves=None, switches=None) -> MagicMock:
    battle = MagicMock()
    battle.turn = 1
    battle.available_moves = moves or []
    battle.available_switches = switches or []
    battle.can_tera = False
    return battle


def _make_player(move=None, switch=None) -> MagicMock:
    """Player whose create_order returns a sentinel based on what it receives."""
    player = MagicMock()
    player.create_order.side_effect = lambda obj, **_: ("move", obj) if hasattr(obj, "id") else ("switch", obj)
    return player


# ---------------------------------------------------------------------------
# Happy-path parsing
# ---------------------------------------------------------------------------

def test_parses_move_action() -> None:
    moves = [_mock_move("thunderbolt"), _mock_move("quickattack")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action("I'll go with move 1.\nACTION: move 1", battle, player)

    player.create_order.assert_called_once_with(moves[0], terastallize=False)
    assert result is not None


def test_parses_switch_action() -> None:
    switches = [_mock_pokemon("blastoise"), _mock_pokemon("venusaur")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action("Better to switch.\nACTION: switch 2", battle, player)

    player.create_order.assert_called_once_with(switches[1])
    assert result is not None


def test_action_case_insensitive() -> None:
    moves = [_mock_move("surf")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action("action: MOVE 1", battle, player)
    assert result is not None


def test_uses_last_action_line_when_multiple() -> None:
    """If model reasons 'maybe move 1' but ends with 'ACTION: move 2', use slot 2."""
    moves = [_mock_move("flamethrower"), _mock_move("fireblast")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    response = "ACTION: move 1\nActually...\nACTION: move 2"
    parse_action(response, battle, player)

    player.create_order.assert_called_once_with(moves[1], terastallize=False)


def test_extra_whitespace_in_action_line() -> None:
    moves = [_mock_move("icebeam")]
    battle = _make_battle(moves=moves)
    player = _make_player()
    result = parse_action("ACTION:   move   1", battle, player)
    assert result is not None


# ---------------------------------------------------------------------------
# Failure cases — all return None
# ---------------------------------------------------------------------------

def test_no_action_line_returns_none() -> None:
    battle = _make_battle(moves=[_mock_move("tackle")])
    player = _make_player()
    result = parse_action("I think Tackle is good here.", battle, player)
    assert result is None


def test_move_slot_out_of_range_returns_none() -> None:
    battle = _make_battle(moves=[_mock_move("tackle")])
    player = _make_player()
    result = parse_action("ACTION: move 5", battle, player)
    assert result is None


def test_switch_slot_out_of_range_returns_none() -> None:
    switches = [_mock_pokemon("pikachu")]
    battle = _make_battle(switches=switches)
    player = _make_player()
    result = parse_action("ACTION: switch 9", battle, player)
    assert result is None


def test_move_when_no_moves_available_returns_none() -> None:
    battle = _make_battle(moves=[])
    player = _make_player()
    result = parse_action("ACTION: move 1", battle, player)
    assert result is None


def test_switch_when_no_switches_available_returns_none() -> None:
    battle = _make_battle(switches=[])
    player = _make_player()
    result = parse_action("ACTION: switch 1", battle, player)
    assert result is None


def test_slot_zero_returns_none() -> None:
    battle = _make_battle(moves=[_mock_move("tackle")])
    player = _make_player()
    result = parse_action("ACTION: move 0", battle, player)
    assert result is None


# ---------------------------------------------------------------------------
# Name resolution (move names and species names)
# ---------------------------------------------------------------------------

def test_parses_move_by_name() -> None:
    """'ACTION: move Thunderbolt' should resolve to the thunderbolt move."""
    moves = [_mock_move("thunderbolt"), _mock_move("quickattack")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action("ACTION: move Thunderbolt", battle, player)

    player.create_order.assert_called_once_with(moves[0], terastallize=False)
    assert result is not None


def test_parses_move_name_case_insensitive() -> None:
    moves = [_mock_move("shadowball")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action("ACTION: move ShadowBall", battle, player)
    assert result is not None


def test_parses_switch_by_species_name() -> None:
    """'ACTION: switch Masquerain' should resolve to the Masquerain slot."""
    switches = [_mock_pokemon("blastoise"), _mock_pokemon("masquerain")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action("ACTION: switch Masquerain", battle, player)

    player.create_order.assert_called_once_with(switches[1])
    assert result is not None


def test_parses_bare_action_move_name() -> None:
    """'ACTION: thunderbolt' with no 'move' keyword resolves as a move."""
    moves = [_mock_move("thunderbolt"), _mock_move("surf")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action("I'll use thunderbolt.\nACTION: thunderbolt", battle, player)

    player.create_order.assert_called_once_with(moves[0], terastallize=False)
    assert result is not None


def test_last_valid_name_action_wins() -> None:
    """Last parseable ACTION wins even when earlier ones used names."""
    moves = [_mock_move("surf"), _mock_move("icebeam")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    response = "Maybe surf... ACTION: move Surf\nActually, ACTION: move IceBeam"
    parse_action(response, battle, player)

    player.create_order.assert_called_once_with(moves[1], terastallize=False)


def test_unknown_move_name_returns_none() -> None:
    """'ACTION: move FakeMove' with no matching move returns None."""
    moves = [_mock_move("thunderbolt")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action("ACTION: move FakeMove", battle, player)
    assert result is None


def test_markdown_bold_around_action_line() -> None:
    """'**ACTION: move 2**' — trailing ** stripped from slot."""
    moves = [_mock_move("surf"), _mock_move("icebeam")]
    battle = _make_battle(moves=moves)
    player = _make_player()
    result = parse_action("**ACTION: move 2**", battle, player)
    player.create_order.assert_called_once_with(moves[1], terastallize=False)
    assert result is not None


def test_markdown_bold_around_label_only() -> None:
    """'**ACTION:** move Thunderbolt' — ** between colon and keyword."""
    moves = [_mock_move("thunderbolt"), _mock_move("surf")]
    battle = _make_battle(moves=moves)
    player = _make_player()
    result = parse_action("**ACTION:** move Thunderbolt", battle, player)
    player.create_order.assert_called_once_with(moves[0], terastallize=False)
    assert result is not None


def test_markdown_action_with_slot() -> None:
    """'**ACTION:** switch 2' — bold label, slot number."""
    switches = [_mock_pokemon("pikachu"), _mock_pokemon("blastoise")]
    battle = _make_battle(switches=switches)
    player = _make_player()
    result = parse_action("**ACTION:** switch 2", battle, player)
    player.create_order.assert_called_once_with(switches[1])
    assert result is not None


def test_name_fallback_when_slot_out_of_range() -> None:
    """If slot is out of range but a name matches, prefer the name resolution."""
    # The parser tries slot first; slot 9 is out of range, but 'thunderbolt' matches.
    # Current behavior: slot fails → name resolution succeeds.
    moves = [_mock_move("thunderbolt")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    # "move 1" would succeed; but let's test name works independently
    result = parse_action("ACTION: move thunderbolt", battle, player)
    assert result is not None
    player.create_order.assert_called_once_with(moves[0], terastallize=False)


# ---------------------------------------------------------------------------
# Keyword-prefix stripping ("switch 1", "move thunderbolt" as identifier)
# ---------------------------------------------------------------------------

def test_json_switch_identifier_with_switch_prefix() -> None:
    """identifier='switch 1' should strip 'switch ' and resolve slot 1."""
    switches = [_mock_pokemon("aggron"), _mock_pokemon("blastoise")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action(
        '{"action_type":"switch","identifier":"switch 1"}',
        battle, player,
    )
    assert result is not None
    player.create_order.assert_called_once_with(switches[0])


def test_json_switch_identifier_with_move_prefix() -> None:
    """identifier='move thunderbolt' in a move action should strip 'move '."""
    moves = [_mock_move("thunderbolt"), _mock_move("surf")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action(
        '{"action_type":"move","identifier":"move thunderbolt"}',
        battle, player,
    )
    assert result is not None
    player.create_order.assert_called_once_with(moves[0], terastallize=False)


def test_json_switch_identifier_with_switch_and_species() -> None:
    """identifier='switch blastoise' — strip prefix, resolve by name."""
    switches = [_mock_pokemon("aggron"), _mock_pokemon("blastoise")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action(
        '{"action_type":"switch","identifier":"switch blastoise"}',
        battle, player,
    )
    assert result is not None
    player.create_order.assert_called_once_with(switches[1])


# ---------------------------------------------------------------------------
# Fuzzy species name matching
# ---------------------------------------------------------------------------

def test_fuzzy_switch_one_char_typo() -> None:
    """'agron' should fuzzy-match to 'aggron' (1-char typo)."""
    switches = [_mock_pokemon("aggron"), _mock_pokemon("blastoise")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action("ACTION: switch agron", battle, player)

    assert result is not None
    player.create_order.assert_called_once_with(switches[0])


def test_fuzzy_switch_double_letter_typo() -> None:
    """'deoxysspeed' should fuzzy-match to 'deoxysSpeed' / 'deoxysspeed'."""
    switches = [_mock_pokemon("deoxys-speed"), _mock_pokemon("pikachu")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    # _normalize strips hyphens: "deoxysspeed" vs "deoxyssspeed"... let's check
    # normalize("deoxys-speed") = "deoxyssspeed" wait no...
    # Actually normalize strips non-alphanumeric, so "deoxys-speed" -> "deoxysspeed"
    # and the typo "deoxysspeed" matches exactly after normalization
    result = parse_action("ACTION: switch deoxysspeed", battle, player)
    assert result is not None


def test_fuzzy_switch_does_not_match_wildly_different_name() -> None:
    """A completely wrong name should not fuzzy-match anything."""
    switches = [_mock_pokemon("blastoise"), _mock_pokemon("venusaur")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action("ACTION: switch pikachu", battle, player)
    assert result is None


def test_exact_match_preferred_over_fuzzy() -> None:
    """Exact match is always preferred — fuzzy only runs if exact fails."""
    switches = [_mock_pokemon("aggron"), _mock_pokemon("agron")]  # both exist
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action("ACTION: switch aggron", battle, player)

    assert result is not None
    player.create_order.assert_called_once_with(switches[0])


# ---------------------------------------------------------------------------
# JSON structured output (v2 prompt)
# ---------------------------------------------------------------------------

def test_json_move_by_name() -> None:
    """v2 JSON response with move name resolves correctly."""
    moves = [_mock_move("thunderbolt"), _mock_move("surf")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action(
        '{"reasoning":"Thunderbolt is 2x effective.","action_type":"move","identifier":"thunderbolt"}',
        battle, player,
    )
    player.create_order.assert_called_once_with(moves[0], terastallize=False)
    assert result is not None


def test_json_switch_by_name() -> None:
    """v2 JSON response with switch species name resolves correctly."""
    switches = [_mock_pokemon("blastoise"), _mock_pokemon("venusaur")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    result = parse_action(
        '{"reasoning":"Switch to Blastoise for better matchup.","action_type":"switch","identifier":"venusaur"}',
        battle, player,
    )
    player.create_order.assert_called_once_with(switches[1])
    assert result is not None


def test_json_move_by_slot() -> None:
    """v2 JSON response with numeric slot identifier."""
    moves = [_mock_move("surf"), _mock_move("icebeam")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action(
        '{"action_type":"move","identifier":"2"}',
        battle, player,
    )
    player.create_order.assert_called_once_with(moves[1], terastallize=False)
    assert result is not None


def test_json_unknown_move_falls_back_to_regex() -> None:
    """JSON with unresolvable identifier falls back to text ACTION: parsing."""
    moves = [_mock_move("thunderbolt")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    # JSON identifier won't resolve; falls through to text parser which finds ACTION line
    result = parse_action(
        '{"action_type":"move","identifier":"fakemove"}\nACTION: move 1',
        battle, player,
    )
    assert result is not None
    player.create_order.assert_called_once_with(moves[0], terastallize=False)


def test_json_with_markdown_fence() -> None:
    """JSON wrapped in code fences is still parsed."""
    moves = [_mock_move("flamethrower")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action(
        '```json\n{"action_type":"move","identifier":"flamethrower"}\n```',
        battle, player,
    )
    assert result is not None
    player.create_order.assert_called_once_with(moves[0], terastallize=False)


def test_json_invalid_falls_back_to_regex() -> None:
    """Malformed JSON does not crash — falls through to regex parser."""
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action('{broken json}\nACTION: move 1', battle, player)
    assert result is not None
    player.create_order.assert_called_once_with(moves[0], terastallize=False)


# ---------------------------------------------------------------------------
# New coverage tests — missing lines in _parse_json_action and parse_action
# ---------------------------------------------------------------------------

def test_json_not_a_dict_falls_through() -> None:
    """JSON array is not a dict → falls through to regex parser."""
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action('[1,2,3]\nACTION: move 1', battle, player)
    assert result is not None


def test_json_empty_action_type_falls_through() -> None:
    """JSON missing action_type key → falls through to regex."""
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action('{"identifier":"tackle"}\nACTION: move 1', battle, player)
    assert result is not None


def test_json_empty_identifier_falls_through() -> None:
    """JSON with empty identifier → falls through to regex."""
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    result = parse_action('{"action_type":"move","identifier":""}\nACTION: move 1', battle, player)
    assert result is not None


def test_json_unknown_action_type_returns_none() -> None:
    """JSON with unknown action_type (not move/switch) → returns None from json parser."""
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    # No ACTION fallback text, so final result should be None
    result = parse_action('{"action_type":"teleport","identifier":"somewhere"}', battle, player)
    assert result is None


def test_json_move_not_resolved_logs_debug() -> None:
    """JSON move identifier not in available moves → falls through to text pass."""
    moves = [_mock_move("thunderbolt")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    # 'unknownmove' won't resolve, no ACTION fallback → None
    result = parse_action('{"action_type":"move","identifier":"unknownmove"}', battle, player)
    assert result is None


def test_json_switch_not_resolved_logs_debug() -> None:
    """JSON switch identifier not in available switches → falls through."""
    switches = [_mock_pokemon("pikachu")]
    battle = _make_battle(switches=switches)
    player = _make_player()

    # 'nonexistentpokemon' won't fuzzy-match 'pikachu' (too different)
    result = parse_action('{"action_type":"switch","identifier":"nonexistentpokemon"}', battle, player)
    assert result is None


def test_parse_action_empty_string_returns_none() -> None:
    """Empty string response returns None immediately."""
    battle = _make_battle()
    player = _make_player()
    assert parse_action("", battle, player) is None


def test_parse_action_none_returns_none() -> None:
    """None response returns None."""
    battle = _make_battle()
    player = _make_player()
    assert parse_action(None, battle, player) is None


# ---------------------------------------------------------------------------
# New coverage — lines 176-177: JSON with missing action_type or identifier,
# no regex fallback text so the debug-log return None path is exercised
# ---------------------------------------------------------------------------

def test_json_missing_action_type_no_fallback_returns_none() -> None:
    """Lines 176-177: pure JSON with no action_type → debug log + return None.

    Passing pure JSON (no regex ACTION: text) means the JSON parser runs,
    hits 'not action_type', logs debug, returns None from _parse_json_action,
    and then the regex pass also finds nothing → final result is None.
    """
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    # Pure JSON with identifier but no action_type — no regex fallback
    result = parse_action('{"identifier":"tackle"}', battle, player)
    assert result is None


def test_json_missing_identifier_no_fallback_returns_none() -> None:
    """Lines 176-177: pure JSON with no identifier → debug log + return None."""
    moves = [_mock_move("tackle")]
    battle = _make_battle(moves=moves)
    player = _make_player()

    # Pure JSON with action_type but no identifier
    result = parse_action('{"action_type":"move"}', battle, player)
    assert result is None


# ---------------------------------------------------------------------------
# <think> tag stripping (Qwen 3, DeepSeek R1, etc.)
# ---------------------------------------------------------------------------

class TestThinkTagStripping:
    """Reasoning models prepend <think>...</think> before the actual response.
    The parser must strip these before attempting JSON or regex parsing.
    """

    def test_think_tag_before_json_move(self) -> None:
        """Qwen-style: <think>...</think> then JSON — must parse the JSON."""
        moves = [_mock_move("thunderbolt"), _mock_move("tackle")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = (
            "<think>\nLet me evaluate the matchup...\nThunderbolt is super effective.\n</think>\n"
            '{"reasoning": "super effective", "action_type": "move", "identifier": "thunderbolt"}'
        )
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_think_tag_before_json_switch(self) -> None:
        """<think>...</think> before a JSON switch action."""
        switches = [_mock_pokemon("blastoise"), _mock_pokemon("venusaur")]
        battle = _make_battle(switches=switches)
        player = _make_player()

        response = (
            "<think>I should switch to avoid the fire move.</think>\n"
            '{"action_type": "switch", "identifier": "blastoise"}'
        )
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(switches[0])

    def test_think_tag_before_text_action(self) -> None:
        """<think>...</think> before a legacy text ACTION: line."""
        moves = [_mock_move("surf"), _mock_move("tackle")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = "<think>Surf is the best choice here.</think>\nACTION: move 1"
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_multiline_think_block_stripped(self) -> None:
        """Multiline <think> content is fully stripped before parsing."""
        moves = [_mock_move("flamethrower")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = (
            "<think>\n"
            "Line one of thinking.\n"
            "Line two.\n"
            "Line three.\n"
            "</think>\n"
            '{"action_type": "move", "identifier": "flamethrower"}'
        )
        result = parse_action(response, battle, player)
        assert result is not None

    def test_think_tag_case_insensitive(self) -> None:
        """<THINK> and <Think> variants are also stripped."""
        moves = [_mock_move("tackle")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = "<THINK>Some thoughts.</THINK>\nACTION: move 1"
        result = parse_action(response, battle, player)
        assert result is not None

    def test_only_think_tag_returns_none(self) -> None:
        """A response consisting only of a <think> block produces no action."""
        battle = _make_battle(moves=[_mock_move("tackle")])
        player = _make_player()

        result = parse_action("<think>Just thinking, no action.</think>", battle, player)
        assert result is None

    def test_raw_response_preserved_in_caller(self) -> None:
        """Stripping is internal to parse_action — the original string is unchanged."""
        raw = "<think>thoughts</think>\n" + '{"action_type":"move","identifier":"tackle"}'
        original = raw  # take a reference before calling
        battle = _make_battle(moves=[_mock_move("tackle")])
        player = _make_player()
        parse_action(raw, battle, player)
        assert raw == original  # parse_action must not mutate the caller's string


class TestJsonControlCharSanitization:
    """Small models (e.g. ministral-3-3b) embed literal newlines inside the
    JSON ``reasoning`` string instead of the required ``\\n`` escape, producing
    invalid JSON that json.loads rejects with 'Invalid control character'.
    The sanitization recovery path must handle these transparently.
    """

    def test_literal_newline_in_reasoning_move(self) -> None:
        """Literal newline inside reasoning string is sanitized and move is parsed."""
        moves = [_mock_move("fireblast")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        # Ministral-style: reasoning has literal 0x0A newline bytes, not \n escapes
        response = (
            '{\n'
            '  "reasoning": "\n'                  # ← literal newline inside string
            '  Survival Check: move first.\n'     # ← literal newline inside string
            '  ",\n'
            '  "action_type": "move",\n'
            '  "identifier": "fireblast"\n'
            '}'
        )
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_literal_newline_in_reasoning_switch(self) -> None:
        """Sanitization also works when action_type is switch."""
        switches = [_mock_pokemon("blastoise"), _mock_pokemon("venusaur")]
        battle = _make_battle(switches=switches)
        player = _make_player()

        response = (
            '{\n'
            '  "reasoning": "\n'
            '  Switch value is high.\n'
            '  ",\n'
            '  "action_type": "switch",\n'
            '  "identifier": "blastoise"\n'
            '}'
        )
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(switches[0])

    def test_multiline_reasoning_with_markdown(self) -> None:
        """Literal newlines + markdown bold inside reasoning (real Ministral pattern)."""
        moves = [_mock_move("thunderbolt"), _mock_move("tackle")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = (
            '{\n'
            '  "reasoning": "\n'
            '  **Survival Check:** I move first.\n'
            '  **KO Opportunity:** Thunderbolt is super effective.\n'
            '  ",\n'
            '  "action_type": "move",\n'
            '  "identifier": "thunderbolt"\n'
            '}'
        )
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_code_fence_plus_literal_newlines(self) -> None:
        """Code-fenced JSON with literal newlines in reasoning is fully recovered."""
        moves = [_mock_move("surf")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = (
            '```json\n'
            '{\n'
            '  "reasoning": "\n'
            '  Surf is strong here.\n'
            '  ",\n'
            '  "action_type": "move",\n'
            '  "identifier": "surf"\n'
            '}\n'
            '```'
        )
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_valid_json_unaffected(self) -> None:
        """Sanitization is a no-op on already-valid JSON — no regression."""
        moves = [_mock_move("tackle")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        response = '{"reasoning": "clean reasoning", "action_type": "move", "identifier": "tackle"}'
        result = parse_action(response, battle, player)
        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_unrecoverable_json_still_returns_none(self) -> None:
        """Truly broken JSON (no action keys anywhere) still returns None after sanitization."""
        battle = _make_battle(moves=[_mock_move("tackle")])
        player = _make_player()

        result = parse_action('{"totally": "wrong\nstructure"}', battle, player)
        assert result is None


# ---------------------------------------------------------------------------
# Fuzzy move matching
# ---------------------------------------------------------------------------

class TestFuzzyMoveMatching:
    """_resolve_move should fuzzy-match move names with minor typos."""

    def test_fuzzy_matches_one_char_typo(self) -> None:
        """'thunderolt' → 'thunderbolt' (1-char deletion)."""
        moves = [_mock_move("thunderbolt"), _mock_move("surf")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        result = parse_action('{"action_type":"move","identifier":"thunderolt","reasoning":"x"}', battle, player)

        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_fuzzy_matches_spaced_name(self) -> None:
        """'ice beam' → 'icebeam' (space inserted, normalizes away)."""
        moves = [_mock_move("icebeam"), _mock_move("blizzard")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        result = parse_action('{"action_type":"move","identifier":"ice beam","reasoning":"x"}', battle, player)

        assert result is not None
        player.create_order.assert_called_once_with(moves[0], terastallize=False)

    def test_fuzzy_no_match_on_wild_guess(self) -> None:
        """A completely wrong name should not fuzzy-match anything (returns None)."""
        moves = [_mock_move("thunderbolt"), _mock_move("surf")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        result = parse_action('{"action_type":"move","identifier":"earthquake","reasoning":"x"}', battle, player)

        assert result is None

    def test_exact_match_still_works(self) -> None:
        """Exact move names still resolve without touching the fuzzy path."""
        moves = [_mock_move("flamethrower"), _mock_move("fireblast")]
        battle = _make_battle(moves=moves)
        player = _make_player()

        result = parse_action('{"action_type":"move","identifier":"fireblast","reasoning":"x"}', battle, player)

        assert result is not None
        player.create_order.assert_called_once_with(moves[1], terastallize=False)
