"""Doodle image upload limits stay bounded for direct and ZIP uploads."""

import io
import zipfile

import pytest
from fastapi import HTTPException, UploadFile

from routers.doodle_images import (
    _MAX_IMAGE_BYTES,
    _MAX_ZIP_IMAGES,
    _MAX_ZIP_BYTES,
    upload_scene_image,
)


pytestmark = pytest.mark.asyncio


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


async def test_single_image_upload_rejects_oversized_content(monkeypatch):
    monkeypatch.setattr(
        "routers.doodle_images._load_or_404",
        lambda _project_id: {"scenes": [{"index": 0}]},
    )

    with pytest.raises(HTTPException) as exc:
        await upload_scene_image(
            "project", 0,
            _upload("scene_000.png", b"x" * (_MAX_IMAGE_BYTES + 1)),
        )

    assert exc.value.status_code == 413


async def test_zip_upload_limit_constants_cover_memory_and_entry_counts():
    assert _MAX_IMAGE_BYTES == 25 * 1024 * 1024
    assert _MAX_ZIP_BYTES == 100 * 1024 * 1024
    assert _MAX_ZIP_IMAGES == 200
