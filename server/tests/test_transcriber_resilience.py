"""
One bad chunk must not cost the whole file.

Measured on a 4-hour VOD: faster-whisper's `find_alignment` raised
`IndexError: boolean index did not match indexed array` on one chunk past the
halfway mark, and because its segment iterator is LAZY the crash landed inside
the consuming loop rather than in the call that created it. Two hours of
finished transcription went with it.

Chunking exists to bound memory on a long source. It only bounds damage too if
a failure inside one chunk stays inside it.
"""

from __future__ import annotations

import pytest

from services.transcriber import _tolerant


def _raises_after(n, exc=RuntimeError("boom")):
    def gen():
        for i in range(n):
            yield i
        raise exc
    return gen()


def test_everything_before_the_error_is_kept():
    failed: list[int] = []
    assert list(_tolerant(_raises_after(3), 2, 5, failed)) == [0, 1, 2]
    assert failed == [2], "the chunk that broke should be named"


def test_the_real_failure_shape_is_survived():
    """The exact exception faster-whisper raised on the 4-hour run."""
    err = IndexError("boolean index did not match indexed array along axis 0")
    failed: list[int] = []
    assert list(_tolerant(_raises_after(2, err), 1, 1, failed)) == [0, 1]
    assert failed == [1]


def test_a_clean_chunk_reports_no_failure():
    failed: list[int] = []
    assert list(_tolerant(iter([1, 2, 3]), 1, 3, failed)) == [1, 2, 3]
    assert failed == []


def test_a_chunk_that_fails_immediately_yields_nothing_and_is_named():
    failed: list[int] = []
    assert list(_tolerant(_raises_after(0), 4, 9, failed)) == []
    assert failed == [4]


def test_an_empty_chunk_is_not_a_failure():
    failed: list[int] = []
    assert list(_tolerant(iter([]), 1, 1, failed)) == []
    assert failed == []


def test_the_worker_error_no_longer_asserts_a_cause():
    """It used to say the worker had run out of memory. The two real crashes
    it was seen on were an alignment bug and a missing __main__ guard, and the
    message sent an hour of debugging at RAM each time."""
    import inspect

    from services import transcriber

    src = inspect.getsource(transcriber.transcribe)
    assert "exited without returning a result" in src
    assert "usually means the worker ran out of memory" not in src
