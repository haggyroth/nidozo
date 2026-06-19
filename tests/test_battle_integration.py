"""Integration tests — real battles against a live Pokémon Showdown server (#191).

Unlike the unit suite (which mocks Showdown), these drive complete battles
through poke-env against a **live** server. They cover the integration surface
that unit tests structurally cannot: the Showdown text protocol, the battle
loop, action submission, state sync, drafted-team validity, and the doubles
path.

Excluded from the default run via ``pytest.mark.integration``. They require a
Showdown server reachable at ``localhost:8000`` (configurable via
``NIDOZO_SHOWDOWN_HOST`` / ``NIDOZO_SHOWDOWN_PORT``):

    ./scripts/start_showdown.sh          # local dev
    docker compose up -d showdown        # or the containerised server

Run with::

    pytest -m integration tests/test_battle_integration.py

No LLM is required: battles use random bots and a deterministic stub backend
(it parses the prompt's AVAILABLE ACTIONS block and returns the first legal
action). A real model would only add non-determinism and cost, so the CI gate
deliberately avoids it. To smoke-test an actual model end to end, see the
opt-in hook at the bottom of this file (``NIDOZO_LLM_INTEGRATION``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import uuid

import pytest
from poke_env import AccountConfiguration, ServerConfiguration

from nidozo.battle.bots import RandomBot
from nidozo.battle.llm_player import LLMPlayer
from nidozo.llm.backend import Message

# A single battle should resolve well within this; guards against a hung server.
_BATTLE_TIMEOUT = 180.0


def _host_port() -> tuple[str, int]:
    return (
        os.environ.get("NIDOZO_SHOWDOWN_HOST", "localhost"),
        int(os.environ.get("NIDOZO_SHOWDOWN_PORT", "8000")),
    )


def _server_config() -> ServerConfiguration:
    host, port = _host_port()
    return ServerConfiguration(
        f"ws://{host}:{port}/showdown/websocket",
        "https://play.pokemonshowdown.com/action.php?",
    )


def _showdown_reachable() -> bool:
    host, port = _host_port()
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


# Skip the whole module (rather than fail) when no server is present, so a bare
# `pytest -m integration` is friendly on a dev box without Showdown running.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _showdown_reachable(),
        reason="no Showdown server reachable (set NIDOZO_SHOWDOWN_HOST/PORT or start one)",
    ),
]


def _account(prefix: str) -> AccountConfiguration:
    """Unique, short username per player so concurrent battles never collide."""
    return AccountConfiguration(f"{prefix}{uuid.uuid4().hex[:8]}", None)


class _FirstActionBackend:
    """Deterministic stub backend satisfying the ModelBackend protocol.

    Reads the prompt's ``--- AVAILABLE ACTIONS ---`` block and returns valid
    action JSON for the first listed move (or the first switch when forced).
    This exercises the real prompt → parse → BattleOrder path instead of the
    random fallback. Anything unparseable still falls back to a random legal
    move inside LLMPlayer, so the battle always completes.
    """

    async def complete(self, messages: list[Message]) -> str:
        content = messages[-1]["content"] if messages else ""
        move = re.search(r"^\s*move (\d+):", content, re.MULTILINE)
        switch = re.search(r"^\s*switch (\d+):", content, re.MULTILINE)
        if move:
            return json.dumps(
                {"reasoning": "stub", "action_type": "move", "identifier": move.group(1)}
            )
        if switch:
            return json.dumps(
                {"reasoning": "stub", "action_type": "switch", "identifier": switch.group(1)}
            )
        # No recognisable action block — let LLMPlayer fall back to random.
        return json.dumps({"reasoning": "stub", "action_type": "move", "identifier": "1"})


def _assert_one_battle_finished(p1: RandomBot, p2: RandomBot) -> None:
    assert p1.n_finished_battles == 1, "battle did not finish"
    decided = p1.n_won_battles + p2.n_won_battles + p1.n_tied_battles
    assert decided == 1, "battle finished without a recorded result"


# ---------------------------------------------------------------------------
# Tier 1 — random bots vs live Showdown
# ---------------------------------------------------------------------------

async def test_random_singles_battle_completes() -> None:
    """Two RandomBots play a full Gen 9 random singles battle end to end."""
    cfg = _server_config()
    p1 = RandomBot(
        account_configuration=_account("nidoRsa"),
        battle_format="gen9randombattle",
        server_configuration=cfg,
    )
    p2 = RandomBot(
        account_configuration=_account("nidoRsb"),
        battle_format="gen9randombattle",
        server_configuration=cfg,
    )
    await asyncio.wait_for(p1.battle_against(p2, n_battles=1), timeout=_BATTLE_TIMEOUT)
    _assert_one_battle_finished(p1, p2)


async def test_random_doubles_battle_completes() -> None:
    """Two RandomBots play a full random doubles battle — exercises the doubles
    battle loop and target selection at the protocol level."""
    cfg = _server_config()
    p1 = RandomBot(
        account_configuration=_account("nidoRda"),
        battle_format="gen9randomdoublesbattle",
        server_configuration=cfg,
    )
    p2 = RandomBot(
        account_configuration=_account("nidoRdb"),
        battle_format="gen9randomdoublesbattle",
        server_configuration=cfg,
    )
    await asyncio.wait_for(p1.battle_against(p2, n_battles=1), timeout=_BATTLE_TIMEOUT)
    _assert_one_battle_finished(p1, p2)


# ---------------------------------------------------------------------------
# Tier 2 — stub LLM player vs live Showdown
# ---------------------------------------------------------------------------

async def test_stub_llm_singles_battle_completes() -> None:
    """Two stub-backed LLMPlayers play a full battle — exercises the real
    LLMPlayer path (prompt build → backend → ActionParser → BattleOrder →
    submission) against the live server, with no model required."""
    cfg = _server_config()
    p1 = LLMPlayer(
        backend=_FirstActionBackend(),
        prompt_version="v9",
        account_configuration=_account("nidoLsa"),
        battle_format="gen9randombattle",
        server_configuration=cfg,
    )
    p2 = LLMPlayer(
        backend=_FirstActionBackend(),
        prompt_version="v9",
        account_configuration=_account("nidoLsb"),
        battle_format="gen9randombattle",
        server_configuration=cfg,
    )
    await asyncio.wait_for(p1.battle_against(p2, n_battles=1), timeout=_BATTLE_TIMEOUT)
    _assert_one_battle_finished(p1, p2)


# ---------------------------------------------------------------------------
# Tier 2 — drafted team validity
# ---------------------------------------------------------------------------

async def test_drafted_team_validates_and_battle_completes(tmp_path) -> None:
    """Run a draft (stub backend), then prove the generated team string is
    accepted by live Showdown by completing a battle with it in the drafted
    (non-random) format. An invalid team would be rejected at battle start.

    Uses the ``freeforall`` tier, which maps to NatDex Anything Goes (no ban
    list), so this isolates the integration risk we want here — that
    ``build_team_string`` emits a Showdown-valid team (well-formed species,
    moves, abilities, EVs) — from tier ban-list drift. (Whether a curated tier
    *pool* stays aligned with its Showdown format's ban list is a separate
    concern; a degenerate "ou" draft was observed producing an Uber-banned
    team.) The integration risk here is team *validity*, not pick quality — the
    stub drives run_draft's fallback, which still builds a real team.
    """
    from nidozo.battle.draft import run_draft
    from nidozo.battle.tiers import resolve_format
    from nidozo.db.store import BattleStore

    tier = "freeforall"
    store = BattleStore(tmp_path / "draft_it.db")
    try:
        model_id = store.get_or_create_model("stub", "stub-model", "v3")
        result = await run_draft(
            backend=_FirstActionBackend(),
            model_id=model_id,
            tier=tier,
            store=store,
            team_size=6,
        )
        team_string = result.team_string
        assert team_string.strip(), "draft produced an empty team"
    finally:
        store.close()

    fmt = resolve_format(tier, doubles=False, team_size=6)
    cfg = _server_config()
    p1 = RandomBot(
        account_configuration=_account("nidoDra"),
        battle_format=fmt,
        team=team_string,
        server_configuration=cfg,
    )
    p2 = RandomBot(
        account_configuration=_account("nidoDrb"),
        battle_format=fmt,
        team=team_string,
        server_configuration=cfg,
    )
    await asyncio.wait_for(p1.battle_against(p2, n_battles=1), timeout=_BATTLE_TIMEOUT)
    _assert_one_battle_finished(p1, p2)


# ---------------------------------------------------------------------------
# Tier 3 — opt-in real-LLM smoke (NOT a CI gate)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("NIDOZO_LLM_INTEGRATION") != "1",
    reason="real-LLM smoke is opt-in; set NIDOZO_LLM_INTEGRATION=1 (and LM Studio / API keys)",
)
async def test_real_lmstudio_singles_battle_completes() -> None:
    """Optional: a real local model (LM Studio) plays a full battle. Excluded
    from CI — non-deterministic and requires a running model. Enable with
    NIDOZO_LLM_INTEGRATION=1 and a model loaded at LM_STUDIO_BASE_URL."""
    from nidozo.llm import OpenAIBackend

    backend = OpenAIBackend(
        model=os.environ.get("LM_STUDIO_MODEL", "local-model"),
        base_url=os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key="lm-studio",
        json_mode=True,
    )
    cfg = _server_config()
    p1 = LLMPlayer(
        backend=backend,
        prompt_version="v9",
        account_configuration=_account("nidoLLa"),
        battle_format="gen9randombattle",
        server_configuration=cfg,
    )
    p2 = RandomBot(
        account_configuration=_account("nidoLLb"),
        battle_format="gen9randombattle",
        server_configuration=cfg,
    )
    await asyncio.wait_for(p1.battle_against(p2, n_battles=1), timeout=_BATTLE_TIMEOUT)
    _assert_one_battle_finished(p1, p2)
