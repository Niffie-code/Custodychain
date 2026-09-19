"""Read-only metadata extraction.

Uses the ExifTool CLI (industry-standard for forensic metadata work —
handles image, video, audio, and document formats) in read-only mode.
We never pass a flag that could write back to the file: this process
must not be able to alter the evidence it's reporting on.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

_EXIFTOOL_PATH = shutil.which("exiftool")

# Keys worth surfacing to a non-technical judge; ExifTool returns dozens
# of low-value fields (ExifToolVersion, FileInodeChangeDate, etc.) we
# don't want cluttering a judicial report.
_INTERESTING_KEYS = {
    "FileType", "MIMEType", "FileSize", "ImageWidth", "ImageHeight",
    "Make", "Model", "Software", "CreateDate", "DateTimeOriginal",
    "ModifyDate", "GPSLatitude", "GPSLongitude", "Duration",
    "AudioFormat", "VideoFrameRate",
}


def extract_metadata(file_path: Path) -> dict[str, Any]:
    """Return a small dict of human-relevant metadata, or {} if unavailable.

    Never raises: metadata is a nice-to-have for the report, and its
    absence must never block hashing or chain-of-custody, which are
    the actual integrity guarantees this system makes.
    """
    if _EXIFTOOL_PATH is None:
        return {"_note": "ExifTool not installed on this host; metadata skipped."}
    try:
        result = subprocess.run(
            [_EXIFTOOL_PATH, "-j", "-n", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        parsed = json.loads(result.stdout)
        if not parsed:
            return {}
        raw = parsed[0]
        return {k: v for k, v in raw.items() if k in _INTERESTING_KEYS}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return {}
