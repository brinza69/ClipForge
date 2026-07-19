"""
ClipForge — Auto Story Doodle: scene image upload/delete/reorder routes.

Split out of routers/doodle.py to keep that file under the 500-line limit.
Merged into the main doodle router via `router.include_router(images_router)`
in doodle.py — same prefix ("/api/doodle"), so all paths are unchanged.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from services.doodle import storage

logger = logging.getLogger("clipforge.routers.doodle_images")
router = APIRouter(tags=["doodle"])


class ReorderRequest(BaseModel):
    order: list[int]

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

_SCENE_NUM_RE = re.compile(r"scene[_\-]?(\d+)", re.IGNORECASE)
_BARE_NUM_RE = re.compile(r"(\d+)")


def _load_or_404(project_id: str) -> dict:
    try:
        return storage.load_storyboard(project_id)
    except FileNotFoundError:
        raise HTTPException(404, "Doodle project not found")


def _find_scene(sb: dict, index: int) -> Optional[dict]:
    for s in sb.get("scenes") or []:
        if int(s.get("index", -1)) == index:
            return s
    return None


def _remove_scene_image_file(project_id: str, scene: dict) -> None:
    image_path = scene.get("image_path")
    if not image_path:
        return
    p = storage.project_dir(project_id) / image_path
    if p.exists():
        try:
            p.unlink()
        except Exception:
            logger.exception(f"could not remove old image {p}")


def _extract_scene_index(filename: str) -> Optional[int]:
    """Match `scene_003.png` style names, or a bare number like `3.png`."""
    stem = Path(filename).stem
    m = _SCENE_NUM_RE.search(stem)
    if m:
        return int(m.group(1))
    m = _BARE_NUM_RE.fullmatch(stem.strip())
    if m:
        return int(m.group(1))
    return None


# ── Images ───────────────────────────────────────────────────────────────────
# NOTE: /images/bulk MUST be registered before /images/{scene_index} — FastAPI
# matches path routes in registration order, and {scene_index}'s int converter
# would otherwise 422 on the literal "bulk" segment before this route is tried.

@router.post("/projects/{project_id}/images/bulk")
async def upload_scene_images_bulk(project_id: str, files: list[UploadFile] = File(...)):
    sb = _load_or_404(project_id)
    scenes = sb.get("scenes") or []

    matched = 0
    unmatched: list[str] = []

    async def _match_and_save(name: str, data: bytes) -> bool:
        idx = _extract_scene_index(name)
        if idx is None:
            return False
        scene = _find_scene(sb, idx)
        if scene is None:
            return False
        ext = Path(name).suffix.lower()
        if ext not in _IMAGE_EXTS:
            ext = ".png"
        flow_filename = scene.get("flow_filename") or f"scene_{idx:03d}.png"
        dest_name = Path(flow_filename).stem + ext
        dest = storage.project_dir(project_id) / "images" / dest_name
        _remove_scene_image_file(project_id, scene)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        scene["image_path"] = f"images/{dest_name}"
        return True

    for upload in files:
        name = upload.filename or ""
        data = await upload.read()
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for zi in zf.infolist():
                        if zi.is_dir():
                            continue
                        inner_name = Path(zi.filename).name
                        if Path(inner_name).suffix.lower() not in _IMAGE_EXTS:
                            continue
                        inner_data = zf.read(zi)
                        if await _match_and_save(inner_name, inner_data):
                            matched += 1
                        else:
                            unmatched.append(inner_name)
            except zipfile.BadZipFile:
                unmatched.append(name)
            continue

        if Path(name).suffix.lower() not in _IMAGE_EXTS:
            unmatched.append(name)
            continue
        if await _match_and_save(name, data):
            matched += 1
        else:
            unmatched.append(name)

    storage.save_storyboard(project_id, sb)
    return {"matched": matched, "unmatched": unmatched}


@router.post("/projects/{project_id}/images/{scene_index}")
async def upload_scene_image(project_id: str, scene_index: int, file: UploadFile = File(...)):
    sb = _load_or_404(project_id)
    scene = _find_scene(sb, scene_index)
    if scene is None:
        raise HTTPException(404, f"Scene {scene_index} not found")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty upload")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _IMAGE_EXTS:
        ext = ".png"
    flow_filename = scene.get("flow_filename") or f"scene_{scene_index:03d}.png"
    dest_name = Path(flow_filename).stem + ext
    dest = storage.project_dir(project_id) / "images" / dest_name

    # Clear any previously stored image under a different extension.
    _remove_scene_image_file(project_id, scene)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    scene["image_path"] = f"images/{dest_name}"
    storage.save_storyboard(project_id, sb)
    return scene


@router.delete("/projects/{project_id}/images/{scene_index}")
async def delete_scene_image(project_id: str, scene_index: int):
    sb = _load_or_404(project_id)
    scene = _find_scene(sb, scene_index)
    if scene is None:
        raise HTTPException(404, f"Scene {scene_index} not found")
    _remove_scene_image_file(project_id, scene)
    scene["image_path"] = None
    storage.save_storyboard(project_id, sb)
    return scene


# ── Reorder ──────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/scenes/reorder")
async def reorder_scenes(project_id: str, req: ReorderRequest):
    sb = _load_or_404(project_id)
    scenes = sb.get("scenes") or []
    by_index = {int(s["index"]): s for s in scenes}

    if sorted(req.order) != sorted(by_index.keys()):
        raise HTTPException(400, "order must be a permutation of existing scene indexes")

    pdir = storage.project_dir(project_id)
    images_dir = pdir / "images"
    audio_dir = pdir / "audio"

    # Stage renames through temp names first to avoid collisions when indexes
    # shuffle (e.g. swapping 0 and 1 would otherwise overwrite mid-loop).
    staged: list[tuple[dict, int, int]] = []  # (scene, old_index, new_index)
    for new_index, old_index in enumerate(req.order):
        staged.append((by_index[old_index], old_index, new_index))

    tmp_suffix = "__reorder_tmp__"
    for scene, old_index, new_index in staged:
        if old_index == new_index:
            continue
        _rename_scene_files(images_dir, scene, old_index, f"{tmp_suffix}{new_index}", is_image=True)
        _rename_scene_files(audio_dir, scene, old_index, f"{tmp_suffix}{new_index}", is_image=False)

    new_scenes: list[dict] = []
    for scene, old_index, new_index in staged:
        if old_index != new_index:
            _rename_scene_files(images_dir, scene, f"{tmp_suffix}{new_index}", new_index, is_image=True)
            _rename_scene_files(audio_dir, scene, f"{tmp_suffix}{new_index}", new_index, is_image=False)
        scene["index"] = new_index
        scene["flow_filename"] = f"scene_{new_index:03d}.png"
        new_scenes.append(scene)

    sb["scenes"] = new_scenes
    storage.save_storyboard(project_id, sb)
    storage.write_prompt_exports(project_id, sb)
    return sb


def _rename_scene_files(directory: Path, scene: dict, old_index, new_index, is_image: bool) -> None:
    """Rename a scene's on-disk file(s) from *_{old_index} to *_{new_index}
    (index args may be int or a str temp suffix) and update the scene dict."""
    if not directory.exists():
        return
    old_stem = f"scene_{int(old_index):03d}" if isinstance(old_index, int) else f"scene_{old_index}"
    new_stem = f"scene_{int(new_index):03d}" if isinstance(new_index, int) else f"scene_{new_index}"

    # Find any file in `directory` whose stem matches old_stem regardless of ext.
    for candidate in directory.glob(f"{old_stem}.*"):
        new_path = directory / f"{new_stem}{candidate.suffix}"
        try:
            if new_path.exists():
                new_path.unlink()
            candidate.rename(new_path)
            if is_image:
                scene["image_path"] = f"images/{new_path.name}"
            else:
                scene["audio_path"] = f"audio/{new_path.name}"
        except Exception:
            logger.exception(f"reorder rename failed: {candidate} -> {new_path}")
