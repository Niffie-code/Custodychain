"""
CustodyChain — analysis_routes.py  (ADDITIVE MODULE)

New endpoints only. Nothing here modifies, imports for mutation, or
depends on internal state from main.py's existing routes — each request
here is self contained and stateless, so it cannot affect the existing
hash upload, verify, or custody chain behavior no matter how it's used.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import analysis
from .report_pdf import build_report_pdf

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/compare")
async def compare_files_endpoint(
    original: UploadFile = File(...),
    compare: UploadFile = File(...),
):
    """Compare two uploaded files and return a similarity score plus a
    kind specific diff. Independent of the evidence database — this
    endpoint does not read or write any evidence or custody_log row."""
    original_bytes = await original.read()
    compare_bytes = await compare.read()

    if not original_bytes or not compare_bytes:
        raise HTTPException(400, "Both files are required and must not be empty.")

    kind = analysis.classify_file_kind(original.filename or "", original.content_type)

    original_path = compare_path = None
    tmp_files = []
    try:
        if kind == "image":
            original_path = _write_temp(original_bytes, Path(original.filename or "original").suffix)
            compare_path = _write_temp(compare_bytes, Path(compare.filename or "compare").suffix)
            tmp_files = [original_path, compare_path]

        result = analysis.compare_files(
            filename=original.filename or "file",
            mime_type=original.content_type,
            original_bytes=original_bytes,
            compare_bytes=compare_bytes,
            original_path=original_path,
            compare_path=compare_path,
        )
    finally:
        for p in tmp_files:
            p.unlink(missing_ok=True)

    return {
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


def _write_temp(data: bytes, suffix: str) -> Path:
    fd, path_str = tempfile.mkstemp(suffix=suffix or ".bin")
    path = Path(path_str)
    with open(fd, "wb") as f:
        f.write(data)
    return path


class ReportRequest(BaseModel):
    file_name: str
    file_type: str
    verified_at: str
    original_hash: str
    compare_hash: str
    similarity_pct: float
    verdict_tier: str  # verified | caution | altered
    verdict_label: str
    explanation: str
    extra_notes: list[str] | None = None


@router.post("/report")
def generate_report_pdf(payload: ReportRequest):
    """Generate the downloadable verification report as a PDF. Purely a
    rendering step over caller supplied values — computes nothing new
    and touches no stored evidence record."""
    pdf_bytes = build_report_pdf(payload.model_dump())
    filename = f"verification_report_{payload.file_name}.pdf".replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
