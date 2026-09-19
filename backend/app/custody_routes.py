"""
CustodyChain — custody_routes.py  (ADDITIVE MODULE)

The evidence-locker API: seal an exhibit, record handoffs, check a circulating
copy against the seal, produce a court report, and run the tamper demo.

Nothing here modifies the existing /evidence/* or /analysis/* routes. The
ad-hoc two-file compare keeps working exactly as before; this router adds the
sealed-exhibit path alongside it.
"""
from __future__ import annotations

import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import analysis, custody
from .custody import EXHIBIT_STORE
from .database import get_conn
from .report_pdf import build_court_report_pdf

router = APIRouter(prefix="/api", tags=["custody"])

DEMO_STORE = EXHIBIT_STORE / "_demo"
DEMO_STORE.mkdir(parents=True, exist_ok=True)


def _safe_name(name: Optional[str], fallback: str = "exhibit.bin") -> str:
    return Path(name or fallback).name or fallback


def _get_exhibit_or_404(conn, exhibit_id: str):
    row = conn.execute(
        "SELECT * FROM exhibits WHERE exhibit_id = ?", (exhibit_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"No exhibit found with ID {exhibit_id}.")
    return row


# --------------------------------------------------------------------------
# A. Seal an exhibit
# --------------------------------------------------------------------------

@router.post("/exhibits/seal")
async def seal_exhibit(
    file: UploadFile = File(...),
    case_id: str = Form(...),
    collected_by: str = Form(...),
    collected_role: str = Form("Collecting officer"),
    collected_place: str = Form(""),
    collected_at: str = Form(""),
    description: str = Form(""),
    exhibit_id: str = Form(""),
):
    """Seal a file at the moment of collection and open its custody trail."""
    if not case_id.strip():
        raise HTTPException(400, "A case ID is required.")
    if not collected_by.strip():
        raise HTTPException(400, "The name of the person collecting is required.")
    if collected_role not in custody.ROLES:
        collected_role = "Other"

    collected_at = collected_at.strip() or custody.now_iso()

    with get_conn() as conn:
        custody.init_custody_schema(conn)

        requested = exhibit_id.strip().upper()
        if requested:
            if not custody.is_wellformed_exhibit_id(requested):
                raise HTTPException(400, "Exhibit ID must look like CC-2026-00042.")
            exists = conn.execute(
                "SELECT 1 FROM exhibits WHERE exhibit_id = ?", (requested,)
            ).fetchone()
            if exists:
                raise HTTPException(409, f"Exhibit {requested} already exists.")
            new_id = requested
        else:
            new_id = custody.generate_exhibit_id(conn)

        original_filename = _safe_name(file.filename)
        dest = EXHIBIT_STORE / f"{new_id}__{original_filename}"

        # Seal and store in a single streaming pass.
        sha256, filesize = await custody.sha256_and_save_upload(file, dest)

        if filesize == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "That file is empty, so there is nothing to seal.")

        conn.execute(
            """INSERT INTO exhibits
               (exhibit_id, case_id, description, collected_by, collected_role,
                collected_place, collected_at, original_filename, filesize,
                sha256, storage_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id, case_id.strip(), description.strip(), collected_by.strip(),
                collected_role, collected_place.strip(), collected_at,
                original_filename, filesize, sha256, str(dest), custody.now_iso(),
            ),
        )

        first_event = custody.append_event(
            conn,
            exhibit_id=new_id,
            actor_name=collected_by,
            actor_role=collected_role,
            action="COLLECTED",
            note=description or None,
            occurred_at=collected_at,
        )

    return {
        "exhibit_id": new_id,
        "case_id": case_id.strip(),
        "seal_number": sha256,
        "algorithm": "SHA-256",
        "original_filename": original_filename,
        "filesize": filesize,
        "collected_at": collected_at,
        "first_event": first_event,
        "limit_note": custody.LIMIT_SENTENCE,
    }


# --------------------------------------------------------------------------
# Listing / detail
# --------------------------------------------------------------------------

@router.get("/exhibits")
def list_exhibits(case_id: Optional[str] = None, q: Optional[str] = None):
    with get_conn() as conn:
        custody.init_custody_schema(conn)
        if case_id:
            rows = conn.execute(
                "SELECT * FROM exhibits WHERE case_id = ? ORDER BY id DESC", (case_id,)
            ).fetchall()
        elif q:
            like = f"%{q.strip()}%"
            rows = conn.execute(
                """SELECT * FROM exhibits
                   WHERE exhibit_id LIKE ? OR case_id LIKE ? OR description LIKE ?
                   ORDER BY id DESC""",
                (like, like, like),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM exhibits ORDER BY id DESC").fetchall()

        out = []
        for r in rows:
            d = custody.exhibit_row_to_dict(r)
            events = custody.load_events(conn, r["exhibit_id"])
            status = custody.verify_chain(events)
            d["event_count"] = len(events)
            d["chain_ok"] = status.ok
            last = custody.latest_check(conn, r["exhibit_id"])
            d["latest_check"] = last
            out.append(d)
        return out


@router.get("/exhibits/{exhibit_id}")
def get_exhibit(exhibit_id: str):
    with get_conn() as conn:
        custody.init_custody_schema(conn)
        row = _get_exhibit_or_404(conn, exhibit_id)
        events = custody.load_events(conn, exhibit_id)
        status = custody.verify_chain(events)
        return {
            "exhibit": custody.exhibit_row_to_dict(row),
            "events": events,
            "chain_ok": status.ok,
            "break_index": status.break_index,
            "chain_reason": status.reason,
            "latest_check": custody.latest_check(conn, exhibit_id),
            "actions": list(custody.ACTIONS),
            "roles": list(custody.ROLES),
            "limit_note": custody.LIMIT_SENTENCE,
        }


# --------------------------------------------------------------------------
# B. Record a handoff / action
# --------------------------------------------------------------------------

@router.post("/exhibits/{exhibit_id}/events")
def add_event(
    exhibit_id: str,
    actor_name: str = Form(...),
    actor_role: str = Form("Other"),
    action: str = Form(...),
    from_person: str = Form(""),
    to_person: str = Form(""),
    note: str = Form(""),
    occurred_at: str = Form(""),
):
    action = (action or "").strip().upper()
    if action not in custody.ACTIONS:
        raise HTTPException(400, f"Action must be one of: {', '.join(custody.ACTIONS)}.")
    if not actor_name.strip():
        raise HTTPException(400, "A name is required for every custody entry.")
    if action in ("TRANSFERRED", "RECEIVED") and not (from_person.strip() or to_person.strip()):
        raise HTTPException(
            400, "A transfer or receipt needs at least a 'from' or a 'to' person."
        )
    if actor_role not in custody.ROLES:
        actor_role = "Other"

    with get_conn() as conn:
        custody.init_custody_schema(conn)
        _get_exhibit_or_404(conn, exhibit_id)
        event = custody.append_event(
            conn,
            exhibit_id=exhibit_id,
            actor_name=actor_name,
            actor_role=actor_role,
            action=action,
            from_person=from_person,
            to_person=to_person,
            note=note,
            occurred_at=occurred_at.strip() or None,
        )
        events = custody.load_events(conn, exhibit_id)
        status = custody.verify_chain(events)

    return {"event": event, "chain_ok": status.ok, "break_index": status.break_index}


# --------------------------------------------------------------------------
# C. Check a copy against the seal
# --------------------------------------------------------------------------

def _write_temp(data: bytes, suffix: str) -> Path:
    fd, path_str = tempfile.mkstemp(suffix=suffix or ".bin")
    path = Path(path_str)
    with open(fd, "wb") as f:
        f.write(data)
    return path


def _run_check(conn, row, copy_filename: str, copy_bytes: bytes, actor_name: str,
               actor_role: str) -> dict:
    """Shared by the upload path and the one-click demo path.

    The seal decides the verdict. The similarity engine is only consulted to
    explain a mismatch that has already been established by the hashes.
    """
    exhibit_id = row["exhibit_id"]
    sealed_hash = row["sha256"]
    copy_sha256 = custody.sha256_file(io.BytesIO(copy_bytes))
    match = copy_sha256 == sealed_hash

    similarity = None
    analysis_payload = None

    if not match:
        sealed_path = Path(row["storage_path"]) if row["storage_path"] else None
        if sealed_path and sealed_path.exists():
            original_bytes = sealed_path.read_bytes()
            kind = analysis.classify_file_kind(row["original_filename"], None)
            tmp_files: list[Path] = []
            original_path = compare_path = None
            try:
                if kind == "image":
                    original_path = _write_temp(original_bytes, sealed_path.suffix)
                    compare_path = _write_temp(copy_bytes, Path(copy_filename).suffix)
                    tmp_files = [original_path, compare_path]
                result = analysis.compare_files(
                    filename=row["original_filename"],
                    mime_type=None,
                    original_bytes=original_bytes,
                    compare_bytes=copy_bytes,
                    original_path=original_path,
                    compare_path=compare_path,
                )
            finally:
                for p in tmp_files:
                    p.unlink(missing_ok=True)

            similarity = result.similarity_pct
            analysis_payload = {
                "kind": result.kind,
                "similarity_pct": result.similarity_pct,
                "headline": result.headline,
                "explanation": result.explanation,
                "diff_ops": [vars(op) for op in result.diff_ops],
                "metadata_comparison": result.metadata_comparison,
                "overlay_png_base64": result.overlay_png_base64,
                "timeline_markers": result.timeline_markers,
                "waveform_profile": result.waveform_profile,
                "note": result.note,
            }

    verdict, summary = custody.verdict_for_check(match, similarity)

    custody.record_check(
        conn, exhibit_id, copy_filename, copy_sha256, match, similarity, verdict, summary
    )
    custody.append_event(
        conn,
        exhibit_id=exhibit_id,
        actor_name=actor_name or "Unnamed checker",
        actor_role=actor_role if actor_role in custody.ROLES else "Other",
        action="SEAL_CHECKED",
        note=f"Result: {'MATCH' if match else 'MISMATCH'} ({copy_filename})",
    )

    events = custody.load_events(conn, exhibit_id)
    status = custody.verify_chain(events)

    return {
        "exhibit_id": exhibit_id,
        "case_id": row["case_id"],
        "copy_filename": copy_filename,
        "sealed_sha256": sealed_hash,
        "copy_sha256": copy_sha256,
        "match": match,
        "result": "MATCH" if match else "MISMATCH",
        "verdict": verdict,
        "summary": summary,
        "similarity_pct": similarity,
        "analysis": analysis_payload,
        "chain_ok": status.ok,
        "events": events,
        "limit_note": custody.LIMIT_SENTENCE,
    }


@router.post("/exhibits/{exhibit_id}/check")
async def check_copy(
    exhibit_id: str,
    file: UploadFile = File(...),
    actor_name: str = Form("Unnamed checker"),
    actor_role: str = Form("Other"),
):
    """Check a circulating copy against a sealed exhibit.

    Retention: the uploaded copy is hashed and compared in memory and is never
    written to the exhibit store. Only its hash and the verdict are kept.
    """
    copy_bytes = await file.read()
    if not copy_bytes:
        raise HTTPException(400, "That file is empty, so there is nothing to check.")

    with get_conn() as conn:
        custody.init_custody_schema(conn)
        row = _get_exhibit_or_404(conn, exhibit_id)
        return _run_check(
            conn, row, _safe_name(file.filename, "copy.bin"), copy_bytes,
            actor_name, actor_role,
        )


# --------------------------------------------------------------------------
# E. Tamper demo
# --------------------------------------------------------------------------

def _altered_bytes(original: bytes) -> bytes:
    """Flip exactly one byte. One byte is the whole point: the seal must break
    on the smallest possible change, not only on an obvious rewrite."""
    if not original:
        return b"\x01"
    buf = bytearray(original)
    idx = len(buf) // 2
    buf[idx] = (buf[idx] + 1) % 256
    return bytes(buf)


@router.post("/exhibits/{exhibit_id}/tamper-copy")
def make_tamper_copy(exhibit_id: str):
    """Create a deliberately altered copy of a sealed exhibit, for demonstration.

    This never touches the sealed original — it writes a separate file into a
    clearly-named demo directory.
    """
    with get_conn() as conn:
        custody.init_custody_schema(conn)
        row = _get_exhibit_or_404(conn, exhibit_id)
        sealed_path = Path(row["storage_path"]) if row["storage_path"] else None
        if not sealed_path or not sealed_path.exists():
            raise HTTPException(
                409, "The sealed file for this exhibit is not stored locally, "
                     "so a demonstration copy cannot be made."
            )
        altered = _altered_bytes(sealed_path.read_bytes())
        name = Path(row["original_filename"])
        out_name = f"{name.stem}__ALTERED-DEMO{name.suffix}"
        out_path = DEMO_STORE / f"{exhibit_id}__{out_name}"
        out_path.write_bytes(altered)

    return {
        "exhibit_id": exhibit_id,
        "filename": out_name,
        "download_url": f"/api/exhibits/{exhibit_id}/tamper-copy/download",
        "altered_sha256": custody.sha256_file(out_path),
        "sealed_sha256": row["sha256"],
        "note": "One byte was changed. Everything else is identical.",
    }


@router.get("/exhibits/{exhibit_id}/tamper-copy/download")
def download_tamper_copy(exhibit_id: str):
    matches = sorted(DEMO_STORE.glob(f"{exhibit_id}__*"))
    if not matches:
        raise HTTPException(404, "No demonstration copy has been created yet.")
    path = matches[-1]
    return FileResponse(
        path, media_type="application/octet-stream",
        filename=path.name.split("__", 1)[-1],
    )


@router.post("/exhibits/{exhibit_id}/demo-check")
def demo_check(exhibit_id: str, variant: str = Form(...),
               actor_name: str = Form("Demonstration"),
               actor_role: str = Form("Analyst")):
    """One-click demo path: check an honest copy (green) or an altered copy (red)
    of the sealed exhibit without the user having to download and re-upload."""
    variant = (variant or "").strip().lower()
    if variant not in ("honest", "altered"):
        raise HTTPException(400, "variant must be 'honest' or 'altered'.")

    with get_conn() as conn:
        custody.init_custody_schema(conn)
        row = _get_exhibit_or_404(conn, exhibit_id)
        sealed_path = Path(row["storage_path"]) if row["storage_path"] else None
        if not sealed_path or not sealed_path.exists():
            raise HTTPException(409, "The sealed file is not stored locally.")

        original = sealed_path.read_bytes()
        name = Path(row["original_filename"])
        if variant == "honest":
            payload, filename = original, f"{name.stem}__COPY{name.suffix}"
        else:
            payload = _altered_bytes(original)
            filename = f"{name.stem}__ALTERED-DEMO{name.suffix}"

        return _run_check(conn, row, filename, payload, actor_name, actor_role)


# --------------------------------------------------------------------------
# D. Court report
# --------------------------------------------------------------------------

@router.get("/exhibits/{exhibit_id}/report.pdf")
def exhibit_report_pdf(exhibit_id: str):
    with get_conn() as conn:
        custody.init_custody_schema(conn)
        row = _get_exhibit_or_404(conn, exhibit_id)
        events = custody.load_events(conn, exhibit_id)
        status = custody.verify_chain(events)
        check = custody.latest_check(conn, exhibit_id)
        exhibit = custody.exhibit_row_to_dict(row)

    pdf = build_court_report_pdf(
        exhibit=exhibit,
        events=events,
        chain_ok=status.ok,
        chain_reason=status.reason,
        latest_check=check,
        limit_sentence=custody.LIMIT_SENTENCE,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="custody_report_{exhibit_id}.pdf"'
        },
    )


# --------------------------------------------------------------------------
# F. Offline sync
# --------------------------------------------------------------------------

@router.get("/sync/export")
def sync_export():
    """Download every exhibit and event as JSON, so a machine that was offline
    can hand its records to another machine later."""
    with get_conn() as conn:
        custody.init_custody_schema(conn)
        exhibits = [
            custody.exhibit_row_to_dict(r)
            for r in conn.execute("SELECT * FROM exhibits ORDER BY id ASC").fetchall()
        ]
        events = [
            custody.load_events(conn, e["exhibit_id"]) for e in exhibits
        ]
    payload = {
        "format": "custodychain.sync.v1",
        "exported_at": custody.now_iso(),
        "exhibits": exhibits,
        "events": [ev for group in events for ev in group],
    }
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition":
                f'attachment; filename="custodychain_sync_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"'
        },
    )


@router.post("/sync/import")
def sync_import(payload: dict = Body(...)):
    """Merge an exported bundle in.

    Events are matched by their event_hash, which makes the merge idempotent:
    re-importing the same bundle changes nothing. Events are inserted in their
    original chain order and an exhibit's incoming events are rejected as a
    group if they would not form an intact chain, so a bad bundle can never
    leave a half-written trail behind.
    """
    if payload.get("format") != "custodychain.sync.v1":
        raise HTTPException(400, "Unrecognised sync file format.")

    added_exhibits = 0
    added_events = 0
    skipped_exhibits: list[str] = []

    incoming_events: dict[str, list[dict]] = {}
    for ev in payload.get("events", []):
        incoming_events.setdefault(ev.get("exhibit_id", ""), []).append(ev)

    with get_conn() as conn:
        custody.init_custody_schema(conn)

        for ex in payload.get("exhibits", []):
            eid = ex.get("exhibit_id")
            if not eid:
                continue
            exists = conn.execute(
                "SELECT 1 FROM exhibits WHERE exhibit_id = ?", (eid,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO exhibits
                       (exhibit_id, case_id, description, collected_by, collected_role,
                        collected_place, collected_at, original_filename, filesize,
                        sha256, storage_path, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                    (
                        eid, ex.get("case_id", ""), ex.get("description", ""),
                        ex.get("collected_by", ""), ex.get("collected_role", "Other"),
                        ex.get("collected_place", ""), ex.get("collected_at", ""),
                        ex.get("original_filename", ""), ex.get("filesize", 0),
                        ex.get("sha256", ""), ex.get("created_at") or custody.now_iso(),
                    ),
                )
                added_exhibits += 1

        for eid, group in incoming_events.items():
            if not eid:
                continue
            existing = custody.load_events(conn, eid)
            known = {e["event_hash"] for e in existing}
            new_ones = [e for e in group if e.get("event_hash") not in known]
            if not new_ones:
                continue

            # Preserve the sender's chain order rather than trusting list order.
            by_prev = {e.get("prev_event_hash"): e for e in new_ones}
            tail = existing[-1]["event_hash"] if existing else custody.GENESIS
            ordered: list[dict] = []
            cursor = tail
            while cursor in by_prev:
                nxt = by_prev.pop(cursor)
                ordered.append(nxt)
                cursor = nxt.get("event_hash")

            if by_prev:
                # Leftovers don't attach to this chain — refuse the whole group.
                skipped_exhibits.append(eid)
                continue

            if not custody.verify_chain(existing + ordered).ok:
                skipped_exhibits.append(eid)
                continue

            for e in ordered:
                conn.execute(
                    """INSERT INTO custody_events
                       (exhibit_id, actor_name, actor_role, action, from_person,
                        to_person, note, occurred_at, prev_event_hash, event_hash,
                        payload_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eid, e.get("actor_name", ""), e.get("actor_role", "Other"),
                        e.get("action", "NOTE"), e.get("from_person"), e.get("to_person"),
                        e.get("note"), e.get("occurred_at", ""),
                        e.get("prev_event_hash", custody.GENESIS), e.get("event_hash", ""),
                        json.dumps(e, sort_keys=True, separators=(",", ":")),
                        custody.now_iso(),
                    ),
                )
                added_events += 1

    return {
        "imported_exhibits": added_exhibits,
        "imported_events": added_events,
        "skipped_exhibits": skipped_exhibits,
        "note": (
            "Exhibits whose incoming events would not form an intact chain were "
            "skipped rather than partially written."
        ),
    }
