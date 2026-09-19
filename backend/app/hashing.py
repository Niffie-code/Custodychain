"""
CustodyChain — Cryptographic core.

This module is the "serious determiner" of the whole build: every
integrity claim the app makes (Authentic / Tampered, chain intact /
chain broken) reduces to the functions in this file. Keep it small,
deterministic, and dependency-free so it can be read and trusted by a
non-cryptographer judge in about two minutes.

Design decisions (see README "Honest Limits" for the full discussion):
  - SHA-256 for file integrity: collision-resistant for our threat
    model (accidental corruption + a rogue actor editing bytes), fast
    enough to stream multi-gigabyte evidence files.
  - BLAKE2b as a second, independent digest: if SHA-256 ever needed to
    be retired, a second algorithm computed at ingest time means old
    evidence can still be re-verified without ever touching the
    original file again.
  - Hash-chaining (each log entry embeds the hash of the previous
    entry) for the audit log: this is the same idea a blockchain uses,
    without any of the distributed-consensus machinery a single
    evidence locker doesn't need. Editing any historical entry changes
    its hash, which breaks every entry after it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB — bounds RAM use regardless of file size
GENESIS_HASH = "0" * 64  # sentinel "previous hash" for the first entry in a chain


# --------------------------------------------------------------------------
# File hashing (streamed / chunked — never loads a whole file into memory)
# --------------------------------------------------------------------------

@dataclass
class FileDigest:
    sha256: str
    blake2b: str
    byte_count: int


def hash_stream(chunks: Iterable[bytes]) -> FileDigest:
    """Compute SHA-256 and BLAKE2b over a stream of byte chunks.

    Accepts any iterable of bytes (a generator reading a file handle,
    an UploadFile's .file, a list of chunks in a test) so the same
    function backs both the real upload endpoint and unit tests.
    """
    sha256 = hashlib.sha256()
    blake2b = hashlib.blake2b()
    total = 0
    for chunk in chunks:
        if not chunk:
            continue
        sha256.update(chunk)
        blake2b.update(chunk)
        total += len(chunk)
    return FileDigest(sha256=sha256.hexdigest(), blake2b=blake2b.hexdigest(), byte_count=total)


def iter_file_chunks(file_obj, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Read a file-like object in fixed-size chunks."""
    while True:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
        yield chunk


def hash_file_object(file_obj, chunk_size: int = CHUNK_SIZE) -> FileDigest:
    return hash_stream(iter_file_chunks(file_obj, chunk_size))


async def hash_and_save_upload(upload_file, dest_path, chunk_size: int = CHUNK_SIZE) -> FileDigest:
    """Stream an (async) FastAPI UploadFile to disk while hashing it once.

    Never buffers the whole file in memory — this is what lets the
    pipeline handle evidence files far larger than available RAM.
    """
    sha256 = hashlib.sha256()
    blake2b = hashlib.blake2b()
    total = 0
    with open(dest_path, "wb") as out:
        while True:
            chunk = await upload_file.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
            blake2b.update(chunk)
            total += len(chunk)
            out.write(chunk)
    return FileDigest(sha256=sha256.hexdigest(), blake2b=blake2b.hexdigest(), byte_count=total)


# --------------------------------------------------------------------------
# Hash-chained custody log
#
# Each log entry commits to the exact bytes of the previous entry's hash.
# entry_hash = SHA256( prev_hash || canonical_json(event_fields) )
#
# canonical_json uses sort_keys + fixed separators so the same event
# fields always serialize to the same bytes, on any machine.
# --------------------------------------------------------------------------

def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_entry_hash(prev_hash: str, event_fields: dict) -> str:
    material = prev_hash + canonical_json(event_fields)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify_chain(entries: list[dict]) -> "ChainVerificationResult":
    """Walk a custody chain and confirm every link is intact.

    `entries` must be in chronological order, each a dict with keys:
    prev_hash, entry_hash, and the same event fields that were hashed
    when the entry was created (everything except entry_hash itself).

    Returns where the chain broke, if it broke, so the UI can point a
    judge at the exact tampered entry rather than a bare "invalid".
    """
    expected_prev = GENESIS_HASH
    for i, entry in enumerate(entries):
        # The pointer this entry claims to extend must match the hash the
        # previous entry actually produced.
        if entry.get("prev_hash") != expected_prev:
            return ChainVerificationResult(
                intact=False,
                broken_at_index=i,
                reason=f"Entry #{i + 1} points to the wrong previous hash — a log entry before it was likely reordered or deleted.",
            )
        event_fields = {k: v for k, v in entry.items() if k not in ("entry_hash", "prev_hash")}
        recomputed = compute_entry_hash(expected_prev, event_fields)
        if recomputed != entry.get("entry_hash"):
            return ChainVerificationResult(
                intact=False,
                broken_at_index=i,
                reason=f"Entry #{i + 1}'s stored hash doesn't match its content — this log entry was edited after it was created.",
            )
        expected_prev = entry["entry_hash"]
    return ChainVerificationResult(intact=True, broken_at_index=None, reason=None)


@dataclass
class ChainVerificationResult:
    intact: bool
    broken_at_index: Optional[int]
    reason: Optional[str]


# --------------------------------------------------------------------------
# Merkle tree — batch tamper-evidence across many evidence items at once.
#
# Instead of trusting the database's word that N files are all still
# intact, you can publish/notarize a single Merkle root (e.g. print it
# on a daily report, or timestamp it externally). Anyone can then
# recompute the root from the current hashes and confirm nothing in
# the whole batch changed, without re-checking every file by hand.
# --------------------------------------------------------------------------

def merkle_root(leaf_hashes: list[str]) -> Optional[str]:
    if not leaf_hashes:
        return None
    level = [bytes.fromhex(h) for h in leaf_hashes]
    if len(level) == 1:
        return level[0].hex()
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]  # duplicate last if odd
            nxt.append(hashlib.sha256(left + right).digest())
        level = nxt
    return level[0].hex()


def merkle_proof(leaf_hashes: list[str], index: int) -> list[dict]:
    """Return the sibling path needed to prove `leaf_hashes[index]` is in the tree."""
    level = [bytes.fromhex(h) for h in leaf_hashes]
    proof = []
    idx = index
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            if i == idx or i + 1 == idx:
                sibling = right if idx == i else left
                side = "right" if idx == i else "left"
                proof.append({"hash": sibling.hex(), "side": side})
                idx = i // 2
            nxt.append(hashlib.sha256(left + right).digest())
        level = nxt
    return proof
