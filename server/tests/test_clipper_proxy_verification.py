"""AD12-r: what a proxy's provenance proves, and when it is written.

(a) the full hash is its own progress stage, stops between two blocks on a
    cancel, publishes nothing then, and the cancel is not swallowed as an
    optional failure; a retry records normally;
(b) the cache (fingerprint) and the full verification are distinct answers: a
    mid-file edit that keeps size, mtime and both ends is invisible to the
    first, which says so, and caught by the second;
(c) the record names the ffmpeg executable identified BEFORE the encode, and a
    file that changes while it is recorded is refused, not recorded.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from job_queue import JobCancelledError
from services.clipper import ingest, proxy_clock, proxy_provenance as P, storage

BUILD = dict(proxy_clock.VALIDATED_CLOCKS[0]["ffmpeg_build"], exe="no-such-ffmpeg.exe",
             exe_size=None, exe_mtime_ns=None)
_RECIPE = P.proxy_recipe("S", "O", 480, 10.0)[1]
VERIFYING = "Verifying source and proxy (sha256)"


@pytest.fixture
def pure(monkeypatch):
    """No ffprobe/ffmpeg: fixed stream fields, the build above, 64 KiB hash blocks."""
    monkeypatch.setattr(P, "_HASH_BLOCK", 1 << 16)
    monkeypatch.setattr(P, "ffmpeg_build", lambda exe=None: dict(BUILD))
    monkeypatch.setattr(P, "probe", lambda p: {"streams": [{"codec_type": "video", "r_frame_rate": "60/1",
                                                            "avg_frame_rate": "60/1", "time_base": "1/15360",
                                                            "start_time": "0.000000", "start_pts": 0}],
                                               "format": {"start_time": "0.000000"}})
    monkeypatch.setattr(ingest, "video_info", lambda p: {"width": 1920, "fps": 60, "duration": 5})


def _fake_encode(monkeypatch, seen: list | None = None):
    async def fake_ffmpeg(cmd, *, timeout, what):
        if seen is not None:
            seen.append(("encode", cmd[0]))
        Path(cmd[-1]).write_bytes(b"proxy" * 40000)          # 200 KB: 4 blocks of 64 KiB
    monkeypatch.setattr(ingest, "_ffmpeg", fake_ffmpeg)


def _source(tmp_path: Path, n: int = 3 << 20) -> Path:
    src = tmp_path / "source.mp4"
    src.write_bytes(hashlib.sha256(b"seed").digest() * (n // 32))
    return src


# ── (a) its own stage, cooperative cancel, retry ─────────────────────────────

async def test_a_cancel_during_the_hash_stops_it_publishes_nothing_and_a_retry_records(
        tmp_path, pure, monkeypatch):
    pid = "ad12rcancel"
    src = _source(tmp_path)                                 # 48 blocks of 64 KiB
    _fake_encode(monkeypatch)
    await ingest.build_proxy(pid, str(src))                 # an earlier, complete record
    assert P.read_provenance(pid)["state"] == "recorded"
    st = src.stat()                                         # a new mtime: the source is rehashed
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))

    progress, checks = [], []

    def cancelled():
        checks.append(1)
        return len(checks) > 5
    with pytest.raises(JobCancelledError):
        await ingest.build_proxy(pid, str(src), on_progress=lambda f, m: progress.append((f, m)),
                                 is_cancelled=cancelled)
    assert progress == [(0.5, VERIFYING)]                   # one event for the stage, none per block
    assert len(checks) == 6                                 # stopped at the 6th block, not after 48
    assert not P._path(pid, P.PROVENANCE_FILE).exists()     # neither the old record nor a partial one
    assert P.read_provenance(pid, source_path=src)["state"] == "provenance_missing"

    progress.clear()
    out = await ingest.build_proxy(pid, str(src), on_progress=lambda f, m: progress.append((f, m)),
                                   is_cancelled=lambda: False)
    raw = P.read_raw(pid)
    assert progress == [(0.5, VERIFYING)]
    assert raw["source_identity"]["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert raw["source_identity"]["sha256_how"] == P.FULL_HASH
    assert raw["proxy_identity"]["sha256"] == hashlib.sha256(Path(out).read_bytes()).hexdigest()
    assert P.read_provenance(pid, source_path=src, proxy_path=out)["state"] == "recorded"


async def test_the_pipeline_reports_verification_inside_the_proxy_stage_and_cancels_through_the_queue(
        tmp_path, pure, monkeypatch):
    from database import async_session
    from models import ProjectModel
    from workers import clipper_pipeline

    pid = "ad12rpipe"
    src = _source(tmp_path)
    _fake_encode(monkeypatch)

    async def fake_ingest_source(project_id, **kw):
        return {"video_path": str(src), "duration": 5.0, "width": 1920, "height": 1080, "fps": 60.0,
                "filesize": src.stat().st_size}
    monkeypatch.setattr(ingest, "ingest_source", fake_ingest_source)
    async with async_session() as session:
        session.add(ProjectModel(id=pid, title="ad12r", source_kind="upload", video_path=str(src),
                                 status="queued", processing_mode="clipping"))
        await session.commit()

    class Queue:
        def __init__(self):
            self.progress, self.armed, self.checks = [], None, 0

        async def update_progress(self, job_id, p, m=""):
            self.progress.append((round(p, 4), m))
            if m == VERIFYING:
                self.armed = self.checks

        def is_cancelled(self, job_id):
            self.checks += 1
            return self.armed is not None and self.checks - self.armed > 3

    q = Queue()
    with pytest.raises(JobCancelledError):
        await clipper_pipeline.handle_ingest("ad12rjob", pid, None, {}, q)
    assert (0.725, VERIFYING) in q.progress and q.progress[-1] == (0.725, VERIFYING)
    assert q.checks - q.armed == 4                          # stopped inside the hash, by the queue
    assert P.read_provenance(pid)["state"] == "provenance_missing"


# ── (b) the cache is not a verification ──────────────────────────────────────

def test_a_mid_file_edit_with_the_same_size_mtime_and_ends_is_invisible_to_the_cache_and_caught_in_full(
        tmp_path, pure):
    pid = "ad12rmid"
    storage.ensure_dirs(pid)
    src, prx = _source(tmp_path), tmp_path / "proxy.mp4"
    prx.write_bytes(b"p" * 5000)
    P.record(pid, src, prx, _RECIPE, build=BUILD)
    registered = P.read_raw(pid)
    assert registered["source_identity"]["sha256_how"] == P.FULL_HASH

    ok = P.verify_provenance(pid, source_path=src, proxy_path=prx)
    assert ok["state"] == "verified" and ok["identity_check"] == {"source": P.FULL_HASH, "proxy": P.FULL_HASH}
    assert P.read_raw(pid) == registered                    # a verification writes nothing back

    st = src.stat()
    fp = P.partial_fingerprint(src)
    b = bytearray(src.read_bytes())
    b[len(b) // 2] ^= 0xFF                                  # the middle: not in the first or last MiB
    src.write_bytes(bytes(b))
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert P.partial_fingerprint(src) == fp                 # the edit is invisible to the fingerprint

    cached = P.read_provenance(pid, source_path=src, proxy_path=prx)
    assert cached["state"] == "recorded"                    # it cannot see it...
    assert cached["identity_check"] == {"source": P.CACHE_REUSE, "proxy": P.CACHE_REUSE}   # ...and says so
    assert cached["state"] != "verified"
    reused = P.record(pid, src, prx, _RECIPE, previous=P.read_raw(pid), build=BUILD)
    assert reused["source_identity"]["sha256_how"] == P.CACHE_REUSE
    assert reused["source_identity"]["sha256"] != hashlib.sha256(bytes(b)).hexdigest()

    full = P.verify_provenance(pid, source_path=src, proxy_path=prx)
    assert full["state"] == "identity_changed" and full["reason"].startswith("source content differs")
    assert full["clock"]["state"] != "validated"


def test_a_read_never_presents_itself_as_a_full_verification(tmp_path, pure):
    pid = "ad12rread"
    storage.ensure_dirs(pid)
    src, prx = _source(tmp_path, 1 << 16), tmp_path / "proxy.mp4"
    prx.write_bytes(b"p")
    P.record(pid, src, prx, _RECIPE, build=BUILD)
    P.verify_provenance(pid, source_path=src, proxy_path=prx)
    assert P.read_provenance(pid, proxy_path=prx)["identity_check"] == {"source": "not_checked",
                                                                         "proxy": P.CACHE_REUSE}
    rows = [P.frame_row("frame_00000.jpg", 0.0, "decoded", {"pts": 0, "time_base": "1/10240"})]
    assert P.record_frames(pid, prx, rows)["proxy"]["identity_check"] == P.CACHE_REUSE


def test_a_full_verification_is_cancellable_between_blocks(tmp_path, pure):
    pid = "ad12rvcancel"
    storage.ensure_dirs(pid)
    src, prx = _source(tmp_path), tmp_path / "proxy.mp4"
    prx.write_bytes(b"p")
    P.record(pid, src, prx, _RECIPE, build=BUILD)
    n = []

    def check():
        n.append(1)
        if len(n) > 2:
            raise JobCancelledError("cancelled")
    with pytest.raises(JobCancelledError):
        P.verify_provenance(pid, source_path=src, proxy_path=prx, check=check)
    assert len(n) == 3


# ── (c) the executable identified before the encode; refusal on change ──────

async def test_the_record_names_the_executable_identified_before_the_encode(tmp_path, pure, monkeypatch):
    pid = "ad12rexe"
    src = _source(tmp_path, 1 << 16)
    events, recorded = [], {}
    resolutions = iter(["C:/ffmpeg-used/ffmpeg.exe", "C:/ffmpeg-later/ffmpeg.exe"])
    monkeypatch.setattr(ingest, "ffmpeg_bin", lambda: next(resolutions))
    monkeypatch.setattr(P, "ffmpeg_bin", lambda: "C:/ffmpeg-later/ffmpeg.exe")   # what "now" would say
    monkeypatch.setattr(P, "ffmpeg_build", lambda exe=None: events.append(("build", exe or P.ffmpeg_bin()))
                        or dict(BUILD, exe=exe or P.ffmpeg_bin()))
    monkeypatch.setattr(P, "record", lambda *a, **k: recorded.update(k))
    _fake_encode(monkeypatch, events)
    await ingest.build_proxy(pid, str(src))
    assert events == [("build", "C:/ffmpeg-used/ffmpeg.exe"), ("encode", "C:/ffmpeg-used/ffmpeg.exe")]
    assert recorded["build"]["exe"] == "C:/ffmpeg-used/ffmpeg.exe"


def test_record_writes_the_build_it_was_given_not_one_it_resolves(tmp_path, pure, monkeypatch):
    pid = "ad12rgiven"
    storage.ensure_dirs(pid)
    src, prx = _source(tmp_path, 1 << 16), tmp_path / "proxy.mp4"
    prx.write_bytes(b"p")
    monkeypatch.setattr(P, "ffmpeg_bin", lambda: "C:/ffmpeg-later/ffmpeg.exe")
    monkeypatch.setattr(P, "ffmpeg_build", lambda exe=None: dict(BUILD, exe=exe or P.ffmpeg_bin()))
    P.record(pid, src, prx, _RECIPE, build=dict(BUILD, exe="C:/ffmpeg-used/ffmpeg.exe"))
    assert P.read_raw(pid)["ffmpeg_build"]["exe"] == "C:/ffmpeg-used/ffmpeg.exe"


@pytest.mark.parametrize("who", ["source", "proxy"])
def test_a_file_that_changes_while_it_is_recorded_is_refused(tmp_path, pure, who):
    pid = f"ad12rchg{who}"
    storage.ensure_dirs(pid)
    src, prx = _source(tmp_path), tmp_path / "proxy.mp4"
    prx.write_bytes(b"p" * (1 << 18))
    target = src if who == "source" else prx
    # check() runs before each block: the source's 48 blocks + its EOF read come
    # first, so call 2 is inside the source's hash and call 51 inside the proxy's.
    at, calls = (2 if who == "source" else 51), []

    def change_once():                                      # a writer appends while the hash runs
        calls.append(1)
        if len(calls) == at:
            with open(target, "ab") as fh:
                fh.write(b"more")
    with pytest.raises(P.ProvenanceRefused, match=f"{who} changed while it was being recorded"):
        P.record(pid, src, prx, _RECIPE, build=BUILD, check=change_once)
    assert len(calls) > at
    assert P.read_raw(pid) is None


async def test_an_executable_replaced_during_the_encode_leaves_no_record(tmp_path, pure, monkeypatch):
    pid = "ad12rexechg"
    src = _source(tmp_path, 1 << 16)
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"build one")
    st = exe.stat()
    monkeypatch.setattr(ingest, "ffmpeg_bin", lambda: str(exe))
    monkeypatch.setattr(P, "ffmpeg_build", lambda exe=None: dict(BUILD, exe=exe, exe_size=st.st_size,
                                                                 exe_mtime_ns=st.st_mtime_ns))

    async def encode_then_upgrade(cmd, *, timeout, what):
        Path(cmd[-1]).write_bytes(b"proxy")
        exe.write_bytes(b"build two, a winget upgrade")
    monkeypatch.setattr(ingest, "_ffmpeg", encode_then_upgrade)
    assert await ingest.build_proxy(pid, str(src))          # the ingest goes on...
    assert P.read_raw(pid) is None                          # ...with no record: provenance_missing
    assert P.read_provenance(pid)["state"] == "provenance_missing"
