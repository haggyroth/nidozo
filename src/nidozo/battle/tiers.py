"""NatDex tier definitions and pool helpers.

Nidozo uses Gen 9 National Dex as its canonical ruleset so that any Pokémon from
any generation can be used with any move it can legally learn today.  This
eliminates per-generation legality maintenance and lets Showdown validate teams
automatically.

Showdown format strings used:
  gen9randombattle    — random tier (Showdown auto-generates teams, no data needed)
  gen9nationaldexag   — freeforall / ubers (NatDex Anything Goes, no ban list)
  gen9nationaldex     — ou (NatDex OU bans applied)
  gen9nationaldexlc   — lc (NatDex Little Cup)

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
    "eternatus",
    "fluttermane",
    "groudon",
    "ho-oh",
    "koraidon",
    "kyogre",
    "kyurem",
    "kyuremblack",
    "kyuremwhite",
    "lugia",
    "lunala",
    "marshadow",
    "mewtwo",
    "miraidon",
    "necrozma-dusk-mane",
    "necrozma-dawn-wings",
    "rayquaza",
    "solgaleo",
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
OU: Final[frozenset[str]] = frozenset({
    "dragapult",
    "garchomp",
    "heatran",
    "landorus",
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
    "urshifu",
    "urshifurapidstrike",
    "tornadus",
    "tornadustherian",
    "tapu-koko",
    "tapu-fini",
    "tapu-lele",
    "tapu-bulu",
    "buzzwole",
    "naganadel",
    "magearna",
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
    "slowbrotrop",  # Slowbro-Galar
    "alakazam",
    "alakazammega",
    "gengar",
    "gengarmega",
    "scizor",
    "scizormega",
    "heracross",
    "heracrossmega",
    "salamence",
    "salamencemega",
    "metagross",
    "metagrossmega",
    "gardevoir",
    "gardevoirmega",
    "lucario",
    "lucariomega",
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
    "rotomcut",
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


def resolve_format(tier: str, *, doubles: bool = False) -> str:
    """Return the Showdown format string for a tier and singles/doubles mode.

    Random tier maps to the random (auto-team) format; other tiers map to the
    NatDex format for the chosen mode. Preset handling is the caller's job —
    presets force their own singles AG format upstream.
    """
    if doubles:
        if tier == "random":
            return DOUBLES_RANDOM_FORMAT
        return TIER_TO_DOUBLES_FORMAT.get(tier, "gen9nationaldexdoublesubers")
    if tier == "random":
        return "gen9randombattle"
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
