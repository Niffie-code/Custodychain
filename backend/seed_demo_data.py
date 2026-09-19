"""
Seed the CustodyChain database with synthetic evidence + custody history
for demos, using Faker so no real names, cases, or files are ever used.

Usage:
    cd backend
    python seed_demo_data.py
"""
import io
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from faker import Faker
from starlette.datastructures import UploadFile

import asyncio
from app.database import reset_db, get_conn, init_db
from app import hashing
from app.main import _append_custody_entry, EVIDENCE_STORE, now_iso

fake = Faker()
Faker.seed(42)
random.seed(42)

N_CASES = 3
FILES_PER_CASE = 2


def make_fake_file_bytes(kind: str) -> bytes:
    """Generate deterministic-looking synthetic 'evidence' content — plain
    bytes standing in for a bodycam clip / photo / PDF report. We never
    use real media; the hash chain doesn't care what the bytes represent."""
    header = f"SYNTHETIC-EVIDENCE::{kind}::{fake.uuid4()}\n".encode()
    body = fake.text(max_nb_chars=2000).encode()
    return header + body


async def _seed():
    reset_db()
    init_db()
    EVIDENCE_STORE.mkdir(exist_ok=True)

    with get_conn() as conn:
        for _ in range(N_CASES):
            case_id = f"CASE-{fake.unique.random_number(digits=5)}"
            officer_name = fake.name()
            officer_id = f"OFC-{fake.unique.random_number(digits=4)}"

            for i in range(FILES_PER_CASE):
                kind = random.choice(["bodycam_clip", "scene_photo", "incident_report"])
                content = make_fake_file_bytes(kind)
                digest = hashing.hash_stream([content])

                evidence_id = fake.uuid4()
                filename = f"{kind}_{i+1}.bin"
                dest = EVIDENCE_STORE / f"{evidence_id}__{filename}"
                dest.write_bytes(content)

                conn.execute(
                    """INSERT INTO evidence
                       (evidence_id, case_id, filename, mime_type, byte_count, sha256_hash,
                        blake2b_hash, officer_id, officer_name, collected_at, device_info,
                        synced_from_offline)
                       VALUES (?, ?, ?, 'application/octet-stream', ?, ?, ?, ?, ?, ?, '{}', 0)""",
                    (
                        evidence_id, case_id, filename, digest.byte_count,
                        digest.sha256, digest.blake2b, officer_id, officer_name,
                        now_iso(),
                    ),
                )
                _append_custody_entry(
                    conn, evidence_id, "COLLECTED", officer_name,
                    {"case_id": case_id, "filename": filename, "byte_count": digest.byte_count},
                )
                # A realistic custody trail: the evidence gets viewed, maybe transferred.
                _append_custody_entry(conn, evidence_id, "VIEWED", officer_name, {})
                if random.random() > 0.5:
                    _append_custody_entry(
                        conn, evidence_id, "TRANSFERRED", officer_name,
                        {"to": "Evidence Locker A-12"},
                    )

                print(f"Seeded {evidence_id}  case={case_id}  file={filename}  sha256={digest.sha256[:16]}...")


if __name__ == "__main__":
    asyncio.run(_seed())
    print("\nDone. Start the API with:  uvicorn app.main:app --reload --port 8000")
