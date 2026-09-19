"""
CustodyChain — Track H: Media & Civic Trust

A forensic evidence-integrity service: cryptographic hashing, a
hash-chained (tamper-evident) chain-of-custody log, and a plain-English
verification report for non-technical stakeholders (judges/magistrates).

Run with:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import hashing
from .database import get_conn, init_db
from .metadata import extract_metadata

EVIDENCE_STORE = Path(__file__).resolve().parent.parent / "evidence_store"
EVIDENCE_STORE.mkdir(exist_ok=True)

app = FastAPI(
    title="CustodyChain API",
    description="Track H: Media & Civic Trust — cryptographic evidence integrity",
    version="1.0.0",
)

# Field officers and judicial dashboard run on separate dev servers
# (Vite on :5173, or a plain static server) — see frontend/README.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo-only; see README "Honest Limits"
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Additive: creates the exhibits / custody_events / checks tables if they
    # are not there yet. Never touches the existing evidence / custody_log
    # tables, so an older database keeps working untouched.
    from .custody import init_custody_schema
    with get_conn() as conn:
        init_custody_schema(conn)


# --- Addition for the public verify flow: new endpoints only, see ---
# --- analysis_routes.py. Does not alter any route defined above. ---
from .analysis_routes import router as analysis_router  # noqa: E402

app.include_router(analysis_router)

# --- Chain-of-custody product (seal / handoff / check / report / sync). ---
# --- Additive router under /api. Existing routes are unchanged. ---
from .custody_routes import router as custody_router  # noqa: E402

app.include_router(custody_router)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class CustodyEventIn(BaseModel):
    event_type: str  # VIEWED | TRANSFERRED | EXPORTED
    actor: str
    note: Optional[str] = None


class OfflineQueueItem(BaseModel):
    case_id: str
    officer_id: str
    officer_name: str
    filename: str
    sha256_hash: str
    blake2b_hash: str
    byte_count: int
    collected_at: str  # client-device timestamp, captured while offline


class OfflineQueueBatch(BaseModel):
    items: list[OfflineQueueItem]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _row_to_evidence_dict(row) -> dict:
    return {
        "evidence_id": row["evidence_id"],
        "case_id": row["case_id"],
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "byte_count": row["byte_count"],
        "sha256_hash": row["sha256_hash"],
        "blake2b_hash": row["blake2b_hash"],
        "officer_id": row["officer_id"],
        "officer_name": row["officer_name"],
        "collected_at": row["collected_at"],
        "device_info": row["device_info"],
        "synced_from_offline": bool(row["synced_from_offline"]),
    }


def _append_custody_entry(conn, evidence_id: str, event_type: str, actor: str, metadata: dict) -> dict:
    cur = conn.execute(
        "SELECT entry_hash, seq FROM custody_log WHERE evidence_id = ? ORDER BY seq DESC LIMIT 1",
        (evidence_id,),
    )
    last = cur.fetchone()
    prev_hash = last["entry_hash"] if last else hashing.GENESIS_HASH
    seq = (last["seq"] + 1) if last else 0
    timestamp = now_iso()

    event_fields = {
        "evidence_id": evidence_id,
        "seq": seq,
        "event_type": event_type,
        "actor": actor,
        "timestamp": timestamp,
        "metadata": metadata,
    }
    entry_hash = hashing.compute_entry_hash(prev_hash, event_fields)

    conn.execute(
        """INSERT INTO custody_log
           (evidence_id, seq, event_type, actor, timestamp, metadata, prev_hash, entry_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (evidence_id, seq, event_type, actor, timestamp, hashing.canonical_json(metadata), prev_hash, entry_hash),
    )
    return {**event_fields, "prev_hash": prev_hash, "entry_hash": entry_hash}


def _load_custody_chain(conn, evidence_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM custody_log WHERE evidence_id = ? ORDER BY seq ASC", (evidence_id,)
    ).fetchall()
    entries = []
    for r in rows:
        entries.append({
            "evidence_id": r["evidence_id"],
            "seq": r["seq"],
            "event_type": r["event_type"],
            "actor": r["actor"],
            "timestamp": r["timestamp"],
            "metadata": json.loads(r["metadata"]),
            "prev_hash": r["prev_hash"],
            "entry_hash": r["entry_hash"],
        })
    return entries


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {"status": "online", "message": "CustodyChain API is running"}


@app.get("/health")
def health():
    return {"status": "ok", "time": now_iso()}


@app.post("/evidence/upload")
async def upload_evidence(
    case_id: str = Form(...),
    officer_id: str = Form(...),
    officer_name: str = Form(...),
    file: UploadFile = File(...),
):
    evidence_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "evidence.bin").name
    dest_path = EVIDENCE_STORE / f"{evidence_id}__{safe_name}"

    digest = await hashing.hash_and_save_upload(file, dest_path)

    if digest.byte_count == 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(400, "Uploaded file is empty.")

    device_info = extract_metadata(dest_path)
    collected_at = now_iso()

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO evidence
               (evidence_id, case_id, filename, mime_type, byte_count, sha256_hash,
                blake2b_hash, officer_id, officer_name, collected_at, device_info,
                synced_from_offline)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                evidence_id, case_id, safe_name, file.content_type, digest.byte_count,
                digest.sha256, digest.blake2b, officer_id, officer_name,
                collected_at, hashing.canonical_json(device_info),
            ),
        )
        entry = _append_custody_entry(
            conn, evidence_id, "COLLECTED", officer_name,
            {"case_id": case_id, "filename": safe_name, "byte_count": digest.byte_count},
        )

    return {
        "evidence_id": evidence_id,
        "sha256_hash": digest.sha256,
        "blake2b_hash": digest.blake2b,
        "byte_count": digest.byte_count,
        "device_info": device_info,
        "custody_entry": entry,
    }


@app.get("/evidence")
def list_evidence(case_id: Optional[str] = None):
    with get_conn() as conn:
        if case_id:
            rows = conn.execute("SELECT * FROM evidence WHERE case_id = ? ORDER BY collected_at DESC", (case_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM evidence ORDER BY collected_at DESC").fetchall()
        return [_row_to_evidence_dict(r) for r in rows]


@app.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Evidence not found.")
        return _row_to_evidence_dict(row)


@app.post("/evidence/{evidence_id}/log")
def add_custody_event(evidence_id: str, event: CustodyEventIn):
    with get_conn() as conn:
        row = conn.execute("SELECT evidence_id FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Evidence not found.")
        if event.event_type not in {"VIEWED", "TRANSFERRED", "EXPORTED"}:
            raise HTTPException(400, "event_type must be VIEWED, TRANSFERRED, or EXPORTED.")
        entry = _append_custody_entry(
            conn, evidence_id, event.event_type, event.actor, {"note": event.note} if event.note else {},
        )
        return entry


@app.post("/evidence/{evidence_id}/verify")
async def verify_evidence(evidence_id: str, actor: str = Form(...), file: UploadFile = File(...)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Evidence not found.")

        digest = hashing.hash_file_object(file.file)
        original_hash = row["sha256_hash"]
        is_authentic = digest.sha256 == original_hash

        entry = _append_custody_entry(
            conn, evidence_id,
            "VERIFIED" if is_authentic else "VERIFICATION_FAILED",
            actor,
            {
                "presented_sha256": digest.sha256,
                "original_sha256": original_hash,
                "match": is_authentic,
            },
        )

        return {
            "status": "AUTHENTIC" if is_authentic else "TAMPERED",
            "original_sha256": original_hash,
            "presented_sha256": digest.sha256,
            "custody_entry": entry,
        }


@app.get("/evidence/{evidence_id}/custody")
def get_custody_chain(evidence_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT evidence_id FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Evidence not found.")
        entries = _load_custody_chain(conn, evidence_id)
        result = hashing.verify_chain(entries)
        return {
            "evidence_id": evidence_id,
            "chain_intact": result.intact,
            "broken_at_index": result.broken_at_index,
            "reason": result.reason,
            "entries": entries,
        }


@app.get("/evidence/{evidence_id}/report")
def get_judicial_report(evidence_id: str):
    """Plain-English report: everything a judge needs, nothing they don't."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Evidence not found.")
        entries = _load_custody_chain(conn, evidence_id)
        chain_result = hashing.verify_chain(entries)

        latest_verification = next(
            (e for e in reversed(entries) if e["event_type"] in ("VERIFIED", "VERIFICATION_FAILED")),
            None,
        )
        if not chain_result.intact:
            headline = "INTEGRITY COMPROMISED"
            explanation = f"The custody log itself has been altered. {chain_result.reason}"
        elif latest_verification is None:
            headline = "NOT YET VERIFIED"
            explanation = "This evidence has a recorded chain of custody but has not been re-checked against its original hash yet."
        elif latest_verification["event_type"] == "VERIFIED":
            headline = "AUTHENTIC"
            explanation = "The file's cryptographic fingerprint matches the fingerprint recorded at the moment it was collected. No bytes have changed."
        else:
            headline = "TAMPERED"
            explanation = "The file presented for verification does NOT match the fingerprint recorded at collection. At least one byte has changed since collection."

        timeline = [
            {
                "step": e["seq"] + 1,
                "event": e["event_type"].replace("_", " ").title(),
                "by": e["actor"],
                "when": e["timestamp"],
            }
            for e in entries
        ]

        return {
            "evidence_id": evidence_id,
            "case_id": row["case_id"],
            "filename": row["filename"],
            "collected_by": row["officer_name"],
            "collected_at": row["collected_at"],
            "status": headline,
            "explanation": explanation,
            "algorithm": "SHA-256",
            "custody_timeline": timeline,
            "chain_intact": chain_result.intact,
        }


@app.get("/merkle/root")
def get_merkle_root():
    """A single hash that commits to every evidence file's hash at once."""
    with get_conn() as conn:
        rows = conn.execute("SELECT evidence_id, sha256_hash FROM evidence ORDER BY evidence_id ASC").fetchall()
        leaf_hashes = [r["sha256_hash"] for r in rows]
        root = hashing.merkle_root(leaf_hashes)
        return {
            "merkle_root": root,
            "leaf_count": len(leaf_hashes),
            "evidence_ids_in_order": [r["evidence_id"] for r in rows],
        }


@app.post("/sync/offline-queue")
def sync_offline_queue(batch: OfflineQueueBatch):
    """Accept evidence that was hashed on-device while offline (Rule 06).

    The device already computed the hash before connectivity returned,
    so we trust the client-supplied hash here rather than re-deriving
    it from file bytes (the file itself may sync later, out of band).
    """
    synced = []
    with get_conn() as conn:
        for item in batch.items:
            evidence_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO evidence
                   (evidence_id, case_id, filename, mime_type, byte_count, sha256_hash,
                    blake2b_hash, officer_id, officer_name, collected_at, device_info,
                    synced_from_offline)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, '{}', 1)""",
                (
                    evidence_id, item.case_id, item.filename, item.byte_count,
                    item.sha256_hash, item.blake2b_hash, item.officer_id,
                    item.officer_name, item.collected_at,
                ),
            )
            _append_custody_entry(
                conn, evidence_id, "COLLECTED_OFFLINE", item.officer_name,
                {"case_id": item.case_id, "filename": item.filename, "synced_at": now_iso()},
            )
            synced.append(evidence_id)
    return {"synced_count": len(synced), "evidence_ids": synced}
