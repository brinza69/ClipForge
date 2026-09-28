"""
ClipForge — AI Stream Clipper: the cooperative stop of ONE analysis attempt (OW1).

`task.cancel()` stops the awaiting coroutine and nothing else: the executor
thread it was waiting on runs to its end (AD3H: an orphaned `build_signals`
wrote `signals.json` 7 min after its cancel). This is the thread-side half: a
`threading.Event` per CLAIMED attempt, set by the queue's cancel and ownership-loss
paths (each for the attempt it verified) and by the handler's own `finally`, and checked between stages, in the 3b
row loop, before every frame-grab ffmpeg and in the vocal-burst block loop.

It SAVES WORK; it is not what keeps an orphan out of the analysis. A thread
that never looks at it can only write inside its own generation directory,
which nothing selects (`analysis_generation`, `clipper_analysis_publish`).

`Stopped` is its own exception, like `end_acoustics.Stopped`, and no generic
`except` may turn it into a partial result.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class Stopped(Exception):
    """The attempt was told to stop. Never a result, never `unavailable`."""


def check(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise Stopped("analysis attempt stopped")


def register(queue: Any, job_id: str, attempt: int) -> threading.Event:
    """A fresh Event for this attempt, reachable from the queue by (job id, attempt).

    Keyed by the CLAIMED attempt (job_attempt.ClaimedAttempt), never by the job
    alone: one process can run two attempts of one job (AQ1), and a signal meant
    for attempt 1 — its heartbeat's loss, its own refused cancel — must not stop
    attempt 2 (codex-verdict-next-27 §4 R3). Each attempt gets its OWN Event
    object, so a re-register can never un-stop the threads of another."""
    event = threading.Event()
    queue.__dict__.setdefault("_stop_tokens", {})[(job_id, attempt)] = event
    return event


def signal(queue: Any, job_id: str, attempt: int | None) -> None:
    """Set THAT attempt's token, if it is registered here. Called only where the
    queue has established which attempt it stops: a person's cancel, for the
    registered attempt; an attempt's own cancel, after the row confirmed it; a
    heartbeat's loss, for the heartbeat's own attempt. `None` signals nothing."""
    if attempt is None:
        return
    event = queue.__dict__.get("_stop_tokens", {}).get((job_id, attempt))
    if event is not None:
        event.set()


def release(queue: Any, job_id: str, attempt: int, event: threading.Event) -> None:
    """Set `event` and forget it — only if it is still the registered one."""
    event.set()
    tokens = queue.__dict__.get("_stop_tokens", {})
    if tokens.get((job_id, attempt)) is event:
        tokens.pop((job_id, attempt), None)


class Threads:
    """Counts this attempt's live worker threads, so its unpublished files are
    removed only AFTER the last one ends (codex-verdict-next-24 §1 (5)).

    `hold()` wraps a thread body. `close(cleanup)` runs `cleanup` now when no
    thread is live, else in the last thread as it leaves. A thread that ignores
    the stop and writes on is therefore never raced by the delete, and a body
    the pool only starts after `close` does not run at all."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live = 0
        self._closed = False
        self._cleanup: Callable[[], None] | None = None

    def hold(self) -> "_Held":
        return _Held(self)

    def wrap(self, fn: Callable[[], Any]) -> Callable[[], Any]:
        def run() -> Any:
            with self.hold():
                return fn()
        return run

    def close(self, cleanup: Callable[[], None]) -> bool:
        """True when the cleanup ran here (no thread was live)."""
        with self._lock:
            self._closed = True
            if self._live:
                self._cleanup = cleanup
                return False
        cleanup()
        return True

    def _enter(self) -> None:
        with self._lock:
            if self._closed:
                raise Stopped("analysis attempt closed before this thread started")
            self._live += 1

    def _leave(self) -> None:
        with self._lock:
            self._live -= 1
            run = self._cleanup if self._live == 0 else None
            if run is not None:
                self._cleanup = None
        if run is not None:
            run()


class _Held:
    def __init__(self, threads: Threads) -> None:
        self._threads = threads

    def __enter__(self) -> None:
        self._threads._enter()

    def __exit__(self, *exc: object) -> None:
        self._threads._leave()
