"""
ClipForge — Auto Story Doodle: ComfyUI workflow graphs (SDXL Turbo / FLUX Schnell).

Pure functions only — no HTTP calls here (see comfy_client.py for that). This
module builds the API-format node graphs ComfyUI's /prompt endpoint expects,
plus the doodle style prompt suffix and checkpoint-file presence check.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Exact style suffix appended to every generated scene prompt (Step 7 spec).
DOODLE_STYLE_SUFFIX = (
    "simple hand-drawn educational doodle, white or cream background, thick black "
    "marker outlines, cozy storytelling style, minimal warm orange accents, simple "
    "objects, clear composition, no photorealism, no cinematic lighting, no 3D, "
    "no complex background"
)

_NEGATIVE_PROMPT = (
    "photo, photorealistic, 3d render, cinematic lighting, complex background, "
    "realistic shading"
)

RESOLUTIONS: dict[str, tuple[int, int]] = {
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "1:1": (768, 768),
}

_SDXL_TURBO_CKPT = "sd_xl_turbo_1.0_fp16.safetensors"
_FLUX_SCHNELL_CKPT = "flux1-schnell-fp8.safetensors"

# Default ComfyUI installation dir — overridable via COMFYUI_DIR for portability.
_DEFAULT_COMFYUI_DIR = Path(r"D:\clipforge\tools\ComfyUI")


def _comfyui_dir() -> Path:
    override = os.environ.get("COMFYUI_DIR")
    return Path(override) if override else _DEFAULT_COMFYUI_DIR


def _checkpoints_dir() -> Path:
    return _comfyui_dir() / "models" / "checkpoints"


def model_file_found(model: str = "sdxl_turbo") -> bool:
    """Checks whether the checkpoint file for `model` exists on disk. Never
    raises — a missing/partial download just means False (feature not ready
    yet), not an error."""
    ckpt_name = _FLUX_SCHNELL_CKPT if model == "flux_schnell" else _SDXL_TURBO_CKPT
    try:
        return (_checkpoints_dir() / ckpt_name).is_file()
    except OSError:
        return False


def build_full_prompt(scene_prompt: str, aspect_ratio: str) -> str:
    """scene image_prompt + ", " + DOODLE_STYLE_SUFFIX + ", " + aspect ratio."""
    ratio = aspect_ratio if aspect_ratio in RESOLUTIONS else "16:9"
    scene_prompt = (scene_prompt or "").strip().rstrip(",")
    return f"{scene_prompt}, {DOODLE_STYLE_SUFFIX}, {ratio}"


def build_workflow(
    prompt: str,
    aspect_ratio: str,
    model: str = "sdxl_turbo",
    seed: int = 0,
    filename_prefix: str = "doodle",
) -> dict[str, Any]:
    """Builds the API-format ComfyUI graph for one image. `prompt` should
    already be the FULL prompt (scene text + style suffix + aspect ratio) —
    use build_full_prompt() to build it before calling this."""
    width, height = RESOLUTIONS.get(aspect_ratio, RESOLUTIONS["16:9"])

    if model == "flux_schnell":
        ckpt_name = _FLUX_SCHNELL_CKPT
        sampler_name = "euler"
        scheduler = "simple"
    else:
        ckpt_name = _SDXL_TURBO_CKPT
        sampler_name = "euler_ancestral"
        scheduler = "normal"

    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": _NEGATIVE_PROMPT, "clip": ["4", 1]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
        },
    }
