"""Badge catalog and evaluation logic.

Each badge is evaluated synchronously inside finish_battle() (already in a DB
transaction).  Evaluators receive the battle row and a connection so they can
query turn / ELO data without an extra round-trip.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Badge:
    slug: str
    name: str
    description: str
    emoji: str


BADGES: dict[str, Badge] = {
    b.slug: b
    for b in [
        Badge("first_blood",  "First Blood",   "Win your first battle.",                          "🩸"),
        Badge("win_streak_3", "On Fire",        "Win 3 battles in a row.",                        "🔥"),
        Badge("win_streak_5", "Lightning Rod",  "Win 5 battles in a row.",                        "⚡"),
        Badge("perfect_game", "Perfect Game",   "Win a battle without losing a single Pokémon.",  "💎"),
        Badge("upset",        "Underdog",        "Beat an opponent with ≥100 more ELO.",           "🐉"),
        Badge("dominant",     "Dominant",        "Accumulate 10 victories.",                       "👑"),
        Badge("centurion",    "Centurion",       "Play 100 battles.",                              "🎖️"),
    ]
}


def _current_win_streak(conn: sqlite3.Connection, model_id: int) -> int:
    """Count consecutive wins in recent battles (newest first), stopping on a non-win."""
    rows = conn.execute(
        """SELECT
               CASE WHEN p1_model_id=:mid AND winner=1 THEN 1
                    WHEN p2_model_id=:mid AND winner=2 THEN 1
                    ELSE 0 END AS won
           FROM battles
           WHERE (p1_model_id=:mid OR p2_model_id=:mid)
             AND status='completed'
           ORDER BY finished_at DESC
           LIMIT 10""",
        {"mid": model_id},
    ).fetchall()
    streak = 0
    for r in rows:
        if r["won"]:
            streak += 1
        else:
            break
    return streak


def _total_wins(conn: sqlite3.Connection, model_id: int) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM battles
           WHERE (p1_model_id=:mid AND winner=1)
              OR (p2_model_id=:mid AND winner=2)""",
        {"mid": model_id},
    ).fetchone()
    return row["c"] if row else 0


def _total_games(conn: sqlite3.Connection, model_id: int) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM battles
           WHERE (p1_model_id=:mid OR p2_model_id=:mid) AND status='completed'""",
        {"mid": model_id},
    ).fetchone()
    return row["c"] if row else 0


def _elo_before(conn: sqlite3.Connection, battle_id: int, model_id: int) -> float | None:
    row = conn.execute(
        "SELECT rating_before FROM elo_history WHERE battle_id=? AND model_id=?",
        (battle_id, model_id),
    ).fetchone()
    return row["rating_before"] if row else None


def _perfect_game(conn: sqlite3.Connection, battle_id: int, winner_role: str) -> bool:
    """True if the winner had no Pokémon faint: check all HP in their last state_json."""
    row = conn.execute(
        """SELECT state_json FROM turns
           WHERE battle_id=? AND player_role=? AND state_json IS NOT NULL
           ORDER BY turn_number DESC LIMIT 1""",
        (battle_id, winner_role),
    ).fetchone()
    if not row or not row["state_json"]:
        return False
    try:
        state = json.loads(row["state_json"])
    except (ValueError, TypeError):
        return False
    active_hp = (state.get("my_active") or {}).get("hp_fraction", 0)
    if active_hp <= 0:
        return False
    for bench in state.get("my_bench") or []:
        if bench.get("hp_fraction", 0) <= 0:
            return False
    return True


def evaluate_badges(
    conn: sqlite3.Connection,
    battle_id: int,
    model_id: int,
    winner_model_id: int | None,
    opponent_model_id: int,
) -> list[str]:
    """Return slugs of badges newly earned by *model_id* in this battle."""
    earned: list[str] = []
    did_win = model_id == winner_model_id

    if did_win:
        wins = _total_wins(conn, model_id)
        if wins == 1:
            earned.append("first_blood")
        if wins >= 10:
            earned.append("dominant")

        streak = _current_win_streak(conn, model_id)
        if streak >= 5:
            earned.append("win_streak_5")
        elif streak >= 3:
            earned.append("win_streak_3")

        # Determine winner role (p1 or p2)
        row = conn.execute(
            "SELECT p1_model_id FROM battles WHERE id=?", (battle_id,)
        ).fetchone()
        winner_role = "p1" if row and row["p1_model_id"] == model_id else "p2"
        if _perfect_game(conn, battle_id, winner_role):
            earned.append("perfect_game")

        # Upset: winner's ELO before < opponent's ELO before - 100
        w_elo = _elo_before(conn, battle_id, model_id)
        l_elo = _elo_before(conn, battle_id, opponent_model_id)
        if w_elo is not None and l_elo is not None and (l_elo - w_elo) >= 100:
            earned.append("upset")

    # Centurion awarded to anyone on their 100th game (win or loss)
    if _total_games(conn, model_id) == 100:
        earned.append("centurion")

    return earned
