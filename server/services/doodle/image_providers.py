"""
ClipForge — Auto Story Doodle: image provider registry.

NO automatic image generation API calls anywhere in this module. Manual Flow
Mode ("manual_flow") and Manual Upload ("manual_upload") are the only enabled
providers — the user generates images themselves in Google Flow (or any tool)
and drags them into scene slots via the /images upload routes. Every paid
provider below is a disabled placeholder stub that raises immediately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DISABLED_MESSAGE = "Paid image API is disabled for now. Use Manual Flow Mode to save credits."

PROVIDERS: list[dict[str, Any]] = [
    {"id": "manual_flow", "label": "Manual Flow (Google Flow)", "enabled": True, "default": True},
    {"id": "manual_upload", "label": "Manual Upload", "enabled": True},
    {"id": "openai_images", "label": "OpenAI Images", "enabled": False},
    {"id": "deepai", "label": "DeepAI", "enabled": False},
    {"id": "higgsfield", "label": "Higgsfield", "enabled": False},
    {"id": "comfyui_local", "label": "ComfyUI (local)", "enabled": False},
]

_PROVIDERS_BY_ID = {p["id"]: p for p in PROVIDERS}


class ImageProvider:
    """Base class for an image-generation provider. `generate` is the only
    entry point: given a prompt and an output path, produce an image file."""

    id: str = "base"
    label: str = "Base provider"
    enabled: bool = False

    async def generate(self, prompt: str, out_path: Path) -> Path:
        raise NotImplementedError("ImageProvider.generate must be implemented by subclasses")


class _DisabledProvider(ImageProvider):
    """Stub for every paid/automated provider. Always raises — zero API calls."""

    def __init__(self, provider_id: str, label: str):
        self.id = provider_id
        self.label = label
        self.enabled = False

    async def generate(self, prompt: str, out_path: Path) -> Path:
        raise RuntimeError(DISABLED_MESSAGE)


class ManualFlowProvider(ImageProvider):
    """Not a generator — images are produced by hand in Google Flow and
    uploaded through the API. generate() is a no-op placeholder that makes
    the "manual" contract explicit: this app never calls out to Flow."""

    id = "manual_flow"
    label = "Manual Flow (Google Flow)"
    enabled = True

    async def generate(self, prompt: str, out_path: Path) -> Path:
        raise RuntimeError(
            "Manual Flow Mode has no automatic generation — copy the prompt, "
            "create the image in Google Flow, then upload it."
        )


class ManualUploadProvider(ImageProvider):
    """Same idea as ManualFlowProvider — user supplies the image file directly."""

    id = "manual_upload"
    label = "Manual Upload"
    enabled = True

    async def generate(self, prompt: str, out_path: Path) -> Path:
        raise RuntimeError("Manual Upload has no automatic generation — upload an image file.")


def get_provider(provider_id: str) -> ImageProvider:
    """Factory: returns the provider instance for a given id. Disabled/unknown
    providers resolve to a stub that raises DISABLED_MESSAGE on generate()."""
    meta = _PROVIDERS_BY_ID.get(provider_id)
    if meta is None:
        return _DisabledProvider(provider_id, provider_id)
    if provider_id == "manual_flow":
        return ManualFlowProvider()
    if provider_id == "manual_upload":
        return ManualUploadProvider()
    return _DisabledProvider(meta["id"], meta["label"])


def list_providers() -> list[dict[str, Any]]:
    return PROVIDERS
