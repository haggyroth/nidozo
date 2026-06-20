"""Tests for the bake-off experiment harness (#226)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nidozo.analysis.significance import bakeoff_result, two_sided_binomial_p
from nidozo.api import orchestration
from nidozo.api.models import ExperimentVariant, StartExperimentRequest
from nidozo.db.store import BattleStore

# ---------------------------------------------------------------------------
# Significance
# ---------------------------------------------------------------------------

def test_even_split_is_not_significant() -> None:
    assert two_sided_binomial_p(10, 10) == pytest.approx(1.0)
    res = bakeoff_result(10, 10, 0)
    assert res["significant"] is False
    assert res["win_rate_a"] == pytest.approx(0.5)


def test_lopsided_split_is_significant() -> None:
    assert two_sided_binomial_p(18, 2) < 0.05
    res = bakeoff_result(18, 2, 1)
    assert res["significant"] is True
    assert res["n_decided"] == 20
    assert res["ties"] == 1


def test_no_decided_battles() -> None:
    assert two_sided_binomial_p(0, 0) == 1.0
    res = bakeoff_result(0, 0, 3)
    assert res["significant"] is False
    assert res["win_rate_a"] is None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_create_and_result_attribution(tmp_path) -> None:
    store = BattleStore(tmp_path / "exp.db")
    try:
        a = store.get_or_create_model("openai", "gpt-4o", "v9")
        b = store.get_or_create_model("openai", "gpt-4o", "v8")
        eid = store.create_experiment(
            name="v9 vs v8",
            variant_a={"provider": "openai", "model_name": "gpt-4o", "prompt_version": "v9"},
            variant_b={"provider": "openai", "model_name": "gpt-4o", "prompt_version": "v8"},
            a_model_id=a, b_model_id=b, tier="random", fmt="gen9randombattle", n_battles=4,
        )
        # Four completed battles, alternating sides; variant A (v9) wins all.
        sides = [(a, b, 1), (b, a, 2), (a, b, 1), (b, a, 2)]  # winner always = A
        for i, (p1, p2, winner) in enumerate(sides):
            bid = store.create_battle(f"e-{eid}-{i}", "gen9randombattle", p1, p2, experiment_id=eid)
            store.finish_battle(bid, winner=winner, total_turns=5)

        counts = store.get_experiment_result(eid)
        assert counts == {"a_wins": 4, "b_wins": 0, "ties": 0}

        exp = store.get_experiment(eid)
        assert exp is not None
        assert exp["variant_a"]["prompt_version"] == "v9"
        assert eid in [e["id"] for e in store.list_experiments()]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Runner (fake players, no Showdown)
# ---------------------------------------------------------------------------

class _FakeBattle:
    def __init__(self, turn: int) -> None:
        self.turn = turn


class _FakePlayer:
    def __init__(self, *, won: bool, tag: str) -> None:
        self.n_won_battles = 1 if won else 0
        self._tag = tag
        self.battles: dict[str, _FakeBattle] = {}

    async def battle_against(self, other: Any, n_battles: int = 1) -> None:
        self.battles[self._tag] = _FakeBattle(3)

    async def terminate(self) -> None:
        pass


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


async def test_run_experiment_completes_and_attributes_winner(tmp_path, monkeypatch) -> None:
    store = BattleStore(tmp_path / "run.db")
    try:
        va = {"provider": "openai", "model_name": "gpt-4o", "prompt_version": "v9"}
        vb = {"provider": "openai", "model_name": "gpt-4o", "prompt_version": "v8"}
        a = store.get_or_create_model(va["provider"], va["model_name"], va["prompt_version"])
        b = store.get_or_create_model(vb["provider"], vb["model_name"], vb["prompt_version"])
        eid = store.create_experiment(
            name="bake", variant_a=va, variant_b=vb, a_model_id=a, b_model_id=b,
            tier="random", fmt="gen9randombattle", n_battles=6,
        )
        battle_ids = []
        for i in range(6):
            p1, p2 = (a, b) if i % 2 == 0 else (b, a)
            battle_ids.append(
                store.create_battle(f"exp-{eid}-{i}", "gen9randombattle", p1, p2, experiment_id=eid)
            )

        # Variant A (prompt v9) always wins, regardless of which side it's on.
        def fake_build(provider, model, role, prompt_version, *args, **kwargs):
            return _FakePlayer(won=(prompt_version == "v9"), tag=f"b-{uuid.uuid4().hex[:8]}")

        monkeypatch.setattr(orchestration, "_build_streaming_player", fake_build)

        req = StartExperimentRequest(
            name="bake",
            variant_a=ExperimentVariant(provider="openai", model="gpt-4o", prompt_version="v9"),
            variant_b=ExperimentVariant(provider="openai", model="gpt-4o", prompt_version="v8"),
            n_battles=6, tier="random",
        )
        bus = _FakeBus()
        await orchestration.run_experiment(req, eid, battle_ids, va, vb, a, b, store, bus, {})

        exp = store.get_experiment(eid)
        assert exp is not None
        assert exp["status"] == "completed"
        counts = store.get_experiment_result(eid)
        assert counts["a_wins"] == 6 and counts["b_wins"] == 0

        end = next(e for e in bus.events if e["type"] == "experiment_end")
        assert end["result"]["significant"] is True
    finally:
        store.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_start_experiment_rejects_identical_variants(tmp_path, monkeypatch) -> None:
    from nidozo.api.app import create_app

    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    client = TestClient(create_app(db_path=tmp_path / "api.db"))
    resp = client.post("/api/experiments/start", json={
        "name": "dup",
        "variant_a": {"provider": "openai", "model": "gpt-4o", "prompt_version": "v9"},
        "variant_b": {"provider": "openai", "model": "gpt-4o", "prompt_version": "v9"},
        "n_battles": 4,
    })
    assert resp.status_code == 422


def test_start_experiment_creates_alternating_battles(tmp_path, monkeypatch) -> None:
    from nidozo.api import routes
    from nidozo.api.app import create_app

    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    # Don't actually run the battles (no Showdown in this test).
    monkeypatch.setattr(routes, "run_experiment", _noop)

    app = create_app(db_path=tmp_path / "api2.db")
    client = TestClient(app)
    resp = client.post("/api/experiments/start", json={
        "name": "v9 vs v8",
        "variant_a": {"provider": "openai", "model": "gpt-4o", "prompt_version": "v9"},
        "variant_b": {"provider": "openai", "model": "gpt-4o", "prompt_version": "v8"},
        "n_battles": 4, "tier": "random",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["battle_ids"]) == 4

    battles = app.state.store.get_experiment_battles(data["experiment_id"])
    # Sides alternate: p1 prompt v9, v8, v9, v8.
    assert [b["p1_prompt"] for b in battles] == ["v9", "v8", "v9", "v8"]
