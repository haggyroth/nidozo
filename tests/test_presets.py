"""Tests for party presets (presets.py)."""

from __future__ import annotations

import pytest

from nidozo.battle.presets import (
    PRESET_FORMAT,
    build_preset_team_string,
    get_preset,
    list_presets,
)

_REQUIRED_KEYS = {"slug", "name", "trainer_class", "flavour", "emoji", "pokemon"}


def test_list_presets_shape() -> None:
    presets = list_presets()
    assert len(presets) >= 10  # the curated trainer archetypes
    for p in presets:
        assert _REQUIRED_KEYS <= p.keys()
        assert len(p["pokemon"]) == 6, f"{p['slug']} should have 6 mons"
        assert all(isinstance(s, str) and s for s in p["pokemon"])


def test_preset_slugs_are_unique() -> None:
    slugs = [p["slug"] for p in list_presets()]
    assert len(slugs) == len(set(slugs))


@pytest.mark.parametrize("slug", [p["slug"] for p in list_presets()])
def test_build_preset_team_string_is_valid_for_every_preset(slug: str) -> None:
    """Every curated preset builds a 6-mon Showdown export with no missing species."""
    team = build_preset_team_string(slug)
    assert team.strip()
    # Six Pokémon blocks → five blank-line separators (matches build_team_string).
    assert team.count("\n\n") == 5


def test_get_preset_known_and_unknown() -> None:
    sample = list_presets()[0]["slug"]
    preset = get_preset(sample)
    assert preset is not None
    assert preset.slug == sample
    assert get_preset("definitely-not-a-preset") is None


def test_build_preset_team_string_unknown_slug_raises() -> None:
    with pytest.raises(KeyError, match="Unknown preset slug"):
        build_preset_team_string("definitely-not-a-preset")


def test_preset_format_is_anything_goes() -> None:
    assert PRESET_FORMAT == "gen9nationaldexag"
