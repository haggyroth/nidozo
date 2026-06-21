"""
Nidozo tournament runner — round-robin battles between a set of models.

Each model pair plays --rounds battles (alternating who goes first).
Results are persisted to the DB and ELO is updated after each battle.
Prints a summary table at the end.

Usage:
    # Two LM Studio models, 3 rounds each matchup
    uv run python scripts/tournament.py \\
        --player lmstudio:ibm/granite-4-h-tiny \\
        --player lmstudio:mistralai/ministral-3b-instruct \\
        --rounds 3

    # Random vs LM Studio (useful for baseline)
    uv run python scripts/tournament.py \\
        --player random:random \\
        --player lmstudio:ibm/granite-4-h-tiny \\
        --rounds 5

    # Override DB path
    uv run python scripts/tournament.py --player ... --db path/to/nidozo.db
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import sys
import time
from pathlib import Path

# Ensure src/ is on the path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nidozo.api.helpers import _JSON_OUTPUT_PROMPT_VERSIONS as _JSON_VERSIONS
from nidozo.db.store import BattleStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _showdown_cfg() -> object:
    """Env-driven Showdown server config (NIDOZO_SHOWDOWN_HOST / NIDOZO_SHOWDOWN_PORT).

    Mirrors the API so the tournament CLI can target a containerised or remote
    Showdown; defaults to localhost:8000 (matching the old LocalhostServerConfiguration).
    """
    from poke_env.ps_client.server_configuration import ServerConfiguration

    host = os.environ.get("NIDOZO_SHOWDOWN_HOST", "localhost")
    port = os.environ.get("NIDOZO_SHOWDOWN_PORT", "8000")
    return ServerConfiguration(
        websocket_url=f"ws://{host}:{port}/showdown/websocket",
        authentication_url="https://play.pokemonshowdown.com/action.php?",
    )


def _parse_player(spec: str) -> tuple[str, str]:
    """Parse 'provider:model_name' → (provider, model_name)."""
    if ":" not in spec:
        raise ValueError(
            f"Player spec must be 'provider:model_name', got: {spec!r}\n"
            f"Examples: lmstudio:ibm/granite-4-h-tiny  random:random  anthropic:claude-sonnet-4-6"
        )
    provider, model = spec.split(":", 1)
    return provider.strip(), model.strip()


def _build_player(provider: str, model: str, role: str, store: BattleStore,
                  battle_id: int, bus, cfg, fmt: str, prompt_version: str):
    """Build a streaming player for the given provider/model."""
    from nidozo.battle.streaming_player import StreamingLLMPlayer, StreamingRandomBot
    from nidozo.llm import AnthropicBackend, OpenAIBackend

    if provider == "random":
        return StreamingRandomBot(
            event_bus=bus, player_role=role,
            battle_format=fmt, server_configuration=cfg,
        )

    use_json_mode = prompt_version in _JSON_VERSIONS and provider in ("lmstudio", "openai")

    if provider == "anthropic":
        backend = AnthropicBackend(
            model=model, api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    elif provider == "openai":
        backend = OpenAIBackend(
            model=model, api_key=os.environ.get("OPENAI_API_KEY"),
            json_mode=use_json_mode,
        )
    else:  # lmstudio — same json_schema response_format (LM Studio rejects json_object)
        backend = OpenAIBackend(
            model=model,
            api_key="lm-studio",
            base_url=os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
            json_mode=use_json_mode,
        )

    return StreamingLLMPlayer(
        backend=backend, event_bus=bus, player_role=role,
        prompt_version=prompt_version, store=store, battle_id=battle_id,
        battle_format=fmt, server_configuration=cfg,
    )


# ---------------------------------------------------------------------------
# Single battle
# ---------------------------------------------------------------------------

async def run_one_battle(
    p1_provider: str, p1_model: str,
    p2_provider: str, p2_model: str,
    store: BattleStore,
    prompt_version: str = "v9",
    fmt: str = "gen9randombattle",
) -> dict:
    """Run one battle, persist results, return summary dict."""
    from nidozo.api.events import EventBus

    cfg = _showdown_cfg()
    bus = EventBus()

    p1_id = store.get_or_create_model(p1_provider, p1_model, prompt_version)
    p2_id = store.get_or_create_model(p2_provider, p2_model, prompt_version)
    battle_id = store.create_battle(
        f"tournament-{p1_provider}-{p1_model}-vs-{p2_provider}-{p2_model}-{int(time.time())}",
        fmt, p1_id, p2_id,
    )

    p1 = _build_player(p1_provider, p1_model, "p1", store, battle_id, bus, cfg, fmt, prompt_version)
    p2 = _build_player(p2_provider, p2_model, "p2", store, battle_id, bus, cfg, fmt, prompt_version)

    await p1.battle_against(p2, n_battles=1)

    winner = 1 if p1.n_won_battles > 0 else (2 if p2.n_won_battles > 0 else None)
    real_tag = next(iter(p1.battles), f"battle-{battle_id}")
    battle_obj = p1.battles.get(real_tag)
    total_turns = battle_obj.turn if battle_obj else 0

    store._conn.execute("UPDATE battles SET battle_tag=? WHERE id=?", (real_tag, battle_id))
    store.finish_battle(battle_id, winner, total_turns)

    winner_label = (
        f"{p1_provider}/{p1_model}" if winner == 1
        else f"{p2_provider}/{p2_model}" if winner == 2
        else "tie"
    )
    return {
        "battle_id": battle_id,
        "p1": f"{p1_provider}/{p1_model}",
        "p2": f"{p2_provider}/{p2_model}",
        "winner": winner_label,
        "turns": total_turns,
    }


# ---------------------------------------------------------------------------
# Tournament
# ---------------------------------------------------------------------------

async def run_tournament(
    players: list[tuple[str, str]],
    rounds: int,
    store: BattleStore,
    prompt_version: str = "v9",
) -> None:
    pairs = list(itertools.combinations(players, 2))
    total = len(pairs) * rounds * 2  # each pair plays both ways each round
    done = 0

    print(f"\n{'─' * 60}")
    print(f"  NIDOZO TOURNAMENT  —  {len(players)} players, {rounds} round(s)")
    print(f"  {total} battles total")
    print(f"{'─' * 60}\n")

    results = []
    for (p1_prov, p1_mod), (p2_prov, p2_mod) in pairs:
        for rnd in range(1, rounds + 1):
            for flip in [False, True]:
                a_prov, a_mod = (p2_prov, p2_mod) if flip else (p1_prov, p1_mod)
                b_prov, b_mod = (p1_prov, p1_mod) if flip else (p2_prov, p2_mod)

                done += 1
                label = f"[{done:>2}/{total}] {a_mod} vs {b_mod} (round {rnd})"
                print(f"  ▶ {label}", flush=True)

                try:
                    result = await run_one_battle(
                        a_prov, a_mod, b_prov, b_mod,
                        store, prompt_version,
                    )
                    results.append(result)
                    print(f"      winner: {result['winner']}  ({result['turns']} turns)\n")
                except Exception as exc:
                    print(f"      ERROR: {exc}\n")

    # Final leaderboard
    _print_leaderboard(store)


def _print_leaderboard(store: BattleStore) -> None:
    rows = store.leaderboard()
    if not rows:
        print("No results recorded.")
        return

    print(f"\n{'═' * 60}")
    print("  FINAL LEADERBOARD")
    print(f"{'═' * 60}")
    print(f"  {'#':<3} {'MODEL':<35} {'ELO':>7}  {'W':>3}{'L':>3}{'T':>3}")
    print(f"  {'─' * 56}")

    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    for i, r in enumerate(rows):
        medal = medals.get(i, f" {i+1}")
        name = f"{r['provider']}/{r['model_name']}"
        print(
            f"  {medal:<3} {name:<35} {r['rating']:>7.1f}"
            f"  {r['wins']:>3}{r['losses']:>3}{r['ties']:>3}"
        )
    print(f"{'═' * 60}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a round-robin tournament between LLM models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--player", action="append", dest="players", required=True,
        metavar="PROVIDER:MODEL",
        help="Add a player (e.g. lmstudio:ibm/granite-4-h-tiny). Repeat for each model.",
    )
    parser.add_argument(
        "--rounds", type=int, default=1,
        help="Number of rounds per matchup (each pair plays both sides). Default: 1",
    )
    parser.add_argument(
        "--prompt-version", default="v9",
        choices=["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"],
        help="Prompt template version (v1=text, v2–v9=JSON output). Default: v9",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to SQLite DB. Defaults to NIDOZO_DB env var or nidozo.db",
    )
    parser.add_argument(
        "--api-url", default=None,
        metavar="URL",
        help=(
            "Route battles through the Nidozo API server at URL "
            "(e.g. http://localhost:5001). When set, battles are published to the "
            "live WebSocket feed and visible in the UI. "
            "Omit to run battles directly (offline mode, no live view)."
        ),
    )
    args = parser.parse_args()

    if len(args.players) < 2:
        parser.error("Need at least two --player arguments.")

    players = [_parse_player(p) for p in args.players]

    if args.api_url:
        asyncio.run(_run_via_api(args.api_url, players, args.rounds, args.prompt_version))
        return

    db_path = Path(args.db) if args.db else Path(os.environ.get("NIDOZO_DB", "nidozo.db"))
    store = BattleStore(db_path)

    try:
        asyncio.run(run_tournament(players, args.rounds, store, args.prompt_version))
    except KeyboardInterrupt:
        print("\nTournament interrupted. Results so far saved to DB.")
        _print_leaderboard(store)
    finally:
        store.close()


async def _run_via_api(
    api_url: str,
    players: list[tuple[str, str]],
    rounds: int,
    prompt_version: str,
) -> None:
    """Run a tournament through the API server so battles appear in the live view."""
    import httpx as _httpx

    api_url = api_url.rstrip("/")
    player_payload = [{"provider": prov, "model": model} for prov, model in players]

    print(f"\n  Routing through API at {api_url}")
    print("  Battles will appear live in the UI WebSocket feed.\n")

    async with _httpx.AsyncClient(timeout=None) as client:
        resp = client.post(
            f"{api_url}/api/tournament/start",
            json={
                "players": player_payload,
                "rounds": rounds,
                "prompt_version": prompt_version,
            },
        )
        if hasattr(resp, '__await__'):
            resp = await resp
        data = resp.json()

        if resp.status_code != 200:
            print(f"  ERROR starting tournament: {data}")
            return

        tournament_id = data["tournament_id"]
        total = data["total_battles"]
        print(f"  Tournament #{tournament_id} started — {total} battles")
        print(f"  Watch live at {api_url}\n")

        # Poll until all battles complete
        battle_ids = data["battle_ids"]
        done = 0
        import asyncio as _asyncio
        while done < total:
            await _asyncio.sleep(5)
            t_resp = await client.get(f"{api_url}/api/tournaments/{tournament_id}")
            if t_resp.status_code == 200:
                t_data = t_resp.json()
                if t_data["status"] in ("completed", "cancelled"):
                    break

            # Count completed battles
            completed = 0
            for bid in battle_ids:
                b_resp = await client.get(f"{api_url}/api/battles/{bid}")
                if b_resp.status_code == 200 and b_resp.json().get("status") in ("completed", "cancelled"):
                    completed += 1
            if completed != done:
                done = completed
                print(f"  Progress: {done}/{total} battles complete", flush=True)

        # Fetch and print final leaderboard from API
        lb_resp = await client.get(f"{api_url}/api/leaderboard")
        if lb_resp.status_code == 200:
            rows = lb_resp.json()
            print(f"\n{'═' * 60}")
            print("  FINAL LEADERBOARD")
            print(f"{'═' * 60}")
            print(f"  {'#':<3} {'MODEL':<35} {'ELO':>7}  {'W':>3}{'L':>3}{'T':>3}")
            print(f"  {'─' * 56}")
            medals = {0: "🥇", 1: "🥈", 2: "🥉"}
            for i, r in enumerate(rows):
                medal = medals.get(i, f" {i+1}")
                name = f"{r['provider']}/{r['model_name']}"
                print(
                    f"  {medal:<3} {name:<35} {r['rating']:>7.1f}"
                    f"  {r['wins']:>3}{r['losses']:>3}{r['ties']:>3}"
                )
            print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
