"""Party preset definitions and team-string builder.

Presets are curated 6-mon teams with trainer-archetype flavour text.  They are
loaded once from data/party_presets.json and cached.  Every preset plays in the
gen9nationaldexag format (Anything Goes) so team legality is never an issue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PRESETS_PATH = Path(__file__).parent.parent.parent.parent / "data" / "party_presets.json"
_PRESET_CACHE: dict[str, "Preset"] | None = None

PRESET_FORMAT = "gen9nationaldexag"


@dataclass(frozen=True)
class Preset:
    slug: str
    name: str
    trainer_class: str
    flavour: str
    emoji: str
    pokemon: list[str]  # ordered list of 6 species IDs


def _load() -> dict[str, "Preset"]:
    global _PRESET_CACHE
    if _PRESET_CACHE is None:
        raw: list[dict[str, Any]] = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        _PRESET_CACHE = {
            entry["slug"]: Preset(
                slug=entry["slug"],
                name=entry["name"],
                trainer_class=entry["trainer_class"],
                flavour=entry["flavour"],
                emoji=entry["emoji"],
                pokemon=entry["pokemon"],
            )
            for entry in raw
        }
    return _PRESET_CACHE


def list_presets() -> list[dict[str, Any]]:
    """Return all presets as lightweight dicts (no team-string) for the API."""
    return [
        {
            "slug": p.slug,
            "name": p.name,
            "trainer_class": p.trainer_class,
            "flavour": p.flavour,
            "emoji": p.emoji,
            "pokemon": p.pokemon,
        }
        for p in _load().values()
    ]


def get_preset(slug: str) -> "Preset | None":
    return _load().get(slug)


def build_preset_team_string(slug: str) -> str:
    """Build a Showdown team export string for the named preset.

    Raises KeyError if the slug is unknown or if any species in the preset
    is missing from natdex_movesets.json.
    """
    from nidozo.battle.team_builder import build_team_string

    preset = _load().get(slug)
    if preset is None:
        raise KeyError(f"Unknown preset slug: {slug!r}")
    return build_team_string(preset.pokemon)
