"""Regression tests for bounded multipart reads."""

import pytest
from fastapi import HTTPException

from routers.upload_limits import read_upload_limited


class _Upload:
    def __init__(self, chunks, headers=None):
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.read_sizes = []

    async def read(self, size=-1):
        self.read_sizes.append(size)
        return self.chunks.pop(0) if self.chunks else b""


@pytest.mark.asyncio
async def test_upload_is_read_in_bounded_chunks():
    upload = _Upload([b"abc", b"def"])

    assert await read_upload_limited(upload, 10) == b"abcdef"
    assert upload.read_sizes[0] == 11
    assert all(size <= 1024 * 1024 for size in upload.read_sizes)


@pytest.mark.asyncio
async def test_advertised_oversize_is_rejected_before_reading():
    upload = _Upload([b"should not be read"], {"content-length": "11"})

    with pytest.raises(HTTPException) as caught:
        await read_upload_limited(upload, 10, too_large_detail="too large")

    assert caught.value.status_code == 413
    assert caught.value.detail == "too large"
    assert upload.read_sizes == []


@pytest.mark.asyncio
async def test_unknown_length_is_rejected_as_soon_as_limit_is_crossed():
    upload = _Upload([b"1234", b"5678"])

    with pytest.raises(HTTPException) as caught:
        await read_upload_limited(upload, 5, too_large_detail="too large")

    assert caught.value.status_code == 413
    assert caught.value.detail == "too large"
    assert len(upload.read_sizes) == 2
