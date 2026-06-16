"""Pydantic request / response models for the Nidozo API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Closed enums shared across requests. Invalid values are rejected at the API
# boundary (422) instead of silently degrading deep in a background task — e.g.
# an unknown provider used to fall through to LM Studio, an unknown tier to AG.
Provider = Literal["random", "anthropic", "openai", "lmstudio"]
# Coaches must be a real LLM backend — "random" has no coach implementation.
CoachProvider = Literal["anthropic", "openai", "lmstudio"]
PromptVersion = Literal["v1", "v2", "v3", "v4", "v5"]
# Exactly the tiers the backend supports (is_valid_tier / _TIER_POOLS + random).
Tier = Literal["random", "ou", "ubers", "uu", "lc", "freeforall"]
TournamentFormat = Literal["round_robin", "single_elim", "double_elim"]
# Named play-style personas injected into the system prompt (None = no persona).
Personality = Literal["aggressive", "defensive", "balanced", "trickster", "momentum"]


class StartBattleRequest(BaseModel):
    p1_provider: Provider = "random"
    p2_provider: Provider = "random"
    p1_model: str | None = None
    p2_model: str | None = None
    model: str | None = None
    prompt_version: PromptVersion = "v5"
    n_battles: int = Field(1, ge=1, le=50)
    tier: Tier = "random"
    draft: bool = False    # If True and tier != "random", run LLM draft phase first
    # Optional coach model per player (None = no coach)
    p1_coach_provider: CoachProvider | None = None
    p1_coach_model: str | None = None
    p2_coach_provider: CoachProvider | None = None
    p2_coach_model: str | None = None
    # Optional play-style persona injected into the system prompt (None = no persona)
    p1_personality: Personality | None = None
    p2_personality: Personality | None = None


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


class StartTournamentRequest(BaseModel):
    players: list[PlayerSpec] = Field(..., min_length=2, max_length=12)
    rounds: int = Field(1, ge=1, le=10)
    prompt_version: PromptVersion = "v5"
    tier: Tier = "random"
    draft: bool = False    # If True and tier != "random", run LLM draft phase before each battle
    tournament_format: TournamentFormat = "round_robin"


class StartTournamentResponse(BaseModel):
    tournament_id: int
    battle_ids: list[int]
    total_battles: int
    message: str


class StartSeasonRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    players: list[PlayerSpec] = Field(..., min_length=2, max_length=12)
    rounds: int = Field(1, ge=1, le=10)
    prompt_version: PromptVersion = "v5"
    tier: Tier = "random"
    draft: bool = False


class StartSeasonResponse(BaseModel):
    season_id: int
    battle_ids: list[int]
    total_battles: int
    message: str
