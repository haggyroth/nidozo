"""Tests for the LLM draft phase (draft.py)."""

from __future__ import annotations

from nidozo.battle.draft import run_draft
from nidozo.battle.team_builder import load_movesets
from nidozo.battle.tiers import get_pool
from nidozo.db.store import BattleStore
from nidozo.llm.backend import Message


class _EmptyBackend:
    """Stub backend that always returns an empty response.

    Forces every draft pick down the parse-failure fallback path
    (``remaining_pool[0]``) — exactly the path that used to IndexError once the
    pool emptied.
    """

    async def complete(self, messages: list[Message]) -> str:
        return ""


async def test_draft_clamps_team_size_to_pool(tmp_path) -> None:
    """team_size larger than the pool clamps to the pool and never IndexErrors (#211)."""
    movesets = load_movesets()
    lc_pool = get_pool("lc", set(movesets.keys()))
    assert len(lc_pool) > 0

    store = BattleStore(tmp_path / "draft.db")
    try:
        model_id = store.get_or_create_model("stub", "stub-model", "v3")
        # Request far more mons than any pool can supply.
        result = await run_draft(
            backend=_EmptyBackend(),
            model_id=model_id,
            tier="lc",
            store=store,
            team_size=999,
        )
    finally:
        store.close()

    # Clamped to the pool size — no crash, no over-draft, no duplicates.
    assert len(result.picked) == len(lc_pool)
    assert len(set(result.picked)) == len(result.picked)
    assert result.team_string.strip()


async def test_draft_respects_team_size_within_pool(tmp_path) -> None:
    """A normal team_size well within the pool drafts exactly that many."""
    store = BattleStore(tmp_path / "draft2.db")
    try:
        model_id = store.get_or_create_model("stub", "stub-model", "v3")
        result = await run_draft(
            backend=_EmptyBackend(),
            model_id=model_id,
            tier="ou",
            store=store,
            team_size=6,
        )
    finally:
        store.close()

    assert len(result.picked) == 6
    assert len(set(result.picked)) == 6
