"""Tests for badge evaluation (achievements.py)."""

from __future__ import annotations

import json
import sqlite3

from nidozo.battle.achievements import evaluate_badges
from nidozo.db.store import BattleStore


def _completed_battle(
    conn: sqlite3.Connection,
    p1: int,
    p2: int,
    winner: int | None,
    tag: str,
    finished_at: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO battles
               (battle_tag, format, p1_model_id, p2_model_id, winner, status, finished_at)
           VALUES (?,?,?,?,?, 'completed', ?)""",
        (tag, "gen9randombattle", p1, p2, winner, finished_at),
    )
    conn.commit()
    bid = cur.lastrowid
    assert bid is not None
    return bid


def _ts(i: int) -> str:
    return f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"


def test_first_blood_on_first_win(tmp_path) -> None:
    store = BattleStore(tmp_path / "a.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        bid = _completed_battle(conn, p1, p2, winner=1, tag="b1", finished_at=_ts(1))
        earned = evaluate_badges(conn, bid, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "first_blood" in earned
    finally:
        store.close()


def test_win_streak_three_and_five(tmp_path) -> None:
    store = BattleStore(tmp_path / "b.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        last = 0
        for i in range(3):
            last = _completed_battle(conn, p1, p2, 1, f"s3-{i}", _ts(i + 1))
        earned3 = evaluate_badges(conn, last, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "win_streak_3" in earned3
        assert "win_streak_5" not in earned3

        for i in range(2):
            last = _completed_battle(conn, p1, p2, 1, f"s5-{i}", _ts(i + 10))
        earned5 = evaluate_badges(conn, last, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "win_streak_5" in earned5
    finally:
        store.close()


def test_dominant_after_ten_wins(tmp_path) -> None:
    store = BattleStore(tmp_path / "c.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        last = 0
        for i in range(10):
            last = _completed_battle(conn, p1, p2, 1, f"d-{i}", _ts(i + 1))
        earned = evaluate_badges(conn, last, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "dominant" in earned
    finally:
        store.close()


def _seed_last_state(conn: sqlite3.Connection, bid: int, role: str, state: dict) -> None:
    conn.execute(
        """INSERT INTO turns (battle_id, turn_number, player_role, prompt_version, state_json)
           VALUES (?,?,?,?,?)""",
        (bid, 1, role, "v9", json.dumps(state)),
    )
    conn.commit()


def test_perfect_game_when_no_mon_fainted(tmp_path) -> None:
    store = BattleStore(tmp_path / "d.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        bid = _completed_battle(conn, p1, p2, 1, "pg", _ts(1))
        _seed_last_state(conn, bid, "p1", {
            "my_active": {"hp_fraction": 1.0},
            "my_bench": [{"hp_fraction": 0.5}, {"hp_fraction": 1.0}],
        })
        earned = evaluate_badges(conn, bid, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "perfect_game" in earned
    finally:
        store.close()


def test_no_perfect_game_when_a_bench_mon_fainted(tmp_path) -> None:
    store = BattleStore(tmp_path / "e.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        bid = _completed_battle(conn, p1, p2, 1, "npg", _ts(1))
        _seed_last_state(conn, bid, "p1", {
            "my_active": {"hp_fraction": 1.0},
            "my_bench": [{"hp_fraction": 0.0}],  # one fainted
        })
        earned = evaluate_badges(conn, bid, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "perfect_game" not in earned
    finally:
        store.close()


def test_upset_when_winner_elo_far_below_opponent(tmp_path) -> None:
    store = BattleStore(tmp_path / "f.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        bid = _completed_battle(conn, p1, p2, 1, "up", _ts(1))
        # Winner (p1) entered 150 ELO below the opponent.
        for mid, before in ((p1, 1000.0), (p2, 1150.0)):
            conn.execute(
                """INSERT INTO elo_history (battle_id, model_id, rating_before, rating_after, delta)
                   VALUES (?,?,?,?,0.0)""",
                (bid, mid, before, before),
            )
        conn.commit()
        earned = evaluate_badges(conn, bid, p1, winner_model_id=p1, opponent_model_id=p2)
        assert "upset" in earned
    finally:
        store.close()


def test_centurion_on_hundredth_game(tmp_path) -> None:
    store = BattleStore(tmp_path / "g.db")
    try:
        conn = store._conn
        p1 = store.get_or_create_model("anthropic", "x", "v9")
        p2 = store.get_or_create_model("openai", "y", "v9")
        last = 0
        for i in range(100):
            last = _completed_battle(conn, p1, p2, None, f"c-{i}", _ts(i + 1))
        # Tie (winner None) → only the centurion (games-based) branch fires.
        earned = evaluate_badges(conn, last, p1, winner_model_id=None, opponent_model_id=p2)
        assert "centurion" in earned
    finally:
        store.close()
