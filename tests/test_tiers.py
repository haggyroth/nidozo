"""Tests for tiers.py and team_builder.py."""

from __future__ import annotations

import pytest

from nidozo.battle.team_builder import (
    all_species,
    build_pokemon_block,
    build_team_string,
    get_pool_info,
    load_movesets,
)
from nidozo.battle.tiers import OU, UU, UBERS, TIER_DISPLAY, TIER_TO_FORMAT, get_pool, is_valid_tier

# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------

class TestGetPool:
    def test_ou_pool_is_nonempty(self) -> None:
        ms = load_movesets()
        pool = get_pool("ou", set(ms.keys()))
        assert len(pool) > 0

    def test_ou_pool_contains_salamence(self) -> None:
        ms = load_movesets()
        pool = get_pool("ou", set(ms.keys()))
        assert "salamence" in pool

    def test_freeforall_returns_all_species(self) -> None:
        ms = load_movesets()
        all_s = set(ms.keys())
        pool = get_pool("freeforall", all_s)
        assert set(pool) == all_s

    def test_uu_excludes_ou_pokemon(self) -> None:
        ms = load_movesets()
        uu_pool = get_pool("uu", set(ms.keys()))
        ou_pool = get_pool("ou", set(ms.keys()))
        assert not (set(uu_pool) & set(ou_pool)), "UU and OU pools should not overlap"

    def test_pool_is_sorted(self) -> None:
        ms = load_movesets()
        pool = get_pool("ou", set(ms.keys()))
        assert pool == sorted(pool)

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tier"):
            get_pool("superubers", {"salamence"})

    def test_lc_pool_contains_lc_pokemon(self) -> None:
        """LC pool returns only the intersection of LC tier and available movesets."""
        ms = load_movesets()
        pool = get_pool("lc", set(ms.keys()))
        # misdreavus is in both the LC tier definition and natdex_movesets.json
        assert "misdreavus" in pool

    def test_pool_only_includes_species_in_movesets(self) -> None:
        """Pool never returns a species that lacks a moveset definition."""
        pool = get_pool("ou", {"salamence", "nonexistent_mon"})
        assert "nonexistent_mon" not in pool
        assert "salamence" in pool


class TestIsValidTier:
    def test_known_tiers_are_valid(self) -> None:
        for tier in ("ubers", "ou", "uu", "lc", "freeforall", "random"):
            assert is_valid_tier(tier), f"Expected {tier!r} to be valid"

    def test_unknown_tier_is_invalid(self) -> None:
        assert not is_valid_tier("bl2")
        assert not is_valid_tier("")
        assert not is_valid_tier("gen9ou")
        assert not is_valid_tier("nu")


class TestTierConstants:
    def test_all_tiers_have_display_names(self) -> None:
        for tier in ("ubers", "ou", "uu", "lc", "freeforall", "random"):
            assert tier in TIER_DISPLAY

    def test_all_tiers_have_format_mapping(self) -> None:
        for tier in ("ubers", "ou", "uu", "lc", "freeforall"):
            assert tier in TIER_TO_FORMAT
            fmt = TIER_TO_FORMAT[tier]
            assert fmt.startswith("gen9"), f"Expected gen9 format for {tier!r}, got {fmt!r}"

    # ND Uber / ban-list guard — species banned from gen9nationaldex must not
    # appear in the OU pool (they cause team rejection at battle start).
    def test_nd_uber_banned_species_not_in_ou(self) -> None:
        nd_ubers_in_pools = {
            "dragapult", "landorus", "urshifu", "naganadel", "magearna",
            "alakazammega", "gengarmega", "salamencemega", "metagrossmega", "lucariomega",
        }
        leaked = nd_ubers_in_pools & OU
        assert not leaked, f"ND Uber species leaked into OU pool: {leaked}"

    def test_ou_pool_uses_correct_showdown_species_ids(self) -> None:
        bad_ids = {"tapu-koko", "tapu-fini", "tapu-lele", "tapu-bulu", "slowbrotrop"}
        leaked = bad_ids & OU
        assert not leaked, f"OU pool contains hyphenated/wrong Showdown IDs: {leaked}"

    def test_uu_pool_uses_correct_showdown_species_ids(self) -> None:
        assert "rotomcut" not in UU, "UU pool uses wrong ID 'rotomcut'; should be 'rotommow'"

    def test_nd_uber_species_available_in_ubers_pool(self) -> None:
        expected = {"dragapult", "landorus", "urshifu", "naganadel", "magearna"}
        missing = expected - UBERS
        assert not missing, f"ND Uber species missing from UBERS pool: {missing}"


# ---------------------------------------------------------------------------
# team_builder helpers
# ---------------------------------------------------------------------------

class TestLoadMovesets:
    def test_returns_dict(self) -> None:
        ms = load_movesets()
        assert isinstance(ms, dict)
        assert len(ms) > 50

    def test_no_comment_key(self) -> None:
        ms = load_movesets()
        assert "__comment" not in ms

    def test_each_entry_has_required_fields(self) -> None:
        ms = load_movesets()
        required = {"species", "item", "ability", "nature", "moves"}
        for sid, entry in ms.items():
            missing = required - entry.keys()
            assert not missing, f"{sid!r} missing fields: {missing}"

    def test_each_entry_has_between_one_and_four_moves(self) -> None:
        """Some Gen 9 species (e.g. Ditto with only Transform) have fewer than 4 moves."""
        ms = load_movesets()
        for sid, entry in ms.items():
            n = len(entry["moves"])
            assert 1 <= n <= 4, f"{sid!r} has {n} moves (expected 1–4)"

    def test_ev_totals_do_not_exceed_510(self) -> None:
        ms = load_movesets()
        for sid, entry in ms.items():
            total = sum(entry.get("evs", {}).values())
            assert total <= 510, f"{sid!r} EV total {total} exceeds 510"

    def test_caching_returns_same_object(self) -> None:
        ms1 = load_movesets()
        ms2 = load_movesets()
        assert ms1 is ms2


class TestAllSpecies:
    def test_returns_set_of_strings(self) -> None:
        s = all_species()
        assert isinstance(s, set)
        assert all(isinstance(x, str) for x in s)

    def test_contains_expected_pokemon(self) -> None:
        s = all_species()
        for mon in ("salamence", "tyranitar", "gengar", "blissey"):
            assert mon in s


class TestBuildPokemonBlock:
    def test_basic_block_structure(self) -> None:
        """Salamence uses a Gen 9 NatDex set — verify the current factory set renders correctly."""
        ms = load_movesets()
        entry = ms["salamence"]
        block = build_pokemon_block("salamence", entry)
        # Header contains species + item
        assert f"Salamence @ {entry['item']}" in block
        # Ability line present
        assert f"Ability: {entry['ability']}" in block
        # Nature line present
        assert f"{entry['nature']} Nature" in block
        # All 4 moves rendered
        for move in entry["moves"]:
            assert f"- {move}" in block

    def test_ev_line_format(self) -> None:
        ms = load_movesets()
        block = build_pokemon_block("salamence", ms["salamence"])
        assert "EVs:" in block

    def test_level_100_not_shown(self) -> None:
        """Level 100 is default and should NOT appear in the block."""
        ms = load_movesets()
        block = build_pokemon_block("salamence", ms["salamence"])
        assert "Level: 100" not in block

    def test_level_5_is_shown_when_set(self) -> None:
        """build_pokemon_block must emit 'Level: 5' for a moveset that specifies level=5."""
        moveset = {
            "species": "Elekid",
            "item": "Eviolite",
            "ability": "Static",
            "nature": "Timid",
            "level": 5,
            "evs": {"SpA": 196, "Spe": 196},
            "moves": ["Thunderbolt", "Ice Punch", "Cross Chop", "Hidden Power [Ice]"],
        }
        block = build_pokemon_block("elekid", moveset)
        assert "Level: 5" in block

    def test_four_moves_rendered(self) -> None:
        ms = load_movesets()
        for sid in ("tyranitar", "gengar", "blissey"):
            block = build_pokemon_block(sid, ms[sid])
            move_lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
            assert len(move_lines) == 4, f"{sid!r} block has {len(move_lines)} move lines"

    def test_iv_line_emitted_when_ivs_present(self) -> None:
        """If a moveset specifies non-31 IVs the block must include an IVs: line."""
        moveset = {
            "species": "Zapdos",
            "item": "Leftovers",
            "ability": "Pressure",
            "nature": "Timid",
            "evs": {"SpA": 252, "Spe": 252, "HP": 4},
            "ivs": {"Spe": 30},   # HP Ice requires 30 Spe
            "moves": ["Thunderbolt", "Hidden Power [Ice]", "Thunder Wave", "Roost"],
        }
        block = build_pokemon_block("zapdos", moveset)
        assert "IVs: 30 Spe" in block

    def test_no_iv_line_when_all_31(self) -> None:
        """If all IVs are 31 (or ivs key absent) no IVs: line should appear."""
        ms = load_movesets()
        # Tyranitar uses no Hidden Power — should have no ivs key and no IVs line.
        block = build_pokemon_block("tyranitar", ms["tyranitar"])
        assert "IVs:" not in block

    def test_iv_line_omits_31_values(self) -> None:
        """Only sub-31 IVs should be listed, not the 31s."""
        moveset = {
            "species": "TestMon",
            "nature": "Hardy",
            "ivs": {"HP": 30, "Atk": 31, "SpA": 28},
            "moves": ["Tackle"],
        }
        block = build_pokemon_block("testmon", moveset)
        assert "30 HP" in block
        assert "28 SpA" in block
        assert "31 Atk" not in block

    def test_hidden_power_users_have_correct_iv_spreads(self) -> None:
        """Every Hidden Power user in gen3_movesets.json must have IVs that
        match the declared HP type (type formula) and hit max power (70)."""
        stats_order = ["HP", "Atk", "Def", "Spe", "SpA", "SpD"]
        weights = [1, 2, 4, 8, 16, 32]
        type_names = [
            "Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost",
            "Steel", "Fire", "Water", "Grass", "Electric", "Psychic", "Ice",
            "Dragon", "Dark",
        ]

        def calc_hp(ivs: dict) -> tuple[str, int]:
            iv_vals = {s: ivs.get(s, 31) for s in stats_order}
            sum1 = sum(weights[i] * (iv_vals[stats_order[i]] % 2) for i in range(6))
            sum2 = sum(weights[i] * ((iv_vals[stats_order[i]] >> 1) % 2) for i in range(6))
            hp_type = type_names[(15 * sum1) // 63]
            hp_power = (40 * sum2) // 63 + 30
            return hp_type, hp_power

        ms = load_movesets()
        for sid, entry in ms.items():
            for move in entry.get("moves", []):
                if move.startswith("Hidden Power ["):
                    expected_type = move[14:-1]
                    ivs = entry.get("ivs", {})
                    actual_type, power = calc_hp(ivs)
                    assert actual_type == expected_type, (
                        f"{sid}: move '{move}' but IVs give HP {actual_type}"
                    )
                    assert power == 70, (
                        f"{sid}: HP power is {power}, expected 70"
                    )

    def test_no_splash_or_struggle_in_sets(self) -> None:
        """Splash and Struggle are filler moves — no competitive set should use them."""
        ms = load_movesets()
        filler = {"Splash", "Struggle"}
        for sid, entry in ms.items():
            for move in entry.get("moves", []):
                assert move not in filler, (
                    f"{sid} has filler move '{move}' in its competitive set"
                )


class TestBuildTeamString:
    def test_six_pokemon_produces_five_separators(self) -> None:
        team = ["salamence", "tyranitar", "gengar", "swampert", "skarmory", "blissey"]
        result = build_team_string(team)
        # Pokémon blocks are separated by double newlines
        assert result.count("\n\n") == 5

    def test_unknown_species_raises(self) -> None:
        with pytest.raises(KeyError, match="nonexistent_mon"):
            build_team_string(["nonexistent_mon"])

    def test_team_string_contains_all_species(self) -> None:
        team = ["salamence", "blissey", "skarmory"]
        result = build_team_string(team)
        assert "Salamence" in result
        assert "Blissey" in result
        assert "Skarmory" in result


class TestGetPoolInfo:
    def test_returns_list_of_dicts(self) -> None:
        info = get_pool_info(["salamence", "tyranitar"])
        assert len(info) == 2
        assert info[0]["species_id"] == "salamence"
        assert info[0]["species"] == "Salamence"
        assert isinstance(info[0]["types"], list)

    def test_unknown_species_returns_fallback(self) -> None:
        info = get_pool_info(["unknownmon"])
        assert info[0]["species_id"] == "unknownmon"
        assert info[0]["types"] == []


# ---------------------------------------------------------------------------
# Store: teams & draft sessions
# ---------------------------------------------------------------------------

class TestStoreTeams:
    def test_save_and_retrieve_team(self, tmp_path: pytest.TempPathFactory) -> None:
        from nidozo.db.store import BattleStore

        store = BattleStore(tmp_path / "test.db")
        model_id = store.get_or_create_model("lmstudio", "test-model", "v3")

        team_id = store.save_team(
            model_id=model_id,
            tier="ou",
            format_="gen9nationaldex",
            pokemon=["salamence", "tyranitar", "gengar"],
            team_string="Salamence @ ...",
        )
        assert isinstance(team_id, int)
        assert team_id > 0

        teams = store.get_teams_for_model(model_id)
        assert len(teams) == 1
        assert teams[0]["tier"] == "ou"
        assert teams[0]["pokemon"] == ["salamence", "tyranitar", "gengar"]

    def test_save_draft_session(self, tmp_path: pytest.TempPathFactory) -> None:
        from nidozo.db.store import BattleStore

        store = BattleStore(tmp_path / "test.db")
        model_id = store.get_or_create_model("lmstudio", "test-model", "v3")

        session_id = store.save_draft_session(
            model_id=model_id,
            tier="ou",
            pool_size=38,
            picked=["salamence", "tyranitar", "gengar", "swampert", "skarmory", "blissey"],
            prompt_version="v3",
            reasoning="Pick 1 (Salamence): Fast attacker.",
        )
        assert isinstance(session_id, int)

    def test_set_and_get_battle_teams(self, tmp_path: pytest.TempPathFactory) -> None:
        from nidozo.db.store import BattleStore

        store = BattleStore(tmp_path / "test.db")
        p1_id = store.get_or_create_model("lmstudio", "m1", "v3")
        p2_id = store.get_or_create_model("lmstudio", "m2", "v3")

        bid = store.create_battle("tag-abc", "gen9nationaldex", p1_id, p2_id)

        t1 = store.save_team(p1_id, "ou", "gen9nationaldex", ["salamence"], "Salamence @ ...")
        t2 = store.save_team(p2_id, "ou", "gen9nationaldex", ["tyranitar"], "Tyranitar @ ...")

        store.set_battle_teams(bid, t1, t2, "ou")

        p1_team, p2_team = store.get_battle_teams(bid)
        assert p1_team is not None
        assert p1_team["pokemon"] == ["salamence"]
        assert p2_team is not None
        assert p2_team["pokemon"] == ["tyranitar"]

    def test_get_battle_teams_for_random_battle(self, tmp_path: pytest.TempPathFactory) -> None:
        from nidozo.db.store import BattleStore

        store = BattleStore(tmp_path / "test.db")
        p1_id = store.get_or_create_model("random", "random", "v2")
        p2_id = store.get_or_create_model("random", "random", "v2")

        bid = store.create_battle("tag-xyz", "gen9randombattle", p1_id, p2_id)
        p1_team, p2_team = store.get_battle_teams(bid)
        assert p1_team is None
        assert p2_team is None


# ---------------------------------------------------------------------------
# New coverage tests — team_builder missing line
# ---------------------------------------------------------------------------

class TestBuildPokemonBlockNoItem:
    def test_no_item_omits_at_sign(self) -> None:
        """When item is empty/falsy, the header has only the species name (no '@')."""
        from nidozo.battle.team_builder import build_pokemon_block

        moveset = {
            "species": "Magikarp",
            "item": "",   # empty → falsy → no @ in header
            "ability": "Swift Swim",
            "nature": "Jolly",
            "level": 100,
            "evs": {},
            "moves": ["Splash", "Tackle", "Flail", "Bounce"],
        }
        block = build_pokemon_block("magikarp", moveset)
        lines = block.splitlines()
        assert lines[0] == "Magikarp"
        assert "@" not in lines[0]
