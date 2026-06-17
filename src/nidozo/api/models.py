"""Pydantic request / response models for the Nidozo API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Closed enums shared across requests. Invalid values are rejected at the API
# boundary (422) instead of silently degrading deep in a background task — e.g.
# an unknown provider used to fall through to LM Studio, an unknown tier to AG.
Provider = Literal["random", "anthropic", "openai", "lmstudio"]
# Coaches must be a real LLM backend — "random" has no coach implementation.
CoachProvider = Literal["anthropic", "openai", "lmstudio"]
PromptVersion = Literal["v1", "v2", "v3", "v4", "v5", "v6", "v7"]
# Exactly the tiers the backend supports (is_valid_tier / _TIER_POOLS + random).
Tier = Literal["random", "ou", "ubers", "uu", "lc", "freeforall"]
TournamentFormat = Literal["round_robin", "single_elim", "double_elim"]
# Named play-style personas injected into the system prompt (None = no persona).
Personality = Literal["aggressive", "defensive", "balanced", "trickster", "momentum"]
# Curated 6-mon trainer-archetype teams (None = use random/draft as normal).
PresetSlug = Literal[
    "ash_kanto", "rival_gauntlet", "dragons_lair", "ghost_gang", "inferno_squad",
    "aqua_armada", "eevee_squad", "steel_titans", "electric_storm", "champion_finest",
]


class StartBattleRequest(BaseModel):
    p1_provider: Provider = "random"
    p2_provider: Provider = "random"
    p1_model: str | None = None
    p2_model: str | None = None
    model: str | None = None
    prompt_version: PromptVersion = "v6"
    n_battles: int = Field(1, ge=1, le=50)
    tier: Tier = "random"
    draft: bool = False    # If True and tier != "random", run LLM draft phase first
    # Doubles (2v2 per turn): when True, uses a Showdown doubles format.
    # Random tier → gen9randomdoublesbattle; non-random → NatDex Doubles.
    # Presets are singles-only and force doubles=False.
    doubles: bool = False
    # Team size: how many Pokémon each player brings. 6=standard, 3=3v3 singles,
    # 4=4v4 doubles (bring 4, use 2 per turn — VGC-style). 3v3/4v4 require custom
    # Showdown format definitions on the local server (see tiers.py docstring).
    # Presets are fixed 6-mon teams and require team_size=6.
    team_size: Literal[3, 4, 6] = 6
    # Optional coach model per player (None = no coach)
    p1_coach_provider: CoachProvider | None = None
    p1_coach_model: str | None = None
    p2_coach_provider: CoachProvider | None = None
    p2_coach_model: str | None = None
    # Optional play-style persona injected into the system prompt (None = no persona)
    p1_personality: Personality | None = None
    p2_personality: Personality | None = None
    # Optional trainer-archetype preset team (overrides draft; forces AG format)
    p1_preset: PresetSlug | None = None
    p2_preset: PresetSlug | None = None

    @model_validator(mode="after")
    def validate_team_size_constraints(self) -> StartBattleRequest:
        if (self.p1_preset or self.p2_preset) and self.team_size != 6:
            raise ValueError("Preset teams are fixed 6-mon rosters — team_size must be 6 when using presets")
        return self


class StartBattleResponse(BaseModel):
    battle_ids: list[int]
    message: str


class PlayerSpec(BaseModel):
    provider: Provider
    model: str | None = None
    # Optional coach — None means this player acts without advisory
    coach_provider: CoachProvider | None = None
    coach_model: str | None = None
    # Optional play-style persona injected into the system prompt
    personality: Personality | None = None
    # Optional trainer-archetype preset team
    preset: PresetSlug | None = None


class StartTournamentRequest(BaseModel):
    players: list[PlayerSpec] = Field(..., min_length=2, max_length=12)
    rounds: int = Field(1, ge=1, le=10)
    prompt_version: PromptVersion = "v6"
    tier: Tier = "random"
    draft: bool = False    # If True and tier != "random", run LLM draft phase before each battle
    tournament_format: TournamentFormat = "round_robin"
    doubles: bool = False
    team_size: Literal[3, 4, 6] = 6


class StartTournamentResponse(BaseModel):
    tournament_id: int
    battle_ids: list[int]
    total_battles: int
    message: str


class StartSeasonRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    players: list[PlayerSpec] = Field(..., min_length=2, max_length=12)
    rounds: int = Field(1, ge=1, le=10)
    prompt_version: PromptVersion = "v6"
    tier: Tier = "random"
    draft: bool = False
    doubles: bool = False
    team_size: Literal[3, 4, 6] = 6


class StartSeasonResponse(BaseModel):
    season_id: int
    battle_ids: list[int]
    total_battles: int
    message: str
