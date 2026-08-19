"""Tests for the SQLite schema DDL and migrate() upgrade paths.

All tests use in-memory or tmp-path SQLite databases — no disk state survives.
"""

from __future__ import annotations

import sqlite3

from nidozo.db.schema import SCHEMA_VERSION, migrate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with Row factory for dict-like access."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT version FROM schema_version").fetchone()["version"]


# ---------------------------------------------------------------------------
# Fresh install tests
# ---------------------------------------------------------------------------

def test_fresh_install_creates_all_tables() -> None:
    """migrate() on a brand-new DB creates all required tables."""
    conn = _fresh_conn()
    migrate(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for expected in ("models", "elo_ratings", "battles", "turns", "elo_history", "tournaments"):
        assert expected in tables, f"Missing table: {expected}"


def test_fresh_install_schema_version_is_current() -> None:
    """After a fresh install, schema_version equals SCHEMA_VERSION."""
    conn = _fresh_conn()
    migrate(conn)

    assert _version(conn) == SCHEMA_VERSION


def test_fresh_install_creates_indexes() -> None:
    """All six performance indexes are created on a fresh install."""
    conn = _fresh_conn()
    migrate(conn)

    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }

    for idx in (
        "idx_turns_battle",
        "idx_battles_finished",
        "idx_battles_tournament",
        "idx_battles_p1",
        "idx_battles_p2",
        "idx_elohist_battle",
    ):
        assert idx in indexes, f"Missing index: {idx}"


# ---------------------------------------------------------------------------
# Idempotency test
# ---------------------------------------------------------------------------

def test_migrate_twice_is_idempotent() -> None:
    """Calling migrate() twice on the same DB raises no errors and version stays the same."""
    conn = _fresh_conn()
    migrate(conn)
    migrate(conn)  # should be a no-op

    assert _version(conn) == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# v1 → current migration
# ---------------------------------------------------------------------------

def _build_v1_db() -> sqlite3.Connection:
    """Create a minimal v1 schema (no state_json, no tournaments, no indexes)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (1);

        CREATE TABLE models (
            id             INTEGER PRIMARY KEY,
            provider       TEXT    NOT NULL,
            model_name     TEXT    NOT NULL,
            prompt_version TEXT    NOT NULL DEFAULT 'v1',
            created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE elo_ratings (
            model_id   INTEGER PRIMARY KEY REFERENCES models(id),
            rating     REAL    NOT NULL DEFAULT 1000.0,
            games      INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE battles (
            id              INTEGER PRIMARY KEY,
            battle_tag      TEXT    NOT NULL UNIQUE,
            format          TEXT    NOT NULL,
            p1_model_id     INTEGER NOT NULL REFERENCES models(id),
            p2_model_id     INTEGER NOT NULL REFERENCES models(id),
            winner          INTEGER,
            total_turns     INTEGER,
            started_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            finished_at     TEXT
        );

        CREATE TABLE elo_history (
            id            INTEGER PRIMARY KEY,
            battle_id     INTEGER NOT NULL REFERENCES battles(id),
            model_id      INTEGER NOT NULL REFERENCES models(id),
            rating_before REAL    NOT NULL,
            rating_after  REAL    NOT NULL,
            delta         REAL    NOT NULL
        );

        CREATE TABLE turns (
            id            INTEGER PRIMARY KEY,
            battle_id     INTEGER NOT NULL REFERENCES battles(id),
            turn_number   INTEGER NOT NULL,
            player_role   TEXT    NOT NULL,
            prompt_version TEXT   NOT NULL,
            action_chosen TEXT,
            parse_success INTEGER NOT NULL DEFAULT 1,
            llm_response  TEXT
        );
    """)
    return conn


def test_migrate_from_v1_to_current() -> None:
    """migrate() on a v1 DB upgrades to SCHEMA_VERSION without errors."""
    conn = _build_v1_db()
    migrate(conn)

    assert _version(conn) == SCHEMA_VERSION


def test_migrate_from_v1_adds_state_json_column() -> None:
    """After migration, turns table has the state_json column (added in v2)."""
    conn = _build_v1_db()
    migrate(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
    assert "state_json" in cols


def test_migrate_from_v1_adds_tournaments_table() -> None:
    """After migration, tournaments table exists (added in v3)."""
    conn = _build_v1_db()
    migrate(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "tournaments" in tables


def test_migrate_from_v1_adds_indexes() -> None:
    """After migration, all six indexes are present (added in v4)."""
    conn = _build_v1_db()
    migrate(conn)

    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }

    for idx in (
        "idx_turns_battle",
        "idx_battles_finished",
        "idx_battles_tournament",
        "idx_battles_p1",
        "idx_battles_p2",
        "idx_elohist_battle",
    ):
        assert idx in indexes, f"Missing index after migration from v1: {idx}"


# ---------------------------------------------------------------------------
# v3 → v4 migration (only indexes added)
# ---------------------------------------------------------------------------

def _build_v3_db() -> sqlite3.Connection:
    """Build a v3 DB (all tables and columns, but no indexes)."""
    conn = _build_v1_db()
    # Apply v2 changes
    conn.execute("ALTER TABLE turns ADD COLUMN state_json TEXT")
    # Apply v3 changes
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id             INTEGER PRIMARY KEY,
            players        TEXT    NOT NULL,
            rounds         INTEGER NOT NULL DEFAULT 1,
            prompt_version TEXT    NOT NULL DEFAULT 'v2',
            total_battles  INTEGER NOT NULL DEFAULT 0,
            status         TEXT    NOT NULL DEFAULT 'running',
            created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            finished_at    TEXT
        );
    """)
    try:
        conn.execute("ALTER TABLE battles ADD COLUMN tournament_id INTEGER REFERENCES tournaments(id)")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE battles ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")
    except Exception:
        pass
    conn.execute("UPDATE schema_version SET version=3")
    conn.commit()
    return conn


def test_migrate_from_v3_to_v4_adds_indexes() -> None:
    """migrate() on a v3 DB adds all six indexes and bumps version to 4."""
    conn = _build_v3_db()
    assert _version(conn) == 3

    migrate(conn)

    assert _version(conn) == SCHEMA_VERSION

    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    for idx in (
        "idx_turns_battle",
        "idx_battles_finished",
        "idx_battles_tournament",
        "idx_battles_p1",
        "idx_battles_p2",
        "idx_elohist_battle",
    ):
        assert idx in indexes, f"Missing index after v3→v4 migration: {idx}"


def test_migrate_from_v3_preserves_existing_data() -> None:
    """migrate() does not destroy existing rows during v3→v4 upgrade."""
    conn = _build_v3_db()
    # Insert a model row before migration
    conn.execute(
        "INSERT INTO models (provider, model_name, prompt_version) VALUES ('random','random','v1')"
    )
    conn.commit()

    migrate(conn)

    count = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# New coverage tests — missing lines
# ---------------------------------------------------------------------------

def test_migrate_idempotent_tables_already_exist() -> None:
    """Running migrate() on an already-migrated DB is safe (CREATE TABLE IF NOT EXISTS)."""
    conn = _fresh_conn()
    migrate(conn)
    # Second call should not raise
    migrate(conn)
    assert _version(conn) == SCHEMA_VERSION


def test_migrate_idempotent_indexes_already_exist() -> None:
    """CREATE INDEX IF NOT EXISTS makes index creation idempotent."""
    conn = _fresh_conn()
    migrate(conn)
    # Manually create an index that would normally be in the migration
    # Then migrate again — should not raise
    migrate(conn)
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    assert "idx_turns_battle" in indexes


def test_migrate_from_v6_adds_bracket_columns() -> None:
    """Migrating from v6 adds tournament_format and bracket_state columns."""
    # Fresh install gives us full schema; downgrade to v6
    conn = _fresh_conn()
    migrate(conn)
    # Simulate a v6 DB by rolling back the bracket columns
    conn.execute("UPDATE schema_version SET version=6")
    try:
        conn.execute("ALTER TABLE tournaments DROP COLUMN tournament_format")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tournaments DROP COLUMN bracket_state")
    except sqlite3.OperationalError:
        pass
    conn.commit()

    migrate(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()}
    assert "tournament_format" in cols
    assert "bracket_state" in cols


def test_migrate_v1_state_json_already_exists_no_error() -> None:
    """Lines 166-167: OperationalError is silenced when state_json already exists.

    Build a v1 DB but pre-add state_json to turns, leave version=1.
    Calling migrate() must not raise even though ALTER TABLE would fail.
    """
    conn = _build_v1_db()
    # Pre-add the column that v2 migration would add
    conn.execute("ALTER TABLE turns ADD COLUMN state_json TEXT")
    conn.commit()
    # migrate() should detect version < 2, try ALTER TABLE, catch OperationalError, and continue
    migrate(conn)
    assert _version(conn) == SCHEMA_VERSION


def test_migrate_v2_battle_columns_already_exist_no_error() -> None:
    """Lines 194-195: OperationalError is silenced when tournament_id/status already exist.

    Build a v1 DB, apply v2 manually (add state_json, set version=2),
    pre-add tournament_id and status columns to battles, then call migrate().
    """
    conn = _build_v1_db()
    # Apply v2 changes manually
    conn.execute("ALTER TABLE turns ADD COLUMN state_json TEXT")
    conn.execute("UPDATE schema_version SET version=2")
    conn.commit()
    # Pre-add the columns that v3 migration would add — triggers OperationalError path
    conn.execute("ALTER TABLE battles ADD COLUMN tournament_id INTEGER")
    conn.execute("ALTER TABLE battles ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'")
    conn.commit()
    # migrate() should detect version < 3, try ALTERs, catch OperationalErrors, and continue
    migrate(conn)
    assert _version(conn) == SCHEMA_VERSION


def test_migrate_v9_creates_unique_elo_history_index() -> None:
    """v9 migration replaces the non-unique idx_elohist_battle with a UNIQUE index."""
    conn = _fresh_conn()
    migrate(conn)
    # PRAGMA index_list returns (seq, name, unique, origin, partial)
    indexes = {
        (row[1], bool(row[2]))  # (name, is_unique)
        for row in conn.execute("PRAGMA index_list(elo_history)").fetchall()
    }
    assert ("idx_elohist_battle", True) in indexes, (
        "idx_elohist_battle should be a UNIQUE index after v9 migration"
    )


def test_migrate_v9_idempotent() -> None:
    """Running migrate() twice does not fail even though DROP INDEX fires on v9."""
    conn = _fresh_conn()
    migrate(conn)
    conn.execute("UPDATE schema_version SET version=8")
    conn.commit()
    migrate(conn)  # re-runs v9 block — DROP INDEX IF EXISTS + CREATE UNIQUE INDEX
    assert _version(conn) == SCHEMA_VERSION


def test_finish_battle_idempotent_elo(tmp_path) -> None:
    """Calling finish_battle twice for the same battle must not apply ELO twice."""
    from nidozo.db.store import BattleStore

    store = BattleStore(db_path=tmp_path / "test.db")
    conn = store._conn

    p1 = store.get_or_create_model("random", "bot1", "v1")
    p2 = store.get_or_create_model("random", "bot2", "v1")
    bid = store.create_battle("tag-idem", "gen3randombattle", p1, p2)

    store.finish_battle(bid, winner=1, total_turns=10)
    r1_after_first = conn.execute(
        "SELECT rating FROM elo_ratings WHERE model_id=?", (p1,)
    ).fetchone()["rating"]

    # Second call — should be a no-op
    store.finish_battle(bid, winner=1, total_turns=10)
    r1_after_second = conn.execute(
        "SELECT rating FROM elo_ratings WHERE model_id=?", (p1,)
    ).fetchone()["rating"]

    assert r1_after_first == r1_after_second, (
        "ELO changed on second finish_battle call — double-apply regression"
    )

    history_rows = conn.execute(
        "SELECT COUNT(*) FROM elo_history WHERE battle_id=? AND model_id=?", (bid, p1)
    ).fetchone()[0]
    assert history_rows == 1, "elo_history should have exactly one row per (battle, model)"


def test_migration_v9_to_v10_adds_seasons_table(tmp_path) -> None:
    """A v9 database gains the seasons table and season_id on battles after migrate()."""
    import sqlite3

    from nidozo.db.schema import migrate

    db_path = tmp_path / "v9.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Replicate a v9 schema (tables without seasons / season_id)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (9);
        CREATE TABLE models (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v1',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE battles (
            id INTEGER PRIMARY KEY,
            battle_tag TEXT NOT NULL UNIQUE,
            format TEXT NOT NULL,
            p1_model_id INTEGER NOT NULL REFERENCES models(id),
            p2_model_id INTEGER NOT NULL REFERENCES models(id),
            winner INTEGER,
            total_turns INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            finished_at TEXT
        );
        CREATE TABLE elo_ratings (
            model_id INTEGER PRIMARY KEY REFERENCES models(id),
            rating REAL NOT NULL DEFAULT 1000.0,
            games INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE elo_history (
            id INTEGER PRIMARY KEY,
            battle_id INTEGER NOT NULL REFERENCES battles(id),
            model_id INTEGER NOT NULL REFERENCES models(id),
            rating_before REAL NOT NULL,
            rating_after REAL NOT NULL,
            delta REAL NOT NULL
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY,
            battle_id INTEGER NOT NULL REFERENCES battles(id),
            turn_number INTEGER NOT NULL,
            player_role TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            action_chosen TEXT,
            parse_success INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY,
            players TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE lessons (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            battle_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            tier TEXT NOT NULL,
            format TEXT NOT NULL,
            pokemon TEXT NOT NULL,
            team_string TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE draft_sessions (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            tier TEXT NOT NULL,
            pool_size INTEGER NOT NULL,
            picked TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v3',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE UNIQUE INDEX idx_elohist_battle ON elo_history(battle_id, model_id);
    """)
    conn.commit()

    migrate(conn)
    conn.close()

    # Re-open and verify
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row

    # seasons table must exist
    tables = {r[0] for r in conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "seasons" in tables, "seasons table not created by v10 migration"

    # season_id column must exist on battles
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(battles)").fetchall()}
    assert "season_id" in cols, "season_id column not added to battles by v10 migration"

    # schema version must be current (v10 migration runs through to latest)
    version = conn2.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION

    conn2.close()


def test_migration_v10_to_v11_adds_narrative_column(tmp_path) -> None:
    """A v10 database gains the narrative column on battles after migrate()."""
    import sqlite3

    db_path = tmp_path / "v10.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Minimal v10 schema — battles table without narrative column
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (10);
        CREATE TABLE models (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v1',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE elo_ratings (
            model_id INTEGER PRIMARY KEY,
            rating REAL NOT NULL DEFAULT 1000.0,
            games INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE tournaments (
            id INTEGER PRIMARY KEY,
            players TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 1,
            prompt_version TEXT NOT NULL DEFAULT 'v2',
            total_battles INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            finished_at TEXT
        );
        CREATE TABLE seasons (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'random',
            format TEXT NOT NULL DEFAULT 'gen3randombattle',
            participants TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 1,
            prompt_version TEXT NOT NULL DEFAULT 'v4',
            total_battles INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            started_at TEXT,
            finished_at TEXT
        );
        CREATE TABLE battles (
            id INTEGER PRIMARY KEY,
            battle_tag TEXT NOT NULL UNIQUE,
            format TEXT NOT NULL,
            p1_model_id INTEGER NOT NULL,
            p2_model_id INTEGER NOT NULL,
            tournament_id INTEGER,
            p1_team_id INTEGER,
            p2_team_id INTEGER,
            tier TEXT,
            season_id INTEGER,
            winner INTEGER,
            total_turns INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            finished_at TEXT
        );
        CREATE TABLE elo_history (
            id INTEGER PRIMARY KEY,
            battle_id INTEGER NOT NULL,
            model_id INTEGER NOT NULL,
            rating_before REAL NOT NULL,
            rating_after REAL NOT NULL,
            delta REAL NOT NULL
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY,
            battle_id INTEGER NOT NULL,
            turn_number INTEGER NOT NULL,
            player_role TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            action_chosen TEXT,
            parse_success INTEGER NOT NULL DEFAULT 1,
            llm_response TEXT,
            state_json TEXT,
            coach_advice TEXT
        );
        CREATE TABLE lessons (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            battle_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            tier TEXT NOT NULL,
            format TEXT NOT NULL,
            pokemon TEXT NOT NULL,
            team_string TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE draft_sessions (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            tier TEXT NOT NULL,
            pool_size INTEGER NOT NULL,
            picked TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v3',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE UNIQUE INDEX idx_elohist_battle ON elo_history(battle_id, model_id);
    """)
    conn.commit()
    conn.close()

    # Re-open and migrate
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    migrate(conn2)
    conn2.close()

    # Verify
    conn3 = sqlite3.connect(str(db_path))
    conn3.row_factory = sqlite3.Row

    cols = {r[1] for r in conn3.execute("PRAGMA table_info(battles)").fetchall()}
    assert "narrative" in cols, "narrative column not added to battles by v11 migration"

    version = conn3.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION

    conn3.close()


def test_migration_v11_narrative_column_already_exists(tmp_path) -> None:
    """v11 migration must not fail if narrative column already exists."""
    import sqlite3

    db_path = tmp_path / "v10b.db"
    conn = sqlite3.connect(str(db_path))
    # Fresh install — narrative column is present from the start
    migrate(conn)

    # Simulate re-running from v10
    conn.execute("UPDATE schema_version SET version=10")
    conn.commit()

    # Should silently skip the duplicate ALTER TABLE
    migrate(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    conn.close()


def test_migration_v11_to_v12_adds_fallback_reason_column(tmp_path) -> None:
    """A v11 database gains the fallback_reason column on turns after migrate()."""
    import sqlite3

    db_path = tmp_path / "v11.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Minimal v11 schema — turns table without fallback_reason
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (11);
        CREATE TABLE models (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v1',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE elo_ratings (
            model_id INTEGER PRIMARY KEY,
            rating REAL NOT NULL DEFAULT 1000.0,
            games INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE battles (
            id INTEGER PRIMARY KEY,
            battle_tag TEXT NOT NULL UNIQUE,
            format TEXT NOT NULL,
            p1_model_id INTEGER NOT NULL,
            p2_model_id INTEGER NOT NULL,
            winner INTEGER,
            total_turns INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            finished_at TEXT,
            narrative TEXT
        );
        CREATE TABLE elo_history (
            id INTEGER PRIMARY KEY,
            battle_id INTEGER NOT NULL,
            model_id INTEGER NOT NULL,
            rating_before REAL NOT NULL,
            rating_after REAL NOT NULL,
            delta REAL NOT NULL
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY,
            battle_id INTEGER NOT NULL,
            turn_number INTEGER NOT NULL,
            player_role TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            action_chosen TEXT,
            parse_success INTEGER NOT NULL DEFAULT 1,
            llm_response TEXT,
            state_json TEXT,
            coach_advice TEXT
        );
        CREATE TABLE tournaments (id INTEGER PRIMARY KEY, players TEXT NOT NULL);
        CREATE TABLE seasons (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            participants TEXT NOT NULL);
        CREATE TABLE lessons (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL,
            battle_id INTEGER NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        CREATE TABLE teams (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL,
            tier TEXT NOT NULL, format TEXT NOT NULL, pokemon TEXT NOT NULL,
            team_string TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        CREATE TABLE draft_sessions (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL,
            tier TEXT NOT NULL, pool_size INTEGER NOT NULL, picked TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v3',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        CREATE UNIQUE INDEX idx_elohist_battle ON elo_history(battle_id, model_id);
    """)
    conn.commit()
    conn.close()

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    migrate(conn2)
    conn2.close()

    conn3 = sqlite3.connect(str(db_path))
    conn3.row_factory = sqlite3.Row
    cols = {r[1] for r in conn3.execute("PRAGMA table_info(turns)").fetchall()}
    assert "fallback_reason" in cols, "fallback_reason column not added by v12 migration"

    version = conn3.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    conn3.close()


def test_migration_v12_fallback_reason_already_exists(tmp_path) -> None:
    """v12 migration must not fail if fallback_reason column already exists."""
    import sqlite3

    db_path = tmp_path / "v12_idem.db"
    conn = sqlite3.connect(str(db_path))
    migrate(conn)  # fresh install — fallback_reason present from the start

    # Simulate re-running from v11
    conn.execute("UPDATE schema_version SET version=11")
    conn.commit()

    migrate(conn)  # should silently skip the duplicate ALTER TABLE
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    conn.close()


# ---------------------------------------------------------------------------
# v16 — model identity uniqueness (#210) + teams index on fresh installs (#209)
# ---------------------------------------------------------------------------

def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_fresh_install_has_teams_index_and_model_uniqueness() -> None:
    """A fresh DB must carry idx_teams_model and the UNIQUE model-identity index."""
    conn = _fresh_conn()
    migrate(conn)
    names = _index_names(conn)
    assert "idx_teams_model" in names, "idx_teams_model missing on fresh install (#209)"
    assert "idx_models_identity" in names, "idx_models_identity missing on fresh install (#210)"
    # Confirm the identity index is actually UNIQUE.
    unique = conn.execute(
        "SELECT \"unique\" FROM pragma_index_list('models') WHERE name='idx_models_identity'"
    ).fetchone()
    assert unique is not None and unique[0] == 1
    conn.close()


def test_get_or_create_model_is_deduplicated(tmp_path) -> None:
    """Same (provider, model_name, prompt_version) always returns one id and row."""
    from nidozo.db.store import BattleStore

    store = BattleStore(db_path=tmp_path / "dedup.db")
    a = store.get_or_create_model("anthropic", "claude-x", "v9")
    b = store.get_or_create_model("anthropic", "claude-x", "v9")
    assert a == b
    # A different prompt version is a distinct model.
    c = store.get_or_create_model("anthropic", "claude-x", "v8")
    assert c != a
    count = store._conn.execute(
        "SELECT COUNT(*) FROM models WHERE provider='anthropic' AND model_name='claude-x'"
    ).fetchone()[0]
    assert count == 2, "expected exactly two rows (one per prompt version)"
    store.close()


def test_direct_duplicate_model_insert_is_rejected(tmp_path) -> None:
    """The UNIQUE index rejects a raw duplicate identity insert."""
    from nidozo.db.store import BattleStore

    store = BattleStore(db_path=tmp_path / "uniq.db")
    store.get_or_create_model("openai", "gpt-4o", "v9")
    try:
        store._conn.execute(
            "INSERT INTO models (provider, model_name, prompt_version) VALUES (?,?,?)",
            ("openai", "gpt-4o", "v9"),
        )
        store._conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        store._conn.rollback()
        raised = True
    assert raised, "duplicate model identity should violate idx_models_identity"
    store.close()


def test_migration_v15_to_v16_adds_indexes(tmp_path) -> None:
    """A v15 DB gains idx_teams_model and idx_models_identity after migrate()."""
    db_path = tmp_path / "v15.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    migrate(conn)  # fresh → current
    # Roll back to v15 and drop the two indexes to simulate an older DB.
    conn.execute("DROP INDEX IF EXISTS idx_teams_model")
    conn.execute("DROP INDEX IF EXISTS idx_models_identity")
    conn.execute("UPDATE schema_version SET version=15")
    conn.commit()

    migrate(conn)  # re-run v16 block
    assert _version(conn) == SCHEMA_VERSION
    names = _index_names(conn)
    assert "idx_teams_model" in names
    assert "idx_models_identity" in names
    conn.close()


# ---------------------------------------------------------------------------
# v20 — Glicko-2 (#231)
# ---------------------------------------------------------------------------

def _pre_glicko_conn() -> sqlite3.Connection:
    """A v19 database: the elo tables as they stood before Glicko-2."""
    conn = _fresh_conn()
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (19);
        CREATE TABLE models (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v1'
        );
        CREATE TABLE elo_ratings (
            model_id   INTEGER PRIMARY KEY REFERENCES models(id),
            rating     REAL    NOT NULL DEFAULT 1000.0,
            games      INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE TABLE elo_history (
            id            INTEGER PRIMARY KEY,
            battle_id     INTEGER NOT NULL,
            model_id      INTEGER NOT NULL,
            rating_before REAL    NOT NULL,
            rating_after  REAL    NOT NULL,
            delta         REAL    NOT NULL,
            UNIQUE(battle_id, model_id)
        );
        INSERT INTO models (id, provider, model_name) VALUES (1, 'anthropic', 'veteran');
        INSERT INTO elo_ratings (model_id, rating, games) VALUES (1, 1337.5, 42);
        INSERT INTO elo_history (battle_id, model_id, rating_before, rating_after, delta)
            VALUES (7, 1, 1300.0, 1337.5, 37.5);
    """)
    return conn


def test_migrate_v19_adds_glicko_columns() -> None:
    conn = _pre_glicko_conn()
    migrate(conn)

    rating_cols = {r["name"] for r in conn.execute("PRAGMA table_info(elo_ratings)")}
    assert {"rd", "volatility"} <= rating_cols

    history_cols = {r["name"] for r in conn.execute("PRAGMA table_info(elo_history)")}
    assert {"rd_before", "rd_after"} <= history_cols

    assert _version(conn) == SCHEMA_VERSION


def test_migrate_v19_backfills_rd_to_the_unplayed_prior() -> None:
    """Existing ratings survive; their uncertainty backfills to the wide prior.

    A rating carried over from plain Elo has no measured uncertainty, so the
    honest backfill is the unplayed-model RD — the model re-converges as it plays.
    """
    conn = _pre_glicko_conn()
    migrate(conn)

    row = conn.execute("SELECT rating, rd, volatility, games FROM elo_ratings WHERE model_id=1").fetchone()
    assert row["rating"] == 1337.5   # preserved exactly
    assert row["games"] == 42        # preserved exactly
    assert row["rd"] == 350.0
    assert row["volatility"] == 0.06


def test_migrate_v19_leaves_pre_glicko_history_rd_null() -> None:
    """Old history rows get NULL RD rather than a fabricated number."""
    conn = _pre_glicko_conn()
    migrate(conn)

    row = conn.execute("SELECT rating_before, rd_before, rd_after FROM elo_history WHERE battle_id=7").fetchone()
    assert row["rating_before"] == 1300.0
    assert row["rd_before"] is None
    assert row["rd_after"] is None


def test_migrate_v19_glicko_columns_already_exist_no_error() -> None:
    """Re-running the v20 block over an already-migrated DB is a no-op."""
    conn = _pre_glicko_conn()
    migrate(conn)
    conn.execute("UPDATE schema_version SET version=19")  # force the block to re-run
    migrate(conn)

    assert _version(conn) == SCHEMA_VERSION


def test_fresh_install_has_glicko_columns() -> None:
    conn = _fresh_conn()
    migrate(conn)

    rating_cols = {r["name"] for r in conn.execute("PRAGMA table_info(elo_ratings)")}
    assert {"rating", "rd", "volatility", "games"} <= rating_cols
