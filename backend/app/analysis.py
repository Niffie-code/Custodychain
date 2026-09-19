"""
CustodyChain — analysis.py  (ADDITIVE MODULE, new for the public verify flow)

This module answers a different question than hashing.py does.
hashing.py answers: "is this file byte for byte identical to the one we
recorded." That answer is binary and always correct, and this module
never touches it or reimplements it.

This module answers a softer, follow up question for the case where
the answer was "no": "okay, so how different is it, and in what way."
That is inherently approximate — a similarity percentage is a judgment
call about what counts as similar, not a cryptographic fact — so every
function here documents what it actually measures.

Nothing in this file is imported by hashing.py, database.py, or the
existing endpoints in main.py. It is wired in only through the new
router in analysis_routes.py.
"""
from __future__ import annotations

import difflib
import io
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops, ImageDraw

from .metadata import extract_metadata  # read only reuse, not modified

FileKind = Literal["text", "image", "video", "audio", "binary"]

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log", ".py", ".js", ".css"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def classify_file_kind(filename: str, mime_type: str | None) -> FileKind:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS or (mime_type or "").startswith("text/"):
        return "text"
    if ext in IMAGE_EXTENSIONS or (mime_type or "").startswith("image/"):
        return "image"
    if ext in VIDEO_EXTENSIONS or (mime_type or "").startswith("video/"):
        return "video"
    if ext in AUDIO_EXTENSIONS or (mime_type or "").startswith("audio/"):
        return "audio"
    return "binary"


@dataclass
class DiffOp:
    kind: Literal["equal", "insert", "delete", "replace"]
    original_text: str = ""
    compare_text: str = ""


@dataclass
class ComparisonResult:
    kind: FileKind
    similarity_pct: float
    headline: str
    explanation: str
    diff_ops: list[DiffOp] = field(default_factory=list)
    metadata_comparison: list[dict] = field(default_factory=list)
    overlay_png_base64: str | None = None
    timeline_markers: list[dict] = field(default_factory=list)
    waveform_profile: dict | None = None
    note: str = ""


def _round_pct(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


# --------------------------------------------------------------------------
# Text and document files — difflib based line diff
# --------------------------------------------------------------------------

def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _tokenize(text: str) -> list[str]:
    """Split into words and whitespace runs, each kept as its own token, so
    the original spacing and line breaks can be reconstructed from a diff
    over these tokens. Word level tokens are what make a single line file
    (a headline, a JSON blob, one paragraph with no internal line breaks)
    diffable at all — line level diffing treats such a file as one giant
    line, so a single word change looks identical to a total rewrite."""
    return re.findall(r"\s+|\S+", text)


def compare_text(original: bytes, compare: bytes) -> ComparisonResult:
    original_text = _decode_text(original)
    compare_text_ = _decode_text(compare)
    original_tokens = _tokenize(original_text)
    compare_tokens = _tokenize(compare_text_)

    matcher = difflib.SequenceMatcher(None, original_tokens, compare_tokens)
    similarity = _round_pct(matcher.ratio() * 100)

    ops: list[DiffOp] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        original_chunk = "".join(original_tokens[i1:i2])
        compare_chunk = "".join(compare_tokens[j1:j2])
        if tag == "equal":
            ops.append(DiffOp(kind="equal", original_text=original_chunk, compare_text=compare_chunk))
        elif tag == "insert":
            ops.append(DiffOp(kind="insert", compare_text=compare_chunk))
        elif tag == "delete":
            ops.append(DiffOp(kind="delete", original_text=original_chunk))
        elif tag == "replace":
            ops.append(DiffOp(kind="replace", original_text=original_chunk, compare_text=compare_chunk))

    headline, explanation = _text_message(similarity, ops)
    return ComparisonResult(kind="text", similarity_pct=similarity, headline=headline, explanation=explanation, diff_ops=ops)


def _text_message(similarity: float, ops: list[DiffOp]) -> tuple[str, str]:
    changed = [o for o in ops if o.kind != "equal"]
    if similarity >= 99.9:
        return "Identical content", "Every line matches. Nothing in the text was changed."
    if similarity >= 90:
        return "Minor wording changes", f"{similarity}% similar. A small number of lines were edited, added, or removed. This looks like light editing rather than a rewrite."
    if similarity >= 60:
        return "Noticeable changes", f"{similarity}% similar. Several sections differ between the two versions. Review the highlighted lines before treating this as the same document."
    return "Substantially different", f"{similarity}% similar. Large portions of the text do not match. This may be a different document entirely, or a heavily rewritten version."


# --------------------------------------------------------------------------
# Image files — pixel difference + overlay + metadata comparison
# --------------------------------------------------------------------------

_INTERESTING_METADATA_KEYS = ["Make", "Model", "Software", "CreateDate", "DateTimeOriginal", "ModifyDate", "GPSLatitude", "GPSLongitude", "ImageWidth", "ImageHeight"]


def compare_image(original_path: Path, compare_path: Path) -> ComparisonResult:
    try:
        img_a = Image.open(original_path).convert("RGB")
        img_b = Image.open(compare_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001 — surfaced as a plain language note, not a crash
        return ComparisonResult(
            kind="image", similarity_pct=0.0, headline="Could not read image",
            explanation="One of the files could not be opened as an image, so no visual comparison could be run.",
            note=str(exc),
        )

    size = (400, 400)
    a_resized = img_a.resize(size)
    b_resized = img_b.resize(size)

    diff = ImageChops.difference(a_resized, b_resized)
    diff_bytes = diff.tobytes()
    mean_diff = sum(diff_bytes) / len(diff_bytes)  # 0..255
    similarity = _round_pct(100 - (mean_diff / 255 * 100))

    overlay = b_resized.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    grid = 20
    cell_w, cell_h = size[0] // grid, size[1] // grid
    diff_gray = diff.convert("L")
    diff_pixels = diff_gray.load()
    flagged_cells = 0
    for gy in range(grid):
        for gx in range(grid):
            cell_total, cell_count = 0, 0
            for py in range(gy * cell_h, min((gy + 1) * cell_h, size[1])):
                for px in range(gx * cell_w, min((gx + 1) * cell_w, size[0])):
                    cell_total += diff_pixels[px, py]
                    cell_count += 1
            if cell_count and (cell_total / cell_count) > 28:
                flagged_cells += 1
                x0, y0 = gx * cell_w, gy * cell_h
                draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], outline=(240, 68, 56, 255), width=2, fill=(240, 68, 56, 60))
    flagged_fraction = flagged_cells / (grid * grid)

    buf = io.BytesIO()
    overlay.save(buf, format="PNG")
    import base64
    overlay_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    meta_a = extract_metadata(original_path)
    meta_b = extract_metadata(compare_path)
    comparison_rows = []
    for key in _INTERESTING_METADATA_KEYS:
        val_a, val_b = meta_a.get(key), meta_b.get(key)
        if val_a is None and val_b is None:
            continue
        comparison_rows.append({"field": key, "original": str(val_a) if val_a is not None else "Not present", "compare": str(val_b) if val_b is not None else "Not present", "changed": val_a != val_b})

    metadata_changed = any(r["changed"] for r in comparison_rows)
    headline, explanation = _image_message(similarity, metadata_changed, flagged_cells, flagged_fraction)

    return ComparisonResult(
        kind="image", similarity_pct=similarity, headline=headline, explanation=explanation,
        metadata_comparison=comparison_rows, overlay_png_base64=overlay_b64,
    )


def _image_message(similarity: float, metadata_changed: bool, flagged_cells: int, flagged_fraction: float) -> tuple[str, str]:
    # flagged_cells counts grid regions where pixels clearly changed (a real,
    # localized visual difference), independent of the overall average
    # similarity. This matters because averaging dilutes a small but
    # deliberate edit: a solid rectangle painted over ten percent of an
    # otherwise identical image can still score ninety plus percent similar
    # on a whole image average, which would wrongly read as "looks like
    # recompression" if similarity alone drove the message. Recompression
    # produces low magnitude noise spread across the whole frame, which
    # rarely crosses the per region threshold used to flag a cell at all,
    # so "zero flagged regions" is a better recompression signal than a
    # high percentage is.
    if flagged_cells == 0:
        if similarity >= 99.9:
            if metadata_changed:
                return "Visually identical, metadata differs", f"{similarity}% similar in pixels, with no localized visual differences detected. The image content matches, but its metadata does not, for example a different capture time or editing software. This can happen from a simple resave, not necessarily content manipulation."
            return "Identical image", f"{similarity}% similar. No visible or metadata differences were found."
        note = " Its metadata also does not match the original." if metadata_changed else ""
        return "Minor visual changes", f"{similarity}% similar. No single region stands out as changed, which is consistent with recompression or a general quality change rather than a targeted edit.{note}"

    region_word = "region" if flagged_cells == 1 else "regions"
    if flagged_fraction < 0.08:
        return "Localized change detected", f"{similarity}% similar overall, but {flagged_cells} specific {region_word} of the image differ, highlighted in the overlay above. A small but concentrated change like this can indicate a targeted edit, such as an object removed, added, or covered, rather than general recompression. Review the highlighted area directly."
    if similarity >= 60:
        return "Visible changes detected", f"{similarity}% similar. {flagged_cells} {region_word} of the image content have changed, highlighted above. Review the highlighted areas closely."
    return "Major alteration likely", f"{similarity}% similar. Large parts of the image do not match. This may indicate the file was significantly manipulated or is not the same image."


# --------------------------------------------------------------------------
# Video and audio — segment level byte hashing (see note on approximation)
#
# Honest limit: without decoding the actual video or audio stream, there is
# no way to know true frame or sample timestamps. This function chunks the
# raw file bytes into equal sized segments and compares them, then reports
# each segment's position as an estimated percentage through the file, not
# a decoded timecode. It is a real, deterministic comparison of the actual
# bytes, not a fabricated visualization, but it is a proxy for time, not a
# measurement of it. The UI must say so.
# --------------------------------------------------------------------------

def _segment_similarity(original: bytes, compare: bytes, n_segments: int = 24) -> tuple[float, list[dict]]:
    len_a, len_b = len(original), len(compare)
    max_len = max(len_a, len_b, 1)
    seg_size = max(1, max_len // n_segments)

    markers = []
    matching = 0
    total = 0
    for i in range(n_segments):
        start = i * seg_size
        end = min(start + seg_size, max_len)
        if start >= max_len:
            break
        chunk_a = original[start:end]
        chunk_b = compare[start:end]
        total += 1
        is_match = chunk_a == chunk_b
        if is_match:
            matching += 1
        else:
            markers.append({"position_pct": _round_pct(start / max_len * 100)})

    similarity = _round_pct((matching / total * 100) if total else 100.0)
    return similarity, markers


def compare_video(original: bytes, compare: bytes) -> ComparisonResult:
    similarity, markers = _segment_similarity(original, compare)
    headline, explanation = _timeline_message("video", similarity, markers)
    return ComparisonResult(
        kind="video", similarity_pct=similarity, headline=headline, explanation=explanation,
        timeline_markers=markers,
        note="Positions are estimated from file byte offsets, not decoded video timestamps.",
    )


def compare_audio(original: bytes, compare: bytes) -> ComparisonResult:
    similarity, markers = _segment_similarity(original, compare)
    profile = _byte_level_profile(compare, buckets=80)
    headline, explanation = _timeline_message("audio", similarity, markers)
    return ComparisonResult(
        kind="audio", similarity_pct=similarity, headline=headline, explanation=explanation,
        timeline_markers=markers, waveform_profile=profile,
        note="This is a byte level difference profile standing in for a waveform, not a decoded audio waveform.",
    )


def _byte_level_profile(data: bytes, buckets: int = 80) -> dict:
    if not data:
        return {"buckets": [0] * buckets}
    bucket_size = max(1, len(data) // buckets)
    values = []
    for i in range(buckets):
        start = i * bucket_size
        end = min(start + bucket_size, len(data))
        if start >= len(data):
            values.append(0)
            continue
        chunk = data[start:end]
        avg = sum(chunk) / len(chunk) if chunk else 0
        values.append(round(avg / 255, 3))
    return {"buckets": values}


def _timeline_message(kind: str, similarity: float, markers: list[dict]) -> tuple[str, str]:
    noun = "video" if kind == "video" else "audio file"
    if similarity >= 99.9:
        return "Identical file", f"This {noun} matches the original exactly, segment for segment."
    if not markers:
        return "No differing segments found", f"{similarity}% similar. No individual segments differed enough to flag, though the files are not byte for byte identical."
    if similarity >= 90:
        return "A few segments differ", f"{similarity}% similar. {len(markers)} segment or segments differ from the original, marked on the timeline above. This is consistent with recompression rather than content changes."
    return "Multiple segments differ", f"{similarity}% similar. {len(markers)} segments differ from the original, marked on the timeline above. Review those sections directly."


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def compare_files(filename: str, mime_type: str | None, original_bytes: bytes, compare_bytes: bytes,
                   original_path: Path | None = None, compare_path: Path | None = None) -> ComparisonResult:
    kind = classify_file_kind(filename, mime_type)
    if kind == "text":
        return compare_text(original_bytes, compare_bytes)
    if kind == "image" and original_path and compare_path:
        return compare_image(original_path, compare_path)
    if kind == "video":
        return compare_video(original_bytes, compare_bytes)
    if kind == "audio":
        return compare_audio(original_bytes, compare_bytes)
    # Fallback for binary/unrecognized types — same honest segment approach.
    similarity, markers = _segment_similarity(original_bytes, compare_bytes)
    if similarity >= 99.9:
        headline, explanation = "Identical file", "This file matches the original exactly, byte for byte."
    else:
        headline = "Files differ"
        explanation = f"{similarity}% similar at the byte level. This file type does not have a specialized comparison view, so this reflects a general byte level comparison rather than a content level one."
    return ComparisonResult(kind="binary", similarity_pct=similarity, headline=headline, explanation=explanation, timeline_markers=markers)
