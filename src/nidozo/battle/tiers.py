"""NatDex tier definitions and pool helpers.

Nidozo uses Gen 9 National Dex as its canonical ruleset so that any Pokémon from
any generation can be used with any move it can legally learn today.  This
eliminates per-generation legality maintenance and lets Showdown validate teams
automatically.

Showdown format strings used:
  gen9randombattle           — random tier (Showdown auto-generates teams, no data needed)
  gen9nationaldexag          — freeforall / ubers (NatDex Anything Goes, no ban list)
  gen9nationaldex            — ou (NatDex OU bans applied)
  gen9nationaldexlc          — lc (NatDex Little Cup)

3v3 / 4v4 custom formats (require a locally patched Showdown server):
  gen9nationaldex3v3         — 3-mon NatDex OU
  gen9nationaldexag3v3       — 3-mon NatDex AG
  gen9nationaldexlc3v3       — 3-mon NatDex LC
  gen9nationaldexdoubles4v4  — 4-mon NatDex Doubles
  gen9nationaldexdoublesubers4v4 — 4-mon NatDex Doubles Ubers

  Add these to your local Pokémon Showdown server's config/formats.ts using:
    { name: "NatDex 3v3", format: "gen9nationaldex3v3", teamSize: 3 }
  etc. — see ROADMAP for the exact snippets.

Tier pools are sourced from Showdown's factory-sets.json (competitive Gen 9 tiers).
``freeforall`` has no restriction — the pool is everything in natdex_movesets.json.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Tier sets — Showdown species IDs, sourced from Gen 9 factory-sets
# ---------------------------------------------------------------------------

# Uber: the very top of the power hierarchy (Ubers-legal)
UBERS: Final[frozenset[str]] = frozenset({
    "calyrexice",
    "calyrexshadow",
    "chiyu",
    "chienpaorestricted",
    "dialga",
    "dialga-origin",
    "dragapult",
    "eternatus",
    "fluttermane",
    "groudon",
    "ho-oh",
    "koraidon",
    "kyogre",
    "kyurem",
    "kyuremblack",
    "kyuremwhite",
    "landorus",
    "lugia",
    "lunala",
    "magearna",
    "marshadow",
    "mewtwo",
    "miraidon",
    "naganadel",
    "necrozma-dusk-mane",
    "necrozma-dawn-wings",
    "rayquaza",
    "solgaleo",
    "urshifu",
    "xerneas",
    "yveltal",
    "zacian",
    "zamazenta",
    "zekrom",
    "reshiram",
    "palkia",
    "giratina",
    "arceus",
})

# OU: the main competitive tier (NatDex OU and above that aren't Uber-banned)
# Species with natDexTier "Uber" or "AG" in Showdown's formats-data.ts are excluded —
# they are banned from gen9nationaldex and would cause team rejection at battle start.
OU: Final[frozenset[str]] = frozenset({
    "garchomp",
    "heatran",
    "landorustherian",
    "toxapex",
    "ferrothorn",
    "rotomwash",
    "clefable",
    "corviknight",
    "zapdos",
    "tyranitar",
    "hippowdon",
    "kartana",
    "volcarona",
    "urshifurapidstrike",
    "tornadus",
    "tornadustherian",
    "tapukoko",
    "tapufini",
    "tapulele",
    "tapubulu",
    "buzzwole",
    "blissey",
    "chansey",
    "skarmory",
    "magnezone",
    "excadrill",
    "serperior",
    "dragonite",
    "pelipper",
    "swampert",
    "hawlucha",
    "gliscor",
    "greninja",
    "slowbro",
    "slowbrogalar",
    "alakazam",
    "gengar",
    "scizor",
    "scizormega",
    "heracross",
    "heracrossmega",
    "salamence",
    "metagross",
    "gardevoir",
    "gardevoirmega",
    "lucario",
})

# UU: strong but not broken — use NatDex UU-legal species
UU: Final[frozenset[str]] = frozenset({
    "azumarill",
    "arcanine",
    "nidoking",
    "nidoqueen",
    "slowking",
    "slowkinggalar",
    "tentacruel",
    "gyarados",
    "gyaradosmega",
    "umbreon",
    "espeon",
    "sylveon",
    "togekiss",
    "glalie",
    "glaliemega",
    "rotomheat",
    "rotommow",
    "rotomfrost",
    "rotomfan",
    "talonflame",
    "amoonguss",
    "reuniclus",
    "jellicent",
    "shaymin",
    "victini",
    "cobalion",
    "virizion",
    "terrakion",
    "keldeo",
    "thundurus",
    "thundurustherian",
    "aegislash",
    "mantine",
    "suicune",
    "entei",
    "raikou",
    "jirachi",
    "celebi",
    "mew",
})

# LC: Little Cup (first-stage unevolved Pokémon at level 5)
LC: Final[frozenset[str]] = frozenset({
    "elekid",
    "magby",
    "dratini",
    "trapinch",
    "machop",
    "gastly",
    "larvitar",
    "abra",
    "snorunt",
    "carvanha",
    "mienfoo",
    "pawniard",
    "murkrow",
    "misdreavus",
    "gothita",
    "solosis",
    "timburr",
    "scraggy",
    "snover",
    "hippopotas",
    "bronzor",
    "staryu",
    "wynaut",
    "porygon",
    "vulpix",
    "slowpoke",
    "shellder",
    "seel",
    "diglett",
})

# Ordered from most to least restrictive (ascending leniency)
TIER_HIERARCHY: Final[list[str]] = ["lc", "uu", "ou", "ubers"]

# ---------------------------------------------------------------------------
# Format mapping — Nidozo tier → Pokémon Showdown format string
# ---------------------------------------------------------------------------

TIER_TO_FORMAT: Final[dict[str, str]] = {
    "ubers":      "gen9nationaldexag",   # NatDex Anything Goes — no ban list
    "ou":         "gen9nationaldex",     # NatDex OU
    "uu":         "gen9nationaldex",     # NatDex OU rules; our pool restricts species
    "lc":         "gen9nationaldexlc",   # NatDex Little Cup
    "freeforall": "gen9nationaldexag",   # most permissive; full natdex_movesets pool
}

# 3v3 singles formats (custom — require local Showdown server config)
TIER_TO_3V3_FORMAT: Final[dict[str, str]] = {
    "ubers":      "gen9nationaldexag3v3",
    "ou":         "gen9nationaldex3v3",
    "uu":         "gen9nationaldex3v3",
    "lc":         "gen9nationaldexlc3v3",
    "freeforall": "gen9nationaldexag3v3",
}

# 4v4 doubles formats (custom — bring 4, use 2 per turn; requires local Showdown server config)
TIER_TO_DOUBLES_4V4_FORMAT: Final[dict[str, str]] = {
    "ubers":      "gen9nationaldexdoublesubers4v4",
    "ou":         "gen9nationaldexdoubles4v4",
    "uu":         "gen9nationaldexdoubles4v4",
    "lc":         "gen9nationaldexdoubles4v4",
    "freeforall": "gen9nationaldexdoublesubers4v4",
}

# Display names shown in the frontend
TIER_DISPLAY: Final[dict[str, str]] = {
    "ubers":      "Ubers (NatDex AG)",
    "ou":         "OU (NatDex)",
    "uu":         "UU (NatDex)",
    "lc":         "Little Cup (NatDex)",
    "freeforall": "Free-for-All (NatDex AG)",
    "random":     "Random Battle (Gen 9)",
}

# Map tier ID → frozenset (None means "no restriction")
_TIER_POOLS: dict[str, frozenset[str] | None] = {
    "ubers":      UBERS,
    "ou":         OU,
    "uu":         UU,
    "lc":         LC,
    "freeforall": None,
}

# ---------------------------------------------------------------------------
# Doubles (2v2) format mapping — used when a battle is started with doubles=True
# ---------------------------------------------------------------------------

# Random doubles needs no team data (Showdown auto-generates). Non-random tiers
# map to NatDex Doubles; Ubers/free-for-all use the most permissive doubles
# ruleset so our broad pools validate.
TIER_TO_DOUBLES_FORMAT: Final[dict[str, str]] = {
    "ubers":      "gen9nationaldexdoublesubers",
    "ou":         "gen9nationaldexdoubles",
    "uu":         "gen9nationaldexdoubles",
    "lc":         "gen9nationaldexdoubles",
    "freeforall": "gen9nationaldexdoublesubers",
}

DOUBLES_RANDOM_FORMAT: Final[str] = "gen9randomdoublesbattle"


def resolve_format(tier: str, *, doubles: bool = False, team_size: int = 6) -> str:
    """Return the Showdown format string for a tier, mode, and team size.

    Random tier always maps to the standard random format regardless of team_size
    (Showdown auto-generates those teams, so our team_size is irrelevant).
    Custom 3v3/4v4 format strings require the local Showdown server to have the
    corresponding format definitions — see module docstring for setup instructions.
    """
    if doubles:
        if tier == "random":
            return DOUBLES_RANDOM_FORMAT
        if team_size == 4:
            return TIER_TO_DOUBLES_4V4_FORMAT.get(tier, "gen9nationaldexdoublesubers4v4")
        return TIER_TO_DOUBLES_FORMAT.get(tier, "gen9nationaldexdoublesubers")
    if tier == "random":
        return "gen9randombattle"
    if team_size == 3:
        return TIER_TO_3V3_FORMAT.get(tier, "gen9nationaldexag3v3")
    return TIER_TO_FORMAT.get(tier, "gen9nationaldexag")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_pool(tier: str, all_species: set[str]) -> list[str]:
    """Return the sorted list of legal species for *tier*.

    Args:
        tier:        One of the tier keys or "freeforall".
        all_species: All species IDs available in the moveset data.

    Returns:
        Sorted list of species IDs that are legal for the tier.

    Raises:
        ValueError: If *tier* is not a known tier key.
    """
    if tier not in _TIER_POOLS:
        raise ValueError(
            f"Unknown tier: {tier!r}. Valid tiers: {sorted(_TIER_POOLS)}"
        )
    allowed = _TIER_POOLS[tier]
    if allowed is None:
        # freeforall — everything that has a moveset defined
        return sorted(all_species)
    return sorted(allowed & all_species)


def is_valid_tier(tier: str) -> bool:
    """Return True if *tier* is a known tier key (including 'random')."""
    return tier in _TIER_POOLS or tier == "random"
