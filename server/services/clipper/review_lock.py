"""Serialize review answers across the two local backend processes.

An atomic JSON replacement prevents torn reads, not lost updates. Each writer
must hold the same OS lock from read through save and feedback. Acquisition is
nonblocking: a competing request retries instead of freezing the event loop.
The OS releases the lock on process exit. The small lock file stays in place;
unlinking it would let two writers lock different files under the same name.
"""

from contextlib import contextmanager
import os
from pathlib import Path


class ReviewBusy(Exception):
    pass


@contextmanager
def answer_lock(session_path: Path):
    with session_path.with_suffix(".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ReviewBusy("review_answer_in_progress") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
