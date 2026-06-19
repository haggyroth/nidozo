"""BattleStore — all database read/write operations."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from nidozo.battle.achievements import BADGES, evaluate_badges
from nidozo.db.elo import DEFAULT_RATING, updated_ratings
from nidozo.db.schema import migrate

_DEFAULT_DB = Path(__file__).parent.parent.parent.parent / "nidozo.db"


class BattleStore:
    """SQLite store for battle results, ELO ratings, and turn logs.

    Uses per-thread SQLite connections so FastAPI's thread-pool workers never
    share a connection object.  SQLite's WAL journal mode (set by the schema
    migration) allows concurrent reads across threads while serialising writes
    at the file level.  Multi-step mutations (e.g. finish_battle + ELO update)
    are wrapped in ``with self._conn:`` for atomic commit/rollback.

    Args:
        db_path: Path to the SQLite file. Created if it doesn't exist.
    """

    def __init__(self, db_path: Path | str = _DEFAULT_DB) -> None:
        self._path = Path(db_path)
        self._local = threading.local()
        self._closed = False
        # Run the schema migration on the main thread's connection so WAL mode
        # and all tables are created before any worker thread connects.
        migrate(self._conn)

    # ------------------------------------------------------------------
    # Internal connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return this thread's SQLite connection, creating it on first access.

        Each OS thread gets its own connection so concurrent route handlers
        never share state.  ``PRAGMA foreign_keys=ON`` is re-applied per
        connection because it is not persisted to disk; WAL mode *is*
        persisted, so it only needs to be set once (the migration does it).

        Raises:
            sqlite3.ProgrammingError: If ``close()`` has been called.
        """
        if self._closed:
            raise sqlite3.ProgrammingError(
                "Cannot operate on a closed BattleStore."
            )
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn  # type: ignore[no-any-return]  # threading.local attrs are Any

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._get_conn()

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------

    def get_or_create_model(
        self,
        provider: str,
        model_name: str,
        prompt_version: str = "v1",
    ) -> int:
        """Return the model id, creating the row if it doesn't exist.

        The (provider, model_name, prompt_version) UNIQUE index makes this
        race-safe: if a concurrent caller inserts the same identity between our
        SELECT and INSERT, the INSERT raises IntegrityError and we re-select the
        winner's row instead of creating a duplicate.
        """
        cur = self._conn.execute(
            "SELECT id FROM models WHERE provider=? AND model_name=? AND prompt_version=?",
            (provider, model_name, prompt_version),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])

        try:
            cur = self._conn.execute(
                "INSERT INTO models (provider, model_name, prompt_version) VALUES (?,?,?)",
                (provider, model_name, prompt_version),
            )
            model_id = cur.lastrowid
            assert model_id is not None
            self._conn.execute(
                "INSERT INTO elo_ratings (model_id, rating, games) VALUES (?,?,0)",
                (model_id, DEFAULT_RATING),
            )
            self._conn.commit()
            return model_id
        except sqlite3.IntegrityError:
            # Lost the insert race (or the UNIQUE index already held this
            # identity) — roll back our partial insert and return the existing row.
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT id FROM models WHERE provider=? AND model_name=? AND prompt_version=?",
                (provider, model_name, prompt_version),
            ).fetchone()
            if row:
                return int(row["id"])
            raise

    # ------------------------------------------------------------------
    # Battles
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Tournaments
    # ------------------------------------------------------------------

    def create_tournament(
        self,
        players: list[dict[str, Any]],
        rounds: int,
        prompt_version: str,
        total_battles: int,
        tier: str = "random",
        tournament_format: str = "round_robin",
        bracket_state: dict[str, Any] | None = None,
    ) -> int:
        """Insert a tournament row and return its id."""
        cur = self._conn.execute(
            """INSERT INTO tournaments
               (players, rounds, prompt_version, total_battles, tier,
                tournament_format, bracket_state)
               VALUES (?,?,?,?,?,?,?)""",
            (
                json.dumps(players), rounds, prompt_version, total_battles, tier,
                tournament_format,
                json.dumps(bracket_state) if bracket_state is not None else None,
            ),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def update_bracket_state(
        self,
        tournament_id: int,
        bracket_state: dict[str, Any],
    ) -> None:
        """Persist the current bracket state JSON."""
        self._conn.execute(
            "UPDATE tournaments SET bracket_state=? WHERE id=?",
            (json.dumps(bracket_state), tournament_id),
        )
        self._conn.commit()

    def get_bracket_state(self, tournament_id: int) -> dict[str, Any] | None:
        """Return parsed bracket_state dict, or None if absent."""
        row = self._conn.execute(
            "SELECT bracket_state FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not row or not row["bracket_state"]:
            return None
        return json.loads(row["bracket_state"])  # type: ignore[no-any-return]

    def finish_tournament(self, tournament_id: int, status: str = "completed") -> None:
        self._conn.execute(
            """UPDATE tournaments SET status=?,
               finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (status, tournament_id),
        )
        self._conn.commit()

    def get_tournament(self, tournament_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_tournaments(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent tournaments, newest first, with battles_completed count."""
        rows = self._conn.execute(
            """SELECT t.id, t.players, t.rounds, t.prompt_version,
                      t.total_battles, t.status, t.created_at, t.finished_at,
                      COALESCE(t.tier, 'random') AS tier,
                      COALESCE(t.tournament_format, 'round_robin') AS tournament_format,
                      COUNT(CASE WHEN b.status='completed' THEN 1 END) AS battles_completed
               FROM tournaments t
               LEFT JOIN battles b ON b.tournament_id = t.id
               GROUP BY t.id
               ORDER BY t.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tournament_standings(self, tournament_id: int) -> list[dict[str, Any]]:
        """Per-player standings within a single tournament.

        Returns one row per model that has at least one battle in this
        tournament, sorted by points (3×win + tie) descending, then wins.

        Columns: model_id, provider, model_name, wins, losses, ties,
                 battles_played, points, elo_delta
        """
        rows = self._conn.execute(
            """WITH tm AS (
                 SELECT DISTINCT m.id, m.provider, m.model_name
                 FROM models m
                 JOIN battles b ON (b.p1_model_id = m.id OR b.p2_model_id = m.id)
                 WHERE b.tournament_id = ?
               ),
               results AS (
                 SELECT tm.id AS model_id, tm.provider, tm.model_name,
                   SUM(CASE WHEN (b.p1_model_id=tm.id AND b.winner=1)
                              OR (b.p2_model_id=tm.id AND b.winner=2) THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN b.winner IS NULL THEN 1 ELSE 0 END) AS ties,
                   SUM(CASE WHEN (b.p1_model_id=tm.id AND b.winner=2)
                              OR (b.p2_model_id=tm.id AND b.winner=1) THEN 1 ELSE 0 END) AS losses,
                   COUNT(b.id) AS battles_played
                 FROM tm
                 JOIN battles b ON (b.p1_model_id=tm.id OR b.p2_model_id=tm.id)
                 WHERE b.tournament_id = ? AND b.status='completed'
                 GROUP BY tm.id
               ),
               elo_deltas AS (
                 SELECT eh.model_id,
                   COALESCE(SUM(eh.delta), 0.0) AS elo_delta
                 FROM elo_history eh
                 JOIN battles b ON b.id = eh.battle_id
                 WHERE b.tournament_id = ?
                 GROUP BY eh.model_id
               )
               SELECT r.*, COALESCE(ed.elo_delta, 0.0) AS elo_delta,
                      r.wins * 3 + r.ties AS points
               FROM results r
               LEFT JOIN elo_deltas ed ON ed.model_id = r.model_id
               ORDER BY points DESC, wins DESC, r.model_id""",
            (tournament_id, tournament_id, tournament_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tournament_battles(self, tournament_id: int) -> list[dict[str, Any]]:
        """All battles for a tournament in scheduled order with results."""
        rows = self._conn.execute(
            """SELECT b.id, b.status, b.winner, b.total_turns,
                      b.started_at, b.finished_at,
                      CAST(b.p1_team_id IS NOT NULL AS INT) AS drafted,
                      p1.provider || '/' || p1.model_name AS p1,
                      p2.provider || '/' || p2.model_name AS p2,
                      p1.model_name AS p1_model, p2.model_name AS p2_model,
                      p1.provider AS p1_provider, p2.provider AS p2_provider
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.tournament_id = ?
               ORDER BY b.id""",
            (tournament_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def cancel_tournament(self, tournament_id: int) -> bool:
        """Mark a running tournament as cancelled.

        Returns False if the tournament is already finished or doesn't exist.
        """
        row = self._conn.execute(
            "SELECT status FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not row or row["status"] != "running":
            return False
        self._conn.execute(
            """UPDATE tournaments SET status='cancelled',
               finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (tournament_id,),
        )
        self._conn.commit()
        return True

    # ------------------------------------------------------------------
    # Battles
    # ------------------------------------------------------------------

    def get_battle(self, battle_id: int) -> dict[str, Any] | None:
        """Return a single battle row by id, or None if not found."""
        row = self._conn.execute(
            """SELECT b.id, b.battle_tag, b.format, b.winner, b.total_turns,
                      b.status, b.started_at, b.finished_at, b.tournament_id,
                      COALESCE(b.tier, 'random') AS tier,
                      CAST(b.p1_team_id IS NOT NULL AS INT) AS drafted,
                      p1.provider||'/'||p1.model_name AS p1,
                      p2.provider||'/'||p2.model_name AS p2,
                      b.narrative
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.id=?""",
            (battle_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_battle_narrative(self, battle_id: int, narrative: str) -> None:
        """Store the LLM-generated narrative for a completed battle."""
        self._conn.execute(
            "UPDATE battles SET narrative=? WHERE id=?",
            (narrative, battle_id),
        )
        self._conn.commit()

    def create_battle(
        self,
        battle_tag: str,
        format: str,
        p1_model_id: int,
        p2_model_id: int,
        tournament_id: int | None = None,
        season_id: int | None = None,
        p1_personality: str | None = None,
        p2_personality: str | None = None,
        team_size: int = 6,
    ) -> int:
        """Insert a battle row and return its id."""
        cur = self._conn.execute(
            """INSERT INTO battles
               (battle_tag, format, p1_model_id, p2_model_id, tournament_id, season_id,
                p1_personality, p2_personality, team_size, status)
               VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
            (battle_tag, format, p1_model_id, p2_model_id, tournament_id, season_id,
             p1_personality, p2_personality, team_size),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def abort_stale_records(self) -> dict[str, int]:
        """Mark in-flight records as failed/cancelled on server startup.

        Called once during the lifespan startup so that battles, tournaments,
        and seasons that were left in a non-terminal state by a previous crash
        or SIGKILL do not linger as permanently 'running'.

        Returns a dict with the count of rows affected per table so the caller
        can log a meaningful startup message.
        """
        battles = self._conn.execute(
            "UPDATE battles SET status='failed' WHERE status IN ('pending','running')"
        ).rowcount
        tournaments = self._conn.execute(
            "UPDATE tournaments SET status='cancelled' WHERE status='running'"
        ).rowcount
        seasons = self._conn.execute(
            "UPDATE seasons SET status='cancelled' WHERE status='running'"
        ).rowcount
        self._conn.commit()
        return {"battles": battles, "tournaments": tournaments, "seasons": seasons}

    def set_battle_status(self, battle_id: int, status: str) -> None:
        """Update the status field of a battle (pending/running/completed/cancelled/failed)."""
        self._conn.execute(
            "UPDATE battles SET status=? WHERE id=?", (status, battle_id)
        )
        self._conn.commit()

    def cancel_battle(self, battle_id: int) -> bool:
        """Mark a battle as cancelled. Returns False if already finished."""
        row = self._conn.execute(
            "SELECT status FROM battles WHERE id=?", (battle_id,)
        ).fetchone()
        if not row or row["status"] in ("completed", "cancelled", "failed"):
            return False
        self._conn.execute(
            """UPDATE battles SET status='cancelled',
               finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (battle_id,),
        )
        self._conn.commit()
        return True

    def finish_battle(
        self,
        battle_id: int,
        winner: int | None,
        total_turns: int,
    ) -> list[dict[str, Any]]:
        """Mark battle finished, update ELO, and award any newly-earned badges.

        Returns a list of badge dicts (model_id, slug, name, emoji) for every
        badge newly awarded in this battle.  Empty list on duplicate call
        (idempotent guard) or when no badges are earned.
        """
        with self._conn:
            cur = self._conn.execute(
                """UPDATE battles
                   SET winner=?, total_turns=?, status='completed',
                       finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                   WHERE id=? AND finished_at IS NULL""",
                (winner, total_turns, battle_id),
            )
            if cur.rowcount == 0:
                # Already finished — skip ELO to prevent double-apply.
                return []
            self._update_elo(battle_id, winner)
            return self._award_badges(battle_id, winner)

    def _update_elo(self, battle_id: int, winner: int | None) -> None:
        """Compute and persist ELO deltas. Must be called inside a transaction."""
        row = self._conn.execute(
            "SELECT p1_model_id, p2_model_id FROM battles WHERE id=?",
            (battle_id,),
        ).fetchone()
        p1_id, p2_id = row["p1_model_id"], row["p2_model_id"]

        r1 = self._conn.execute(
            "SELECT rating FROM elo_ratings WHERE model_id=?", (p1_id,)
        ).fetchone()["rating"]
        r2 = self._conn.execute(
            "SELECT rating FROM elo_ratings WHERE model_id=?", (p2_id,)
        ).fetchone()["rating"]

        new_r1, new_r2 = updated_ratings(r1, r2, winner)

        for model_id, before, after in ((p1_id, r1, new_r1), (p2_id, r2, new_r2)):
            self._conn.execute(
                """UPDATE elo_ratings
                   SET rating=?, games=games+1,
                       updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                   WHERE model_id=?""",
                (after, model_id),
            )
            self._conn.execute(
                """INSERT OR IGNORE INTO elo_history
                   (battle_id, model_id, rating_before, rating_after, delta)
                   VALUES (?,?,?,?,?)""",
                (battle_id, model_id, before, after, after - before),
            )
        # No commit here — the `with self._conn:` block in finish_battle handles it.

    # ------------------------------------------------------------------
    # Badges
    # ------------------------------------------------------------------

    def _award_badges(self, battle_id: int, winner: int | None) -> list[dict[str, Any]]:
        """Evaluate and persist badges for both players.  Must be called inside a transaction."""
        row = self._conn.execute(
            "SELECT p1_model_id, p2_model_id FROM battles WHERE id=?", (battle_id,)
        ).fetchone()
        p1_id, p2_id = row["p1_model_id"], row["p2_model_id"]
        winner_mid = p1_id if winner == 1 else (p2_id if winner == 2 else None)

        new_badges: list[dict[str, Any]] = []
        for model_id, opp_id in ((p1_id, p2_id), (p2_id, p1_id)):
            for slug in evaluate_badges(self._conn, battle_id, model_id, winner_mid, opp_id):
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO badges (model_id, battle_id, slug) VALUES (?,?,?)",
                    (model_id, battle_id, slug),
                )
                if cur.rowcount:  # was genuinely new (not a duplicate)
                    badge = BADGES[slug]
                    new_badges.append({
                        "model_id": model_id,
                        "battle_id": battle_id,
                        "slug": slug,
                        "name": badge.name,
                        "emoji": badge.emoji,
                        "description": badge.description,
                    })
        return new_badges

    def get_model_badges(self, model_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT b.slug, b.battle_id, b.earned_at
               FROM badges b
               WHERE b.model_id=?
               ORDER BY b.earned_at ASC""",
            (model_id,),
        ).fetchall()
        result = []
        for r in rows:
            badge = BADGES.get(r["slug"])
            result.append({
                "slug": r["slug"],
                "name": badge.name if badge else r["slug"],
                "emoji": badge.emoji if badge else "🏅",
                "description": badge.description if badge else "",
                "battle_id": r["battle_id"],
                "earned_at": r["earned_at"],
            })
        return result

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def log_turn(
        self,
        battle_id: int,
        turn_number: int,
        player_role: str,
        prompt_version: str,
        action_chosen: str | None,
        parse_success: bool,
        llm_response: str | None = None,
        state_json: str | None = None,
        coach_advice: str | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO turns
               (battle_id, turn_number, player_role, prompt_version,
                action_chosen, parse_success, fallback_reason,
                llm_response, state_json, coach_advice)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                battle_id, turn_number, player_role, prompt_version,
                action_chosen, int(parse_success), fallback_reason,
                llm_response, state_json, coach_advice,
            ),
        )
        self._conn.commit()

    def get_turns_basic(self, battle_id: int) -> list[dict[str, Any]]:
        """Return per-turn summary rows (no state_json) ordered by turn number."""
        cur = self._conn.execute(
            """SELECT turn_number, player_role, prompt_version,
                      action_chosen, parse_success, fallback_reason
               FROM turns WHERE battle_id=? ORDER BY turn_number""",
            (battle_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_player_model_ids(self, battle_id: int) -> tuple[int, int] | None:
        """Return (p1_model_id, p2_model_id) for a battle, or None if not found."""
        row = self._conn.execute(
            "SELECT p1_model_id, p2_model_id FROM battles WHERE id=?", (battle_id,)
        ).fetchone()
        return (row["p1_model_id"], row["p2_model_id"]) if row else None

    def get_battle_players(self, battle_id: int) -> dict[str, Any] | None:
        """Return provider and model_name for both players of a battle, or None."""
        row = self._conn.execute(
            """SELECT p1.provider AS p1_provider, p1.model_name AS p1_model,
                      p2.provider AS p2_provider, p2.model_name AS p2_model
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.id=?""",
            (battle_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_battle_tag(self, battle_id: int, battle_tag: str) -> None:
        """Overwrite the battle_tag once the real Showdown tag is known."""
        self._conn.execute(
            "UPDATE battles SET battle_tag=? WHERE id=?", (battle_tag, battle_id)
        )
        self._conn.commit()

    def get_turns_with_state(self, battle_id: int) -> list[dict[str, Any]]:
        """Return all turns for a battle including state_json and coach_advice, ordered by turn then player."""
        cur = self._conn.execute(
            """SELECT turn_number, player_role, prompt_version,
                      action_chosen, parse_success, fallback_reason,
                      llm_response, state_json, coach_advice
               FROM turns WHERE battle_id=?
               ORDER BY turn_number, player_role""",
            (battle_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Leaderboard queries
    # ------------------------------------------------------------------

    def leaderboard(self, grouped: bool = True, tier: str | None = None) -> list[dict[str, Any]]:
        """Return models sorted by ELO descending.

        Args:
            grouped: If True (default), aggregate all prompt versions for the same
                     (provider, model_name) into a single row — shows best ELO,
                     summed W/L/T, and a 'versions' field listing what was played.
                     If False, return one row per (provider, model_name, prompt_version).
            tier: If provided, restrict W/L/T/games to battles of that tier only.
                  ELO still reflects global rating.
        """
        if grouped:
            return self._leaderboard_grouped(tier=tier)
        return self._leaderboard_per_version()

    def _leaderboard_grouped(self, tier: str | None = None) -> list[dict[str, Any]]:
        """One row per (provider, model_name) — aggregated across prompt versions."""
        tier_filter = "AND b.tier = :tier" if tier and tier != "all" else ""
        cur = self._conn.execute(
            f"""SELECT m.provider, m.model_name,
                      MAX(e.rating) AS rating,
                      COALESCE(SUM(wld.wins),   0)
                        + COALESCE(SUM(wld.losses), 0)
                        + COALESCE(SUM(wld.ties),   0) AS games,
                      COALESCE(SUM(wld.wins),   0) AS wins,
                      COALESCE(SUM(wld.losses), 0) AS losses,
                      COALESCE(SUM(wld.ties),   0) AS ties,
                      GROUP_CONCAT(DISTINCT m.prompt_version) AS versions,
                      (SELECT m2.id FROM models m2
                         JOIN elo_ratings e2 ON e2.model_id = m2.id
                        WHERE m2.provider = m.provider AND m2.model_name = m.model_name
                        ORDER BY e2.rating DESC LIMIT 1) AS model_id
               FROM models m
               JOIN elo_ratings e ON e.model_id = m.id
               LEFT JOIN (
                   SELECT model_id,
                          SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) AS wins,
                          SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) AS losses,
                          SUM(CASE WHEN result='tie'  THEN 1 ELSE 0 END) AS ties
                   FROM (
                       SELECT p1_model_id AS model_id,
                              CASE WHEN winner=1 THEN 'win'
                                   WHEN winner=2 THEN 'loss'
                                   ELSE 'tie' END AS result
                       FROM battles b WHERE finished_at IS NOT NULL AND b.status='completed' {tier_filter}
                       UNION ALL
                       SELECT p2_model_id,
                              CASE WHEN winner=2 THEN 'win'
                                   WHEN winner=1 THEN 'loss'
                                   ELSE 'tie' END
                       FROM battles b WHERE finished_at IS NOT NULL AND b.status='completed' {tier_filter}
                   ) GROUP BY model_id
               ) wld ON wld.model_id = m.id
               GROUP BY m.provider, m.model_name
               ORDER BY MAX(e.rating) DESC""",
            {"tier": tier},
        )
        rows = [dict(r) for r in cur.fetchall()]
        streaks = self._win_streaks()
        for row in rows:
            row["streak"] = streaks.get(row["model_id"], 0)
        return rows

    def _win_streaks(self) -> dict[int, int]:
        """Return the current consecutive-win streak for every model.

        Uses a window-function CTE to rank each model's completed battles from
        most-recent to oldest, then counts how many wins appear before the first
        non-win.  Models with no battles or whose last result was not a win get 0.
        """
        cur = self._conn.execute(
            """
            WITH all_results AS (
                SELECT p1_model_id AS model_id, finished_at,
                       CASE WHEN winner = 1 THEN 1 ELSE 0 END AS is_win
                FROM battles
                WHERE finished_at IS NOT NULL AND status = 'completed'
                UNION ALL
                SELECT p2_model_id, finished_at,
                       CASE WHEN winner = 2 THEN 1 ELSE 0 END AS is_win
                FROM battles
                WHERE finished_at IS NOT NULL AND status = 'completed'
            ),
            ranked AS (
                SELECT model_id, is_win,
                       ROW_NUMBER() OVER (
                           PARTITION BY model_id ORDER BY finished_at DESC
                       ) AS rn
                FROM all_results
            ),
            first_non_win AS (
                SELECT model_id, MIN(rn) AS at
                FROM ranked WHERE is_win = 0
                GROUP BY model_id
            )
            SELECT r.model_id, COUNT(*) AS streak
            FROM ranked r
            LEFT JOIN first_non_win fnw ON fnw.model_id = r.model_id
            WHERE r.is_win = 1
              AND r.rn < COALESCE(fnw.at, 9999999)
            GROUP BY r.model_id
            """
        )
        return {row["model_id"]: row["streak"] for row in cur.fetchall()}

    def _leaderboard_per_version(self) -> list[dict[str, Any]]:
        """One row per (provider, model_name, prompt_version) — original behaviour."""
        cur = self._conn.execute(
            """SELECT m.provider, m.model_name, m.prompt_version,
                      e.rating, e.games,
                      COALESCE(wld.wins,   0) AS wins,
                      COALESCE(wld.losses, 0) AS losses,
                      COALESCE(wld.ties,   0) AS ties
               FROM models m
               JOIN elo_ratings e ON e.model_id = m.id
               LEFT JOIN (
                   SELECT model_id,
                          SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) AS wins,
                          SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) AS losses,
                          SUM(CASE WHEN result='tie'  THEN 1 ELSE 0 END) AS ties
                   FROM (
                       SELECT p1_model_id AS model_id,
                              CASE WHEN winner=1 THEN 'win'
                                   WHEN winner=2 THEN 'loss'
                                   ELSE 'tie' END AS result
                       FROM battles WHERE finished_at IS NOT NULL AND status='completed'
                       UNION ALL
                       SELECT p2_model_id,
                              CASE WHEN winner=2 THEN 'win'
                                   WHEN winner=1 THEN 'loss'
                                   ELSE 'tie' END
                       FROM battles WHERE finished_at IS NOT NULL AND status='completed'
                   ) GROUP BY model_id
               ) wld ON wld.model_id = m.id
               ORDER BY e.rating DESC""",
        )
        return [dict(r) for r in cur.fetchall()]

    def matchup_matrix(self, tier: str | None = None) -> list[dict[str, Any]]:
        """Return per-pair win/loss/tie counts for the head-to-head matchup matrix.

        Each row represents model A vs model B (A's perspective).  Models are
        grouped by (provider, model_name) — same granularity as the grouped
        leaderboard.  Both side-assignments (A as p1 and A as p2) are unioned
        so every battle is counted exactly once per ordered pair.

        Returns a list of dicts with keys:
            row_provider, row_model, col_provider, col_model,
            wins, losses, ties, games
        """
        tier_filter = "AND b.tier = :tier" if tier and tier != "all" else ""
        cur = self._conn.execute(
            f"""WITH ag AS (
                    -- A is p1, B is p2
                    SELECT mr.provider AS row_provider, mr.model_name AS row_model,
                           mc.provider AS col_provider, mc.model_name AS col_model,
                           CASE WHEN b.winner = 1 THEN 1 ELSE 0 END AS is_win,
                           CASE WHEN b.winner = 2 THEN 1 ELSE 0 END AS is_loss,
                           CASE WHEN b.winner IS NULL OR (b.winner != 1 AND b.winner != 2)
                                THEN 1 ELSE 0 END AS is_tie
                    FROM battles b
                    JOIN models mr ON mr.id = b.p1_model_id
                    JOIN models mc ON mc.id = b.p2_model_id
                    WHERE b.finished_at IS NOT NULL AND b.status='completed' {tier_filter}
                    UNION ALL
                    -- A is p2, B is p1
                    SELECT mr.provider, mr.model_name,
                           mc.provider, mc.model_name,
                           CASE WHEN b.winner = 2 THEN 1 ELSE 0 END,
                           CASE WHEN b.winner = 1 THEN 1 ELSE 0 END,
                           CASE WHEN b.winner IS NULL OR (b.winner != 1 AND b.winner != 2)
                                THEN 1 ELSE 0 END
                    FROM battles b
                    JOIN models mr ON mr.id = b.p2_model_id
                    JOIN models mc ON mc.id = b.p1_model_id
                    WHERE b.finished_at IS NOT NULL AND b.status='completed' {tier_filter}
                )
                SELECT row_provider, row_model, col_provider, col_model,
                       SUM(is_win)  AS wins,
                       SUM(is_loss) AS losses,
                       SUM(is_tie)  AS ties,
                       COUNT(*)     AS games
                FROM ag
                GROUP BY row_provider, row_model, col_provider, col_model
                ORDER BY row_provider, row_model, col_provider, col_model""",
            {"tier": tier},
        )
        return [dict(r) for r in cur.fetchall()]

    def get_model_matchups(self, model_id: int) -> list[dict[str, Any]]:
        """Return this model's head-to-head record against every distinct opponent.

        Each row is one opponent, with the win/loss/tie counts from this model's
        perspective. Results are sorted by games played descending.
        """
        cur = self._conn.execute(
            """WITH ag AS (
                   SELECT mc.provider AS opp_provider, mc.model_name AS opp_model,
                          CASE WHEN b.winner = 1 THEN 1 ELSE 0 END AS is_win,
                          CASE WHEN b.winner = 2 THEN 1 ELSE 0 END AS is_loss,
                          CASE WHEN b.winner IS NULL THEN 1 ELSE 0 END  AS is_tie
                   FROM battles b
                   JOIN models mc ON mc.id = b.p2_model_id
                   WHERE b.p1_model_id = :mid
                     AND b.finished_at IS NOT NULL AND b.status = 'completed'
                   UNION ALL
                   SELECT mc.provider, mc.model_name,
                          CASE WHEN b.winner = 2 THEN 1 ELSE 0 END,
                          CASE WHEN b.winner = 1 THEN 1 ELSE 0 END,
                          CASE WHEN b.winner IS NULL THEN 1 ELSE 0 END
                   FROM battles b
                   JOIN models mc ON mc.id = b.p1_model_id
                   WHERE b.p2_model_id = :mid
                     AND b.finished_at IS NOT NULL AND b.status = 'completed'
               )
               SELECT opp_provider, opp_model,
                      SUM(is_win)  AS wins,
                      SUM(is_loss) AS losses,
                      SUM(is_tie)  AS ties,
                      COUNT(*)     AS games
               FROM ag
               GROUP BY opp_provider, opp_model
               ORDER BY games DESC, wins DESC""",
            {"mid": model_id},
        )
        return [dict(r) for r in cur.fetchall()]

    def recent_battles(self, limit: int = 10) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """SELECT b.id, b.battle_tag, b.format, b.total_turns, b.winner, b.finished_at,
                      b.status,
                      COALESCE(b.tier, 'random') AS tier,
                      CAST(b.p1_team_id IS NOT NULL AS INT) AS drafted,
                      p1.provider||'/'||p1.model_name AS p1,
                      p2.provider||'/'||p2.model_name AS p2
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.finished_at IS NOT NULL
               ORDER BY b.finished_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Lessons (post-battle LLM reflections)
    # ------------------------------------------------------------------

    def create_lesson(self, model_id: int, battle_id: int, content: str) -> int:
        """Persist a post-battle lesson and return its id."""
        cur = self._conn.execute(
            "INSERT INTO lessons (model_id, battle_id, content) VALUES (?,?,?)",
            (model_id, battle_id, content),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def get_lessons(self, model_id: int, limit: int = 5) -> list[dict[str, Any]]:
        """Return the most recent lessons for a model, newest first."""
        cur = self._conn.execute(
            """SELECT id, battle_id, content, created_at
               FROM lessons WHERE model_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (model_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Model stats (per-model history page)
    # ------------------------------------------------------------------

    def get_model_stats(self, model_id: int) -> dict[str, Any] | None:
        """Return comprehensive stats for a model: identity, ELO history,
        battle history, turn quality summary, and recent lessons.

        Returns None if the model_id does not exist.
        """
        # Identity + current ELO + W/L/T
        info_row = self._conn.execute(
            """SELECT m.id, m.provider, m.model_name, m.prompt_version,
                      e.rating, e.games,
                      COALESCE(wld.wins,   0) AS wins,
                      COALESCE(wld.losses, 0) AS losses,
                      COALESCE(wld.ties,   0) AS ties
               FROM models m
               JOIN elo_ratings e ON e.model_id = m.id
               LEFT JOIN (
                   SELECT model_id,
                          SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) AS wins,
                          SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) AS losses,
                          SUM(CASE WHEN result='tie'  THEN 1 ELSE 0 END) AS ties
                   FROM (
                       SELECT p1_model_id AS model_id,
                              CASE WHEN winner=1 THEN 'win' WHEN winner=2 THEN 'loss' ELSE 'tie' END AS result
                       FROM battles WHERE finished_at IS NOT NULL AND status='completed'
                       UNION ALL
                       SELECT p2_model_id,
                              CASE WHEN winner=2 THEN 'win' WHEN winner=1 THEN 'loss' ELSE 'tie' END AS result
                       FROM battles WHERE finished_at IS NOT NULL AND status='completed'
                   ) GROUP BY model_id
               ) wld ON wld.model_id = m.id
               WHERE m.id = ?""",
            (model_id,),
        ).fetchone()
        if not info_row:
            return None

        # ELO history (chronological, capped to the *most recent* 30 battles).
        # Take the latest 30 by finished_at DESC, then re-sort ASC so the chart
        # reads left-to-right in time.  A plain "ORDER BY ASC LIMIT 30" would pin
        # the chart to the model's earliest 30 battles forever.
        elo_rows = self._conn.execute(
            """SELECT battle_id, rating_before, rating_after, delta, finished_at
               FROM (
                   SELECT eh.battle_id, eh.rating_before, eh.rating_after,
                          eh.delta, b.finished_at
                   FROM elo_history eh
                   JOIN battles b ON b.id = eh.battle_id
                   WHERE eh.model_id = ?
                   ORDER BY b.finished_at DESC
                   LIMIT 30
               )
               ORDER BY finished_at ASC""",
            (model_id,),
        ).fetchall()

        # Per-battle history (last 20 completed)
        battle_rows = self._conn.execute(
            """SELECT b.id, b.total_turns, b.winner, b.finished_at,
                      CASE WHEN b.p1_model_id = ? THEN 'p1' ELSE 'p2' END AS my_role,
                      CASE WHEN b.p1_model_id = ?
                           THEN p2.provider||'/'||p2.model_name
                           ELSE p1.provider||'/'||p1.model_name END AS opponent,
                      CASE WHEN (b.p1_model_id = ? AND b.winner = 1)
                                OR (b.p2_model_id = ? AND b.winner = 2) THEN 'win'
                           WHEN b.winner IS NULL THEN 'tie'
                           ELSE 'loss' END AS result
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE (b.p1_model_id = ? OR b.p2_model_id = ?)
                 AND b.finished_at IS NOT NULL
                 AND b.status = 'completed'
               ORDER BY b.finished_at DESC
               LIMIT 20""",
            (model_id, model_id, model_id, model_id, model_id, model_id),
        ).fetchall()

        # Turn quality summary — parse_success rate (aggregate in SQL, no row fetch)
        parse_agg = self._conn.execute(
            """SELECT COUNT(*) AS total, SUM(parse_success) AS ok
               FROM turns t
               JOIN battles b ON b.id = t.battle_id
               WHERE (b.p1_model_id = ? AND t.player_role = 'p1')
                  OR (b.p2_model_id = ? AND t.player_role = 'p2')""",
            (model_id, model_id),
        ).fetchone()

        total_turns = parse_agg["total"] or 0
        parse_ok = int(parse_agg["ok"] or 0)
        parse_fail = total_turns - parse_ok
        parse_success_rate = round(parse_ok / total_turns * 100, 1) if total_turns else None

        # Recent lessons
        lesson_rows = self._conn.execute(
            """SELECT id, battle_id, content, created_at
               FROM lessons WHERE model_id=?
               ORDER BY created_at DESC LIMIT 10""",
            (model_id,),
        ).fetchall()

        usage = self.get_model_usage_stats(model_id)

        return {
            "model": dict(info_row),
            "elo_history": [dict(r) for r in elo_rows],
            "battle_history": [dict(r) for r in battle_rows],
            "turn_stats": {
                "total_turns": total_turns,
                "parse_ok": parse_ok,
                "parse_fail": parse_fail,
                "parse_success_rate": parse_success_rate,
            },
            "lessons": [dict(r) for r in lesson_rows],
            "usage": usage,
        }

    def get_model_usage_stats(self, model_id: int) -> dict[str, Any]:
        """Mine turns.state_json and turns.llm_response for per-model usage stats.

        Uses SQLite's json_extract so no Python-side JSON parsing loop is needed.
        Returns empty lists/dicts if the model has no recorded turns yet.
        """
        # Top Pokémon used as the active mon across all turns for this model.
        species_rows = self._conn.execute(
            """SELECT json_extract(t.state_json, '$.my_active.species') AS species,
                      COUNT(*) AS cnt
               FROM turns t
               JOIN battles b ON b.id = t.battle_id
               WHERE ((b.p1_model_id = ? AND t.player_role = 'p1')
                   OR (b.p2_model_id = ? AND t.player_role = 'p2'))
                 AND t.state_json IS NOT NULL
                 AND json_extract(t.state_json, '$.my_active.species') IS NOT NULL
               GROUP BY species
               ORDER BY cnt DESC LIMIT 8""",
            (model_id, model_id),
        ).fetchall()

        # Top moves chosen (action_type = "move", identifier = move name).
        move_rows = self._conn.execute(
            """SELECT json_extract(t.llm_response, '$.identifier') AS move,
                      COUNT(*) AS cnt
               FROM turns t
               JOIN battles b ON b.id = t.battle_id
               WHERE ((b.p1_model_id = ? AND t.player_role = 'p1')
                   OR (b.p2_model_id = ? AND t.player_role = 'p2'))
                 AND t.llm_response IS NOT NULL
                 AND json_valid(t.llm_response)
                 AND json_extract(t.llm_response, '$.action_type') = 'move'
               GROUP BY move
               ORDER BY cnt DESC LIMIT 10""",
            (model_id, model_id),
        ).fetchall()

        # Action type distribution: move / switch / (null=fallback).
        action_rows = self._conn.execute(
            """SELECT COALESCE(json_extract(t.llm_response, '$.action_type'), 'fallback') AS action_type,
                      COUNT(*) AS cnt
               FROM turns t
               JOIN battles b ON b.id = t.battle_id
               WHERE ((b.p1_model_id = ? AND t.player_role = 'p1')
                   OR (b.p2_model_id = ? AND t.player_role = 'p2'))
                 AND (t.llm_response IS NULL OR json_valid(t.llm_response))
               GROUP BY action_type""",
            (model_id, model_id),
        ).fetchall()

        # Win rate broken down by tier.
        tier_rows = self._conn.execute(
            """SELECT COALESCE(b.tier, 'random') AS tier,
                      COUNT(*) AS total,
                      SUM(CASE WHEN (b.p1_model_id = ? AND b.winner = 1)
                                 OR (b.p2_model_id = ? AND b.winner = 2) THEN 1 ELSE 0 END) AS wins
               FROM battles b
               WHERE (b.p1_model_id = ? OR b.p2_model_id = ?)
                 AND b.finished_at IS NOT NULL
                 AND b.status = 'completed'
               GROUP BY tier
               ORDER BY total DESC""",
            (model_id, model_id, model_id, model_id),
        ).fetchall()

        return {
            "top_pokemon": [dict(r) for r in species_rows],
            "top_moves": [dict(r) for r in move_rows],
            "action_distribution": [dict(r) for r in action_rows],
            "win_rate_by_tier": [dict(r) for r in tier_rows],
        }

    def get_global_stats(self) -> dict[str, Any]:
        """Aggregate stats across all battles and models for the global dashboard."""
        # Summary numbers.
        summary_row = self._conn.execute(
            """SELECT COUNT(*)                                    AS total_battles,
                      ROUND(AVG(total_turns), 1)                 AS avg_turns,
                      SUM(CASE WHEN winner IS NOT NULL THEN 1 ELSE 0 END) AS decided_battles
               FROM battles WHERE finished_at IS NOT NULL AND status='completed'"""
        ).fetchone()
        model_count = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM models"
        ).fetchone()

        # Battles by tier with avg turns per tier.
        tier_rows = self._conn.execute(
            """SELECT COALESCE(tier, 'random') AS tier,
                      COUNT(*) AS cnt,
                      ROUND(AVG(total_turns), 1) AS avg_turns
               FROM battles WHERE finished_at IS NOT NULL AND status='completed'
               GROUP BY tier ORDER BY cnt DESC"""
        ).fetchall()

        # Type usage: count how often each type appears in the active Pokémon's
        # type slot(s) across all turns. Union both slots so dual-types contribute
        # to both counts. Lower() normalises the strings from the serialiser.
        type_rows = self._conn.execute(
            """SELECT LOWER(type) AS type, COUNT(*) AS cnt
               FROM (
                   SELECT json_extract(t.state_json, '$.my_active.types[0]') AS type
                   FROM turns t WHERE t.state_json IS NOT NULL
                     AND json_extract(t.state_json, '$.my_active.types[0]') IS NOT NULL
                   UNION ALL
                   SELECT json_extract(t.state_json, '$.my_active.types[1]')
                   FROM turns t WHERE t.state_json IS NOT NULL
                     AND json_extract(t.state_json, '$.my_active.types[1]') IS NOT NULL
               )
               GROUP BY type ORDER BY cnt DESC"""
        ).fetchall()

        # Top Pokémon globally (all models, all turns).
        species_rows = self._conn.execute(
            """SELECT json_extract(t.state_json, '$.my_active.species') AS species,
                      COUNT(*) AS cnt
               FROM turns t
               WHERE t.state_json IS NOT NULL
                 AND json_extract(t.state_json, '$.my_active.species') IS NOT NULL
               GROUP BY species ORDER BY cnt DESC LIMIT 15"""
        ).fetchall()

        # Top moves globally.
        move_rows = self._conn.execute(
            """SELECT json_extract(t.llm_response, '$.identifier') AS move,
                      COUNT(*) AS cnt
               FROM turns t
               WHERE t.llm_response IS NOT NULL
                 AND json_valid(t.llm_response)
                 AND json_extract(t.llm_response, '$.action_type') = 'move'
               GROUP BY move ORDER BY cnt DESC LIMIT 12"""
        ).fetchall()

        # Recent completed battles.
        recent_rows = self._conn.execute(
            """SELECT b.id, b.finished_at, b.total_turns, b.winner,
                      COALESCE(b.tier, 'random') AS tier,
                      p1.provider||'/'||p1.model_name AS p1,
                      p2.provider||'/'||p2.model_name AS p2
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.finished_at IS NOT NULL AND b.status = 'completed'
               ORDER BY b.finished_at DESC LIMIT 10"""
        ).fetchall()

        return {
            "summary": {
                "total_battles": summary_row["total_battles"] if summary_row else 0,
                "avg_turns": summary_row["avg_turns"] if summary_row else None,
                "decided_battles": summary_row["decided_battles"] if summary_row else 0,
                "total_models": model_count["cnt"] if model_count else 0,
            },
            "battles_by_tier": [dict(r) for r in tier_rows],
            "type_usage": [dict(r) for r in type_rows],
            "top_pokemon": [dict(r) for r in species_rows],
            "top_moves": [dict(r) for r in move_rows],
            "recent_battles": [dict(r) for r in recent_rows],
        }

    def get_personality_stats(self) -> list[dict[str, Any]]:
        """Win/loss/tie breakdown per personality slug across all completed battles.

        Each row in the result covers battles where the personality was used by
        *either* player (as-player perspective), giving an overall picture of
        how each play style performs. Battles with no personality set are excluded.
        """
        rows = self._conn.execute(
            """
            SELECT personality,
                   COUNT(*) AS total,
                   SUM(CASE WHEN result = 'win'  THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN result = 'tie'  THEN 1 ELSE 0 END) AS ties
            FROM (
                SELECT b.p1_personality AS personality,
                       CASE WHEN b.winner = 1 THEN 'win'
                            WHEN b.winner = 2 THEN 'loss'
                            ELSE 'tie' END AS result
                FROM battles b
                WHERE b.p1_personality IS NOT NULL
                  AND b.finished_at IS NOT NULL AND b.status = 'completed'
                UNION ALL
                SELECT b.p2_personality AS personality,
                       CASE WHEN b.winner = 2 THEN 'win'
                            WHEN b.winner = 1 THEN 'loss'
                            ELSE 'tie' END AS result
                FROM battles b
                WHERE b.p2_personality IS NOT NULL
                  AND b.finished_at IS NOT NULL AND b.status = 'completed'
            )
            GROUP BY personality
            ORDER BY wins DESC, total DESC
            """
        ).fetchall()
        return [
            {
                **dict(r),
                "win_rate": round(r["wins"] / r["total"] * 100, 1) if r["total"] else None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Teams & Draft Sessions
    # ------------------------------------------------------------------

    def save_team(
        self,
        model_id: int,
        tier: str,
        format_: str,
        pokemon: list[str],
        team_string: str,
    ) -> int:
        """Persist a drafted team and return its primary key."""
        cur = self._conn.execute(
            """INSERT INTO teams (model_id, tier, format, pokemon, team_string)
               VALUES (?, ?, ?, ?, ?)""",
            (model_id, tier, format_, json.dumps(pokemon), team_string),
        )
        team_id = cur.lastrowid
        assert team_id is not None
        self._conn.commit()
        return team_id

    def save_draft_session(
        self,
        model_id: int,
        tier: str,
        pool_size: int,
        picked: list[str],
        prompt_version: str,
        reasoning: str | None,
    ) -> int:
        """Persist a draft session log and return its primary key."""
        cur = self._conn.execute(
            """INSERT INTO draft_sessions
               (model_id, tier, pool_size, picked, prompt_version, reasoning)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (model_id, tier, pool_size, json.dumps(picked), prompt_version, reasoning),
        )
        session_id = cur.lastrowid
        assert session_id is not None
        self._conn.commit()
        return session_id

    def set_battle_teams(
        self,
        battle_id: int,
        p1_team_id: int | None,
        p2_team_id: int | None,
        tier: str | None,
    ) -> None:
        """Set the drafted team IDs and tier on a battle row."""
        self._conn.execute(
            """UPDATE battles
               SET p1_team_id=?, p2_team_id=?, tier=?
               WHERE id=?""",
            (p1_team_id, p2_team_id, tier, battle_id),
        )
        self._conn.commit()

    def get_battle_teams(
        self, battle_id: int
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return (p1_team_dict, p2_team_dict) for a battle, or (None, None)."""
        row = self._conn.execute(
            "SELECT p1_team_id, p2_team_id FROM battles WHERE id=?",
            (battle_id,),
        ).fetchone()
        if not row:
            return None, None

        def _fetch_team(team_id: int | None) -> dict[str, Any] | None:
            if team_id is None:
                return None
            t = self._conn.execute(
                "SELECT * FROM teams WHERE id=?", (team_id,)
            ).fetchone()
            if not t:
                return None
            d = dict(t)
            try:
                d["pokemon"] = json.loads(d["pokemon"])
            except (json.JSONDecodeError, KeyError):
                pass
            return d

        return _fetch_team(row["p1_team_id"]), _fetch_team(row["p2_team_id"])

    def get_teams_for_model(self, model_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent drafted teams for a model."""
        rows = self._conn.execute(
            """SELECT id, tier, format, pokemon, created_at
               FROM teams WHERE model_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (model_id, limit),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["pokemon"] = json.loads(d["pokemon"])
            except (json.JSONDecodeError, KeyError):
                pass
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Seasons
    # ------------------------------------------------------------------

    def create_season(
        self,
        name: str,
        tier: str,
        fmt: str,
        participants: list[dict[str, Any]],
        rounds: int,
        prompt_version: str,
        total_battles: int,
    ) -> int:
        """Insert a season row and return its id."""
        cur = self._conn.execute(
            """INSERT INTO seasons
               (name, tier, format, participants, rounds, prompt_version, total_battles, status)
               VALUES (?,?,?,?,?,?,?,'pending')""",
            (name, tier, fmt, json.dumps(participants), rounds, prompt_version, total_battles),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def get_season(self, season_id: int) -> dict[str, Any] | None:
        """Return a single season row by id, or None if not found."""
        row = self._conn.execute(
            "SELECT * FROM seasons WHERE id=?", (season_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["participants"] = json.loads(d["participants"])
        except (json.JSONDecodeError, KeyError):
            pass
        return d

    def list_seasons(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent seasons, newest first."""
        rows = self._conn.execute(
            """SELECT s.id, s.name, s.tier, s.format, s.rounds, s.prompt_version,
                      s.total_battles, s.status, s.created_at, s.started_at, s.finished_at,
                      COUNT(b.id) AS battles_done
               FROM seasons s
               LEFT JOIN battles b ON b.season_id = s.id AND b.finished_at IS NOT NULL
               GROUP BY s.id
               ORDER BY s.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            result.append(d)
        return result

    def finish_season(self, season_id: int, status: str = "completed") -> None:
        """Mark a season as finished with the given status."""
        self._conn.execute(
            """UPDATE seasons SET status=?,
               finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (status, season_id),
        )
        self._conn.commit()

    def set_season_running(self, season_id: int) -> None:
        """Mark a season as running and record started_at."""
        self._conn.execute(
            """UPDATE seasons SET status='running',
               started_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (season_id,),
        )
        self._conn.commit()

    def cancel_season(self, season_id: int) -> bool:
        """Mark a season as cancelled. Returns False if already finished or not found."""
        row = self._conn.execute(
            "SELECT status FROM seasons WHERE id=?", (season_id,)
        ).fetchone()
        if not row or row["status"] in ("completed", "cancelled"):
            return False
        self._conn.execute(
            """UPDATE seasons SET status='cancelled',
               finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (season_id,),
        )
        self._conn.commit()
        return True

    def get_season_standings(self, season_id: int) -> list[dict[str, Any]]:
        """Return per-season standings with W/L/T and ELO replayed from 1000.

        ELO is computed fresh from DEFAULT_RATING for each participant using
        only battles that belong to this season, in chronological order.
        """
        from nidozo.db.elo import DEFAULT_RATING, updated_ratings

        season = self.get_season(season_id)
        if season is None:
            return []

        participants: list[dict[str, Any]] = (
            season["participants"] if isinstance(season["participants"], list) else []
        )

        ratings: dict[str, float] = {}
        wins: dict[str, int] = {}
        losses: dict[str, int] = {}
        ties: dict[str, int] = {}

        for p in participants:
            key = f"{p['provider']}/{p['model_name']}"
            ratings[key] = DEFAULT_RATING
            wins[key] = 0
            losses[key] = 0
            ties[key] = 0

        rows = self._conn.execute(
            """SELECT b.winner,
                      p1.provider||'/'||p1.model_name AS p1_key,
                      p2.provider||'/'||p2.model_name AS p2_key
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.season_id = ? AND b.finished_at IS NOT NULL AND b.status = 'completed'
               ORDER BY b.finished_at""",
            (season_id,),
        ).fetchall()

        for row in rows:
            p1_key: str = row["p1_key"]
            p2_key: str = row["p2_key"]
            winner: int | None = row["winner"]

            r1 = ratings.get(p1_key, DEFAULT_RATING)
            r2 = ratings.get(p2_key, DEFAULT_RATING)
            r1_new, r2_new = updated_ratings(r1, r2, winner)
            ratings[p1_key] = r1_new
            ratings[p2_key] = r2_new

            if winner == 1:
                wins[p1_key] = wins.get(p1_key, 0) + 1
                losses[p2_key] = losses.get(p2_key, 0) + 1
            elif winner == 2:
                losses[p1_key] = losses.get(p1_key, 0) + 1
                wins[p2_key] = wins.get(p2_key, 0) + 1
            else:
                ties[p1_key] = ties.get(p1_key, 0) + 1
                ties[p2_key] = ties.get(p2_key, 0) + 1

        standings: list[dict[str, Any]] = []
        for p in participants:
            key = f"{p['provider']}/{p['model_name']}"
            w = wins.get(key, 0)
            lo = losses.get(key, 0)
            t = ties.get(key, 0)
            standings.append({
                "provider":   p["provider"],
                "model_name": p["model_name"],
                "elo":        round(ratings.get(key, DEFAULT_RATING), 1),
                "wins":       w,
                "losses":     lo,
                "ties":       t,
                "games":      w + lo + t,
            })

        standings.sort(key=lambda x: x["elo"], reverse=True)
        for i, s in enumerate(standings):
            s["rank"] = i + 1
        return standings

    def get_season_battles(self, season_id: int) -> list[dict[str, Any]]:
        """Return all battles belonging to a season."""
        rows = self._conn.execute(
            """SELECT b.id, b.battle_tag, b.winner, b.total_turns,
                      b.status, b.started_at, b.finished_at,
                      p1.provider||'/'||p1.model_name AS p1,
                      p2.provider||'/'||p2.model_name AS p2
               FROM battles b
               JOIN models p1 ON p1.id = b.p1_model_id
               JOIN models p2 ON p2.id = b.p2_model_id
               WHERE b.season_id = ?
               ORDER BY b.started_at""",
            (season_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        """Mark the store as closed and release the calling thread's connection.

        After this call, any further access via ``_conn`` raises
        ``sqlite3.ProgrammingError`` regardless of which thread calls it.
        Other threads' open connections are not explicitly closed — the OS
        releases them when those threads exit or the process ends.
        """
        self._closed = True
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
