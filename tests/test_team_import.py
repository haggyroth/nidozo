"""Tests for pasted-team import (#228)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nidozo.api import orchestration
from nidozo.api.models import StartBattleRequest
from nidozo.db.store import BattleStore

_TEAM = "Pikachu @ Light Ball\nAbility: Static\nEVs: 252 SpA / 252 Spe\n- Thunderbolt\n"


class _FakeBattle:
    def __init__(self) -> None:
        self.turn = 3


class _FakePlayer:
    def __init__(self) -> None:
        self.n_won_battles = 1
        self.battles = {"battle-x": _FakeBattle()}

    async def battle_against(self, other: Any, n_battles: int = 1) -> None:
        pass

    async def terminate(self) -> None:
        pass


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_preset_and_team_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        StartBattleRequest(p1_preset="ash_kanto", p1_team=_TEAM)


def test_team_alone_is_valid() -> None:
    req = StartBattleRequest(p1_provider="anthropic", p1_team=_TEAM, tier="ou")
    assert req.p1_team == _TEAM


# ---------------------------------------------------------------------------
# Runner routes the imported team + forces AG
# ---------------------------------------------------------------------------

async def test_run_battles_uses_imported_team(tmp_path, monkeypatch) -> None:
    store = BattleStore(tmp_path / "imp.db")
    try:
        p1 = store.get_or_create_model("anthropic", "claude-x", "v9")
        p2 = store.get_or_create_model("anthropic", "claude-y", "v9")
        bid = store.create_battle("imp", "gen9nationaldexag", p1, p2)

        captured: dict[str, dict[str, Any]] = {}

        def fake_build(provider, model, role, prompt_version, store_, battle_id, bus_, cfg, fmt, **kw):
            captured[role] = {"team": kw.get("team"), "fmt": fmt}
            return _FakePlayer()

        async def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(orchestration, "_build_streaming_player", fake_build)
        monkeypatch.setattr(orchestration, "generate_and_store_lessons", _noop)
        monkeypatch.setattr(orchestration, "generate_and_store_narrative", _noop)

        req = StartBattleRequest(
            p1_provider="anthropic", p1_model="claude-x",
            p2_provider="anthropic", p2_model="claude-y",
            tier="ou", p1_team=_TEAM,  # imported team for p1, draft skipped
        )
        bus = _FakeBus()
        await orchestration.run_battles(req, [bid], store, bus, {})

        # p1 battles with the exact pasted team; AG format forced.
        assert captured["p1"]["team"] == _TEAM
        assert captured["p1"]["fmt"] == "gen9nationaldexag"
        # p2 had no import → no team string from this path.
        assert captured["p2"]["team"] is None
        start = next(e for e in bus.events if e["type"] == "battle_start")
        assert start["p1_imported"] is True
        assert start["p2_imported"] is False
        assert start["drafted"] is False
    finally:
        store.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_start_battle_with_import_forces_ag_format(tmp_path, monkeypatch) -> None:
    from nidozo.api import routes
    from nidozo.api.app import create_app

    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(routes, "run_battles", _noop)
    app = create_app(db_path=tmp_path / "api.db")
    client = TestClient(app)

    resp = client.post("/api/battles/start", json={
        "p1_provider": "anthropic", "p1_model": "claude-x",
        "p2_provider": "random", "tier": "ou", "p1_team": _TEAM,
    })
    assert resp.status_code == 200
    bid = resp.json()["battle_ids"][0]
    assert app.state.store.get_battle(bid)["format"] == "gen9nationaldexag"


def test_start_battle_rejects_preset_plus_team(tmp_path, monkeypatch) -> None:
    from nidozo.api.app import create_app

    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    client = TestClient(create_app(db_path=tmp_path / "api2.db"))
    resp = client.post("/api/battles/start", json={
        "p1_provider": "anthropic", "p1_preset": "ash_kanto", "p1_team": _TEAM,
    })
    assert resp.status_code == 422
