"""
Run a random-singles battle between two players.

Requires the local Showdown server to be running first:
    ./scripts/start_showdown.sh

Usage:
    # Two random bots (default)
    uv run python scripts/run_battle.py

    # LLM (Anthropic) vs random bot
    uv run python scripts/run_battle.py --p1 anthropic --model claude-sonnet-4-6

    # LLM vs LLM
    uv run python scripts/run_battle.py --p1 anthropic --p2 anthropic

Environment variables:
    ANTHROPIC_API_KEY   — required when --p1 or --p2 is "anthropic"
    OPENAI_API_KEY      — required when --p1 or --p2 is "openai"
    LM_STUDIO_BASE_URL  — base URL for LM Studio (default: http://localhost:1234/v1)
    LM_STUDIO_MODEL     — model name for LM Studio (default: local-model)
    NIDOZO_DB           — path to SQLite DB (default: nidozo.db in repo root)
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from poke_env.player import Player

from nidozo.api.helpers import _JSON_OUTPUT_PROMPT_VERSIONS as _JSON_VERSIONS
from nidozo.battle.bots import RandomBot
from nidozo.battle.llm_player import LLMPlayer
from nidozo.db.store import BattleStore
from nidozo.llm import AnthropicBackend, OpenAIBackend

logging.basicConfig(level=logging.WARNING)

_FORMAT = "gen9randombattle"


def _showdown_cfg() -> object:
    """Env-driven Showdown server config (NIDOZO_SHOWDOWN_HOST / NIDOZO_SHOWDOWN_PORT).

    Mirrors the API's ``nidozo.api.orchestration._showdown_cfg`` so the CLI can
    run against a containerised or remote Showdown, not just localhost. Defaults
    to ``localhost:8000`` (bare-metal dev), matching the old LocalhostServerConfiguration.
    """
    from poke_env.ps_client.server_configuration import ServerConfiguration

    host = os.environ.get("NIDOZO_SHOWDOWN_HOST", "localhost")
    port = os.environ.get("NIDOZO_SHOWDOWN_PORT", "8000")
    return ServerConfiguration(
        websocket_url=f"ws://{host}:{port}/showdown/websocket",
        authentication_url="https://play.pokemonshowdown.com/action.php?",
    )


def _build_player(
    provider: str,
    model: str | None,
    role: str,
    store: BattleStore,
    battle_id: int,
    prompt_version: str = "v9",
) -> Player:
    cfg = _showdown_cfg()

    if provider == "random":
        return RandomBot(battle_format=_FORMAT, server_configuration=cfg)

    use_json_mode = prompt_version in _JSON_VERSIONS and provider in ("lmstudio", "openai")

    if provider == "anthropic":
        backend = AnthropicBackend(
            model=model or "claude-sonnet-4-6",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    elif provider == "openai":
        backend = OpenAIBackend(
            model=model or "gpt-4o",
            api_key=os.environ.get("OPENAI_API_KEY"),
            json_mode=use_json_mode,
        )
    elif provider == "lmstudio":
        backend = OpenAIBackend(
            model=model or os.environ.get("LM_STUDIO_MODEL", "local-model"),
            base_url=os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
            api_key="lm-studio",
            json_mode=use_json_mode,
        )
    else:
        raise ValueError(
            f"Unknown provider '{provider}'. Use: random, anthropic, openai, lmstudio"
        )

    return LLMPlayer(
        backend=backend,
        prompt_version=prompt_version,
        store=store,
        battle_id=battle_id,
        player_role=role,
        battle_format=_FORMAT,
        server_configuration=cfg,
    )


def _model_name_for(provider: str, model: str | None) -> str:
    defaults = {
        "anthropic": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "lmstudio": os.environ.get("LM_STUDIO_MODEL", "local-model"),
        "random": "random",
    }
    return model or defaults.get(provider, provider)


async def main(
    p1_provider: str,
    p2_provider: str,
    model: str | None,
    db_path: Path,
    n_battles: int = 1,
    prompt_version: str = "v9",
) -> None:
    store = BattleStore(db_path)

    p1_model = _model_name_for(p1_provider, model)
    p2_model = _model_name_for(p2_provider, model)

    p1_id = store.get_or_create_model(p1_provider, p1_model, prompt_version)
    p2_id = store.get_or_create_model(p2_provider, p2_model, prompt_version)

    for i in range(n_battles):
        # Placeholder tag — poke-env assigns the real tag after login;
        # we update it once the battle finishes.
        battle_tag = f"pending-{p1_provider}-vs-{p2_provider}-{i}"
        battle_id = store.create_battle(battle_tag, _FORMAT, p1_id, p2_id)

        p1 = _build_player(p1_provider, model, "p1", store, battle_id, prompt_version)
        p2 = _build_player(p2_provider, model, "p2", store, battle_id, prompt_version)

        if n_battles > 1:
            print(f"\nBattle {i+1}/{n_battles}: {p1.username} ({p1_provider}) vs {p2.username} ({p2_provider})")
        else:
            print(f"Starting battle: {p1.username} ({p1_provider}) vs {p2.username} ({p2_provider})")

        await p1.battle_against(p2, n_battles=1)

        if p1.n_finished_battles == 0:
            print("WARNING: no battles finished — is the Showdown server running?")
            store.close()
            return

        # Determine winner (1=p1, 2=p2, None=tie)
        if p1.n_won_battles > 0:
            winner = 1
            winner_name = f"{p1.username} ({p1_provider})"
        elif p2.n_won_battles > 0:
            winner = 2
            winner_name = f"{p2.username} ({p2_provider})"
        else:
            winner = None
            winner_name = "tie"

        # poke-env battle tag is the key in the battles dict
        real_tag = next(iter(p1.battles), battle_tag)
        battle_obj = p1.battles.get(real_tag)
        total_turns = battle_obj.turn if battle_obj else 0

        # Update the placeholder tag to the real one
        store._conn.execute(
            "UPDATE battles SET battle_tag=? WHERE id=?", (real_tag, battle_id)
        )
        store.finish_battle(battle_id, winner, total_turns)

        for player, provider in ((p1, p1_provider), (p2, p2_provider)):
            w, lo, t = player.n_won_battles, player.n_lost_battles, player.n_tied_battles
            print(f"  {player.username} [{provider}]: {w}W / {lo}L / {t}T")
        print(f"  Winner: {winner_name}  |  Turns: {total_turns}")

    store.close()
    print(f"\nResults saved to {db_path}")
    print("Run `uv run python scripts/leaderboard.py` to see rankings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Pokémon battle")
    parser.add_argument("--p1", default="random",
                        help="Player 1 provider (random/anthropic/openai/lmstudio)")
    parser.add_argument("--p2", default="random",
                        help="Player 2 provider (random/anthropic/openai/lmstudio)")
    parser.add_argument("--model", default=None,
                        help="Model name override for LLM players")
    parser.add_argument("--battles", type=int, default=1,
                        help="Number of battles to run (default: 1)")
    parser.add_argument("--db", default=None,
                        help="Path to SQLite DB (default: nidozo.db in repo root)")
    parser.add_argument("--prompt-version", default="v9",
                        choices=["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"],
                        help="Prompt version to use (default: v9)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(
        os.environ.get("NIDOZO_DB") or os.environ.get("NIMZO_DB", "nidozo.db")
    )

    asyncio.run(main(args.p1, args.p2, args.model, db_path, args.battles, args.prompt_version))
