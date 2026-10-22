"""SQLite schema, connection helper and transaction helper."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

STATUSES = (
    "new", "drafted", "draft_only", "approved", "post_failed", "verification_pending",
    "replied", "rejected", "skipped", "needs_attention", "already_replied",
)
_status_sql = ",".join(f"'{s}'" for s in STATUSES)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    reviewer_name TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    text TEXT NOT NULL,
    existing_reply TEXT,
    review_date TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ({_status_sql})),
    status_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tone TEXT NOT NULL,
    extra_instructions TEXT NOT NULL DEFAULT '',
    require_approval INTEGER NOT NULL DEFAULT 1 CHECK (require_approval = 1),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reply_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id),
    version INTEGER NOT NULL,
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    source TEXT NOT NULL CHECK (source IN ('ai', 'owner')),
    tone TEXT,
    flagged INTEGER NOT NULL DEFAULT 0,
    flag_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (review_id, version)
);

CREATE TABLE IF NOT EXISTS approval_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id),
    draft_id INTEGER REFERENCES reply_drafts(id),
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'skipped', 'draft_only')),
    reason TEXT,
    created_at TEXT NOT NULL,
    invalidated_at TEXT,
    invalidated_reason TEXT
);
-- Only one live decision per review; older ones are kept as history.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_decision
    ON approval_decisions(review_id) WHERE invalidated_at IS NULL;

CREATE TABLE IF NOT EXISTS posting_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id),
    draft_id INTEGER NOT NULL REFERENCES reply_drafts(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    posted_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'posted', 'failed', 'unknown')),
    attempt_count INTEGER NOT NULL DEFAULT 1,
    dashboard_reply_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Database-level guarantee: a review can have at most one successfully posted reply.
CREATE UNIQUE INDEX IF NOT EXISTS one_posted_per_review
    ON posting_attempts(review_id) WHERE status = 'posted';

CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES posting_attempts(id),
    review_id TEXT NOT NULL REFERENCES reviews(id),
    draft_id INTEGER NOT NULL REFERENCES reply_drafts(id),
    verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
    observed_reply_text TEXT,
    observed_reply_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id),
    old_status TEXT,
    new_status TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO owner_settings (id, tone, extra_instructions, require_approval, updated_at) "
        "VALUES (1, 'professional', '', 1, ?)",
        (now(),),
    )


@contextmanager
def tx(conn: sqlite3.Connection):
    """Explicit write transaction (the connection is in autocommit mode)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
