"""The attempt a handler invocation runs as — fixed at the queue's successful claim (BURST R1c).

`job_recovery._claim` returns it from the UPDATE that took the job (RETURNING the attempt_count it
wrote), and `JobQueue._process_next` sets it in the handler's OWN task before calling the handler:
everything the handler awaits, and every task it creates, sees it; nothing else does. It is never
re-read from the job row. Between the claim and any later read another worker — or this worker's
own retry — may have claimed the job, and a handler that read the row then published under the NEW
attempt's identity (codex-verdict-next-25 §1, `next25-check/probe_capture.py`).
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import NamedTuple


class ClaimedAttempt(NamedTuple):
    job_id: str
    attempt: int
    worker: str


CLAIMED_ATTEMPT: ContextVar[ClaimedAttempt | None] = ContextVar("claimed_attempt", default=None)
