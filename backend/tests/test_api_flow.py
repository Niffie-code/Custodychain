"""
CustodyChain — tests/test_api_flow.py

Walks the whole product the way a user does: seal an exhibit, hand it over,
check an honest copy (green), check a one-byte-altered copy (red), pull the
court report, and round-trip the offline sync bundle.

Runs against a scratch database so it never touches the demo data.

    python tests/test_api_flow.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import _fastapi_stub  # noqa: E402
_fastapi_stub.install()
from _fastapi_stub import FakeUpload, HTTPException  # noqa: E402

# Point the app at a scratch DB and exhibit store before app modules load.
_TMP = Path(tempfile.mkdtemp(prefix="custodychain_test_"))
import app.database as database  # noqa: E402
database.DB_PATH = _TMP / "test.db"

import app.custody as custody  # noqa: E402
custody.EXHIBIT_STORE = _TMP / "exhibits"
custody.EXHIBIT_STORE.mkdir(parents=True, exist_ok=True)

import app.custody_routes as routes  # noqa: E402
routes.EXHIBIT_STORE = custody.EXHIBIT_STORE
routes.DEMO_STORE = custody.EXHIBIT_STORE / "_demo"
routes.DEMO_STORE.mkdir(parents=True, exist_ok=True)

from app.database import get_conn, init_db  # noqa: E402

NOTICE = b"PUBLIC NOTICE\nWard 6 water works.\nNotice period: 14 days.\n" * 20

results = {"passed": 0, "failed": 0}


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
        results["passed"] += 1
    else:
        print(f"  FAIL  {label} {detail}")
        results["failed"] += 1


async def main():
    init_db()
    with get_conn() as conn:
        custody.init_custody_schema(conn)

    # ---------------- A. Seal ----------------
    sealed = await routes.seal_exhibit(
        file=FakeUpload("ward6_notice.txt", NOTICE),
        case_id="CASE-2026-099",
        collected_by="A. Bello",
        collected_role="Collecting officer",
        collected_place="Ward 6 noticeboard",
        collected_at="",
        description="Notice photographed at the board",
        exhibit_id="",
    )
    exhibit_id = sealed["exhibit_id"]
    check("seal returns a well-formed exhibit ID",
          custody.is_wellformed_exhibit_id(exhibit_id), exhibit_id)
    check("seal number is a 64-char SHA-256", len(sealed["seal_number"]) == 64)
    check("seal records the file size", sealed["filesize"] == len(NOTICE))
    check("seal surfaces the honest limit", "does not prove" in sealed["limit_note"])

    detail = routes.get_exhibit(exhibit_id)
    check("first event is COLLECTED", detail["events"][0]["action"] == "COLLECTED")
    check("first event links to genesis",
          detail["events"][0]["prev_event_hash"] == custody.GENESIS)
    check("chain intact after sealing", detail["chain_ok"] is True)

    # ---------------- Duplicate ID is refused ----------------
    try:
        await routes.seal_exhibit(
            file=FakeUpload("x.txt", b"data"), case_id="C", collected_by="B",
            collected_role="Analyst", collected_place="", collected_at="",
            description="", exhibit_id=exhibit_id,
        )
        check("duplicate exhibit ID is refused", False, "no error raised")
    except HTTPException as e:
        check("duplicate exhibit ID is refused", e.status_code == 409)

    # ---------------- Empty file is refused ----------------
    try:
        await routes.seal_exhibit(
            file=FakeUpload("empty.txt", b""), case_id="C", collected_by="B",
            collected_role="Analyst", collected_place="", collected_at="",
            description="", exhibit_id="",
        )
        check("empty file is refused", False, "no error raised")
    except HTTPException as e:
        check("empty file is refused", e.status_code == 400)

    # ---------------- B. Handoff ----------------
    routes.add_event(
        exhibit_id, actor_name="A. Bello", actor_role="Collecting officer",
        action="TRANSFERRED", from_person="A. Bello", to_person="C. Okafor",
        note="Handed to the analyst", occurred_at="",
    )
    routes.add_event(
        exhibit_id, actor_name="C. Okafor", actor_role="Analyst",
        action="RECEIVED", from_person="A. Bello", to_person="C. Okafor",
        note="", occurred_at="",
    )
    detail = routes.get_exhibit(exhibit_id)
    check("handoff events are recorded", len(detail["events"]) == 3)
    check("chain still intact after handoffs", detail["chain_ok"] is True)
    check("each event links to the previous",
          detail["events"][2]["prev_event_hash"] == detail["events"][1]["event_hash"])

    try:
        routes.add_event(exhibit_id, actor_name="X", actor_role="Analyst",
                         action="TELEPORTED", from_person="", to_person="",
                         note="", occurred_at="")
        check("unknown action is refused", False, "no error raised")
    except HTTPException as e:
        check("unknown action is refused", e.status_code == 400)

    try:
        routes.add_event(exhibit_id, actor_name="X", actor_role="Analyst",
                         action="TRANSFERRED", from_person="", to_person="",
                         note="", occurred_at="")
        check("transfer without from/to is refused", False, "no error raised")
    except HTTPException as e:
        check("transfer without from/to is refused", e.status_code == 400)

    # ---------------- C. Check an honest copy (green) ----------------
    honest = await routes.check_copy(
        exhibit_id, file=FakeUpload("ward6_notice_COPY.txt", NOTICE),
        actor_name="C. Okafor", actor_role="Analyst",
    )
    check("honest copy matches the seal", honest["match"] is True)
    check("honest copy is green", honest["verdict"] == "green")
    check("honest copy result reads UNCHANGED", honest["result"] == "MATCH")
    check("honest copy hash equals sealed hash",
          honest["copy_sha256"] == sealed["seal_number"])

    # ---------------- C. Check an altered copy (red) ----------------
    altered = bytearray(NOTICE)
    altered[len(altered) // 2] = (altered[len(altered) // 2] + 1) % 256
    changed = await routes.check_copy(
        exhibit_id, file=FakeUpload("ward6_notice_LEAKED.txt", bytes(altered)),
        actor_name="C. Okafor", actor_role="Analyst",
    )
    check("one-byte change breaks the seal", changed["match"] is False)
    check("mismatch is never called a match",
          "does not match" in changed["summary"].lower())
    check("mismatch still reports similarity for explanation",
          changed["similarity_pct"] is not None)
    check("similarity does not override the seal",
          changed["verdict"] in ("amber", "red"))

    detail = routes.get_exhibit(exhibit_id)
    seal_checks = [e for e in detail["events"] if e["action"] == "SEAL_CHECKED"]
    check("every check appends a SEAL_CHECKED event", len(seal_checks) == 2)
    check("chain intact after checks", detail["chain_ok"] is True)
    check("latest check is recorded on the exhibit",
          detail["latest_check"] is not None and detail["latest_check"]["match"] is False)

    # ---------------- E. Tamper demo ----------------
    demo_green = routes.demo_check(exhibit_id, variant="honest",
                                   actor_name="Demo", actor_role="Analyst")
    demo_red = routes.demo_check(exhibit_id, variant="altered",
                                 actor_name="Demo", actor_role="Analyst")
    check("one-click honest demo is green", demo_green["verdict"] == "green")
    check("one-click altered demo is red", demo_red["verdict"] == "red")
    check("altered demo hash differs from the seal",
          demo_red["copy_sha256"] != demo_red["sealed_sha256"])

    tamper = routes.make_tamper_copy(exhibit_id)
    check("tamper copy has a different seal",
          tamper["altered_sha256"] != tamper["sealed_sha256"])
    sealed_path = custody.EXHIBIT_STORE / f"{exhibit_id}__ward6_notice.txt"
    check("the sealed original is never modified",
          custody.sha256_file(sealed_path) == sealed["seal_number"])

    # ---------------- F. Offline sync (on an intact chain) ----------------
    import json
    export = routes.sync_export()
    bundle = json.loads(export.body)
    check("export contains the exhibit",
          any(e["exhibit_id"] == exhibit_id for e in bundle["exhibits"]))
    check("export contains the events", len(bundle["events"]) >= 5)

    reimport = routes.sync_import(bundle)
    check("re-importing the same bundle is idempotent",
          reimport["imported_exhibits"] == 0 and reimport["imported_events"] == 0)

    # Import into a clean database, as a second machine would.
    primary_db = database.DB_PATH
    database.DB_PATH = _TMP / "second.db"
    init_db()
    with get_conn() as conn:
        custody.init_custody_schema(conn)
    fresh = routes.sync_import(bundle)
    check("bundle imports into an empty locker",
          fresh["imported_exhibits"] == 1 and fresh["imported_events"] >= 5,
          str(fresh))
    moved = routes.get_exhibit(exhibit_id)
    check("imported exhibit keeps its seal number",
          moved["exhibit"]["sha256"] == sealed["seal_number"])
    check("imported events keep their order",
          [e["action"] for e in moved["events"]][:3]
          == ["COLLECTED", "TRANSFERRED", "RECEIVED"],
          str([e["action"] for e in moved["events"]]))
    check("imported chain verifies on the second machine",
          moved["chain_ok"] is True)

    # A bundle whose chain has been tampered with must be refused outright.
    forged = json.loads(export.body)
    for ev in forged["events"]:
        if ev["action"] == "TRANSFERRED":
            ev["to_person"] = "Someone Else"
    database.DB_PATH = _TMP / "third.db"
    init_db()
    with get_conn() as conn:
        custody.init_custody_schema(conn)
    refused = routes.sync_import(forged)
    check("a tampered sync bundle is refused, not partly written",
          exhibit_id in refused["skipped_exhibits"] and refused["imported_events"] == 0,
          str(refused))

    database.DB_PATH = primary_db

    # ---------------- Broken chain is detected and located ----------------
    with get_conn() as conn:
        conn.execute(
            "UPDATE custody_events SET to_person = ? WHERE exhibit_id = ? AND action = 'TRANSFERRED'",
            ("Someone Else", exhibit_id),
        )
    tampered_detail = routes.get_exhibit(exhibit_id)
    check("editing the log with raw SQL breaks the chain",
          tampered_detail["chain_ok"] is False)
    check("the break is located", tampered_detail["break_index"] == 1)
    check("the break has a plain-language reason",
          bool(tampered_detail["chain_reason"]))

    # ---------------- D. Court report ----------------
    resp = routes.exhibit_report_pdf(exhibit_id)
    check("report is a PDF", resp.body[:5] == b"%PDF-")
    check("report has content", len(resp.body) > 1500)
    check("report downloads with a sensible name",
          exhibit_id in resp.headers.get("Content-Disposition", ""))

    # ---------------- Unknown exhibit ----------------
    try:
        routes.get_exhibit("CC-2026-00000")
        check("unknown exhibit returns a clear error", False, "no error raised")
    except HTTPException as e:
        check("unknown exhibit returns a clear error", e.status_code == 404)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{results['passed']} passed, {results['failed']} failed")
    sys.exit(1 if results["failed"] else 0)
