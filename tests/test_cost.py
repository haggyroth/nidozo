"""Tests for token-usage capture + cost analytics (#225)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nidozo.llm.openai import OpenAIBackend
from nidozo.llm.pricing import estimate_cost, model_price

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def test_estimate_cost_priced_model() -> None:
    # gpt-4o: 2.5 / 10.0 per 1M → 1M in + 1M out = 12.5
    assert estimate_cost("gpt-4o", 1_000_000, 1_000_000) == pytest.approx(12.5)


def test_estimate_cost_unpriced_model_is_free() -> None:
    assert estimate_cost("some-local-model", 1_000_000, 1_000_000) == 0.0
    assert estimate_cost("random", 5000, 5000) == 0.0


def test_model_price_lookup() -> None:
    assert model_price("gpt-4o") is not None
    assert model_price("not-a-real-model") is None


# ---------------------------------------------------------------------------
# Backend usage capture (fake client, no network)
# ---------------------------------------------------------------------------

class _FakeUsage:
    prompt_tokens = 120
    completion_tokens = 34


class _FakeMessage:
    content = '{"action_type":"move","identifier":"1","reasoning":"x"}'


class _FakeChoice:
    def __init__(self) -> None:
        self.message = _FakeMessage()
        self.finish_reason = "stop"


class _FakeResponse:
    def __init__(self) -> None:
        self.choices = [_FakeChoice()]
        self.usage = _FakeUsage()


class _FakeCompletions:
    async def create(self, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


async def test_openai_backend_records_last_usage() -> None:
    backend = OpenAIBackend(model="gpt-4o", api_key="x")
    backend._client = _FakeClient()  # type: ignore[assignment]
    assert backend.last_usage is None

    content = await backend.complete([{"role": "user", "content": "hi"}])
    assert content
    assert backend.last_usage == {"prompt_tokens": 120, "completion_tokens": 34}


# ---------------------------------------------------------------------------
# Persistence + aggregation
# ---------------------------------------------------------------------------

def _seed_tokens(store: Any) -> None:
    p1 = store.get_or_create_model("openai", "gpt-4o", "v9")
    p2 = store.get_or_create_model("lmstudio", "local-model", "v9")
    bid = store.create_battle("tok-tag", "gen9randombattle", p1, p2)
    store.log_turn(
        battle_id=bid, turn_number=1, player_role="p1", prompt_version="v9",
        action_chosen="move 1", parse_success=True,
        prompt_tokens=1000, completion_tokens=200,
    )
    store.log_turn(
        battle_id=bid, turn_number=2, player_role="p1", prompt_version="v9",
        action_chosen="move 2", parse_success=True,
        prompt_tokens=500, completion_tokens=100,
    )
    # Local model: no usage recorded (NULL) — should be excluded from sums.
    store.log_turn(
        battle_id=bid, turn_number=1, player_role="p2", prompt_version="v9",
        action_chosen="move 1", parse_success=True,
    )


def test_log_turn_persists_tokens(tmp_path) -> None:
    from nidozo.db.store import BattleStore

    store = BattleStore(tmp_path / "tok.db")
    try:
        _seed_tokens(store)
        rows = store.get_token_usage_by_model()
        by_model = {r["model_name"]: r for r in rows}
        assert by_model["gpt-4o"]["prompt_tokens"] == 1500
        assert by_model["gpt-4o"]["completion_tokens"] == 300
        # The local model logged a turn with NULL tokens → excluded entirely.
        assert "local-model" not in by_model
    finally:
        store.close()


def test_cost_endpoint(tmp_path, monkeypatch) -> None:
    from nidozo.api.app import create_app

    monkeypatch.delenv("NIDOZO_API_TOKEN", raising=False)
    app = create_app(db_path=tmp_path / "cost.db")
    _seed_tokens(app.state.store)
    client = TestClient(app)

    resp = client.get("/api/stats/cost")
    assert resp.status_code == 200
    data = resp.json()

    gpt = next(m for m in data["by_model"] if m["model_name"] == "gpt-4o")
    assert gpt["prompt_tokens"] == 1500
    assert gpt["completion_tokens"] == 300
    # 1500/1M*2.5 + 300/1M*10 = 0.00675, rounded to 4dp by the endpoint.
    assert gpt["est_cost_usd"] == pytest.approx(0.00675, abs=5e-4)
    assert gpt["est_cost_usd"] > 0
    assert data["total"]["prompt_tokens"] == 1500
    assert data["total"]["est_cost_usd"] > 0
