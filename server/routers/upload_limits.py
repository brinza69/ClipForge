"""Bounded multipart reads shared by upload endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


async def read_upload_limited(
    upload: Any,
    max_bytes: int,
    *,
    too_large_detail: str = "File too large.",
) -> bytes:
    """Read an upload in chunks and fail as soon as its limit is exceeded.

    ``UploadFile.read()`` without a size allocates the whole request body at
    once. This helper keeps the existing byte-returning API of the routers but
    makes the memory bound explicit and honours a trustworthy content length
    before reading any bytes.
    """
    try:
        advertised = int((getattr(upload, "headers", {}) or {}).get("content-length") or 0)
    except (TypeError, ValueError):
        advertised = 0
    if advertised > max_bytes:
        raise HTTPException(413, too_large_detail)

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, too_large_detail)
        chunks.append(chunk)
    return b"".join(chunks)
