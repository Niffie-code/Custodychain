"""
CustodyChain — tests/test_chain.py

The four properties the whole product rests on:
  1. an identical copy matches the seal;
  2. a one-byte change breaks the seal;
  3. each event commits to the one before it;
  4. an edited or reordered event is detected, and we can say where.

Run from the backend directory:
    python -m pytest tests/ -v
or without pytest installed:
    python tests/test_chain.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import custody  # noqa: E402


# --------------------------------------------------------------------------
# 1 + 2. Sealing a file: identical copy matches, one-byte change does not
# --------------------------------------------------------------------------

def test_identical_copy_matches_the_seal():
    original = b"Notice of hearing. Room 4. 09:00.\n" * 40
    seal = custody.sha256_file(io.BytesIO(original))
    honest_copy = bytes(original)  # a real copy, byte for byte
    assert custody.sha256_file(io.BytesIO(honest_copy)) == seal


def test_one_byte_change_breaks_the_seal():
    original = b"Notice of hearing. Room 4. 09:00.\n" * 40
    seal = custody.sha256_file(io.BytesIO(original))

    altered = bytearray(original)
    altered[len(altered) // 2] = (altered[len(altered) // 2] + 1) % 256
    altered = bytes(altered)

    assert len(altered) == len(original), "same length — only the content moved"
    assert altered != original
    assert custody.sha256_file(io.BytesIO(altered)) != seal


def test_appending_a_single_byte_breaks_the_seal():
    original = b"chat export\n"
    seal = custody.sha256_file(io.BytesIO(original))
    assert custody.sha256_file(io.BytesIO(original + b"x")) != seal


def test_seal_is_stable_across_chunk_sizes():
    """Streaming must not change the answer, whatever the buffer size."""
    data = bytes(range(256)) * 500
    one_shot = custody.sha256_file(io.BytesIO(data))
    chunked = custody.sha256_file(io.BytesIO(data), chunk_size=7)
    assert one_shot == chunked


# --------------------------------------------------------------------------
# Helpers for building an in-memory chain without a database
# --------------------------------------------------------------------------

def _linked_event(exhibit_id, actor, role, action, prev_hash, occurred_at,
                  from_person=None, to_person=None, note=None):
    payload = custody.canonical_event_payload(
        exhibit_id=exhibit_id, actor_name=actor, actor_role=role, action=action,
        from_person=from_person, to_person=to_person, note=note,
        occurred_at=occurred_at, prev_event_hash=prev_hash,
    )
    return {**payload, "event_hash": custody.event_hash(payload)}


def _build_chain():
    eid = "CC-2026-00042"
    e1 = _linked_event(eid, "A. Bello", "Collecting officer", "COLLECTED",
                       custody.GENESIS, "2026-01-05T09:00:00+00:00")
    e2 = _linked_event(eid, "A. Bello", "Collecting officer", "TRANSFERRED",
                       e1["event_hash"], "2026-01-05T11:30:00+00:00",
                       from_person="A. Bello", to_person="C. Okafor")
    e3 = _linked_event(eid, "C. Okafor", "Analyst", "RECEIVED",
                       e2["event_hash"], "2026-01-05T11:45:00+00:00",
                       from_person="A. Bello", to_person="C. Okafor")
    return [e1, e2, e3]


# --------------------------------------------------------------------------
# 3. Event hashes link to each other
# --------------------------------------------------------------------------

def test_each_event_points_at_the_one_before_it():
    chain = _build_chain()
    assert chain[0]["prev_event_hash"] == custody.GENESIS
    assert chain[1]["prev_event_hash"] == chain[0]["event_hash"]
    assert chain[2]["prev_event_hash"] == chain[1]["event_hash"]
    assert custody.verify_chain(chain).ok


def test_event_hash_is_deterministic():
    a = _build_chain()
    b = _build_chain()
    assert [e["event_hash"] for e in a] == [e["event_hash"] for e in b]


def test_blank_and_missing_optional_fields_hash_the_same():
    """'' and None must not produce two hashes for the same event."""
    p1 = custody.canonical_event_payload(
        "CC-2026-00001", "A", "Analyst", "VIEWED", "", "", "  ",
        "2026-01-01T00:00:00+00:00", custody.GENESIS)
    p2 = custody.canonical_event_payload(
        "CC-2026-00001", "A", "Analyst", "VIEWED", None, None, None,
        "2026-01-01T00:00:00+00:00", custody.GENESIS)
    assert custody.event_hash(p1) == custody.event_hash(p2)


# --------------------------------------------------------------------------
# 4. Broken links are detected, and located
# --------------------------------------------------------------------------

def test_editing_an_event_in_place_is_detected():
    chain = _build_chain()
    chain[1]["to_person"] = "Someone Else"  # rewrite history, keep the old hash
    status = custody.verify_chain(chain)
    assert status.ok is False
    assert status.break_index == 1
    assert "edited" in status.reason.lower()


def test_deleting_a_middle_event_is_detected():
    chain = _build_chain()
    del chain[1]
    status = custody.verify_chain(chain)
    assert status.ok is False
    assert status.break_index == 1


def test_reordering_events_is_detected():
    chain = _build_chain()
    chain[1], chain[2] = chain[2], chain[1]
    status = custody.verify_chain(chain)
    assert status.ok is False
    assert status.break_index == 1


def test_inserting_a_forged_event_is_detected():
    """A forged entry can be self-consistent and still not fit the chain."""
    chain = _build_chain()
    forged = _linked_event("CC-2026-00042", "Nobody", "Other", "VIEWED",
                           custody.GENESIS, "2026-01-05T10:00:00+00:00")
    chain.insert(1, forged)
    status = custody.verify_chain(chain)
    assert status.ok is False
    assert status.break_index == 1


def test_empty_chain_is_trivially_intact():
    assert custody.verify_chain([]).ok


def test_tampering_with_the_first_event_is_detected():
    chain = _build_chain()
    chain[0]["actor_name"] = "Someone Else"
    status = custody.verify_chain(chain)
    assert status.ok is False
    assert status.break_index == 0


# --------------------------------------------------------------------------
# Verdict language: a mismatch is never reported as a match
# --------------------------------------------------------------------------

def test_match_is_green():
    verdict, summary = custody.verdict_for_check(True, None)
    assert verdict == "green"
    assert "has not been changed" in summary


def test_high_similarity_mismatch_is_amber_but_still_says_it_does_not_match():
    verdict, summary = custody.verdict_for_check(False, 99.4)
    assert verdict == "amber"
    assert "seal does not match" in summary.lower()
    assert "identical" in summary.lower()  # "Bytes are not identical."


def test_low_similarity_mismatch_is_red():
    verdict, _ = custody.verdict_for_check(False, 12.0)
    assert verdict == "red"


def test_no_mismatch_is_ever_called_a_match():
    for similarity in (None, 0.0, 50.0, 85.0, 99.9, 100.0):
        verdict, summary = custody.verdict_for_check(False, similarity)
        assert verdict in ("amber", "red")
        assert verdict != "green"
        assert "does not match" in summary.lower()


# --------------------------------------------------------------------------
# Exhibit IDs
# --------------------------------------------------------------------------

def test_generated_exhibit_ids_are_wellformed():
    for _ in range(50):
        assert custody.is_wellformed_exhibit_id(custody.generate_exhibit_id())


def test_malformed_exhibit_ids_are_rejected():
    for bad in ("", "CC-26-1", "cc-2026-00001", "CC-2026-1", "X"):
        assert not custody.is_wellformed_exhibit_id(bad)


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except AssertionError as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
