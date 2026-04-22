"""SQLite storage for the governance module.

One DB per company at `<company>/governance/governance.sqlite`.
Pragmas set on every connection open:

    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;
    PRAGMA busy_timeout = 5000;
    PRAGMA synchronous = NORMAL;

A `schema_meta` table records the schema version and `migrate()` is the
single entry point for future phases to add tables (holds, requests,
agent_autonomy, etc.). Phase 1 has no migrations; `migrate()` is a stub
that does nothing beyond ensuring the Phase 1 tables exist.

All write helpers open a transaction with `BEGIN IMMEDIATE` to acquire
the write lock up front. This is overkill for Phase 1 concurrency but
builds the habit for later phases with real contention, and costs
essentially nothing now.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

import json
import logging

from core.governance.models import DecisionRecord, TrustSnapshot


GOVERNANCE_SUBDIR = "governance"
DB_FILENAME = "governance.sqlite"
CURRENT_SCHEMA_VERSION = 1

_logger = logging.getLogger("governance.storage")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def governance_dir(company_dir: Path) -> Path:
    return company_dir / GOVERNANCE_SUBDIR


def db_path(company_dir: Path) -> Path:
    return governance_dir(company_dir) / DB_FILENAME


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def _apply_pragmas(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA foreign_keys = ON")
    cur.execute("PRAGMA busy_timeout = 5000")
    cur.execute("PRAGMA synchronous = NORMAL")
    cur.close()


def open_db(company_dir: Path) -> sqlite3.Connection:
    """Open (and create on first call) the governance DB for a company.

    Pragmas are applied every time so WAL mode survives re-opens.
    Schema and Phase 1 tables are created if missing. Safe to call
    from any request handler."""
    path = db_path(company_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None gives us manual transaction control so we can
    # use BEGIN IMMEDIATE explicitly. autocommit-style commits otherwise.
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    _ensure_schema(conn)
    return conn


@contextlib.contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    """Run a write-intended block with BEGIN IMMEDIATE so the writer
    lock is acquired up front. Prevents deadlocks when two concurrent
    write-intended sessions both start with reads."""
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA_META_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_TRUST_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS trust_snapshots (
    agent_id TEXT NOT NULL,
    score REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    last_sample_at TEXT,
    computed_at TEXT NOT NULL,
    breakdown_json TEXT NOT NULL,
    PRIMARY KEY (agent_id, computed_at)
);
"""

_TRUST_SNAPSHOTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_trust_agent
    ON trust_snapshots (agent_id, computed_at DESC);
"""

_DECISIONS_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('human', 'agent')),
    agent_id TEXT,
    action_type TEXT NOT NULL,
    action_summary TEXT,
    outcome TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    founder_trigger_route TEXT,
    job_id TEXT,
    notes TEXT
);
"""

_DECISIONS_INDEX_AGENT_SQL = """
CREATE INDEX IF NOT EXISTS idx_decisions_agent
    ON decisions (agent_id, decided_at DESC);
"""

_DECISIONS_INDEX_SOURCE_SQL = """
CREATE INDEX IF NOT EXISTS idx_decisions_source
    ON decisions (source, decided_at DESC);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: creates schema_meta, Phase 1 tables, indexes. Seeds
    schema_meta.version if missing. Calls `migrate()` to bring the DB
    forward if an older version is recorded."""
    with write_transaction(conn) as cur:
        cur.execute(_SCHEMA_META_SQL)
        cur.execute(_TRUST_SNAPSHOTS_SQL)
        cur.execute(_TRUST_SNAPSHOTS_INDEX_SQL)
        cur.execute(_DECISIONS_SQL)
        cur.execute(_DECISIONS_INDEX_AGENT_SQL)
        cur.execute(_DECISIONS_INDEX_SOURCE_SQL)
        cur.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """Migration entry point. Phase 1 has no migrations, but this
    stub exists so later phases can add tables through a controlled
    path instead of ad-hoc schema edits scattered across the codebase.

    Implementation contract for the future:
      - read current version from schema_meta
      - if < CURRENT_SCHEMA_VERSION, run each intermediate migration
        in a write_transaction
      - update schema_meta.version on success
    """
    cur = conn.cursor()
    cur.execute("SELECT value FROM schema_meta WHERE key = 'version'")
    row = cur.fetchone()
    current = int(row[0]) if row else 0
    cur.close()
    if current < CURRENT_SCHEMA_VERSION:
        # No migrations needed in Phase 1; upcoming phases will populate
        # this block. For now just ensure the version reflects the
        # current schema.
        with write_transaction(conn) as w:
            w.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'version'",
                (str(CURRENT_SCHEMA_VERSION),),
            )


# ---------------------------------------------------------------------------
# Trust snapshot helpers
# ---------------------------------------------------------------------------
def persist_trust_snapshot(conn: sqlite3.Connection, snap: TrustSnapshot) -> None:
    with write_transaction(conn) as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO trust_snapshots
                (agent_id, score, sample_count, last_sample_at, computed_at, breakdown_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snap.agent_id,
                float(snap.score),
                int(snap.sample_count),
                snap.last_sample_at,
                snap.computed_at,
                json.dumps(snap.breakdown, sort_keys=True),
            ),
        )


def persist_trust_snapshot_if_stale(
    conn: sqlite3.Connection,
    snap: TrustSnapshot,
    *,
    min_interval_seconds: int = 60,
) -> bool:
    """Persist `snap` only if the most recent stored row for this agent
    is older than `min_interval_seconds`. Returns True when a row was
    written, False when skipped as too recent.

    Motivation: the Phase 1 aggregator runs on every `/governance` page
    load. Without this guard the `trust_snapshots` table accumulates a
    new row per agent per page hit, so a few minutes of active use can
    produce thousands of near-duplicate rows. The PK is
    `(agent_id, computed_at)` so duplicates are legal but useless.
    """
    import datetime as _dt

    cur = conn.cursor()
    cur.execute(
        "SELECT computed_at FROM trust_snapshots "
        "WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1",
        (snap.agent_id,),
    )
    row = cur.fetchone()
    cur.close()
    if row is not None and row["computed_at"]:
        try:
            prev_raw = row["computed_at"]
            if prev_raw.endswith("Z"):
                prev_raw = prev_raw[:-1] + "+00:00"
            prev = _dt.datetime.fromisoformat(prev_raw)
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=_dt.timezone.utc)
            cur_raw = snap.computed_at
            if cur_raw.endswith("Z"):
                cur_raw = cur_raw[:-1] + "+00:00"
            now_dt = _dt.datetime.fromisoformat(cur_raw)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=_dt.timezone.utc)
            if (now_dt - prev).total_seconds() < float(min_interval_seconds):
                return False
        except ValueError:
            # Unparseable stored timestamp: fall through and write a fresh
            # snapshot so the table self-heals rather than locks up.
            pass
    persist_trust_snapshot(conn, snap)
    return True


def latest_trust_snapshot(conn: sqlite3.Connection, agent_id: str) -> TrustSnapshot | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT agent_id, score, sample_count, last_sample_at, computed_at, breakdown_json
        FROM trust_snapshots
        WHERE agent_id = ?
        ORDER BY computed_at DESC
        LIMIT 1
        """,
        (agent_id,),
    )
    row = cur.fetchone()
    cur.close()
    if row is None:
        return None
    return TrustSnapshot(
        agent_id=row["agent_id"],
        score=float(row["score"]),
        sample_count=int(row["sample_count"]),
        last_sample_at=row["last_sample_at"],
        computed_at=row["computed_at"],
        breakdown=json.loads(row["breakdown_json"] or "{}"),
    )


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------
def persist_decision(conn: sqlite3.Connection, rec: DecisionRecord) -> None:
    with write_transaction(conn) as cur:
        cur.execute(
            """
            INSERT INTO decisions
                (decision_id, source, agent_id, action_type, action_summary,
                 outcome, decided_at, founder_trigger_route, job_id, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.decision_id,
                rec.source,
                rec.agent_id,
                rec.action_type,
                rec.action_summary,
                rec.outcome,
                rec.decided_at,
                rec.founder_trigger_route,
                rec.job_id,
                rec.notes,
            ),
        )


def recent_decisions(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DecisionRecord]:
    """Return up to `limit` recent decisions, newest first. Optional
    `source` filter ("human" or "agent")."""
    cur = conn.cursor()
    if source:
        cur.execute(
            """
            SELECT decision_id, source, agent_id, action_type, action_summary,
                   outcome, decided_at, founder_trigger_route, job_id, notes
            FROM decisions
            WHERE source = ?
            ORDER BY decided_at DESC
            LIMIT ? OFFSET ?
            """,
            (source, int(limit), int(offset)),
        )
    else:
        cur.execute(
            """
            SELECT decision_id, source, agent_id, action_type, action_summary,
                   outcome, decided_at, founder_trigger_route, job_id, notes
            FROM decisions
            ORDER BY decided_at DESC
            LIMIT ? OFFSET ?
            """,
            (int(limit), int(offset)),
        )
    out = [
        DecisionRecord(
            decision_id=r["decision_id"],
            source=r["source"],
            agent_id=r["agent_id"],
            action_type=r["action_type"],
            action_summary=r["action_summary"] or "",
            outcome=r["outcome"],
            decided_at=r["decided_at"],
            founder_trigger_route=r["founder_trigger_route"],
            job_id=r["job_id"],
            notes=r["notes"] or "",
        )
        for r in cur.fetchall()
    ]
    cur.close()
    return out


def most_recent_decision_at(conn: sqlite3.Connection) -> str | None:
    """Timestamp of the most recent decision row of any kind. Used to
    surface `last_successful_retrolog_write` on the UI."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(decided_at) AS ts FROM decisions")
    row = cur.fetchone()
    cur.close()
    if row is None:
        return None
    return row["ts"]
