"""Integration tests for BattleStore using an in-memory SQLite database."""

import pytest

from nidozo.db.elo import DEFAULT_RATING, DEFAULT_RD, PROVISIONAL_RD
from nidozo.db.store import BattleStore


@pytest.fixture
def store(tmp_path):
    s = BattleStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


# ------------------------------------------------------------------
# Model registration
# ------------------------------------------------------------------

def test_get_or_create_model_creates_new(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    assert isinstance(mid, int)


def test_get_or_create_model_idempotent(store) -> None:
    mid1 = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    mid2 = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    assert mid1 == mid2


def test_different_prompt_version_is_different_model(store) -> None:
    mid1 = store.get_or_create_model("anthropic", "claude-sonnet-4-6", prompt_version="v1")
    mid2 = store.get_or_create_model("anthropic", "claude-sonnet-4-6", prompt_version="v2")
    assert mid1 != mid2


def test_new_model_gets_default_elo(store) -> None:
    mid = store.get_or_create_model("random", "random")
    row = store._conn.execute(
        "SELECT rating FROM elo_ratings WHERE model_id=?", (mid,)
    ).fetchone()
    assert row["rating"] == DEFAULT_RATING


# ------------------------------------------------------------------
# Battle lifecycle
# ------------------------------------------------------------------

def test_create_battle_returns_id(store) -> None:
    p1 = store.get_or_create_model("random", "random")
    p2 = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    bid = store.create_battle("battle-1", "gen3randombattle", p1, p2)
    assert isinstance(bid, int)


def test_finish_battle_records_winner(store) -> None:
    p1 = store.get_or_create_model("random", "random")
    p2 = store.get_or_create_model("random", "random-2")
    bid = store.create_battle("battle-2", "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=25)

    row = store._conn.execute(
        "SELECT winner, total_turns FROM battles WHERE id=?", (bid,)
    ).fetchone()
    assert row["winner"] == 1
    assert row["total_turns"] == 25


def test_finish_battle_updates_elo(store) -> None:
    p1 = store.get_or_create_model("random", "a")
    p2 = store.get_or_create_model("random", "b")
    bid = store.create_battle("battle-3", "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=10)

    r1 = store._conn.execute("SELECT rating FROM elo_ratings WHERE model_id=?", (p1,)).fetchone()["rating"]
    r2 = store._conn.execute("SELECT rating FROM elo_ratings WHERE model_id=?", (p2,)).fetchone()["rating"]

    assert r1 > DEFAULT_RATING
    assert r2 < DEFAULT_RATING


def test_elo_history_row_created(store) -> None:
    p1 = store.get_or_create_model("random", "c")
    p2 = store.get_or_create_model("random", "d")
    bid = store.create_battle("battle-4", "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=2, total_turns=15)

    count = store._conn.execute(
        "SELECT COUNT(*) FROM elo_history WHERE battle_id=?", (bid,)
    ).fetchone()[0]
    assert count == 2  # one row per player


def test_tie_elo_unchanged(store) -> None:
    p1 = store.get_or_create_model("random", "e")
    p2 = store.get_or_create_model("random", "f")
    bid = store.create_battle("battle-5", "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=None, total_turns=50)

    r1 = store._conn.execute("SELECT rating FROM elo_ratings WHERE model_id=?", (p1,)).fetchone()["rating"]
    r2 = store._conn.execute("SELECT rating FROM elo_ratings WHERE model_id=?", (p2,)).fetchone()["rating"]

    assert r1 == pytest.approx(DEFAULT_RATING)
    assert r2 == pytest.approx(DEFAULT_RATING)


# ------------------------------------------------------------------
# Turn logging
# ------------------------------------------------------------------

def test_log_turn_inserts_row(store) -> None:
    p1 = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    p2 = store.get_or_create_model("random", "random")
    bid = store.create_battle("battle-6", "gen3randombattle", p1, p2)

    store.log_turn(bid, 1, "p1", "v1", "move 2", True, "I'll use Thunderbolt.\nACTION: move 2")

    row = store._conn.execute("SELECT * FROM turns WHERE battle_id=?", (bid,)).fetchone()
    assert row["action_chosen"] == "move 2"
    assert row["parse_success"] == 1


def test_log_turn_parse_failure(store) -> None:
    p1 = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    p2 = store.get_or_create_model("random", "random")
    bid = store.create_battle("battle-7", "gen3randombattle", p1, p2)

    store.log_turn(bid, 3, "p1", "v1", None, False, "I'm not sure...",
                   fallback_reason="parse_failure")

    row = store._conn.execute("SELECT * FROM turns WHERE battle_id=?", (bid,)).fetchone()
    assert row["parse_success"] == 0
    assert row["action_chosen"] is None
    assert row["fallback_reason"] == "parse_failure"


def test_log_turn_success_has_no_fallback_reason(store) -> None:
    p1 = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    p2 = store.get_or_create_model("random", "random")
    bid = store.create_battle("battle-7b", "gen3randombattle", p1, p2)

    store.log_turn(bid, 1, "p1", "v5", "move 1", True, "Thunderbolt it is.")

    row = store._conn.execute("SELECT * FROM turns WHERE battle_id=?", (bid,)).fetchone()
    assert row["parse_success"] == 1
    assert row["fallback_reason"] is None


# ------------------------------------------------------------------
# Leaderboard
# ------------------------------------------------------------------

def test_leaderboard_sorted_by_elo(store) -> None:
    strong = store.get_or_create_model("anthropic", "strong-model")
    weak = store.get_or_create_model("anthropic", "weak-model")

    # strong wins 3 battles in a row
    for i in range(3):
        bid = store.create_battle(f"lb-battle-{i}", "gen3randombattle", strong, weak)
        store.finish_battle(bid, winner=1, total_turns=20)

    rows = store.leaderboard()
    assert rows[0]["model_name"] == "strong-model"
    assert rows[1]["model_name"] == "weak-model"
    assert rows[0]["rating"] > rows[1]["rating"]

# ------------------------------------------------------------------
# Model stats
# ------------------------------------------------------------------

def test_leaderboard_grouped_includes_model_id(store) -> None:
    """Grouped leaderboard rows include a model_id field."""
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("tag-1", "gen3randombattle", mid, opp)
    store.finish_battle(bid, winner=1, total_turns=10)
    rows = store.leaderboard(grouped=True)
    claude_row = next(r for r in rows if r["model_name"] == "claude-test")
    assert "model_id" in claude_row
    assert claude_row["model_id"] == mid


def test_get_model_stats_returns_none_for_missing(store) -> None:
    assert store.get_model_stats(99999) is None


def test_get_model_stats_identity_fields(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    stats = store.get_model_stats(mid)
    assert stats is not None
    assert stats["model"]["provider"] == "anthropic"
    assert stats["model"]["model_name"] == "claude-test"
    assert stats["model"]["prompt_version"] == "v2"
    assert "rating" in stats["model"]


def test_get_model_stats_win_loss_counts(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")

    bid1 = store.create_battle("t1", "gen3randombattle", mid, opp)
    store.finish_battle(bid1, winner=1, total_turns=10)   # win (p1)

    bid2 = store.create_battle("t2", "gen3randombattle", opp, mid)
    store.finish_battle(bid2, winner=1, total_turns=8)    # loss (p2, opp won)

    bid3 = store.create_battle("t3", "gen3randombattle", mid, opp)
    store.finish_battle(bid3, winner=None, total_turns=50) # tie

    stats = store.get_model_stats(mid)
    assert stats is not None
    m = stats["model"]
    assert m["wins"] == 1
    assert m["losses"] == 1
    assert m["ties"] == 1


def test_get_model_stats_elo_history(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    for i in range(3):
        bid = store.create_battle(f"h{i}", "gen3randombattle", mid, opp)
        store.finish_battle(bid, winner=1, total_turns=10)

    stats = store.get_model_stats(mid)
    assert stats is not None
    assert len(stats["elo_history"]) == 3
    row = stats["elo_history"][0]
    assert "rating_before" in row
    assert "rating_after" in row
    assert "delta" in row


def test_get_model_stats_elo_history_shows_most_recent_not_first(store) -> None:
    """With >30 battles the ELO chart must show the most recent 30, not the
    earliest 30 (issue #132). finished_at is second-granularity, so set distinct
    timestamps explicitly to make ordering deterministic."""
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")

    battle_ids = []
    for i in range(1, 36):  # 35 battles, cap is 30
        bid = store.create_battle(f"eh{i}", "gen9randombattle", mid, opp)
        store.finish_battle(bid, winner=1, total_turns=10)
        # Distinct, increasing timestamp so DESC ordering is unambiguous.
        store._conn.execute(
            "UPDATE battles SET finished_at=? WHERE id=?",
            (f"2026-01-01T00:00:{i:02d}Z", bid),
        )
        battle_ids.append(bid)
    store._conn.commit()

    stats = store.get_model_stats(mid)
    assert stats is not None
    history = stats["elo_history"]
    assert len(history) == 30  # capped

    returned_ids = {row["battle_id"] for row in history}
    # Newest battle is present; oldest five are dropped.
    assert battle_ids[-1] in returned_ids
    assert battle_ids[0] not in returned_ids
    assert battle_ids[4] not in returned_ids
    assert battle_ids[5] in returned_ids  # first of the kept window

    # Returned rows are in ascending chronological order for the chart.
    finished = [row["finished_at"] for row in history]
    assert finished == sorted(finished)


def test_get_model_stats_battle_history(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("t1", "gen3randombattle", mid, opp)
    store.finish_battle(bid, winner=1, total_turns=15)

    stats = store.get_model_stats(mid)
    assert stats is not None
    assert len(stats["battle_history"]) == 1
    b = stats["battle_history"][0]
    assert b["result"] == "win"
    assert "opponent" in b
    assert b["total_turns"] == 15


def test_get_model_stats_turn_stats(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("t1", "gen3randombattle", mid, opp)
    store.log_turn(bid, 1, "p1", "v2", "surf", True)
    store.log_turn(bid, 2, "p1", "v2", None, False)
    store.log_turn(bid, 1, "p2", "v2", "flamethrower", True)  # opponent — should not count

    stats = store.get_model_stats(mid)
    assert stats is not None
    ts = stats["turn_stats"]
    assert ts["total_turns"] == 2   # only p1 turns
    assert ts["parse_ok"] == 1
    assert ts["parse_fail"] == 1
    assert ts["parse_success_rate"] == 50.0


def test_get_model_stats_lessons(store) -> None:
    mid = store.get_or_create_model("anthropic", "claude-test", "v2")
    opp = store.get_or_create_model("random", "random", "v2")
    bid = store.create_battle("t1", "gen3randombattle", mid, opp)
    store.create_lesson(mid, bid, "Always use Water-type moves.")

    stats = store.get_model_stats(mid)
    assert stats is not None
    assert len(stats["lessons"]) == 1
    assert stats["lessons"][0]["content"] == "Always use Water-type moves."


# ---------------------------------------------------------------------------
# Tournament store methods  (feat/tournament-mode)
# ---------------------------------------------------------------------------

def _make_tournament(store, n_players: int = 3, rounds: int = 1) -> tuple[int, list[int]]:
    """Helper: create a tournament with n_players and register it in the store."""
    players = [{"provider": "random", "model_name": f"bot{i}"} for i in range(n_players)]
    tid = store.create_tournament(players=players, rounds=rounds, prompt_version="v2", total_battles=n_players * (n_players - 1) * rounds)
    model_ids = [store.get_or_create_model("random", f"bot{i}", "v2") for i in range(n_players)]
    return tid, model_ids


def test_list_tournaments_empty(store) -> None:
    result = store.list_tournaments()
    assert isinstance(result, list)
    assert len(result) == 0


def test_list_tournaments_returns_rows(store) -> None:
    tid, _ = _make_tournament(store)
    rows = store.list_tournaments()
    assert len(rows) >= 1
    t = next(r for r in rows if r["id"] == tid)
    assert t["status"] == "running"
    assert t["rounds"] == 1
    assert "battles_completed" in t


def test_list_tournaments_battles_completed_count(store) -> None:
    tid, mids = _make_tournament(store, n_players=2)
    bid = store.create_battle("tag-lsbc", "gen3randombattle", mids[0], mids[1], tournament_id=tid)
    store.finish_battle(bid, winner=1, total_turns=5)
    store.set_battle_status(bid, "completed")

    rows = store.list_tournaments()
    t = next(r for r in rows if r["id"] == tid)
    assert t["battles_completed"] == 1


def test_get_tournament_standings_empty(store) -> None:
    """Standings are empty when no battles have been completed."""
    tid, _ = _make_tournament(store)
    standings = store.get_tournament_standings(tid)
    assert standings == []


def test_get_tournament_standings_wins_losses(store) -> None:
    """Wins and losses are correctly attributed within the tournament."""
    tid, mids = _make_tournament(store, n_players=2)
    a_id, b_id = mids[0], mids[1]

    bid = store.create_battle("tag-wl", "gen3randombattle", a_id, b_id, tournament_id=tid)
    store.finish_battle(bid, winner=1, total_turns=10)
    store.set_battle_status(bid, "completed")

    standings = store.get_tournament_standings(tid)
    a_row = next(r for r in standings if r["model_id"] == a_id)
    b_row = next(r for r in standings if r["model_id"] == b_id)

    assert a_row["wins"] == 1
    assert a_row["losses"] == 0
    assert b_row["wins"] == 0
    assert b_row["losses"] == 1


def test_get_tournament_standings_ties(store) -> None:
    tid, mids = _make_tournament(store, n_players=2)
    bid = store.create_battle("tag-tie", "gen3randombattle", mids[0], mids[1], tournament_id=tid)
    store.finish_battle(bid, winner=None, total_turns=50)
    store.set_battle_status(bid, "completed")

    standings = store.get_tournament_standings(tid)
    for row in standings:
        assert row["ties"] == 1
        assert row["wins"] == 0


def test_get_tournament_standings_points(store) -> None:
    """Points = 3 × wins + 1 × ties."""
    tid, mids = _make_tournament(store, n_players=2)

    # Game 1: a wins
    b1 = store.create_battle("tag-pts1", "gen3randombattle", mids[0], mids[1], tournament_id=tid)
    store.finish_battle(b1, winner=1, total_turns=8)
    store.set_battle_status(b1, "completed")

    # Game 2: tie
    b2 = store.create_battle("tag-pts2", "gen3randombattle", mids[0], mids[1], tournament_id=tid)
    store.finish_battle(b2, winner=None, total_turns=20)
    store.set_battle_status(b2, "completed")

    standings = store.get_tournament_standings(tid)
    a_row = next(r for r in standings if r["model_id"] == mids[0])
    # 1 win (3 pts) + 1 tie (1 pt) = 4 pts
    assert a_row["points"] == 4


def test_get_tournament_standings_sorted_by_points(store) -> None:
    """Standings are sorted descending by points."""
    tid, mids = _make_tournament(store, n_players=3)
    a, b, c = mids

    # a beats b; a beats c; b beats c
    for winner_idx, (p1, p2) in [(1, (a, b)), (1, (a, c)), (1, (b, c))]:
        tag = f"tag-sort-{p1}-{p2}"
        bid = store.create_battle(tag, "gen3randombattle", p1, p2, tournament_id=tid)
        store.finish_battle(bid, winner=winner_idx, total_turns=5)
        store.set_battle_status(bid, "completed")

    standings = store.get_tournament_standings(tid)
    pts = [r["points"] for r in standings]
    assert pts == sorted(pts, reverse=True)


def test_get_tournament_battles_returns_all(store) -> None:
    tid, mids = _make_tournament(store, n_players=2)
    b1 = store.create_battle("tag-gb1", "gen3randombattle", mids[0], mids[1], tournament_id=tid)
    b2 = store.create_battle("tag-gb2", "gen3randombattle", mids[1], mids[0], tournament_id=tid)

    battles = store.get_tournament_battles(tid)
    battle_ids = {b["id"] for b in battles}
    assert b1 in battle_ids
    assert b2 in battle_ids
    assert len(battles) == 2


def test_get_tournament_battles_not_mixed_with_other_tournaments(store) -> None:
    tid1, mids1 = _make_tournament(store, n_players=2)
    tid2, mids2 = _make_tournament(store, n_players=2)
    b1 = store.create_battle("tag-mx1", "gen3randombattle", mids1[0], mids1[1], tournament_id=tid1)
    b2 = store.create_battle("tag-mx2", "gen3randombattle", mids2[0], mids2[1], tournament_id=tid2)

    battles1 = store.get_tournament_battles(tid1)
    assert any(b["id"] == b1 for b in battles1)
    assert all(b["id"] != b2 for b in battles1)


def test_cancel_tournament_running(store) -> None:
    tid, _ = _make_tournament(store)
    result = store.cancel_tournament(tid)
    assert result is True
    t = store.get_tournament(tid)
    assert t is not None
    assert t["status"] == "cancelled"


def test_cancel_tournament_already_finished_returns_false(store) -> None:
    tid, _ = _make_tournament(store)
    store.finish_tournament(tid, status="completed")
    result = store.cancel_tournament(tid)
    assert result is False


def test_cancel_tournament_nonexistent_returns_false(store) -> None:
    result = store.cancel_tournament(99999)
    assert result is False


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

# --- update_bracket_state / get_bracket_state ---

def test_update_and_get_bracket_state(store) -> None:
    """update_bracket_state persists and get_bracket_state retrieves the bracket."""
    tid, _ = _make_tournament(store)
    bracket = {"format": "single_elim", "size": 4, "champion_seed": None}

    store.update_bracket_state(tid, bracket)

    result = store.get_bracket_state(tid)
    assert result is not None
    assert result["format"] == "single_elim"
    assert result["size"] == 4


def test_get_bracket_state_returns_none_when_absent(store) -> None:
    """get_bracket_state returns None when bracket_state column is empty."""
    tid, _ = _make_tournament(store)
    # No update_bracket_state called → column is NULL
    result = store.get_bracket_state(tid)
    assert result is None


def test_get_bracket_state_returns_none_for_nonexistent_tournament(store) -> None:
    """get_bracket_state returns None when tournament doesn't exist."""
    result = store.get_bracket_state(99999)
    assert result is None


# --- get_model_stats ---

def test_get_model_stats_returns_correct_structure(store) -> None:
    """get_model_stats returns a dict with expected keys for a valid model."""
    mid = store.get_or_create_model("anthropic", "claude-sonnet-4-6")
    result = store.get_model_stats(mid)

    assert result is not None
    assert "model" in result
    assert "elo_history" in result
    assert "battle_history" in result
    assert "turn_stats" in result
    assert "lessons" in result

    assert result["model"]["id"] == mid
    assert result["model"]["provider"] == "anthropic"
    assert result["model"]["model_name"] == "claude-sonnet-4-6"


def test_get_model_stats_returns_none_for_nonexistent_model(store) -> None:
    """get_model_stats returns None when model_id doesn't exist."""
    result = store.get_model_stats(99999)
    assert result is None


def test_get_model_stats_parse_success_rate_none_with_no_turns(store) -> None:
    """With no turns logged, parse_success_rate is None."""
    mid = store.get_or_create_model("random", "no-turns")
    result = store.get_model_stats(mid)
    assert result is not None
    assert result["turn_stats"]["parse_success_rate"] is None
    assert result["turn_stats"]["total_turns"] == 0


# ---------------------------------------------------------------------------
# New coverage tests — store.py missing lines
# ---------------------------------------------------------------------------

def test_get_battle_players_returns_dict(store) -> None:
    """Lines 397-406: get_battle_players returns p1/p2 provider+model info."""
    p1 = store.get_or_create_model("anthropic", "claude-test")
    p2 = store.get_or_create_model("random", "random")
    bid = store.create_battle("tag-gbp", "gen3randombattle", p1, p2)

    result = store.get_battle_players(bid)
    assert result is not None
    assert result["p1_provider"] == "anthropic"
    assert result["p1_model"] == "claude-test"
    assert result["p2_provider"] == "random"
    assert result["p2_model"] == "random"


def test_get_battle_players_returns_none_for_nonexistent(store) -> None:
    """Lines 397-406: get_battle_players returns None when battle not found."""
    result = store.get_battle_players(99999)
    assert result is None


def test_update_battle_tag(store) -> None:
    """Lines 410-413: update_battle_tag changes the battle_tag in the DB."""
    p1 = store.get_or_create_model("random", "a")
    p2 = store.get_or_create_model("random", "b")
    bid = store.create_battle("old-tag", "gen3randombattle", p1, p2)

    store.update_battle_tag(bid, "new-tag-123")

    row = store._conn.execute(
        "SELECT battle_tag FROM battles WHERE id=?", (bid,)
    ).fetchone()
    assert row["battle_tag"] == "new-tag-123"


def test_get_battle_teams_returns_none_none_for_nonexistent(store) -> None:
    """Line 740: get_battle_teams returns (None, None) for missing battle."""
    result = store.get_battle_teams(99999)
    assert result == (None, None)


def test_get_battle_teams_none_when_team_id_missing(store) -> None:
    """Line 749: _fetch_team returns None when team_id references nothing."""
    p1 = store.get_or_create_model("random", "a")
    p2 = store.get_or_create_model("random", "b")
    bid = store.create_battle("tag-bbt", "gen3randombattle", p1, p2)
    # Disable FK enforcement temporarily to allow a bogus team id reference
    store._conn.execute("PRAGMA foreign_keys=OFF")
    store._conn.execute("UPDATE battles SET p1_team_id=999 WHERE id=?", (bid,))
    store._conn.commit()
    store._conn.execute("PRAGMA foreign_keys=ON")

    p1_team, p2_team = store.get_battle_teams(bid)
    # p1_team_id=999 exists but no teams row → _fetch_team returns None
    assert p1_team is None
    # p2_team_id is NULL → also None
    assert p2_team is None


def test_get_battle_teams_bad_pokemon_json(store) -> None:
    """Lines 753-754: json.loads failure silently passes; dict still returned."""
    p1 = store.get_or_create_model("random", "a")
    p2 = store.get_or_create_model("random", "b")
    bid = store.create_battle("tag-bbtj", "gen3randombattle", p1, p2)

    # Insert a teams row with invalid pokemon JSON
    store._conn.execute(
        "INSERT INTO teams (id, model_id, tier, format, pokemon, team_string) "
        "VALUES (100, ?, 'ou', 'gen3ou', 'NOT_VALID_JSON', 'team-string')",
        (p1,),
    )
    # Point battles.p1_team_id at this team (id=100) — disable FK to allow it
    store._conn.execute("PRAGMA foreign_keys=OFF")
    store._conn.execute("UPDATE battles SET p1_team_id=100 WHERE id=?", (bid,))
    store._conn.commit()
    store._conn.execute("PRAGMA foreign_keys=ON")

    p1_team, _ = store.get_battle_teams(bid)
    # Team row found but pokemon field wasn't parsed — it's left as-is
    assert p1_team is not None
    assert p1_team["pokemon"] == "NOT_VALID_JSON"


def test_get_teams_for_model_bad_pokemon_json(store) -> None:
    """Lines 772-773: json.loads failure in get_teams_for_model silently passes."""
    mid = store.get_or_create_model("random", "x")

    # Insert a teams row with invalid pokemon JSON
    store._conn.execute(
        "INSERT INTO teams (model_id, tier, format, pokemon, team_string) "
        "VALUES (?, 'ou', 'gen3ou', 'BAD_JSON', 'team-string')",
        (mid,),
    )
    store._conn.commit()

    teams = store.get_teams_for_model(mid)
    assert len(teams) == 1
    # pokemon field kept as raw string since json.loads failed
    assert teams[0]["pokemon"] == "BAD_JSON"


# ------------------------------------------------------------------
# matchup_matrix
# ------------------------------------------------------------------

def _finish(store, tag, p1, p2, winner):
    """Helper: create and finish a battle, returning the battle id."""
    bid = store.create_battle(tag, "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=winner, total_turns=5)
    return bid


def test_matchup_matrix_empty_when_no_battles(store) -> None:
    """Returns an empty list when there are no completed battles."""
    assert store.matchup_matrix() == []


def test_matchup_matrix_symmetric_games(store) -> None:
    """Each ordered pair (A,B) and (B,A) is reported independently."""
    a = store.get_or_create_model("random", "model-a")
    b = store.get_or_create_model("random", "model-b")
    # Two battles: A wins one, B wins one
    _finish(store, "tag-mm1", a, b, winner=1)  # A(p1) wins
    _finish(store, "tag-mm2", b, a, winner=1)  # B(p1) wins → B wins as A's opponent

    matrix = store.matchup_matrix()
    # Build a lookup keyed by (row_model, col_model)
    lookup = {(r["row_model"], r["col_model"]): r for r in matrix}

    a_vs_b = lookup[("model-a", "model-b")]
    assert a_vs_b["wins"] == 1
    assert a_vs_b["losses"] == 1
    assert a_vs_b["ties"] == 0
    assert a_vs_b["games"] == 2

    b_vs_a = lookup[("model-b", "model-a")]
    assert b_vs_a["wins"] == 1
    assert b_vs_a["losses"] == 1
    assert b_vs_a["games"] == 2


def test_matchup_matrix_tie_counted(store) -> None:
    """Tied battles (winner=None) appear in ties column."""
    a = store.get_or_create_model("random", "alpha")
    b = store.get_or_create_model("random", "beta")
    _finish(store, "tag-tie", a, b, winner=None)

    lookup = {(r["row_model"], r["col_model"]): r for r in store.matchup_matrix()}
    assert lookup[("alpha", "beta")]["ties"] == 1
    assert lookup[("alpha", "beta")]["wins"] == 0


def test_matchup_matrix_excludes_unfinished(store) -> None:
    """Battles without finished_at are not counted."""
    a = store.get_or_create_model("random", "aa")
    b = store.get_or_create_model("random", "bb")
    store.create_battle("tag-unfinished", "gen3randombattle", a, b)
    # Do NOT call finish_battle — battle stays unfinished
    assert store.matchup_matrix() == []


def test_matchup_matrix_tier_filter(store) -> None:
    """tier= filter restricts results to that tier's battles."""
    a = store.get_or_create_model("random", "a-tier")
    b = store.get_or_create_model("random", "b-tier")

    # Create battles with explicit tier by patching the tier column
    bid_ou  = store.create_battle("tag-ou",  "gen3ou",           a, b)
    bid_rnd = store.create_battle("tag-rnd", "gen3randombattle", a, b)
    store._conn.execute("UPDATE battles SET tier='ou'     WHERE id=?", (bid_ou,))
    store._conn.execute("UPDATE battles SET tier='random' WHERE id=?", (bid_rnd,))
    store._conn.commit()
    store.finish_battle(bid_ou,  winner=1, total_turns=3)
    store.finish_battle(bid_rnd, winner=2, total_turns=3)

    ou_matrix  = store.matchup_matrix(tier="ou")
    all_matrix = store.matchup_matrix()

    ou_lookup  = {(r["row_model"], r["col_model"]): r for r in ou_matrix}
    all_lookup = {(r["row_model"], r["col_model"]): r for r in all_matrix}

    assert ou_lookup[("a-tier", "b-tier")]["games"] == 1
    assert all_lookup[("a-tier", "b-tier")]["games"] == 2


# ===========================================================================
# Seasons
# ===========================================================================

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_season(store, name="Season 1", tier="random", fmt="gen3randombattle",
                 participants=None, rounds=1, prompt_version="v4", total_battles=2):
    if participants is None:
        participants = [
            {"provider": "random", "model_name": "model-a"},
            {"provider": "random", "model_name": "model-b"},
        ]
    return store.create_season(
        name=name, tier=tier, fmt=fmt, participants=participants,
        rounds=rounds, prompt_version=prompt_version, total_battles=total_battles,
    )


def _season_battle(store, season_id, p1_model, p2_model, winner=1, tag=None):
    """Create and finish a battle tagged to a season."""
    p1_id = store.get_or_create_model("random", p1_model)
    p2_id = store.get_or_create_model("random", p2_model)
    tag = tag or f"s{season_id}-{p1_model}-{p2_model}"
    bid = store.create_battle(tag, "gen3randombattle", p1_id, p2_id, season_id=season_id)
    store.finish_battle(bid, winner=winner, total_turns=5)
    return bid


# ------------------------------------------------------------------
# create / get / list
# ------------------------------------------------------------------

def test_create_season_returns_int(store) -> None:
    sid = _make_season(store)
    assert isinstance(sid, int)


def test_get_season_returns_dict(store) -> None:
    sid = _make_season(store, name="Test Season")
    s = store.get_season(sid)
    assert s is not None
    assert s["name"] == "Test Season"
    assert s["status"] == "pending"


def test_get_season_parses_participants_as_list(store) -> None:
    participants = [
        {"provider": "random", "model_name": "alpha"},
        {"provider": "random", "model_name": "beta"},
    ]
    sid = _make_season(store, participants=participants)
    s = store.get_season(sid)
    assert isinstance(s["participants"], list)
    assert len(s["participants"]) == 2
    assert s["participants"][0]["model_name"] == "alpha"


def test_get_season_returns_none_for_missing(store) -> None:
    assert store.get_season(9999) is None


def test_list_seasons_empty(store) -> None:
    assert store.list_seasons() == []


def test_list_seasons_returns_rows(store) -> None:
    _make_season(store, name="S1")
    _make_season(store, name="S2")
    rows = store.list_seasons()
    assert len(rows) == 2


def test_list_seasons_newest_first(store) -> None:
    _make_season(store, name="Old")
    _make_season(store, name="New")
    rows = store.list_seasons()
    assert rows[0]["name"] == "New"


def test_list_seasons_battles_done_count(store) -> None:
    sid = _make_season(store)
    _season_battle(store, sid, "model-a", "model-b", winner=1, tag="s-tag1")
    rows = store.list_seasons()
    assert rows[0]["battles_done"] == 1


# ------------------------------------------------------------------
# Status transitions
# ------------------------------------------------------------------

def test_set_season_running_updates_status(store) -> None:
    sid = _make_season(store)
    store.set_season_running(sid)
    s = store.get_season(sid)
    assert s["status"] == "running"
    assert s["started_at"] is not None


def test_finish_season_completed(store) -> None:
    sid = _make_season(store)
    store.finish_season(sid, status="completed")
    s = store.get_season(sid)
    assert s["status"] == "completed"
    assert s["finished_at"] is not None


def test_cancel_season_returns_true(store) -> None:
    sid = _make_season(store)
    store.set_season_running(sid)
    assert store.cancel_season(sid) is True
    assert store.get_season(sid)["status"] == "cancelled"


def test_cancel_season_already_completed_returns_false(store) -> None:
    sid = _make_season(store)
    store.finish_season(sid, status="completed")
    assert store.cancel_season(sid) is False


def test_cancel_season_nonexistent_returns_false(store) -> None:
    assert store.cancel_season(9999) is False


# ------------------------------------------------------------------
# create_battle with season_id
# ------------------------------------------------------------------

def test_create_battle_with_season_id(store) -> None:
    sid = _make_season(store)
    p1 = store.get_or_create_model("random", "r1")
    p2 = store.get_or_create_model("random", "r2")
    bid = store.create_battle("s-tag", "gen3randombattle", p1, p2, season_id=sid)
    row = store._conn.execute("SELECT season_id FROM battles WHERE id=?", (bid,)).fetchone()
    assert row["season_id"] == sid


# ------------------------------------------------------------------
# get_season_battles
# ------------------------------------------------------------------

def test_get_season_battles_empty(store) -> None:
    sid = _make_season(store)
    assert store.get_season_battles(sid) == []


def test_get_season_battles_returns_tagged_battles(store) -> None:
    sid = _make_season(store)
    _season_battle(store, sid, "model-a", "model-b", tag="sb1")
    _season_battle(store, sid, "model-b", "model-a", tag="sb2")
    battles = store.get_season_battles(sid)
    assert len(battles) == 2


def test_get_season_battles_excludes_other_seasons(store) -> None:
    sid1 = _make_season(store, name="S1")
    sid2 = _make_season(store, name="S2")
    _season_battle(store, sid1, "model-a", "model-b", tag="s1b1")
    _season_battle(store, sid2, "model-a", "model-b", tag="s2b1")
    assert len(store.get_season_battles(sid1)) == 1
    assert len(store.get_season_battles(sid2)) == 1


# ------------------------------------------------------------------
# get_season_standings — ELO replay
# ------------------------------------------------------------------

def test_get_season_standings_empty_no_battles(store) -> None:
    """With no battles the standings return 1000.0 ELO for each participant."""
    sid = _make_season(store, participants=[
        {"provider": "random", "model_name": "alpha"},
        {"provider": "random", "model_name": "beta"},
    ])
    standings = store.get_season_standings(sid)
    assert len(standings) == 2
    for s in standings:
        assert s["elo"] == pytest.approx(DEFAULT_RATING, abs=0.1)
        assert s["wins"] == 0
        assert s["losses"] == 0
        assert s["ties"] == 0
        assert s["games"] == 0


def test_get_season_standings_winner_gains_elo(store) -> None:
    """The battle winner's season ELO exceeds 1000; loser's falls below 1000."""
    sid = _make_season(store, participants=[
        {"provider": "random", "model_name": "winner"},
        {"provider": "random", "model_name": "loser"},
    ])
    _season_battle(store, sid, "winner", "loser", winner=1, tag="s-w-l")
    standings = store.get_season_standings(sid)
    by_name = {s["model_name"]: s for s in standings}
    assert by_name["winner"]["elo"] > DEFAULT_RATING
    assert by_name["loser"]["elo"] < DEFAULT_RATING
    assert by_name["winner"]["wins"] == 1
    assert by_name["loser"]["losses"] == 1


def test_get_season_standings_tie_both_near_1000(store) -> None:
    """A tied battle nudges both players toward 1000 (expected score ≈ 0.5 each)."""
    sid = _make_season(store, participants=[
        {"provider": "random", "model_name": "p1"},
        {"provider": "random", "model_name": "p2"},
    ])
    _season_battle(store, sid, "p1", "p2", winner=None, tag="s-tie")
    standings = store.get_season_standings(sid)
    by_name = {s["model_name"]: s for s in standings}
    # Starting at 1000 vs 1000 means expected score is 0.5; tie gives 0.5 → no change
    assert by_name["p1"]["elo"] == pytest.approx(DEFAULT_RATING, abs=1.0)
    assert by_name["p2"]["elo"] == pytest.approx(DEFAULT_RATING, abs=1.0)
    assert by_name["p1"]["ties"] == 1
    assert by_name["p2"]["ties"] == 1


def test_get_season_standings_sorted_by_elo_descending(store) -> None:
    """Standings are sorted highest ELO first; rank=1 for top entry."""
    sid = _make_season(store, participants=[
        {"provider": "random", "model_name": "champ"},
        {"provider": "random", "model_name": "scrub"},
    ])
    _season_battle(store, sid, "champ", "scrub", winner=1, tag="s-rank")
    standings = store.get_season_standings(sid)
    assert standings[0]["model_name"] == "champ"
    assert standings[0]["rank"] == 1
    assert standings[1]["rank"] == 2


def test_get_season_standings_elo_isolated_from_all_time(store) -> None:
    """Season standings do not use the all-time ELO table — completely independent."""
    sid = _make_season(store, participants=[
        {"provider": "random", "model_name": "iso-a"},
        {"provider": "random", "model_name": "iso-b"},
    ])
    # Run 5 wins for iso-a to inflate all-time ELO significantly
    for i in range(5):
        _finish(store, f"alltime-{i}", *[
            store.get_or_create_model("random", "iso-a"),
            store.get_or_create_model("random", "iso-b"),
        ], winner=1)
    # Season has no battles yet
    standings = store.get_season_standings(sid)
    by_name = {s["model_name"]: s for s in standings}
    # Both must start at 1000 in the season regardless of all-time ELO
    assert by_name["iso-a"]["elo"] == pytest.approx(DEFAULT_RATING, abs=0.1)


def test_get_season_standings_returns_empty_for_missing_season(store) -> None:
    assert store.get_season_standings(9999) == []


def test_get_season_standings_multi_battle_elo_progression(store) -> None:
    """Season ratings move correctly across multiple sequential battles."""
    from nidozo.db.elo import Rating, updated_glicko

    sid = _make_season(store, participants=[
        {"provider": "random", "model_name": "aa"},
        {"provider": "random", "model_name": "bb"},
    ])
    # aa wins twice
    _season_battle(store, sid, "aa", "bb", winner=1, tag="multi-1")
    _season_battle(store, sid, "aa", "bb", winner=1, tag="multi-2")

    standings = store.get_season_standings(sid)
    by_name = {s["model_name"]: s for s in standings}

    # Manually replay the same Glicko-2 sequence to verify
    r_aa, r_bb = Rating(), Rating()
    for _ in range(2):
        r_aa, r_bb = updated_glicko(r_aa, r_bb, winner=1)

    assert by_name["aa"]["elo"] == pytest.approx(r_aa.rating, abs=0.1)
    assert by_name["bb"]["elo"] == pytest.approx(r_bb.rating, abs=0.1)
    assert by_name["aa"]["wins"] == 2
    assert by_name["bb"]["losses"] == 2
    # Two games is not enough to settle a rating — both stay provisional, and
    # the winner's RD has narrowed from the unplayed prior.
    assert by_name["aa"]["provisional"] is True
    assert by_name["aa"]["rd"] < DEFAULT_RD


# ------------------------------------------------------------------
# Battle narrative
# ------------------------------------------------------------------

def test_set_battle_narrative_stores_and_retrieves(store) -> None:
    """set_battle_narrative persists narrative; get_battle returns it."""
    p1 = store.get_or_create_model("anthropic", "claude-test", "v5")
    p2 = store.get_or_create_model("random", "random", "v1")
    bid = store.create_battle("tag-narr-1", "gen3randombattle", p1, p2)

    store.set_battle_narrative(bid, "P1 swept P2 in 12 turns.")
    row = store.get_battle(bid)
    assert row is not None
    assert row["narrative"] == "P1 swept P2 in 12 turns."


def test_set_battle_narrative_overwrites_existing(store) -> None:
    """Calling set_battle_narrative twice updates the stored value."""
    p1 = store.get_or_create_model("openai", "gpt-4o", "v5")
    p2 = store.get_or_create_model("random", "random", "v1")
    bid = store.create_battle("tag-narr-2", "gen3randombattle", p1, p2)

    store.set_battle_narrative(bid, "First version.")
    store.set_battle_narrative(bid, "Updated version.")
    row = store.get_battle(bid)
    assert row is not None
    assert row["narrative"] == "Updated version."


def test_battle_narrative_defaults_to_none(store) -> None:
    """A newly created battle has narrative=None until set."""
    p1 = store.get_or_create_model("anthropic", "claude-test", "v5")
    p2 = store.get_or_create_model("random", "random", "v1")
    bid = store.create_battle("tag-narr-3", "gen3randombattle", p1, p2)
    row = store.get_battle(bid)
    assert row is not None
    assert row["narrative"] is None


# ------------------------------------------------------------------
# Usage stats
# ------------------------------------------------------------------

import json as _json  # noqa: E402 — only for test helpers below


def _seed_usage_turns(store: BattleStore) -> int:
    """Create a finished battle with several turns carrying state_json + llm_response."""
    p1 = store.get_or_create_model("lmstudio", "qwen3-4b", "v5")
    p2 = store.get_or_create_model("random", "random", "v1")
    bid = store.create_battle("usage-tag-1", "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=6)

    turns = [
        ("p1", {"my_active": {"species": "Charizard"}},
         {"action_type": "move", "identifier": "flamethrower"}),
        ("p1", {"my_active": {"species": "Charizard"}},
         {"action_type": "move", "identifier": "flamethrower"}),
        ("p1", {"my_active": {"species": "Blastoise"}},
         {"action_type": "switch", "identifier": "Blastoise"}),
        ("p1", {"my_active": {"species": "Blastoise"}},
         {"action_type": "move", "identifier": "surf"}),
        ("p2", {"my_active": {"species": "Gengar"}},
         {"action_type": "move", "identifier": "shadowball"}),
        ("p2", {"my_active": {"species": "Gengar"}},
         {"action_type": "move", "identifier": "shadowball"}),
    ]
    for i, (role, state, resp) in enumerate(turns, start=1):
        store.log_turn(
            battle_id=bid,
            turn_number=i,
            player_role=role,
            prompt_version="v5",
            action_chosen=f"{resp['action_type']} {resp['identifier']}",
            parse_success=True,
            llm_response=_json.dumps(resp),
            state_json=_json.dumps(state),
        )
    return p1


def test_model_usage_stats_top_pokemon(store) -> None:
    """Top Pokémon for a model are derived from state_json my_active species."""
    p1 = _seed_usage_turns(store)
    usage = store.get_model_usage_stats(p1)

    species = [r["species"] for r in usage["top_pokemon"]]
    assert "Charizard" in species
    assert "Blastoise" in species
    # p2's Gengar should NOT appear (different model)
    assert "Gengar" not in species


def test_model_usage_stats_top_moves(store) -> None:
    """Top moves come from llm_response action_type=move rows only."""
    p1 = _seed_usage_turns(store)
    usage = store.get_model_usage_stats(p1)

    moves = {r["move"]: r["cnt"] for r in usage["top_moves"]}
    assert "flamethrower" in moves
    assert moves["flamethrower"] == 2
    assert "surf" in moves
    # switch identifier must NOT appear in move list
    assert "Blastoise" not in moves


def test_model_usage_stats_action_distribution(store) -> None:
    """Action distribution counts move and switch turns separately."""
    p1 = _seed_usage_turns(store)
    usage = store.get_model_usage_stats(p1)

    dist = {r["action_type"]: r["cnt"] for r in usage["action_distribution"]}
    assert dist.get("move", 0) == 3   # flamethrower x2 + surf
    assert dist.get("switch", 0) == 1


def test_model_usage_stats_win_rate_by_tier(store) -> None:
    """Win rate by tier returns a row for each tier the model has played."""
    p1 = _seed_usage_turns(store)
    usage = store.get_model_usage_stats(p1)

    tiers = {r["tier"]: r for r in usage["win_rate_by_tier"]}
    assert "random" in tiers
    row = tiers["random"]
    assert row["total"] == 1
    assert row["wins"] == 1


def test_get_model_stats_includes_usage(store) -> None:
    """get_model_stats folds usage stats into the response."""
    p1 = _seed_usage_turns(store)
    stats = store.get_model_stats(p1)
    assert stats is not None
    assert "usage" in stats
    assert "top_pokemon" in stats["usage"]
    assert "top_moves" in stats["usage"]
    assert "action_distribution" in stats["usage"]
    assert "win_rate_by_tier" in stats["usage"]


# ------------------------------------------------------------------
# Global stats
# ------------------------------------------------------------------

def test_get_global_stats_empty_db(store) -> None:
    """Global stats on a fresh DB returns zeros, not errors."""
    gs = store.get_global_stats()
    assert gs["summary"]["total_battles"] == 0
    assert gs["summary"]["total_models"] == 0
    assert gs["top_pokemon"] == []
    assert gs["top_moves"] == []
    assert gs["battles_by_tier"] == []
    assert gs["recent_battles"] == []


def test_get_global_stats_counts_battles(store) -> None:
    """Global stats total_battles counts finished battles correctly."""
    _seed_usage_turns(store)
    gs = store.get_global_stats()
    assert gs["summary"]["total_battles"] == 1
    assert gs["summary"]["total_models"] >= 2


def test_get_global_stats_top_pokemon(store) -> None:
    """Top Pokémon aggregates across all models."""
    _seed_usage_turns(store)
    gs = store.get_global_stats()

    # Gengar appears in p2 turns too (no model filter for global)
    species_names = [r["species"] for r in gs["top_pokemon"]]
    assert "Charizard" in species_names
    assert "Gengar" in species_names


def test_get_global_stats_top_moves(store) -> None:
    """Top moves across all turns, all models."""
    _seed_usage_turns(store)
    gs = store.get_global_stats()

    moves = {r["move"]: r["cnt"] for r in gs["top_moves"]}
    # shadowball appears in p2 turns; flamethrower in p1 — both should be present globally
    assert "flamethrower" in moves
    assert "shadowball" in moves


def _seed_malformed_llm_response(store: BattleStore) -> tuple[int, int]:
    """Seed a battle with one well-formed and one malformed llm_response row."""
    p1 = store.get_or_create_model("lmstudio", "qwen3-bad", "v5")
    p2 = store.get_or_create_model("random", "random", "v1")
    bid = store.create_battle("bad-resp-tag", "gen3randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=2)
    # Good row
    store.log_turn(bid, 1, "p1", "v5", "move flamethrower", True,
                   _json.dumps({"action_type": "move", "identifier": "flamethrower"}),
                   _json.dumps({"my_active": {"species": "Charizard"}}))
    # Malformed row: raw text stored on parse failure (not valid JSON)
    store.log_turn(bid, 2, "p1", "v5", None, False,
                   "I choose Thunderbolt because it has high damage",
                   None)
    # Empty-string row: stored when retries exhausted
    store.log_turn(bid, 3, "p1", "v5", None, False, "", None)
    return p1, bid


def test_model_usage_stats_tolerates_malformed_llm_response(store) -> None:
    """get_model_usage_stats does not 500 when llm_response contains non-JSON text."""
    p1, _ = _seed_malformed_llm_response(store)
    usage = store.get_model_usage_stats(p1)
    # Only the well-formed row should count
    moves = {r["move"]: r["cnt"] for r in usage["top_moves"]}
    assert moves.get("flamethrower") == 1
    dist = {r["action_type"]: r["cnt"] for r in usage["action_distribution"]}
    # malformed rows excluded — only the one valid JSON move row counts
    assert dist.get("move", 0) == 1


def test_get_global_stats_tolerates_malformed_llm_response(store) -> None:
    """get_global_stats does not 500 when llm_response contains non-JSON text."""
    _seed_malformed_llm_response(store)
    gs = store.get_global_stats()
    moves = {r["move"]: r["cnt"] for r in gs["top_moves"]}
    assert moves.get("flamethrower") == 1


# ------------------------------------------------------------------
# Startup stale-record cleanup
# ------------------------------------------------------------------

_stale_seq = 0

def _make_battle(store: BattleStore, status: str) -> int:
    global _stale_seq
    _stale_seq += 1
    m1 = store.get_or_create_model("random", "r1")
    m2 = store.get_or_create_model("random", "r2")
    bid = store.create_battle(f"battle-gen3-{_stale_seq}", "gen3randombattle", m1, m2)
    store.set_battle_status(bid, status)
    return bid


def test_abort_stale_records_marks_running_battles_failed(store) -> None:
    bid = _make_battle(store, "running")
    store.abort_stale_records()
    row = store._conn.execute("SELECT status FROM battles WHERE id=?", (bid,)).fetchone()
    assert row["status"] == "failed"


def test_abort_stale_records_marks_pending_battles_failed(store) -> None:
    bid = _make_battle(store, "pending")
    store.abort_stale_records()
    row = store._conn.execute("SELECT status FROM battles WHERE id=?", (bid,)).fetchone()
    assert row["status"] == "failed"


def test_abort_stale_records_does_not_touch_completed_battles(store) -> None:
    bid = _make_battle(store, "completed")
    store.abort_stale_records()
    row = store._conn.execute("SELECT status FROM battles WHERE id=?", (bid,)).fetchone()
    assert row["status"] == "completed"


def test_abort_stale_records_returns_counts(store) -> None:
    _make_battle(store, "running")
    _make_battle(store, "pending")
    _make_battle(store, "completed")
    result = store.abort_stale_records()
    assert result["battles"] == 2
    assert result["tournaments"] == 0
    assert result["seasons"] == 0


def test_abort_stale_records_marks_running_tournament_cancelled(store) -> None:
    m1 = store.get_or_create_model("random", "r1")
    m2 = store.get_or_create_model("random", "r2")
    tid = store.create_tournament([m1, m2], rounds=1, prompt_version="v5", total_battles=1, tier="random")
    # Manually set to running (normally done by orchestration)
    store._conn.execute("UPDATE tournaments SET status='running' WHERE id=?", (tid,))
    store._conn.commit()
    store.abort_stale_records()
    row = store._conn.execute("SELECT status FROM tournaments WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "cancelled"


def test_abort_stale_records_noop_on_clean_db(store) -> None:
    result = store.abort_stale_records()
    assert result == {"battles": 0, "tournaments": 0, "seasons": 0}


# ---------------------------------------------------------------------------
# Regression: cancelled battles must not corrupt W/L/T counts (issue #129)
# ---------------------------------------------------------------------------

def _setup_cancelled_battle(store):
    """Helper: one finished battle (p1 wins) and one cancelled battle (no winner).

    Returns (p1_id, p2_id, finished_bid, cancelled_bid).
    """
    p1 = store.get_or_create_model("random", "p1-model", "v5")
    p2 = store.get_or_create_model("random", "p2-model", "v5")
    finished_bid = store.create_battle("tag-finished", "gen9randombattle", p1, p2)
    store.finish_battle(finished_bid, winner=1, total_turns=20)
    cancelled_bid = store.create_battle("tag-cancelled", "gen9randombattle", p1, p2)
    store.cancel_battle(cancelled_bid)
    return p1, p2, finished_bid, cancelled_bid


def test_cancelled_battle_excluded_from_leaderboard(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    rows = store.leaderboard(grouped=True)
    p1_row = next(r for r in rows if r["model_name"] == "p1-model")
    p2_row = next(r for r in rows if r["model_name"] == "p2-model")
    # p1 should show exactly 1 win, 0 ties; p2 should show 0 ties
    assert p1_row["wins"] == 1
    assert p1_row["ties"] == 0
    assert p2_row["losses"] == 1
    assert p2_row["ties"] == 0


def test_cancelled_battle_excluded_from_leaderboard_per_version(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    rows = store.leaderboard(grouped=False)
    p1_row = next(r for r in rows if r["model_name"] == "p1-model")
    assert p1_row["wins"] == 1
    assert p1_row["ties"] == 0


def test_cancelled_battle_excluded_from_matchup_matrix(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    matrix = store.matchup_matrix()
    # Find the p1→p2 cell
    cell = next(
        (r for r in matrix if r["row_model"] == "p1-model" and r["col_model"] == "p2-model"),
        None,
    )
    assert cell is not None
    assert cell["games"] == 1   # only the completed battle
    assert cell["wins"] == 1
    assert cell["ties"] == 0


def test_cancelled_battle_excluded_from_model_stats_wlt(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    stats = store.get_model_stats(p1)
    assert stats is not None
    assert stats["model"]["wins"] == 1
    assert stats["model"]["ties"] == 0


def test_cancelled_battle_excluded_from_model_battle_history(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    stats = store.get_model_stats(p1)
    assert stats is not None
    assert len(stats["battle_history"]) == 1
    assert stats["battle_history"][0]["result"] == "win"


def test_cancelled_battle_excluded_from_win_rate_by_tier(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    usage = store.get_model_usage_stats(p1)
    assert len(usage["win_rate_by_tier"]) == 1
    row = usage["win_rate_by_tier"][0]
    assert row["total"] == 1   # only the completed battle
    assert row["wins"] == 1


def test_cancelled_battle_excluded_from_season_standings(store) -> None:
    p1, p2, _fb, _cb = _setup_cancelled_battle(store)
    # Create a season and wire both battles to it
    sid = store.create_season(
        name="test-season",
        tier="random",
        fmt="gen9randombattle",
        participants=[
            {"provider": "random", "model_name": "p1-model"},
            {"provider": "random", "model_name": "p2-model"},
        ],
        rounds=1,
        prompt_version="v5",
        total_battles=2,
    )
    store._conn.execute("UPDATE battles SET season_id=? WHERE id=?", (sid, _fb))
    store._conn.execute("UPDATE battles SET season_id=? WHERE id=?", (sid, _cb))
    store._conn.commit()

    standings = store.get_season_standings(sid)
    p1_row = next(s for s in standings if s["model_name"] == "p1-model")
    p2_row = next(s for s in standings if s["model_name"] == "p2-model")
    assert p1_row["wins"] == 1
    assert p1_row["ties"] == 0
    assert p2_row["ties"] == 0
    # ELO should reflect only the one real result, not a phantom draw
    assert p1_row["elo"] > 1000
    assert p2_row["elo"] < 1000


def test_cancelled_battle_excluded_from_global_stats_summary(store) -> None:
    _setup_cancelled_battle(store)
    gs = store.get_global_stats()
    # Only the 1 completed battle counts
    assert gs["summary"]["total_battles"] == 1


# ------------------------------------------------------------------
# Glicko-2 persistence (#231)
# ------------------------------------------------------------------

def test_new_model_seeded_with_glicko_priors(store) -> None:
    """A model that has never played carries the wide unplayed prior."""
    mid = store.get_or_create_model("random", "glicko-newcomer")
    row = store._conn.execute(
        "SELECT rating, rd, volatility FROM elo_ratings WHERE model_id=?", (mid,)
    ).fetchone()

    assert row["rating"] == DEFAULT_RATING
    assert row["rd"] == DEFAULT_RD
    assert row["volatility"] == pytest.approx(0.06)


def test_finish_battle_persists_rd_and_volatility(store) -> None:
    """Playing a battle narrows RD for both players and records volatility."""
    p1 = store.get_or_create_model("random", "g2a")
    p2 = store.get_or_create_model("random", "g2b")
    bid = store.create_battle("glicko-1", "gen9randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=10)

    for mid in (p1, p2):
        row = store._conn.execute(
            "SELECT rd, volatility, games FROM elo_ratings WHERE model_id=?", (mid,)
        ).fetchone()
        assert row["rd"] < DEFAULT_RD, "playing should reduce uncertainty"
        assert row["rd"] > 0
        assert row["volatility"] > 0
        assert row["games"] == 1


def test_elo_history_records_rd_transition(store) -> None:
    p1 = store.get_or_create_model("random", "g2c")
    p2 = store.get_or_create_model("random", "g2d")
    bid = store.create_battle("glicko-2", "gen9randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=10)

    row = store._conn.execute(
        "SELECT rd_before, rd_after FROM elo_history WHERE battle_id=? AND model_id=?",
        (bid, p1),
    ).fetchone()

    assert row["rd_before"] == DEFAULT_RD
    assert row["rd_after"] < row["rd_before"]


def test_rd_keeps_narrowing_across_many_battles(store) -> None:
    """A model with a real track record leaves provisional status behind."""
    p1 = store.get_or_create_model("random", "g2veteran")
    p2 = store.get_or_create_model("random", "g2sparring")
    for i in range(25):
        bid = store.create_battle(f"glicko-series-{i}", "gen9randombattle", p1, p2)
        store.finish_battle(bid, winner=1 if i % 2 == 0 else 2, total_turns=10)

    rd = store._conn.execute(
        "SELECT rd FROM elo_ratings WHERE model_id=?", (p1,)
    ).fetchone()["rd"]
    assert rd < PROVISIONAL_RD


def test_leaderboard_exposes_uncertainty_fields(store) -> None:
    p1 = store.get_or_create_model("random", "g2lb1")
    p2 = store.get_or_create_model("random", "g2lb2")
    bid = store.create_battle("glicko-lb", "gen9randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=10)

    for grouped in (True, False):
        rows = store.leaderboard(grouped=grouped)
        assert rows, f"expected leaderboard rows (grouped={grouped})"
        for row in rows:
            assert "rd" in row
            assert "provisional" in row
            assert row["rating_low"] == pytest.approx(row["rating"] - 2 * row["rd"], abs=0.2)
            assert row["rating_high"] == pytest.approx(row["rating"] + 2 * row["rd"], abs=0.2)
            assert row["provisional"] is (row["rd"] >= PROVISIONAL_RD)


def test_model_stats_exposes_uncertainty(store) -> None:
    p1 = store.get_or_create_model("random", "g2stats1")
    p2 = store.get_or_create_model("random", "g2stats2")
    bid = store.create_battle("glicko-stats", "gen9randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=10)

    stats = store.get_model_stats(p1)
    assert stats is not None
    assert stats["model"]["rd"] < DEFAULT_RD
    assert stats["model"]["provisional"] is True


def test_glicko_update_is_idempotent_on_replayed_finish(store) -> None:
    """A second finish_battle must not re-apply the rating change."""
    p1 = store.get_or_create_model("random", "g2idem1")
    p2 = store.get_or_create_model("random", "g2idem2")
    bid = store.create_battle("glicko-idem", "gen9randombattle", p1, p2)
    store.finish_battle(bid, winner=1, total_turns=10)

    after_first = store._conn.execute(
        "SELECT rating, rd, volatility, games FROM elo_ratings WHERE model_id=?", (p1,)
    ).fetchone()
    store.finish_battle(bid, winner=1, total_turns=10)
    after_second = store._conn.execute(
        "SELECT rating, rd, volatility, games FROM elo_ratings WHERE model_id=?", (p1,)
    ).fetchone()

    assert dict(after_first) == dict(after_second)
