"""
Pre-flight resource check — run AFTER transcribe, BEFORE the expensive erase
stage, so a job that CAN'T finish (dead key, exhausted ElevenLabs quota) fails
in seconds instead of after minutes of GPU inpainting.

Directly prevents the real-world waste: a row died at 48% on ElevenLabs
`quota_exceeded` only AFTER a full download + transcribe + LaMa erase. This
check catches that at ~10% (before erase) and fails the row with a clear,
actionable message so a batch loop can skip it and move on.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("clipforge.preflight")


def _estimate_el_chars(raw_text: str) -> int:
    # ElevenLabs bills ~1 credit per character. Cleaning almost always SHORTENS
    # the raw transcript, so its length is a safe upper bound for the estimate.
    return len(raw_text or "")


async def preflight_check(
    cfg: Dict[str, Any],
    variants: List[Dict[str, Any]],
    raw_transcript_text: str,
) -> None:
    """Raise RuntimeError with a clear message if the job cannot complete with
    the current keys / quota. No-op when everything is fine."""
    problems: List[str] = []

    # 1) Transcript-cleaning engine key/reachability.
    engine = (cfg.get("transcript_engine") or "ollama").lower()
    from services import transcript_cleaner as tc
    if engine == "openai" and not tc.get_openai_key():
        problems.append("OpenAI key missing (transcript engine = openai) — set it in Settings.")
    elif engine == "anthropic" and not tc.get_anthropic_key():
        problems.append("Anthropic key missing (transcript engine = anthropic) — set it in Settings.")
    elif engine == "ollama":
        try:
            status = await tc.ollama_status()
            if not (status.get("ready", True) and status.get("available", True)):
                problems.append(status.get("hint") or "Ollama not reachable (transcript engine = ollama).")
        except Exception as e:
            logger.warning(f"preflight: ollama status probe failed ({e}); not blocking")

    # 2) TTS keys + ElevenLabs quota vs estimated need (the real failure mode).
    el_variants = [v for v in variants if (v.get("tts_engine") or "").lower() == "elevenlabs"]
    if el_variants:
        from services import elevenlabs
        if not elevenlabs.is_configured():
            problems.append(
                "ElevenLabs key missing but a variant uses it — set it in Settings "
                "or switch that variant to XTTS."
            )
        else:
            try:
                info = await elevenlabs.get_user_info()
                limit = info.get("character_limit")
                count = info.get("character_count")
                if isinstance(limit, int) and isinstance(count, int):
                    remaining = max(0, limit - count)
                    per = _estimate_el_chars(raw_transcript_text)
                    needed = per * len(el_variants)
                    if remaining < needed:
                        problems.append(
                            f"ElevenLabs quota too low: ~{needed} credits needed "
                            f"({len(el_variants)} ElevenLabs variant(s) × ~{per} chars), "
                            f"{remaining} remaining. Top up, wait for the monthly reset, "
                            f"or switch those variants to XTTS (local, free)."
                        )
                    else:
                        logger.info(
                            f"preflight: ElevenLabs quota OK ({remaining} remaining, "
                            f"~{needed} needed)"
                        )
            except Exception as e:
                # Never block on a transient quota-read error — the synth call
                # will surface a real failure if the key is genuinely bad.
                logger.warning(f"preflight: could not read ElevenLabs quota ({e}); skipping quota gate")

    if problems:
        raise RuntimeError(
            "Pre-flight failed (checked before erase to save GPU/time): "
            + " | ".join(problems)
        )
    logger.info("preflight: all required keys/quota OK")
