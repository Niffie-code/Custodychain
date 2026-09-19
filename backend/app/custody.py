"""
CustodyChain — custody.py  (ADDITIVE MODULE)

The chain-of-custody product around the existing compare tool.

STORAGE AND RETENTION POLICY (read this before changing anything here):
  - A *sealed exhibit* is the only thing whose bytes we keep. We keep them
    because "check a copy against the seal" and the tamper demo both need
    the original on disk to be useful. They live under backend/exhibits/,
    keyed by exhibit_id, and nowhere else.
  - A *visitor file* submitted to the ad-hoc two-file compare, or as the
    "copy" side of a seal check, is hashed in-flight and NOT retained. The
    only thing that survives the request is its SHA-256 and the verdict row.
  - We never store personal data. Seeds are synthetic (see seed_demo.py).

Why a hash chain and not a blockchain: a single evidence locker has one
writer and one custodian. The property we actually need is "you cannot edit
history without it being obvious", which a hash chain gives us for free.
Distributed consensus solves a problem (mutually distrusting writers) that
this deployment does not have, so we do not pay for it.

The seal is SHA-256 and only SHA-256. hashing.py also computes BLAKE2b at
ingest and we keep recording it for the existing evidence table, but the
custody verdict reduces to one number so a magistrate has one thing to
check, not two.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import hashing

EXHIBIT_STORE = Path(__file__).resolve().parent.parent / "exhibits"
EXHIBIT_STORE.mkdir(exist_ok=True)

GENESIS = hashing.GENESIS_HASH  # 64 zeros, shared with the existing chain

# The plain-language action vocabulary from the brief. Kept as a frozen set so
# a typo in a client can never quietly widen the trail's meaning.
ACTIONS = (
    "COLLECTED", "COPIED", "TRANSFERRED", "RECEIVED", "VIEWED",
    "ANALYSED", "EXPORTED", "RETURNED", "SEAL_CHECKED", "NOTE",
)

ROLES = ("Collecting officer", "Analyst", "Counsel", "Other")

# Exactly the keys the brief specifies, in the order they are hashed.
EVENT_PAYLOAD_KEYS = (
    "exhibit_id", "actor_name", "actor_role", "action",
    "from_person", "to_person", "note", "occurred_at", "prev_event_hash",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS exhibits (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id        TEXT NOT NULL UNIQUE,
    case_id           TEXT NOT NULL,
    description       TEXT,
    collected_by      TEXT NOT NULL,
    collected_role    TEXT NOT NULL,
    collected_place   TEXT,
    collected_at      TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    filesize          INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    storage_path      TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custody_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id      TEXT NOT NULL,
    actor_name      TEXT NOT NULL,
    actor_role      TEXT NOT NULL,
    action          TEXT NOT NULL,
    from_person     TEXT,
    to_person       TEXT,
    note            TEXT,
    occurred_at     TEXT NOT NULL,
    prev_event_hash TEXT NOT NULL,
    event_hash      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (exhibit_id) REFERENCES exhibits(exhibit_id)
);

CREATE TABLE IF NOT EXISTS checks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id       TEXT,
    copy_filename    TEXT NOT NULL,
    copy_sha256      TEXT NOT NULL,
    match            INTEGER NOT NULL,
    similarity_score REAL,
    verdict          TEXT NOT NULL,
    summary          TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_exhibit ON custody_events(exhibit_id, id);
CREATE INDEX IF NOT EXISTS idx_checks_exhibit ON checks(exhibit_id, id);
CREATE INDEX IF NOT EXISTS idx_exhibits_case ON exhibits(case_id);
"""


def init_custody_schema(conn) -> None:
    """Additive. Never drops or alters the existing evidence/custody_log tables."""
    conn.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Exhibit identifiers
# --------------------------------------------------------------------------

_EXHIBIT_RE = re.compile(r"^CC-\d{4}-\d{5}$")


def generate_exhibit_id(conn=None) -> str:
    """CC-YYYY-##### per the brief. Retries on the (unlikely) collision."""
    year = datetime.now(timezone.utc).year
    for _ in range(50):
        candidate = f"CC-{year}-{random.randint(0, 99999):05d}"
        if conn is None:
            return candidate
        row = conn.execute(
            "SELECT 1 FROM exhibits WHERE exhibit_id = ?", (candidate,)
        ).fetchone()
        if row is None:
            return candidate
    raise RuntimeError("Could not allocate a free exhibit ID.")


def is_wellformed_exhibit_id(value: str) -> bool:
    return bool(_EXHIBIT_RE.match(value or ""))


# --------------------------------------------------------------------------
# Streaming SHA-256 (the seal)
# --------------------------------------------------------------------------

def sha256_file(path_or_file, chunk_size: int = hashing.CHUNK_SIZE) -> str:
    """Seal a file by streaming it. Accepts a path or an already-open
    file-like object, so the same helper serves upload handling, the tamper
    demo, and the tests without any of them loading a whole file into RAM."""
    if isinstance(path_or_file, (str, Path)):
        with open(path_or_file, "rb") as fh:
            return hashing.hash_file_object(fh, chunk_size).sha256
    return hashing.hash_file_object(path_or_file, chunk_size).sha256


async def sha256_and_save_upload(upload_file, dest_path: Path) -> tuple[str, int]:
    """Stream an UploadFile to disk while sealing it in one pass."""
    sha = hashlib.sha256()
    total = 0
    with open(dest_path, "wb") as out:
        while True:
            chunk = await upload_file.read(hashing.CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            total += len(chunk)
            out.write(chunk)
    return sha.hexdigest(), total


async def sha256_upload_no_retain(upload_file) -> tuple[str, int]:
    """Seal an uploaded copy without ever writing it to disk. Used for the
    'check a copy' path, where retention policy says the visitor's file must
    not outlive the request."""
    sha = hashlib.sha256()
    total = 0
    while True:
        chunk = await upload_file.read(hashing.CHUNK_SIZE)
        if not chunk:
            break
        sha.update(chunk)
        total += len(chunk)
    return sha.hexdigest(), total


# --------------------------------------------------------------------------
# Hash-chained custody events
# --------------------------------------------------------------------------

def canonical_event_payload(
    exhibit_id: str,
    actor_name: str,
    actor_role: str,
    action: str,
    from_person: Optional[str],
    to_person: Optional[str],
    note: Optional[str],
    occurred_at: str,
    prev_event_hash: str,
) -> dict[str, Any]:
    """Exactly the keys the brief specifies. Empty strings are normalised to
    None so that "field omitted" and "field submitted blank" cannot produce
    two different hashes for what is the same event."""
    def clean(v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

    return {
        "exhibit_id": exhibit_id,
        "actor_name": actor_name.strip(),
        "actor_role": actor_role.strip(),
        "action": action.strip().upper(),
        "from_person": clean(from_person),
        "to_person": clean(to_person),
        "note": clean(note),
        "occurred_at": occurred_at,
        "prev_event_hash": prev_event_hash,
    }


def event_hash(payload: dict) -> str:
    """SHA-256 over canonical JSON (sorted keys, no whitespace variance), so
    the same event always hashes identically on any machine."""
    return hashlib.sha256(
        hashing.canonical_json(payload).encode("utf-8")
    ).hexdigest()


@dataclass
class ChainStatus:
    ok: bool
    break_index: Optional[int]
    reason: Optional[str]


def verify_chain(events: list[dict]) -> ChainStatus:
    """Walk a custody trail in order and confirm every link holds.

    Two independent failures are detected:
      1. a broken pointer — an event claims a previous hash that the previous
         event did not actually produce (something was reordered or deleted);
      2. a broken seal on the event itself — the stored event_hash does not
         match a fresh hash of the event's own content (it was edited in place).

    Returns the index of the first break so the UI can point at the exact row
    rather than saying a useless "invalid".
    """
    expected_prev = GENESIS
    for i, ev in enumerate(events):
        if ev.get("prev_event_hash") != expected_prev:
            return ChainStatus(
                ok=False,
                break_index=i,
                reason=(
                    f"Step {i + 1} does not follow on from the step before it. "
                    "An earlier entry was changed, reordered, or removed."
                ),
            )
        payload = canonical_event_payload(
            exhibit_id=ev.get("exhibit_id", ""),
            actor_name=ev.get("actor_name", ""),
            actor_role=ev.get("actor_role", ""),
            action=ev.get("action", ""),
            from_person=ev.get("from_person"),
            to_person=ev.get("to_person"),
            note=ev.get("note"),
            occurred_at=ev.get("occurred_at", ""),
            prev_event_hash=expected_prev,
        )
        if event_hash(payload) != ev.get("event_hash"):
            return ChainStatus(
                ok=False,
                break_index=i,
                reason=(
                    f"Step {i + 1} was edited after it was recorded. Its stored "
                    "seal no longer matches what the entry now says."
                ),
            )
        expected_prev = ev["event_hash"]
    return ChainStatus(ok=True, break_index=None, reason=None)


def append_event(
    conn,
    exhibit_id: str,
    actor_name: str,
    actor_role: str,
    action: str,
    from_person: Optional[str] = None,
    to_person: Optional[str] = None,
    note: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> dict:
    """Append one event, linked to whatever the current tail of the chain is."""
    action = (action or "").strip().upper()
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'.")

    row = conn.execute(
        "SELECT event_hash FROM custody_events WHERE exhibit_id = ? ORDER BY id DESC LIMIT 1",
        (exhibit_id,),
    ).fetchone()
    prev_hash = row["event_hash"] if row else GENESIS

    payload = canonical_event_payload(
        exhibit_id=exhibit_id,
        actor_name=actor_name,
        actor_role=actor_role,
        action=action,
        from_person=from_person,
        to_person=to_person,
        note=note,
        occurred_at=occurred_at or now_iso(),
        prev_event_hash=prev_hash,
    )
    payload_json = hashing.canonical_json(payload)
    digest = event_hash(payload)

    conn.execute(
        """INSERT INTO custody_events
           (exhibit_id, actor_name, actor_role, action, from_person, to_person,
            note, occurred_at, prev_event_hash, event_hash, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            exhibit_id, payload["actor_name"], payload["actor_role"], payload["action"],
            payload["from_person"], payload["to_person"], payload["note"],
            payload["occurred_at"], prev_hash, digest, payload_json, now_iso(),
        ),
    )
    return {**payload, "event_hash": digest}


def load_events(conn, exhibit_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM custody_events WHERE exhibit_id = ? ORDER BY id ASC",
        (exhibit_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "exhibit_id": r["exhibit_id"],
            "actor_name": r["actor_name"],
            "actor_role": r["actor_role"],
            "action": r["action"],
            "from_person": r["from_person"],
            "to_person": r["to_person"],
            "note": r["note"],
            "occurred_at": r["occurred_at"],
            "prev_event_hash": r["prev_event_hash"],
            "event_hash": r["event_hash"],
        }
        for r in rows
    ]


def exhibit_row_to_dict(row) -> dict:
    return {
        "exhibit_id": row["exhibit_id"],
        "case_id": row["case_id"],
        "description": row["description"],
        "collected_by": row["collected_by"],
        "collected_role": row["collected_role"],
        "collected_place": row["collected_place"],
        "collected_at": row["collected_at"],
        "original_filename": row["original_filename"],
        "filesize": row["filesize"],
        "sha256": row["sha256"],
        "has_file": bool(row["storage_path"]) and Path(row["storage_path"]).exists(),
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------
# Verdict language
#
# One rule governs everything below: SHA-256 is the authority. Similarity is
# only ever an explanation of a mismatch that has already been established.
# A hash mismatch is NEVER described as a match, at any similarity score.
# --------------------------------------------------------------------------

LIMIT_SENTENCE = (
    "This system proves the file was not changed after it was sealed at "
    "collection. It does not prove the file was true before collection."
)

# Above this similarity, a mismatch is *explained* as likely recompression or
# light editing — but it is still reported as a failed seal.
MINOR_DISCREPANCY_THRESHOLD = 85.0


def verdict_for_check(match: bool, similarity: Optional[float]) -> tuple[str, str]:
    """Return (verdict, summary). verdict is 'green' | 'amber' | 'red'."""
    if match:
        return "green", (
            "This copy matches the sealed exhibit. The file has not been "
            "changed since collection."
        )
    if similarity is not None and similarity >= MINOR_DISCREPANCY_THRESHOLD:
        return "amber", (
            "The seal does not match. Bytes are not identical. The content is "
            f"still {similarity:g}% similar to the sealed exhibit, which is "
            "consistent with a resave or recompression rather than a rewrite, "
            "but this is not the sealed file."
        )
    extra = (
        f" The content is only {similarity:g}% similar to the sealed exhibit."
        if similarity is not None else ""
    )
    return "red", (
        "The seal does not match. Bytes are not identical, and the content has "
        "changed substantially since collection." + extra
    )


def record_check(
    conn,
    exhibit_id: Optional[str],
    copy_filename: str,
    copy_sha256: str,
    match: bool,
    similarity: Optional[float],
    verdict: str,
    summary: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO checks
           (exhibit_id, copy_filename, copy_sha256, match, similarity_score,
            verdict, summary, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (exhibit_id, copy_filename, copy_sha256, 1 if match else 0,
         similarity, verdict, summary, now_iso()),
    )
    return cur.lastrowid


def latest_check(conn, exhibit_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM checks WHERE exhibit_id = ? ORDER BY id DESC LIMIT 1",
        (exhibit_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "copy_filename": row["copy_filename"],
        "copy_sha256": row["copy_sha256"],
        "match": bool(row["match"]),
        "similarity_score": row["similarity_score"],
        "verdict": row["verdict"],
        "summary": row["summary"],
        "created_at": row["created_at"],
    }
