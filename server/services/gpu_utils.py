"""
ClipForge — GPU memory helpers.

Whisper (~3GB for large-v3), LaMa (~2-3GB) and EasyOCR can all be resident
in VRAM at once. On an 8GB Turing card that's tight. Calling these between
major pipeline stages reclaims headroom. All no-ops on CPU.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("clipforge.gpu")


def free_gpu_memory(label: str = "") -> None:
    """Release cached GPU memory: gc.collect() + torch.cuda.empty_cache().
    Cheap; safe to call anywhere. Logs current allocation when on CUDA."""
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            allocated = torch.cuda.memory_allocated() / 1024**3
            reserved = torch.cuda.memory_reserved() / 1024**3
            logger.info(
                f"GPU memory after {label or 'stage'}: "
                f"allocated={allocated:.2f}GB reserved={reserved:.2f}GB"
            )
    except ImportError:
        pass
    except Exception:
        logger.exception("free_gpu_memory failed")


def unload_inpaint_model() -> None:
    """Drop the cached LaMa model from GPU so its ~2-3GB is reclaimed. The
    next inpaint call reloads it on demand (~5s). Worth it after the erase
    stage finishes, since the rest of the pipeline (caption burn, mux) is
    ffmpeg-only and doesn't need LaMa."""
    try:
        from services import inpaint
        if getattr(inpaint, "_LAMA_MODEL", None) is not None:
            inpaint._LAMA_MODEL = None
            logger.info("LaMa model unloaded from GPU")
        free_gpu_memory("LaMa unload")
    except Exception:
        logger.exception("could not unload LaMa")
