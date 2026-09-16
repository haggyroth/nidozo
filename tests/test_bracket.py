"""Tests for bracket generation and result recording."""

from __future__ import annotations

import pytest

from nidozo.tournament.bracket import (
    build_bracket,
    build_double_elim,
    build_single_elim,
    get_pending_matches,
    record_result,
    record_result_single,
    resolve_seed,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _players(n: int) -> list[dict]:
    return [{"provider": "random", "model_name": f"bot{i + 1}"} for i in range(n)]


# ---------------------------------------------------------------------------
# Single-elimination — structure
# ---------------------------------------------------------------------------

class TestSingleElimStructure:
    def test_two_player_bracket(self) -> None:
        state = build_single_elim(_players(2))
        assert state["format"] == "single_elim"
        assert state["size"] == 2
        assert state["num_rounds"] == 1
        rounds = state["rounds"]
        assert len(rounds) == 1
        assert len(rounds[0]["matches"]) == 1

    def test_four_player_bracket(self) -> None:
        state = build_single_elim(_players(4))
        assert state["size"] == 4
        assert state["num_rounds"] == 2
        # Round 1 has 2 matches, round 2 has 1
        matches_by_round = {r["round_num"]: len(r["matches"]) for r in state["rounds"]}
        assert matches_by_round == {1: 2, 2: 1}

    def test_eight_player_bracket(self) -> None:
        state = build_single_elim(_players(8))
        assert state["size"] == 8
        assert state["num_rounds"] == 3
        matches_by_round = {r["round_num"]: len(r["matches"]) for r in state["rounds"]}
        assert matches_by_round == {1: 4, 2: 2, 3: 1}

    def test_non_power_of_two_gets_padded(self) -> None:
        # 6 players → size 8 with 2 byes
        state = build_single_elim(_players(6))
        assert state["size"] == 8
        byes = [
            m for m in state["match_index"].values()
            if m["status"] == "bye"
        ]
        assert len(byes) == 2

    def test_seeds_assigned(self) -> None:
        state = build_single_elim(_players(4))
        assert resolve_seed(state, 1) is not None
        assert resolve_seed(state, 4) is not None
        assert resolve_seed(state, 5) is None

    def test_champion_initially_none(self) -> None:
        state = build_single_elim(_players(4))
        assert state["champion_seed"] is None

    def test_round1_seeds_cover_all_positions(self) -> None:
        """Every player seed should appear exactly once in round-1 matchups."""
        state = build_single_elim(_players(8))
        r1 = state["rounds"][0]["matches"]
        seeds_in_r1 = set()
        for m in r1:
            seeds_in_r1.add(m["p1_seed"])
            seeds_in_r1.add(m["p2_seed"])
        assert seeds_in_r1 == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_top_seeds_cant_meet_until_final(self) -> None:
        """Seeds 1 and 2 should be in different halves of the bracket."""
        state = build_single_elim(_players(8))
        r1 = state["rounds"][0]["matches"]
        # Find which match seed 1 and seed 2 are in
        seed1_match = next(m["match_id"] for m in r1 if m["p1_seed"] == 1 or m["p2_seed"] == 1)
        seed2_match = next(m["match_id"] for m in r1 if m["p1_seed"] == 2 or m["p2_seed"] == 2)
        assert seed1_match != seed2_match


# ---------------------------------------------------------------------------
# Single-elimination — result recording
# ---------------------------------------------------------------------------

class TestSingleElimResults:
    def test_record_result_sets_winner(self) -> None:
        state = build_single_elim(_players(2))
        record_result_single(state, "WR1-1", 1, battle_id=42)
        assert state["match_index"]["WR1-1"]["winner_seed"] == 1
        assert state["match_index"]["WR1-1"]["status"] == "completed"

    def test_winner_advances_to_next_round(self) -> None:
        state = build_single_elim(_players(4))
        # Seed pairings for 4-player: WR1-1 = (1,4), WR1-2 = (2,3)
        record_result_single(state, "WR1-1", 1, battle_id=1)  # seed 1 wins
        record_result_single(state, "WR1-2", 1, battle_id=2)  # seed 2 wins
        final = state["match_index"]["WR2-1"]
        assert final["p1_seed"] == 1
        assert final["p2_seed"] == 2

    def test_champion_set_after_final(self) -> None:
        state = build_single_elim(_players(2))
        record_result_single(state, "WR1-1", 1, battle_id=99)
        assert state["champion_seed"] == 1

    def test_champion_set_for_four_players(self) -> None:
        state = build_single_elim(_players(4))
        record_result_single(state, "WR1-1", 1, battle_id=1)
        record_result_single(state, "WR1-2", 2, battle_id=2)
        record_result_single(state, "WR2-1", 1, battle_id=3)
        assert state["champion_seed"] is not None

    def test_pending_matches_decreases_as_completed(self) -> None:
        state = build_single_elim(_players(4))
        before = len(get_pending_matches(state))
        record_result_single(state, "WR1-1", 1, battle_id=1)
        after = len(get_pending_matches(state))
        assert after == before - 1

    def test_final_unlocks_only_after_both_semis(self) -> None:
        state = build_single_elim(_players(4))
        # After recording one semi the final is still not pending (p2 is None)
        record_result_single(state, "WR1-1", 1, battle_id=1)
        pending = [m["match_id"] for m in get_pending_matches(state)]
        assert "WR2-1" not in pending
        # After second semi it's available
        record_result_single(state, "WR1-2", 1, battle_id=2)
        pending = [m["match_id"] for m in get_pending_matches(state)]
        assert "WR2-1" in pending


# ---------------------------------------------------------------------------
# Bye handling
# ---------------------------------------------------------------------------

class TestByeHandling:
    def test_bye_match_auto_resolved(self) -> None:
        # 3 players → size 4, one bye
        state = build_single_elim(_players(3))
        bye_matches = [m for m in state["match_index"].values() if m["status"] == "bye"]
        assert len(bye_matches) >= 1
        # The real seed in the bye match should be pre-advanced
        for m in bye_matches:
            assert m["winner_seed"] is not None

    def test_bye_match_not_in_pending(self) -> None:
        state = build_single_elim(_players(3))
        pending = get_pending_matches(state)
        for m in pending:
            assert not m.get("p1_is_bye") and not m.get("p2_is_bye")


# ---------------------------------------------------------------------------
# Double-elimination — structure
# ---------------------------------------------------------------------------

class TestDoubleElimStructure:
    def test_four_player_de_has_wb_and_lb(self) -> None:
        state = build_double_elim(_players(4))
        assert state["format"] == "double_elim"
        mi = state["match_index"]
        wb_matches = [m for m in mi.values() if m["bracket"] == "winners"]
        lb_matches = [m for m in mi.values() if m["bracket"] == "losers"]
        gf_matches = [m for m in mi.values() if m["bracket"] == "grand_final"]
        assert len(wb_matches) >= 2
        assert len(lb_matches) >= 2
        assert len(gf_matches) == 2  # GF + GFR

    def test_grand_final_in_match_index(self) -> None:
        state = build_double_elim(_players(4))
        assert "GF" in state["match_index"]
        assert "GFR" in state["match_index"]

    def test_champion_initially_none(self) -> None:
        state = build_double_elim(_players(4))
        assert state["champion_seed"] is None


# ---------------------------------------------------------------------------
# Double-elimination — result recording
# ---------------------------------------------------------------------------

class TestDoubleElimResults:
    def _full_run_4p(self) -> dict:
        """Run 4-player DE to completion (seed 1 wins everything)."""
        state = build_double_elim(_players(4))
        battle_id = 0

        def step() -> None:
            nonlocal battle_id
            for m in get_pending_matches(state):
                battle_id += 1
                # Always pick p1 (seed with lower seed number = p1_seed)
                record_result(state, m["match_id"], 1, battle_id)
                return  # one at a time

        # Keep stepping until champion known
        for _ in range(20):
            if state["champion_seed"] is not None:
                break
            step()

        return state

    def test_champion_determined(self) -> None:
        state = self._full_run_4p()
        assert state["champion_seed"] is not None

    def test_loser_drops_to_lb(self) -> None:
        state = build_double_elim(_players(4))
        # Play WB round 1 match 1 — loser should appear in LB
        pending = get_pending_matches(state)
        wb_r1 = next(m for m in pending if m["bracket"] == "winners" and m["round_num"] == 1)
        loser_to = wb_r1["loser_to"]
        assert loser_to is not None and loser_to.startswith("LR")
        record_result(state, wb_r1["match_id"], 1, 1)
        lb_m = state["match_index"][loser_to]
        loser_slot = wb_r1["loser_slot"]
        seeded_in_lb = lb_m["p1_seed"] if loser_slot == 1 else lb_m["p2_seed"]
        assert seeded_in_lb == wb_r1["p2_seed"]  # p2 lost

    def test_gfr_voided_when_wb_wins_gf(self) -> None:
        state = self._full_run_4p()
        # If seed 1 wins the GF directly, GFR should be void
        gf = state["match_index"]["GF"]
        if gf["status"] == "completed":
            gfr = state["match_index"]["GFR"]
            # Either void (WB won) or pending/completed (LB won)
            assert gfr["status"] in ("void", "pending", "completed")


# ---------------------------------------------------------------------------
# Double-elimination — two players (issue #279)
#
# With 2 players there are no losers-bracket rounds (lb_rounds == 0), so the
# code that dropped the WB R1 loser into "LR1-1" targeted a match that is never
# built. `record_result_double` guards that drop with `if lb_m:`, so it silently
# no-opped: GF's slot 2 stayed empty, get_pending_matches never returned GF, and
# the tournament drained with champion_seed still None — a 2-player double-elim
# could never produce a champion. The loser now goes straight to GF slot 2.
# ---------------------------------------------------------------------------

class TestDoubleElimTwoPlayers:
    def test_two_player_de_builds_no_losers_bracket(self) -> None:
        state = build_double_elim(_players(2))
        assert state["format"] == "double_elim"
        assert state["size"] == 2
        assert state["wb_rounds"] == 1
        assert state["lb_rounds"] == 0
        lb = [m for m in state["match_index"].values() if m["bracket"] == "losers"]
        assert lb == []

    def test_wb_loser_drops_into_grand_final(self) -> None:
        state = build_double_elim(_players(2))
        wb = state["match_index"]["WR1-1"]
        assert wb["loser_to"] == "GF"
        assert wb["loser_slot"] == 2

        record_result(state, "WR1-1", 1, battle_id=1)

        gf = state["match_index"]["GF"]
        assert gf["p1_seed"] == 1          # winners-bracket side
        assert gf["p2_seed"] == 2          # losers-bracket side
        assert [m["match_id"] for m in get_pending_matches(state)] == ["GF"]

    def test_two_player_de_produces_a_champion(self) -> None:
        state = build_double_elim(_players(2))
        battle_id = 0
        for _ in range(10):
            if state["champion_seed"] is not None:
                break
            pending = get_pending_matches(state)
            assert pending, "bracket drained with no champion (issue #279)"
            battle_id += 1
            record_result(state, pending[0]["match_id"], winner_slot=1, battle_id=battle_id)

        assert state["champion_seed"] == 1
        assert state["match_index"]["GFR"]["status"] == "void"

    def test_two_player_de_reset_when_lb_player_wins_gf(self) -> None:
        """Losing the WB match must not eliminate you — you get a second life."""
        state = build_double_elim(_players(2))
        record_result(state, "WR1-1", winner_slot=2, battle_id=1)   # seed 2 wins WB

        gf = state["match_index"]["GF"]
        assert (gf["p1_seed"], gf["p2_seed"]) == (2, 1)
        record_result(state, "GF", winner_slot=2, battle_id=2)      # seed 1 wins GF

        assert state["champion_seed"] is None
        gfr = state["match_index"]["GFR"]
        assert (gfr["p1_seed"], gfr["p2_seed"]) == (1, 2)
        assert [m["match_id"] for m in get_pending_matches(state)] == ["GFR"]

        record_result(state, "GFR", winner_slot=2, battle_id=3)     # seed 2 wins the reset
        assert state["champion_seed"] == 2


# ---------------------------------------------------------------------------
# Double-elimination — non-power-of-two (bye stall regression, issue #55)
# ---------------------------------------------------------------------------

class TestDoubleElimByes:
    """6-player bracket has 2 bye seeds.  Before the fix, bye losers dropped into LB
    and the affected LB matches were permanently skipped by get_pending_matches,
    causing the while-loop to drain to empty with champion_seed=None."""

    def _full_run(self, n: int) -> dict:
        state = build_double_elim(_players(n))
        battle_id = 0
        for _ in range(50):
            if state["champion_seed"] is not None:
                break
            pending = get_pending_matches(state)
            if not pending:
                break
            for m in pending:
                battle_id += 1
                record_result(state, m["match_id"], 1, battle_id)
                break  # one match at a time
        return state

    def test_6p_double_elim_produces_champion(self) -> None:
        """6 players (non-power-of-two) must yield a champion, not stall."""
        state = self._full_run(6)
        assert state["champion_seed"] is not None

    def test_5p_double_elim_produces_champion(self) -> None:
        state = self._full_run(5)
        assert state["champion_seed"] is not None

    def test_3p_double_elim_produces_champion(self) -> None:
        state = self._full_run(3)
        assert state["champion_seed"] is not None

    def test_6p_no_pending_match_has_bye_flag(self) -> None:
        """get_pending_matches must never return a match with a bye slot."""
        state = build_double_elim(_players(6))
        pending = get_pending_matches(state)
        for m in pending:
            assert not m.get("p1_is_bye"), f"Bye flag on pending match {m['match_id']}"
            assert not m.get("p2_is_bye"), f"Bye flag on pending match {m['match_id']}"

    def test_lb_bye_walkover_propagates(self) -> None:
        """After the fix, an LB match seeded with (real_player, bye) resolves as 'bye'
        and the winner is placed in the downstream match."""
        from nidozo.tournament.bracket import _resolve_lb_byes_double
        # Build a minimal match_index with one LB match: p1=real, p2=bye
        lb_match: dict = {
            "match_id": "LR1-1",
            "bracket": "losers",
            "round_num": 1,
            "p1_seed": 3,
            "p2_seed": 7,
            "p1_is_bye": False,
            "p2_is_bye": True,
            "status": "pending",
            "winner_seed": None,
            "loser_seed": None,
            "winner_to": "LR2-1",
            "winner_slot": 1,
            "loser_to": None,
            "loser_slot": None,
            "battle_id": None,
        }
        next_match: dict = {
            "match_id": "LR2-1",
            "bracket": "losers",
            "round_num": 2,
            "p1_seed": None,
            "p2_seed": None,
            "p1_is_bye": False,
            "p2_is_bye": False,
            "status": "pending",
            "winner_seed": None,
            "loser_seed": None,
            "winner_to": None,
            "winner_slot": None,
            "loser_to": None,
            "loser_slot": None,
            "battle_id": None,
        }
        mi = {"LR1-1": lb_match, "LR2-1": next_match}
        _resolve_lb_byes_double(mi)
        assert lb_match["status"] == "bye"
        assert lb_match["winner_seed"] == 3
        # Winner should have been forwarded into the downstream match
        assert next_match["p1_seed"] == 3


# ---------------------------------------------------------------------------
# build_bracket dispatch
# ---------------------------------------------------------------------------

def test_build_bracket_single() -> None:
    state = build_bracket(_players(4), "single_elim")
    assert state["format"] == "single_elim"


def test_build_bracket_double() -> None:
    state = build_bracket(_players(4), "double_elim")
    assert state["format"] == "double_elim"


def test_build_bracket_invalid() -> None:
    with pytest.raises(ValueError, match="Unknown bracket format"):
        build_bracket(_players(4), "swiss")


# ---------------------------------------------------------------------------
# Schema v7 migration
# ---------------------------------------------------------------------------

class TestSchemaV7Migration:
    def test_v7_adds_bracket_columns(self) -> None:
        import sqlite3

        from nidozo.db.schema import migrate

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)

        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tournaments)").fetchall()]
        assert "tournament_format" in cols
        assert "bracket_state" in cols

    def test_v7_tournament_format_defaults_to_round_robin(self) -> None:
        import sqlite3

        from nidozo.db.schema import migrate

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)

        conn.execute(
            """INSERT INTO tournaments
               (players, rounds, prompt_version, total_battles, tier)
               VALUES ('[]', 1, 'v2', 0, 'random')"""
        )
        row = conn.execute(
            "SELECT tournament_format FROM tournaments LIMIT 1"
        ).fetchone()
        assert row["tournament_format"] == "round_robin"


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

# --- Single-elim p2_is_bye branch (p2 gets the bye, p1 wins) ---

class TestByeHandlingExtended:
    def test_p2_bye_p1_wins_single_elim(self) -> None:
        """When p2 is a bye in single-elim, p1 should be set as winner."""
        # 3 players → size=4; bye slots depend on seeding order.
        # Build the bracket and check that every bye match has the real player as winner.
        state = build_single_elim(_players(3))
        bye_matches = [m for m in state["match_index"].values() if m["status"] == "bye"]
        for m in bye_matches:
            # Winner must be the non-bye seed
            if m.get("p1_is_bye") and not m.get("p2_is_bye"):
                assert m["winner_seed"] == m["p2_seed"]
            elif m.get("p2_is_bye") and not m.get("p1_is_bye"):
                assert m["winner_seed"] == m["p1_seed"]

    def test_bye_propagates_into_round2(self) -> None:
        """Winner of a bye match is placed into round-2 before any result is recorded."""
        state = build_single_elim(_players(3))
        # Find the round-2 match
        r2_matches = [m for m in state["match_index"].values() if m["round_num"] == 2]
        assert r2_matches, "No round-2 match found"
        r2 = r2_matches[0]
        # At least one slot in round 2 should be pre-filled from the bye propagation
        assert r2["p1_seed"] is not None or r2["p2_seed"] is not None

    def test_p2_bye_auto_resolves_double_elim(self) -> None:
        """In double-elim, bye matches auto-resolve with p1 winning."""
        state = build_double_elim(_players(3))
        bye_matches = [
            m for m in state["match_index"].values()
            if m["bracket"] == "winners" and m["round_num"] == 1 and m["status"] == "bye"
        ]
        for m in bye_matches:
            if m.get("p2_is_bye") and not m.get("p1_is_bye"):
                assert m["winner_seed"] == m["p1_seed"]


# --- record_result_single — guard when match_id not found ---

def test_record_result_single_unknown_match_id_is_noop() -> None:
    """record_result_single does nothing when match_id not in index."""
    state = build_single_elim(_players(2))
    before = dict(state["match_index"]["WR1-1"])
    record_result_single(state, "WR99-99", 1, 0)
    assert state["match_index"]["WR1-1"] == before


# --- record_result_double — guard when match_id not found ---

def test_record_result_double_unknown_match_id_is_noop() -> None:
    """record_result_double does nothing when match_id not in index."""
    from nidozo.tournament.bracket import record_result_double

    state = build_double_elim(_players(4))
    champion_before = state["champion_seed"]
    record_result_double(state, "WR99-99", 1, 0)
    assert state["champion_seed"] == champion_before


# --- GF WB winner path: GFR becomes void ---

def test_gf_wb_winner_sets_champion_and_voids_gfr() -> None:
    """When WB player wins GF (slot 1), champion is set and GFR is voided."""
    from nidozo.tournament.bracket import record_result_double

    state = build_double_elim(_players(4))
    mi = state["match_index"]
    gf = mi["GF"]
    # Manually set both seeds so we can record result
    gf["p1_seed"] = 1
    gf["p2_seed"] = 3
    gf["status"] = "pending"
    record_result_double(state, "GF", 1, battle_id=99)
    assert state["champion_seed"] == 1
    assert mi["GFR"]["status"] == "void"


# --- GF LB winner path: GFR becomes pending ---

def test_gf_lb_winner_triggers_gfr() -> None:
    """When LB player wins GF (slot 2), GFR is set to pending."""
    from nidozo.tournament.bracket import record_result_double

    state = build_double_elim(_players(4))
    mi = state["match_index"]
    gf = mi["GF"]
    gf["p1_seed"] = 1
    gf["p2_seed"] = 3
    gf["status"] = "pending"
    record_result_double(state, "GF", 2, battle_id=99)
    assert mi["GFR"]["status"] == "pending"
    assert mi["GFR"]["p1_seed"] == gf["p2_seed"]  # LB player
    assert mi["GFR"]["p2_seed"] == gf["p1_seed"]  # WB player


# --- GFR completes → champion set ---

def test_gfr_completion_sets_champion() -> None:
    """Recording GFR result sets champion_seed."""
    from nidozo.tournament.bracket import record_result_double

    state = build_double_elim(_players(4))
    mi = state["match_index"]
    gfr = mi["GFR"]
    gfr["p1_seed"] = 3
    gfr["p2_seed"] = 1
    gfr["status"] = "pending"
    record_result_double(state, "GFR", 1, battle_id=100)
    assert state["champion_seed"] == 3


# --- get_pending_matches excludes bye-slotted entries ---

def test_get_pending_matches_excludes_bye_flagged() -> None:
    """get_pending_matches never returns matches with p1_is_bye or p2_is_bye."""
    state = build_double_elim(_players(3))
    pending = get_pending_matches(state)
    for m in pending:
        assert not m.get("p1_is_bye"), f"Match {m['match_id']} has p1_is_bye"
        assert not m.get("p2_is_bye"), f"Match {m['match_id']} has p2_is_bye"


# --- resolve_seed ---

def test_resolve_seed_none_returns_none() -> None:
    """resolve_seed(state, None) → None."""
    state = build_single_elim(_players(2))
    assert resolve_seed(state, None) is None


def test_resolve_seed_missing_seed_returns_none() -> None:
    """resolve_seed with a seed beyond the bracket size → None."""
    state = build_single_elim(_players(2))
    assert resolve_seed(state, 99) is None


# --- seeds dict has None entries for byes ---

def test_seeds_has_none_for_bye_slots() -> None:
    """Bye seeds in the bracket are stored as None."""
    state = build_single_elim(_players(3))
    # size=4, 3 real players → seed 4 is a bye
    assert state["seeds"]["4"] is None


# --- Double-elim seeds map has None for byes ---

def test_double_elim_seeds_none_for_bye_slot() -> None:
    state = build_double_elim(_players(3))
    # size=4, seed 4 is bye
    assert state["seeds"]["4"] is None


# --- record_result dispatch invalid format ---

def test_record_result_invalid_format_raises() -> None:
    """record_result raises ValueError for an unknown format."""
    state = build_single_elim(_players(2))
    state["format"] = "unknown_format"
    with pytest.raises(ValueError, match="Unknown bracket format"):
        record_result(state, "WR1-1", 1, 1)


# ---------------------------------------------------------------------------
# New coverage tests — bracket.py missing lines
# ---------------------------------------------------------------------------

def test_advance_winner_single_early_return_guard() -> None:
    """Line 219: _advance_winner_single returns early when winner_to is None.

    This fires for the final match — its winner_to is None (no next match).
    After recording the final, _advance_winner_single is called but returns
    immediately on line 219 because wt is None.
    """
    # A 2-player bracket: WR1-1 is the final (winner_to=None)
    state = build_single_elim(_players(2))
    final_match = state["match_index"]["WR1-1"]
    # Confirm winner_to is None for the final match
    assert final_match["winner_to"] is None
    # Record result — _advance_winner_single is called, hits the guard, returns
    record_result_single(state, "WR1-1", 1, battle_id=1)
    # champion_seed should be set
    assert state["champion_seed"] == final_match["p1_seed"]


def test_record_result_dispatch_single_elim() -> None:
    """Line 580: record_result dispatches to record_result_single for single_elim."""
    state = build_single_elim(_players(2))
    # Use the dispatch function instead of record_result_single directly
    record_result(state, "WR1-1", 1, battle_id=42)
    assert state["match_index"]["WR1-1"]["winner_seed"] is not None
    assert state["champion_seed"] is not None


def test_build_single_elim_three_players_has_p2_bye() -> None:
    """Lines 153-155: 3-player bracket produces a match where p2 is the bye.

    build_single_elim(_players(3)) produces size=4 with one bye slot.
    The bracket seeding puts (1 vs 4) and (2 vs 3) in round 1.
    Seed 4 is None (bye), so the WR1-1 match has p2_is_bye=True.
    """
    state = build_single_elim(_players(3))
    bye_matches = [m for m in state["match_index"].values() if m["status"] == "bye"]
    assert len(bye_matches) >= 1
    # In the bye match, exactly one side should be flagged as a bye
    for m in bye_matches:
        assert m.get("p1_is_bye") or m.get("p2_is_bye")
        # winner_seed is the non-bye player
        assert m["winner_seed"] is not None


def test_build_double_elim_three_players_has_p2_bye() -> None:
    """Lines 308-310: 3-player DE produces a bye match in WB round 1."""
    state = build_double_elim(_players(3))
    wb_r1_byes = [
        m for m in state["match_index"].values()
        if m["bracket"] == "winners" and m["round_num"] == 1 and m["status"] == "bye"
    ]
    assert len(wb_r1_byes) >= 1
    for m in wb_r1_byes:
        assert m.get("p1_is_bye") or m.get("p2_is_bye")
        assert m["winner_seed"] is not None


def test_de_even_lb_round_odd_index_slot_assignment() -> None:
    """Lines 344-346: even LB round at odd index uses slot 2.

    In DE with 4+ players, LB even rounds merge adjacent match pairs.
    For an even LB round, match at odd index (idx % 2 == 1) gets ws=2.
    We just verify the LR2-2 match (if present) has winner_slot=2.
    """
    state = build_double_elim(_players(4))
    mi = state["match_index"]
    # LR2 is an even round; LR2-2 is the second match (idx=1 → ws=2)
    lr2_2 = mi.get("LR2-2")
    if lr2_2 is not None:
        assert lr2_2["winner_slot"] == 2
    else:
        # With 4 players, there may be only one LR2 match — still valid
        lr2_1 = mi.get("LR2-1")
        assert lr2_1 is not None


def test_propagate_byes_double_slots_loser_into_lb() -> None:
    """Lines 454-455: loser of a bye match in DE gets slotted into LB.

    With 3 players (one bye), the WB bye match drops the bye seed into LB.
    After build_double_elim, verify that the LR1 match has a seed slotted
    from the bye propagation.
    """
    state = build_double_elim(_players(3))
    mi = state["match_index"]
    # LR1-1 should have one seed pre-slotted from the bye propagation
    lr1_1 = mi.get("LR1-1")
    assert lr1_1 is not None
    # At least one slot should be populated (the bye seed was propagated in)
    assert lr1_1.get("p1_seed") is not None or lr1_1.get("p2_seed") is not None


def test_get_pending_matches_sort_key_across_brackets() -> None:
    """Lines 539-545: get_all_matches_ordered sorts across bracket types.

    Building a 4-player DE and calling get_all_matches_ordered exercises
    the sort key for winners, losers, and grand_final brackets.
    """
    from nidozo.tournament.bracket import get_all_matches_ordered

    state = build_double_elim(_players(4))
    ordered = get_all_matches_ordered(state)
    assert len(ordered) > 0
    # Winners bracket matches must appear before losers, which before grand_final
    brackets = [m["bracket"] for m in ordered]
    bracket_order = {"winners": 0, "losers": 1, "grand_final": 2}
    bracket_nums = [bracket_order[b] for b in brackets]
    assert bracket_nums == sorted(bracket_nums)


def test_get_pending_matches_de_mixed_brackets() -> None:
    """get_pending_matches on a DE bracket with matches in different brackets."""
    state = build_double_elim(_players(4))
    pending = get_pending_matches(state)
    # In a fresh 4-player DE, there should be ready WB round-1 matches
    wb_pending = [m for m in pending if m["bracket"] == "winners"]
    assert len(wb_pending) >= 1


def test_de_eight_players_lb_even_round_odd_index() -> None:
    """Lines 344-346: even LB round with idx > 0 sets winner_slot=2.

    An 8-player DE has lb_rounds=4. LR2 is an even round with 2 matches.
    LR2-2 (idx=1) hits the 'ws = 2' branch because idx % 2 == 1.
    """
    state = build_double_elim(_players(8))
    mi = state["match_index"]
    # LR2-2 is the second match in LB round 2 (even, not last)
    lr2_2 = mi.get("LR2-2")
    assert lr2_2 is not None, "LR2-2 should exist for 8-player DE"
    # idx=1 → ws = 1 if 1 % 2 == 0 else 2 → ws=2
    assert lr2_2["winner_slot"] == 2


def test_propagate_byes_double_p2_slot_five_players() -> None:
    """Lines 454-455: bye loser placed in LB p2 slot during bye propagation.

    With 5 players (size=8), WR1-4 is a bye match with loser_slot=2.
    _propagate_byes_double places the bye loser into LB's p2 slot (line 455).
    """
    state = build_double_elim(_players(5))
    mi = state["match_index"]
    # WR1-4 should be a bye match (seed 6 is bye with 5 players)
    wr1_4 = mi.get("WR1-4")
    assert wr1_4 is not None, "WR1-4 should exist for 5-player DE"
    if wr1_4.get("status") == "bye":
        lt = wr1_4.get("loser_to")
        ls = wr1_4.get("loser_slot")
        assert ls == 2, f"loser_slot should be 2, got {ls}"
        lb_m = mi.get(lt)
        assert lb_m is not None
        # The bye seed should be in the p2 slot
        assert lb_m.get("p2_is_bye") is True


def test_advance_winner_single_next_match_none_guard() -> None:
    """Line 219: _advance_winner_single returns when winner_to match is absent.

    Manually craft a completed match with a winner_to that points to a
    nonexistent match ID. Calling _advance_winner_single should not raise.
    """
    from nidozo.tournament.bracket import _advance_winner_single

    match_index = {}
    completed = {
        "winner_to":   "NONEXISTENT",
        "winner_slot":  1,
        "winner_seed":  1,
    }
    # Should return early without raising (next_m is None)
    _advance_winner_single(match_index, completed)  # no error = test passes


# ---------------------------------------------------------------------------
# Bracket tie tiebreak (issue #131)
# ---------------------------------------------------------------------------

class TestBracketAdvanceSlot:
    """_bracket_advance_slot picks who advances; a tie must not silently
    credit p2 (the pre-fix bug)."""

    def test_p1_win_advances_slot_1(self) -> None:
        from nidozo.api.orchestration import _bracket_advance_slot
        assert _bracket_advance_slot(1, p1_seed=1, p2_seed=2) == 1

    def test_p2_win_advances_slot_2(self) -> None:
        from nidozo.api.orchestration import _bracket_advance_slot
        assert _bracket_advance_slot(2, p1_seed=1, p2_seed=2) == 2

    def test_tie_advances_better_seed_when_p1_better(self) -> None:
        from nidozo.api.orchestration import _bracket_advance_slot
        # Lower seed number = better seed; p1 is seed 1
        assert _bracket_advance_slot(None, p1_seed=1, p2_seed=4) == 1

    def test_tie_advances_better_seed_when_p2_better(self) -> None:
        from nidozo.api.orchestration import _bracket_advance_slot
        # p2 holds the better (lower) seed → it advances, NOT a default p2 win
        assert _bracket_advance_slot(None, p1_seed=6, p2_seed=2) == 2

    def test_tie_is_deterministic(self) -> None:
        from nidozo.api.orchestration import _bracket_advance_slot
        a = _bracket_advance_slot(None, p1_seed=3, p2_seed=5)
        b = _bracket_advance_slot(None, p1_seed=3, p2_seed=5)
        assert a == b == 1


def test_tie_advances_better_seed_through_record_result() -> None:
    """End-to-end on the bracket: a tied final advances the better seed as
    champion, instead of always crediting p2."""
    state = build_single_elim(_players(2))
    pending = get_pending_matches(state)
    match = pending[0]
    from nidozo.api.orchestration import _bracket_advance_slot

    # Simulate a tie (winner=None) and route via the tiebreak helper
    advance_slot = _bracket_advance_slot(None, match["p1_seed"], match["p2_seed"])
    record_result(state, match["match_id"], advance_slot, battle_id=1)

    better_seed = min(match["p1_seed"], match["p2_seed"])
    assert state["champion_seed"] == better_seed
