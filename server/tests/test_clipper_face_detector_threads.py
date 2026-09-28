"""FD1: the Haar cascades under concurrent use (ND0).

Two analyses in one process detected faces on the SAME CascadeClassifier
instances at once and got answers a single run does not give, and a thread
that arrived during the process-wide lazy load read the half-built list as
"no cascades". The fix gives every thread its own instances, each list built
whole before it is stored. These pin that, and that one thread's answer is
the one the shared detector gave.
"""
from __future__ import annotations

import hashlib
import json
import threading
import types

import cv2
import numpy as np
import pytest

from services.clipper import face_detector


def _fake_cv2(make):
    return types.SimpleNamespace(CascadeClassifier=make, equalizeHist=lambda grey: grey,
                                 data=types.SimpleNamespace(haarcascades="cascades"))


def test_first_use_racing_a_load_gets_a_whole_list_of_its_own(monkeypatch):
    """A thread asking while another is mid-load never gets [] or half a list."""
    entered, release = threading.Event(), threading.Event()
    built = []

    class _SlowCascade:
        def __init__(self, path):
            built.append(path)
            entered.set()
            release.wait(5)

        def empty(self):
            return False

    monkeypatch.setattr(face_detector, "_LOCAL", threading.local())
    monkeypatch.setattr(face_detector, "_cv2", lambda: _fake_cv2(_SlowCascade))
    got = {}

    def ask(who):
        got[who] = list(face_detector.face_cascades())   # a copy AT RETURN

    loader = threading.Thread(target=ask, args=("loader",))
    loader.start()
    assert entered.wait(5)
    racer = threading.Thread(target=ask, args=("racer",))
    racer.start()
    racer.join(0.3)
    release.set()
    loader.join(5)
    racer.join(5)
    assert [len(got["loader"]), len(got["racer"])] == [2, 2]
    assert not {id(c) for c in got["loader"]} & {id(c) for c in got["racer"]}
    assert len(built) == 4      # two per thread, each thread once


def test_no_cascade_instance_is_used_by_two_threads(monkeypatch):
    made = []

    class _Recorder:
        def __init__(self, path):
            self.threads = set()
            made.append(self)

        def empty(self):
            return False

        def detectMultiScale(self, image, *args, **kwargs):
            self.threads.add(threading.get_ident())
            return []

    monkeypatch.setattr(face_detector, "_LOCAL", threading.local())
    monkeypatch.setattr(face_detector, "_cv2", lambda: _fake_cv2(_Recorder))
    grey = np.zeros((36, 64), dtype=np.uint8)
    start = threading.Barrier(4)

    def work():
        start.wait()
        for _ in range(10):
            face_detector.detect_faces(grey)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(made) == 8                                   # two per thread, loaded once
    assert [len(c.threads) for c in made] == [1] * 8        # each used by ONE thread
    assert len({t for c in made for t in c.threads}) == 4


def _frames() -> list:
    """16 grey frames: a real face (skimage's astronaut portrait) pasted on seeded noise.

    Small on purpose. On the shared-cascade detector this set disagreed with
    the single run in every thread of every round measured (4 threads, 6
    rounds, 1–6 of 16 frames each), in about a second per round.
    """
    data = pytest.importorskip("skimage.data")
    face = cv2.cvtColor(data.astronaut(), cv2.COLOR_RGB2GRAY)
    rng = np.random.default_rng(7)
    out = []
    for i in range(16):
        canvas = rng.integers(0, 255, size=(180, 320), dtype=np.uint8)
        side = 60 + (i * 13) % 100
        x, y = (i * 37) % (320 - side), (i * 23) % (180 - side)
        canvas[y:y + side, x:x + side] = cv2.resize(face, (side, side))
        out.append(canvas)
    return out


# One thread's answer on _frames(), from the shared-cascade detector at 3229423
# (opencv-python 4.14.0): 10 of 16 frames with a face. FD1 must not move it.
_ALONE_SHA256 = "f5a9c91f6466293c17665ae2bb518ba2758719eee3e652764647cdcc7de9e6fc"


def test_one_thread_answers_as_the_shared_detector_did():
    alone = [face_detector.detect_faces(f) for f in _frames()]
    assert sum(1 for boxes in alone if boxes) == 10
    assert hashlib.sha256(json.dumps(alone).encode()).hexdigest() == _ALONE_SHA256


def test_warm_threads_detect_exactly_what_one_thread_does():
    frames = _frames()
    alone = [face_detector.detect_faces(f) for f in frames]
    assert sum(1 for boxes in alone if boxes) >= 8          # faces to disagree about
    for _ in range(2):
        loaded: list = [None] * 4
        results: list = [None] * 4
        start = threading.Barrier(4)

        def work(k):
            loaded[k] = len(face_detector.face_cascades())  # warm before the race
            start.wait()
            results[k] = [face_detector.detect_faces(f) for f in frames]

        threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert loaded == [2] * 4
        assert [len(r or []) for r in results] == [len(frames)] * 4   # every frame, before content
        assert [[i for i, (a, b) in enumerate(zip(alone, r)) if a != b] for r in results] == [[]] * 4
