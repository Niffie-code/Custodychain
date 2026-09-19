"""SQLite storage for CustodyChain.

Two tables:
  evidence      — one row per uploaded evidence file (its identity + original hash)
  custody_log   — append-only, hash-chained events against an evidence row

The custody_log table is deliberately "append-only" only by convention
of what the API exposes (there's no UPDATE/DELETE endpoint for it) —
the real tamper-evidence comes from the hash chain, not from database
permissions. That's intentional: Day 6 of the brief asks us to edit a
log row directly with raw SQL and prove the chain still catches it.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "custodychain.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id     TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    filename        TEXT NOT NULL,
    mime_type       TEXT,
    byte_count      INTEGER NOT NULL,
    sha256_hash     TEXT NOT NULL,
    blake2b_hash    TEXT NOT NULL,
    officer_id      TEXT NOT NULL,
    officer_name    TEXT NOT NULL,
    collected_at    TEXT NOT NULL,
    device_info     TEXT,
    synced_from_offline INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS custody_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id     TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL,
    entry_hash      TEXT NOT NULL,
    FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_custody_evidence ON custody_log(evidence_id, seq);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def reset_db() -> None:
    """Wipe all data. Used by the synthetic data seeder and tests only."""
    with get_conn() as conn:
        conn.executescript("DROP TABLE IF EXISTS custody_log; DROP TABLE IF EXISTS evidence;")
        conn.executescript(SCHEMA)
