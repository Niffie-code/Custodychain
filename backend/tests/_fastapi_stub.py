"""Minimal stand-ins for the FastAPI surface custody_routes.py imports.

This exists only so the route handlers can be exercised in an environment
where FastAPI is not installed. It is a test aid, never imported by the app.
"""
from __future__ import annotations

import sys
import types


class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class APIRouter:
    def __init__(self, *a, **kw):
        self.prefix = kw.get("prefix", "")
        self.routes = []

    def _register(self, method, path):
        def decorator(fn):
            self.routes.append((method, self.prefix + path, fn))
            return fn
        return decorator

    def get(self, path, **kw): return self._register("GET", path)
    def post(self, path, **kw): return self._register("POST", path)
    def put(self, path, **kw): return self._register("PUT", path)
    def delete(self, path, **kw): return self._register("DELETE", path)


def _passthrough(default=None, *a, **kw):
    return default


class UploadFile:  # pragma: no cover - type marker only
    pass


class Response:
    def __init__(self, content=None, media_type=None, headers=None, **kw):
        self.body = content
        self.media_type = media_type
        self.headers = headers or {}


class FileResponse:
    def __init__(self, path, media_type=None, filename=None, **kw):
        self.path = path
        self.media_type = media_type
        self.filename = filename


def install():
    """Insert the stub modules into sys.modules (idempotent)."""
    if "fastapi" in sys.modules and getattr(sys.modules["fastapi"], "_cc_stub", False):
        return

    fastapi = types.ModuleType("fastapi")
    fastapi._cc_stub = True
    fastapi.APIRouter = APIRouter
    fastapi.HTTPException = HTTPException
    fastapi.UploadFile = UploadFile
    fastapi.File = _passthrough
    fastapi.Form = _passthrough
    fastapi.Body = _passthrough
    fastapi.Depends = _passthrough

    responses = types.ModuleType("fastapi.responses")
    responses.Response = Response
    responses.FileResponse = FileResponse
    responses.JSONResponse = Response
    fastapi.responses = responses

    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses


class FakeUpload:
    """Stands in for an UploadFile: async chunked read, like the real thing."""

    def __init__(self, filename: str, data: bytes, content_type: str | None = None):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk
