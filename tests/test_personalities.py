"""Tests for the personalities module — registry consistency and lookup helpers."""

import pytest

from nidozo.battle.personalities import PERSONALITIES, Personality, get_personality

EXPECTED_SLUGS = {"aggressive", "defensive", "balanced", "trickster", "momentum"}


class TestRegistry:
    def test_all_expected_slugs_present(self) -> None:
        assert set(PERSONALITIES.keys()) == EXPECTED_SLUGS

    def test_each_entry_is_personality_instance(self) -> None:
        for slug, p in PERSONALITIES.items():
            assert isinstance(p, Personality), f"{slug!r} is not a Personality"

    def test_slug_matches_dict_key(self) -> None:
        for key, p in PERSONALITIES.items():
            assert p.slug == key, f"slug mismatch: key={key!r}, p.slug={p.slug!r}"

    def test_all_display_names_non_empty(self) -> None:
        for p in PERSONALITIES.values():
            assert p.display_name.strip(), f"{p.slug!r} has empty display_name"

    def test_all_descriptions_non_empty(self) -> None:
        for p in PERSONALITIES.values():
            assert p.description.strip(), f"{p.slug!r} has empty description"

    def test_all_emojis_non_empty(self) -> None:
        for p in PERSONALITIES.values():
            assert p.emoji.strip(), f"{p.slug!r} has empty emoji"

    def test_all_prompt_blocks_non_empty(self) -> None:
        for p in PERSONALITIES.values():
            assert len(p.prompt_block.strip()) > 50, (
                f"{p.slug!r} prompt_block too short: {p.prompt_block!r}"
            )

    def test_prompt_blocks_unique(self) -> None:
        blocks = [p.prompt_block for p in PERSONALITIES.values()]
        assert len(blocks) == len(set(blocks)), "Two personalities share an identical prompt block"

    def test_display_names_unique(self) -> None:
        names = [p.display_name for p in PERSONALITIES.values()]
        assert len(names) == len(set(names)), "Two personalities share the same display_name"


class TestGetPersonality:
    def test_known_slug_returns_personality(self) -> None:
        p = get_personality("aggressive")
        assert p is not None
        assert p.slug == "aggressive"

    @pytest.mark.parametrize("slug", list(EXPECTED_SLUGS))
    def test_all_slugs_resolve(self, slug: str) -> None:
        p = get_personality(slug)
        assert p is not None
        assert p.slug == slug

    def test_none_slug_returns_none(self) -> None:
        assert get_personality(None) is None

    def test_unknown_slug_returns_none(self) -> None:
        assert get_personality("does_not_exist") is None

    def test_empty_string_returns_none(self) -> None:
        assert get_personality("") is None
