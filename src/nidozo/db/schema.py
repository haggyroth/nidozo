"""SQLite schema — all CREATE TABLE statements and the migrate() entry point."""

import logging
import sqlite3

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 17

# Table definitions only — safe to run against any DB version via IF NOT EXISTS.
# Indexes are kept separate because they may reference columns (e.g. tournament_id)
# that do not yet exist in old databases.  Those columns are added by the
# version-increment migration blocks below before the indexes are created.
_DDL_TABLES = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- One row per distinct model+prompt configuration
CREATE TABLE IF NOT EXISTS models (
    id             INTEGER PRIMARY KEY,
    provider       TEXT    NOT NULL,  -- "anthropic" | "openai" | "lmstudio" | "random"
    model_name     TEXT    NOT NULL,
    prompt_version TEXT    NOT NULL DEFAULT 'v1',
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Current ELO rating per model (single live row per model)
CREATE TABLE IF NOT EXISTS elo_ratings (
    model_id   INTEGER PRIMARY KEY REFERENCES models(id),
    rating     REAL    NOT NULL DEFAULT 1000.0,
    games      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Round-robin or bracket tournament sessions
CREATE TABLE IF NOT EXISTS tournaments (
    id                INTEGER PRIMARY KEY,
    players           TEXT    NOT NULL,  -- JSON array of {provider, model_name}
    rounds            INTEGER NOT NULL DEFAULT 1,
    prompt_version    TEXT    NOT NULL DEFAULT 'v2',
    total_battles     INTEGER NOT NULL DEFAULT 0,
    tier              TEXT    NOT NULL DEFAULT 'random',  -- 'random' | 'ou' | 'ubers' | ...
    status            TEXT    NOT NULL DEFAULT 'running',  -- running|completed|cancelled
    tournament_format TEXT    NOT NULL DEFAULT 'round_robin',  -- 'round_robin'|'single_elim'|'double_elim'
    bracket_state     TEXT,   -- JSON bracket state (NULL for round_robin)
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at       TEXT
);

-- One row per completed battle
CREATE TABLE IF NOT EXISTS battles (
    id              INTEGER PRIMARY KEY,
    battle_tag      TEXT    NOT NULL UNIQUE,
    format          TEXT    NOT NULL,
    p1_model_id     INTEGER NOT NULL REFERENCES models(id),
    p2_model_id     INTEGER NOT NULL REFERENCES models(id),
    tournament_id   INTEGER REFERENCES tournaments(id),   -- NULL for standalone battles
    p1_team_id      INTEGER REFERENCES teams(id),         -- NULL for random battles
    p2_team_id      INTEGER REFERENCES teams(id),         -- NULL for random battles
    tier            TEXT,                                  -- NULL for random battles
    season_id       INTEGER REFERENCES seasons(id),       -- NULL for non-season battles
    winner          INTEGER,           -- 1=p1, 2=p2, NULL=tie
    total_turns     INTEGER,
    status          TEXT    NOT NULL DEFAULT 'pending',   -- pending|running|completed|cancelled|failed
    started_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at     TEXT,
    narrative       TEXT,   -- LLM-generated post-battle story (NULL until generated)
    p1_personality  TEXT,   -- play-style persona slug for p1 (NULL = no persona)
    p2_personality  TEXT,   -- play-style persona slug for p2 (NULL = no persona)
    team_size       INTEGER NOT NULL DEFAULT 6   -- 3, 4, or 6
);

-- ELO delta per model per battle (for history / audit)
CREATE TABLE IF NOT EXISTS elo_history (
    id            INTEGER PRIMARY KEY,
    battle_id     INTEGER NOT NULL REFERENCES battles(id),
    model_id      INTEGER NOT NULL REFERENCES models(id),
    rating_before REAL    NOT NULL,
    rating_after  REAL    NOT NULL,
    delta         REAL    NOT NULL,
    UNIQUE(battle_id, model_id)  -- prevents double ELO application on retry
);

-- Per-turn log: action chosen, parse outcome, raw LLM response, and full battle state
CREATE TABLE IF NOT EXISTS turns (
    id            INTEGER PRIMARY KEY,
    battle_id     INTEGER NOT NULL REFERENCES battles(id),
    turn_number   INTEGER NOT NULL,
    player_role   TEXT    NOT NULL,   -- "p1" | "p2"
    prompt_version TEXT   NOT NULL,
    action_chosen TEXT,               -- e.g. "move 2" or "switch pikachu"
    parse_success INTEGER NOT NULL DEFAULT 1,  -- 0=fell back to random
    fallback_reason TEXT,                      -- why the turn fell back: 'backend_timeout' | 'backend_error' | 'empty_response' | 'parse_failure' | 'no_legal_move' | NULL
    llm_response  TEXT,                        -- full raw response (may be large)
    state_json    TEXT,                        -- serialized battle state at decision time (v2+)
    coach_advice  TEXT,                        -- free-form advice from the coach model (NULL if no coach)
    prompt_tokens     INTEGER,                 -- tokens spent producing this turn (player + coach); NULL = unknown/local (#225)
    completion_tokens INTEGER                  -- completion tokens for this turn (player + coach); NULL = unknown/local (#225)
);

-- Post-battle lessons: one row per model per battle (LLM-generated reflection)
CREATE TABLE IF NOT EXISTS lessons (
    id         INTEGER PRIMARY KEY,
    model_id   INTEGER NOT NULL REFERENCES models(id),
    battle_id  INTEGER NOT NULL REFERENCES battles(id),
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Drafted teams: one row per drafted team (pre-battle)
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    model_id    INTEGER NOT NULL REFERENCES models(id),
    tier        TEXT    NOT NULL,   -- 'ou' | 'ubers' | 'uu' | 'nu' | 'lc' | 'freeforall'
    format      TEXT    NOT NULL,   -- showdown format string e.g. 'gen3ou'
    pokemon     TEXT    NOT NULL,   -- JSON list of species IDs in pick order
    team_string TEXT    NOT NULL,   -- Showdown export format string
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Named competition seasons — each season is a fixed round-robin among registered participants
CREATE TABLE IF NOT EXISTS seasons (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    tier            TEXT    NOT NULL DEFAULT 'random',
    format          TEXT    NOT NULL DEFAULT 'gen9randombattle',
    participants    TEXT    NOT NULL,  -- JSON [{provider, model_name}]
    rounds          INTEGER NOT NULL DEFAULT 1,
    prompt_version  TEXT    NOT NULL DEFAULT 'v6',
    total_battles   INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending|running|completed|cancelled
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    started_at      TEXT,
    finished_at     TEXT
);

-- Per-model achievement badges earned after each battle
CREATE TABLE IF NOT EXISTS badges (
    id         INTEGER PRIMARY KEY,
    model_id   INTEGER NOT NULL REFERENCES models(id),
    battle_id  INTEGER NOT NULL REFERENCES battles(id),
    slug       TEXT    NOT NULL,
    earned_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(model_id, slug)   -- one lifetime award per badge per model
);

-- Draft session log: one row per pick sequence (for analysis)
CREATE TABLE IF NOT EXISTS draft_sessions (
    id              INTEGER PRIMARY KEY,
    model_id        INTEGER NOT NULL REFERENCES models(id),
    tier            TEXT    NOT NULL,
    pool_size       INTEGER NOT NULL,
    picked          TEXT    NOT NULL,   -- JSON list of picks in order
    prompt_version  TEXT    NOT NULL DEFAULT 'v3',
    reasoning       TEXT,               -- concatenated reasoning from all picks
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Indexes for hot read paths — separate from _DDL_TABLES so they are only
# applied once all required columns are guaranteed to exist.
_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_turns_battle       ON turns(battle_id);
CREATE INDEX IF NOT EXISTS idx_battles_finished   ON battles(finished_at);
CREATE INDEX IF NOT EXISTS idx_battles_tournament ON battles(tournament_id);
CREATE INDEX IF NOT EXISTS idx_battles_p1         ON battles(p1_model_id);
CREATE INDEX IF NOT EXISTS idx_battles_p2         ON battles(p2_model_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_elohist_battle ON elo_history(battle_id, model_id);
CREATE INDEX IF NOT EXISTS idx_lessons_model      ON lessons(model_id, created_at);
CREATE INDEX IF NOT EXISTS idx_battles_season     ON battles(season_id);
CREATE INDEX IF NOT EXISTS idx_badges_model       ON badges(model_id, earned_at);
CREATE INDEX IF NOT EXISTS idx_teams_model        ON teams(model_id, created_at);
-- One row per distinct model configuration. The UNIQUE index also makes
-- get_or_create_model's insert race-safe: a losing racer hits IntegrityError
-- and re-selects the winner's row instead of creating a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS idx_models_identity
    ON models(provider, model_name, prompt_version);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist and upgrade schema version.

    Running _DDL_TABLES is always safe — all statements use IF NOT EXISTS.
    Indexes (_DDL_INDEXES) reference columns added in later migrations, so they
    are applied only after the relevant columns are guaranteed to exist:
    - Fresh installs: all tables include the latest columns, so indexes are safe immediately.
    - Existing DBs: version-increment blocks below add missing columns first,
      then the v4 block creates the indexes.
    """
    conn.executescript(_DDL_TABLES)

    cur = conn.execute("SELECT COUNT(*) FROM schema_version")
    if cur.fetchone()[0] == 0:
        # Fresh install — all columns from latest DDL are present; create indexes now.
        conn.executescript(_DDL_INDEXES)
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        return

    cur = conn.execute("SELECT version FROM schema_version")
    version = cur.fetchone()[0]

    if version < 2:
        # Add state_json column to existing turns table
        try:
            conn.execute("ALTER TABLE turns ADD COLUMN state_json TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("UPDATE schema_version SET version=2")
        conn.commit()

    if version < 3:
        # Add tournaments table and new columns to battles
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id             INTEGER PRIMARY KEY,
                    players        TEXT    NOT NULL,
                    rounds         INTEGER NOT NULL DEFAULT 1,
                    prompt_version TEXT    NOT NULL DEFAULT 'v2',
                    total_battles  INTEGER NOT NULL DEFAULT 0,
                    status         TEXT    NOT NULL DEFAULT 'running',
                    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    finished_at    TEXT
                )
            """)
        except sqlite3.OperationalError:
            pass
        for col, defn in (
            ("tournament_id", "INTEGER REFERENCES tournaments(id)"),
            ("status", "TEXT NOT NULL DEFAULT 'completed'"),
        ):
            try:
                conn.execute(f"ALTER TABLE battles ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("UPDATE schema_version SET version=3")
        conn.commit()

    if version < 4:
        # Add indexes for hot read paths (idempotent via IF NOT EXISTS).
        # All required columns now exist (tournament_id added above in v3 block).
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_turns_battle       ON turns(battle_id);
            CREATE INDEX IF NOT EXISTS idx_battles_finished   ON battles(finished_at);
            CREATE INDEX IF NOT EXISTS idx_battles_tournament ON battles(tournament_id);
            CREATE INDEX IF NOT EXISTS idx_battles_p1         ON battles(p1_model_id);
            CREATE INDEX IF NOT EXISTS idx_battles_p2         ON battles(p2_model_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_elohist_battle ON elo_history(battle_id, model_id);
        """)
        conn.execute("UPDATE schema_version SET version=4")
        conn.commit()

    if version < 5:
        # Add lessons table for post-battle LLM reflections
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id         INTEGER PRIMARY KEY,
                model_id   INTEGER NOT NULL REFERENCES models(id),
                battle_id  INTEGER NOT NULL REFERENCES battles(id),
                content    TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_model ON lessons(model_id, created_at)"
        )
        conn.execute("UPDATE schema_version SET version=5")
        conn.commit()

    if version < 6:
        # Add drafted-team tables and new columns to battles + tournaments
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS teams (
                id          INTEGER PRIMARY KEY,
                model_id    INTEGER NOT NULL REFERENCES models(id),
                tier        TEXT    NOT NULL,
                format      TEXT    NOT NULL,
                pokemon     TEXT    NOT NULL,
                team_string TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS draft_sessions (
                id              INTEGER PRIMARY KEY,
                model_id        INTEGER NOT NULL REFERENCES models(id),
                tier            TEXT    NOT NULL,
                pool_size       INTEGER NOT NULL,
                picked          TEXT    NOT NULL,
                prompt_version  TEXT    NOT NULL DEFAULT 'v3',
                reasoning       TEXT,
                created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
        """)
        for col, defn in (
            ("p1_team_id", "INTEGER REFERENCES teams(id)"),
            ("p2_team_id", "INTEGER REFERENCES teams(id)"),
            ("tier",       "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE battles ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        try:
            conn.execute("ALTER TABLE tournaments ADD COLUMN tier TEXT NOT NULL DEFAULT 'random'")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_teams_model ON teams(model_id, created_at)
        """)
        conn.execute("UPDATE schema_version SET version=6")
        conn.commit()

    if version < 7:
        # Add tournament_format and bracket_state columns for bracket tournament support
        for col, defn in (
            ("tournament_format", "TEXT NOT NULL DEFAULT 'round_robin'"),
            ("bracket_state",     "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE tournaments ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("UPDATE schema_version SET version=7")
        conn.commit()

    if version < 8:
        # Add coach_advice column to turns for multi-agent coach mode
        try:
            conn.execute("ALTER TABLE turns ADD COLUMN coach_advice TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("UPDATE schema_version SET version=8")
        conn.commit()

    if version < 9:
        # Make elo_history(battle_id, model_id) unique to prevent double ELO
        # application if finish_battle is ever called twice for the same battle.
        # SQLite can't add a UNIQUE constraint to an existing table; upgrade the
        # index instead (drop non-unique, create unique).
        conn.execute("DROP INDEX IF EXISTS idx_elohist_battle")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_elohist_battle"
            " ON elo_history(battle_id, model_id)"
        )
        conn.execute("UPDATE schema_version SET version=9")
        conn.commit()

    if version < 10:
        # Add named seasons and tag battles with a season_id.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seasons (
                id              INTEGER PRIMARY KEY,
                name            TEXT    NOT NULL,
                tier            TEXT    NOT NULL DEFAULT 'random',
                format          TEXT    NOT NULL DEFAULT 'gen9randombattle',
                participants    TEXT    NOT NULL,
                rounds          INTEGER NOT NULL DEFAULT 1,
                prompt_version  TEXT    NOT NULL DEFAULT 'v6',
                total_battles   INTEGER NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'pending',
                created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                started_at      TEXT,
                finished_at     TEXT
            )
        """)
        try:
            conn.execute(
                "ALTER TABLE battles ADD COLUMN season_id INTEGER REFERENCES seasons(id)"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_battles_season ON battles(season_id)"
        )
        conn.execute("UPDATE schema_version SET version=10")
        conn.commit()

    if version < 11:
        # Add narrative column to battles for LLM-generated post-battle stories.
        try:
            conn.execute("ALTER TABLE battles ADD COLUMN narrative TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("UPDATE schema_version SET version=11")
        conn.commit()

    if version < 12:
        # Add fallback_reason column to turns to distinguish why a turn fell back
        # to a random move. Values: 'backend_timeout' | 'backend_error' |
        # 'empty_response' | 'parse_failure' | 'no_legal_move' | NULL (success).
        # (Column is plain TEXT, so newer reason strings need no migration.)
        try:
            conn.execute("ALTER TABLE turns ADD COLUMN fallback_reason TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("UPDATE schema_version SET version=12")
        conn.commit()

    if version < 13:
        # Add play-style persona columns so personality is stored per battle and
        # can be correlated with ELO outcomes during analysis.
        for col in ("p1_personality TEXT", "p2_personality TEXT"):
            try:
                conn.execute(f"ALTER TABLE battles ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("UPDATE schema_version SET version=13")
        conn.commit()

    if version < 14:
        # Add badges table for per-model achievement tracking.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS badges (
                id         INTEGER PRIMARY KEY,
                model_id   INTEGER NOT NULL REFERENCES models(id),
                battle_id  INTEGER NOT NULL REFERENCES battles(id),
                slug       TEXT    NOT NULL,
                earned_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(model_id, slug)
            );
            CREATE INDEX IF NOT EXISTS idx_badges_model ON badges(model_id, earned_at);
        """)
        conn.execute("UPDATE schema_version SET version=14")
        conn.commit()

    if version < 15:
        # Add team_size column to battles for audit / filtering by format variant.
        try:
            conn.execute(
                "ALTER TABLE battles ADD COLUMN team_size INTEGER NOT NULL DEFAULT 6"
            )
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("UPDATE schema_version SET version=15")
        conn.commit()

    if version < 16:
        # #209: idx_teams_model was created only in the v6 migration block, so
        # databases born after v6's DDL was folded into _DDL_TABLES never got it
        # on a fresh install. Recreate idempotently here so every DB has it.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_teams_model ON teams(model_id, created_at)"
        )
        # #210: enforce model identity uniqueness so get_or_create_model's
        # non-atomic SELECT-then-INSERT can't create duplicate model rows (which
        # would split a model's ELO/stats across two ids).
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_models_identity"
                " ON models(provider, model_name, prompt_version)"
            )
        except sqlite3.IntegrityError:
            # An older DB created via the racy path may already hold duplicate
            # (provider, model_name, prompt_version) rows, which block a UNIQUE
            # index. Leave it unenforced rather than crash startup — the
            # duplicates are harmless beyond split stats, and fresh DBs get the
            # constraint from _DDL_INDEXES regardless.
            logger.warning(
                "Could not add UNIQUE index on models(provider, model_name, "
                "prompt_version): duplicate rows exist. Dedupe manually to enforce it."
            )
        conn.execute("UPDATE schema_version SET version=16")
        conn.commit()

    if version < 17:
        # #225: per-turn token usage for cost analytics. Nullable — NULL means
        # the turn predates this column or the provider reported no usage (local).
        for col in ("prompt_tokens INTEGER", "completion_tokens INTEGER"):
            try:
                conn.execute(f"ALTER TABLE turns ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("UPDATE schema_version SET version=17")
        conn.commit()
