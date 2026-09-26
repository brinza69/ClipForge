"""
ClipForge — AI Stream Clipper: the candidate-building stage's boundary pass —
refining every window's boundaries, and what it records about them.

Split out of clipper_build.py when that file reached 500 lines (EN1, adding the
audio to `refine_boundaries`); `_record_completeness` moved whole and unchanged,
and clipper_build.py imports it back under its old name.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from job_queue import JobCancelledError
from services.clipper.end_acoustics import SpeechAudio, Stopped

logger = logging.getLogger("clipforge.clipper.build")


async def refine_off_loop(raw: list[dict], transcript: dict, sig: dict, *, min_s: float,
                          max_s: float, atoms: list[dict], audio_path, queue,
                          job_id: str) -> list[dict]:
    """`refine_boundaries` over every candidate, in a worker thread (EN2 R3).

    With the audio the loop reads the VAD over nearly the whole file: ~150 s for
    4 h, ~7 min extrapolated for 12 h (B/EN1-result.md §5). On the event loop
    that starves the heartbeat, and the lease is 120 s. So the loop runs in a
    thread with its OWN SpeechAudio (the run's block cache, touched by no one
    else) and no DB session; progress and publication stay with the caller.

    `to_thread` alone does not stop a thread when the coroutine is cancelled,
    so the thread is asked — between candidates, and inside the reader before
    every block and every VAD phase — whether the job was cancelled or this
    coroutine was (lost ownership, shutdown). A stop is JobCancelledError: never
    the VAD's `unavailable` fallback, never the per-candidate fallback. On
    cancellation the coroutine waits for the thread to reach its next check, so
    no refinement outlives the job, and nothing it made is returned.
    """
    from services.clipper import candidates as cand_mod

    halt = threading.Event()

    def stopping() -> bool:
        return halt.is_set() or bool(queue.is_cancelled(job_id))

    def work() -> list[dict]:
        audio = SpeechAudio(audio_path, stop=stopping)
        refined = []
        for cand in raw:
            if stopping():
                raise JobCancelledError("Cancelled by user.")
            try:
                refined.append(
                    cand_mod.refine_boundaries(cand, transcript, sig, min_s=min_s,
                                               max_s=max_s, atoms=atoms, audio=audio)
                )
            except Stopped:
                raise JobCancelledError("Cancelled by user.") from None
            except Exception:
                # One bad window must not sink the run — keep the unrefined form.
                logger.warning("boundary refinement failed for a candidate", exc_info=True)
                refined.append(cand)
        return refined

    done = asyncio.ensure_future(asyncio.to_thread(work))
    try:
        return await asyncio.shield(done)
    except BaseException:
        halt.set()
        await asyncio.gather(done, return_exceptions=True)
        raise


def _record_completeness(refined: list[dict], transcript: dict, *,
                         max_s: float, duration: float) -> None:
    """Record R5's `boundary_view` on every candidate. Never fails the run.

    The work is `boundary_completion.attach`, not a second implementation of it:
    the audit script runs the SAME function over historical windows, and two
    copies would let the artefact and the gate describe different measurements.
    """
    from services.clipper import boundary_completion
    from services.clipper.candidate_terms import _words_for

    try:
        boundary_completion.attach(refined, _words_for({}, transcript),
                                   max_s=max_s, duration=duration)
    except Exception:
        # An observability field must never cost the run it describes.
        logger.warning("boundary completeness failed", exc_info=True)
