"""
ClipForge — Auto Story Doodle: ComfyUI HTTP client.

Thin async httpx wrapper around the ComfyUI REST API (submit /prompt, poll
/history, fetch /view). No image-generation logic lives here beyond building
the request via comfy_workflows.build_workflow — see that module for the
graph shape and style suffix.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from services.doodle import comfy_workflows

logger = logging.getLogger("clipforge.doodle.comfy_client")

# Fixed defaults per the dual-GPU rig; overridable via env for portability.
DEFAULT_GPU_URLS: list[str] = [
    os.environ.get("COMFYUI_GPU0_URL", "http://127.0.0.1:8188"),
    os.environ.get("COMFYUI_GPU1_URL", "http://127.0.0.1:8189"),
]

_STATUS_TIMEOUT = 2.0
_POLL_INTERVAL = 1.5


async def check_comfy_status(url: str) -> dict[str, Any]:
    """Alive + queue-depth check for one ComfyUI instance. Never raises —
    any connection failure just yields alive=False with the error message."""
    try:
        async with httpx.AsyncClient(timeout=_STATUS_TIMEOUT) as client:
            resp = await client.get(f"{url}/system_stats")
            if resp.status_code != 200:
                return {"alive": False, "queue_pending": 0, "error": f"HTTP {resp.status_code}"}

            queue_pending = 0
            try:
                qresp = await client.get(f"{url}/queue")
                if qresp.status_code == 200:
                    qdata = qresp.json()
                    queue_pending = len(qdata.get("queue_running") or []) + len(
                        qdata.get("queue_pending") or []
                    )
            except Exception:
                pass  # queue depth is best-effort; alive status already confirmed

            return {"alive": True, "queue_pending": queue_pending, "error": None}
    except Exception as e:
        # httpx connect errors (e.g. ConnectTimeout/ConnectError) sometimes carry
        # an empty message on Windows — fall back to the exception class name so
        # the UI/logs never show a blank error string.
        message = str(e) or f"{type(e).__name__} connecting to {url}"
        return {"alive": False, "queue_pending": 0, "error": message}


def _extract_error_message(node_errors: Optional[dict], fallback: str) -> str:
    if not node_errors:
        return fallback
    parts = []
    for node_id, info in node_errors.items():
        errors = info.get("errors") if isinstance(info, dict) else None
        if errors:
            for err in errors:
                msg = err.get("message") if isinstance(err, dict) else str(err)
                parts.append(f"node {node_id}: {msg}")
    return "; ".join(parts) if parts else fallback


def _extract_execution_error(messages: list) -> str:
    for entry in messages or []:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "execution_error":
            payload = entry[1]
            if isinstance(payload, dict):
                return payload.get("exception_message") or str(payload)
            return str(payload)
    return "ComfyUI reported a generation error (see server logs)."


async def generate_image_on_comfy(
    url: str,
    prompt: str,
    output_path: Path,
    aspect_ratio: str,
    model: str = "sdxl_turbo",
    seed: int = 0,
    timeout: float = 420.0,
) -> Path:
    """Submits one image generation job to a ComfyUI instance, polls until
    complete, fetches the PNG bytes, and writes them to output_path. Raises
    RuntimeError with an actionable message on any failure (bad graph, node
    execution error, or timeout)."""
    filename_prefix = f"doodle_{uuid.uuid4().hex[:8]}"
    graph = comfy_workflows.build_workflow(
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        model=model,
        seed=seed,
        filename_prefix=filename_prefix,
    )
    client_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            submit_resp = await client.post(
                f"{url}/prompt", json={"prompt": graph, "client_id": client_id}
            )
        except Exception as e:
            raise RuntimeError(
                f"Could not reach ComfyUI at {url} to submit the job: {e}. "
                "Start ComfyUI first using scripts/start_comfy_all.bat"
            ) from e

        if submit_resp.status_code == 400:
            try:
                body = submit_resp.json()
            except Exception:
                body = {}
            node_errors = body.get("node_errors")
            message = _extract_error_message(node_errors, body.get("error") or submit_resp.text)
            raise RuntimeError(f"ComfyUI rejected the workflow ({url}): {message}")
        submit_resp.raise_for_status()

        prompt_id = submit_resp.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI ({url}) did not return a prompt_id for the submitted job.")

    elapsed = 0.0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while elapsed < timeout:
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

            try:
                hist_resp = await client.get(f"{url}/history/{prompt_id}")
                hist_resp.raise_for_status()
                history = hist_resp.json()
            except Exception as e:
                logger.warning(f"comfy history poll failed ({url}/{prompt_id}): {e}")
                continue

            entry = history.get(prompt_id)
            if not entry:
                continue  # still running

            status = entry.get("status") or {}
            status_str = status.get("status_str")
            if status_str == "error":
                message = _extract_execution_error(status.get("messages") or [])
                raise RuntimeError(f"ComfyUI execution failed ({url}): {message}")

            if not status.get("completed"):
                continue

            outputs = entry.get("outputs") or {}
            image_info = None
            for node_output in outputs.values():
                images = node_output.get("images") or []
                if images:
                    image_info = images[0]
                    break

            if not image_info:
                raise RuntimeError(
                    f"ComfyUI ({url}) finished the job but produced no output image "
                    f"(prompt_id={prompt_id})."
                )

            view_resp = await client.get(
                f"{url}/view",
                params={
                    "filename": image_info["filename"],
                    "subfolder": image_info.get("subfolder", ""),
                    "type": image_info.get("type", "output"),
                },
            )
            view_resp.raise_for_status()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(view_resp.content)
            return output_path

    raise RuntimeError(
        f"ComfyUI ({url}) timed out after {timeout:.0f}s waiting for prompt_id={prompt_id}."
    )
