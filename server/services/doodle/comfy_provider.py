"""
ClipForge — Auto Story Doodle: ComfyUI dual-GPU orchestration.

Combines comfy_client (HTTP) + comfy_workflows (graph building) into the
project-level operations the router/worker need: status polling across both
GPUs, splitting scenes across whichever GPUs are alive, and running the full
per-project image generation batch in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from services.doodle import comfy_client, comfy_workflows, storage

logger = logging.getLogger("clipforge.doodle.comfy_provider")

ProgressCb = Optional[Callable[[float, str], "asyncio.Future | None"]]


async def get_comfy_status() -> dict[str, Any]:
    """Returns the /api/doodle/comfy/status response shape: per-GPU alive
    state + queue depth, whether any GPU is alive, and whether the SDXL
    Turbo checkpoint file is present on disk."""
    urls = comfy_client.DEFAULT_GPU_URLS
    results = await asyncio.gather(*(comfy_client.check_comfy_status(u) for u in urls))

    gpus = []
    any_alive = False
    for idx, (url, result) in enumerate(zip(urls, results)):
        gpus.append({
            "index": idx,
            "url": url,
            "alive": result["alive"],
            "queue_pending": result["queue_pending"],
            "error": result["error"],
        })
        any_alive = any_alive or result["alive"]

    model_found = comfy_workflows.model_file_found("sdxl_turbo")
    hint = None
    if not any_alive:
        hint = "Start ComfyUI first using scripts/start_comfy_all.bat"

    return {
        "gpus": gpus,
        "any_alive": any_alive,
        "model": "sdxl_turbo" if model_found else None,
        "model_file_found": model_found,
        "hint": hint,
    }


def split_jobs_across_gpus(
    scenes: list[dict], alive_urls: list[str]
) -> list[tuple[str, list[dict]]]:
    """Splits `scenes` across `alive_urls`. With two alive GPUs: even scene
    index -> GPU0 (first alive url), odd -> GPU1 (second alive url). With
    exactly one alive GPU: ALL scenes go to it. With zero alive GPUs: returns
    an empty list (caller is expected to have already checked any_alive)."""
    if not alive_urls:
        return []
    if len(alive_urls) == 1:
        return [(alive_urls[0], list(scenes))]

    url0, url1 = alive_urls[0], alive_urls[1]
    even_scenes = [s for s in scenes if int(s.get("index", 0)) % 2 == 0]
    odd_scenes = [s for s in scenes if int(s.get("index", 0)) % 2 == 1]
    return [(url0, even_scenes), (url1, odd_scenes)]


def copy_comfy_output_to_project(project_id: str, scene_index: int, source_path: Path) -> str:
    """Copies/moves the ComfyUI-generated image into the project's images/
    dir as scene_{index:03d}.png, sets scene["image_path"], and saves the
    storyboard immediately. Returns the relative image_path written."""
    pdir = storage.project_dir(project_id)
    dest_name = f"scene_{scene_index:03d}.png"
    dest = pdir / "images" / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, dest)

    sb = storage.load_storyboard(project_id)
    for scene in sb.get("scenes") or []:
        if int(scene.get("index", -1)) == scene_index:
            scene["image_path"] = f"images/{dest_name}"
            break
    storage.save_storyboard(project_id, sb)
    return f"images/{dest_name}"


async def generate_project_images_parallel(
    project_id: str,
    scene_indexes: Optional[list[int]] = None,
    only_missing: bool = True,
    progress_cb=None,
) -> dict[str, Any]:
    """Generates images for a doodle project's scenes across both alive
    ComfyUI GPUs in parallel. One failed image never aborts the batch — each
    failure is recorded per-scene and the rest continue. progress_cb(fraction,
    message) is invoked after every completed image (success or failure)."""
    sb = storage.load_storyboard(project_id)
    scenes = sb.get("scenes") or []
    if not scenes:
        return {"generated": 0, "failed": [], "model": "sdxl_turbo"}

    explicit = set(scene_indexes) if scene_indexes else None

    def _needs_image(s: dict) -> bool:
        idx = int(s.get("index", 0))
        if explicit is not None and idx in explicit:
            return True
        if explicit is not None:
            return False
        if not only_missing:
            return True
        image_path = s.get("image_path")
        pdir = storage.project_dir(project_id)
        return not image_path or not (pdir / image_path).exists()

    target_scenes = [s for s in scenes if _needs_image(s)]
    if not target_scenes:
        return {"generated": 0, "failed": [], "model": "sdxl_turbo"}

    status = await get_comfy_status()
    alive_urls = [g["url"] for g in status["gpus"] if g["alive"]]
    if not alive_urls:
        raise RuntimeError(
            "No ComfyUI GPU is reachable. Start ComfyUI first using scripts/start_comfy_all.bat"
        )

    aspect_ratio = (sb.get("settings") or {}).get("aspect_ratio", "16:9")
    model = "sdxl_turbo"
    plan = split_jobs_across_gpus(target_scenes, alive_urls)

    total = len(target_scenes)
    done_count = 0
    failed: list[dict[str, Any]] = []
    lock = asyncio.Lock()

    async def _report(message: str) -> None:
        nonlocal done_count
        async with lock:
            done_count += 1
            frac = done_count / total if total else 1.0
        if progress_cb:
            result = progress_cb(frac, message)
            if asyncio.iscoroutine(result):
                await result

    async def _run_gpu_queue(url: str, gpu_scenes: list[dict]) -> None:
        for scene in gpu_scenes:
            idx = int(scene.get("index", 0))
            scene_prompt = scene.get("image_prompt") or scene.get("narration") or ""
            full_prompt = comfy_workflows.build_full_prompt(scene_prompt, aspect_ratio)
            tmp_path = storage.project_dir(project_id) / "images" / f".tmp_scene_{idx:03d}.png"
            try:
                await comfy_client.generate_image_on_comfy(
                    url,
                    full_prompt,
                    tmp_path,
                    aspect_ratio,
                    model=model,
                    seed=100000 + idx,
                )
                copy_comfy_output_to_project(project_id, idx, tmp_path)
                await _report(f"Generated image for scene {idx + 1}")
            except Exception as e:
                logger.warning(f"comfy image generation failed for scene {idx} ({url}): {e}")
                failed.append({"index": idx, "error": str(e)})
                await _report(f"Failed image for scene {idx + 1}")
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass

    await asyncio.gather(*(_run_gpu_queue(url, gpu_scenes) for url, gpu_scenes in plan))

    generated = total - len(failed)
    return {"generated": generated, "failed": failed, "model": model}
