"""AD12 step 2: build_proxy provenance and the decoded PTS of stored frames.

- the recipe (its own version) is recorded apart from the addressing version, and
  the command build_proxy runs is the command it ran before;
- the ffmpeg build identity, and a FULL sha256 of source and proxy computed once
  and reused, checked by fingerprint afterwards, never rehashed per read;
- sample_frames records, per stored frame, the PTS ffmpeg decoded, parallel to
  the requested time, with JPEGs byte-identical to the legacy grab;
- a project built before all this reads as `provenance_missing`, is not stale,
  and `/retry` resumes it exactly as before.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from services.clipper import ingest, proxy_clock, proxy_provenance as P, storage
from services.clipper.ffmpeg_tools import ffmpeg_bin, ffprobe_bin

_HAVE_FFMPEG = bool(shutil.which(ffmpeg_bin()) and shutil.which(ffprobe_bin()))
real = pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _legacy_proxy_cmd(video_path, out, target_w, target_fps):
    """build_proxy's list as it was at fd76b8f (ingest.py:339-354), literally."""
    return [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-an", "-sn", "-dn",
        "-vf", f"scale={target_w}:-2:flags=bilinear",
        "-r", f"{target_fps:g}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-pix_fmt", "yuv420p",
        "-g", f"{max(1, int(round(target_fps)))}",
        "-movflags", "+faststart",
        str(out),
    ]


def _legacy_frame_cmd(video_path, t, out, quality):
    """_frame_cmd as it was at fd76b8f (ingest.py:453-461)."""
    return [ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video_path),
            "-frames:v", "1", "-an", "-sn", "-dn", "-q:v", str(quality), str(out)]


# ── the recipe and the command ───────────────────────────────────────────────

@pytest.mark.parametrize("src_w,src_fps,want_w,want_fps", [
    (1920, 60.0, 480, 10.0), (3840, 23.976, 480, 10.0), (320, 30.0, 320, 10.0), (1280, 8.0, 480, 8.0),
])
async def test_build_proxy_runs_the_same_command_as_before(monkeypatch, src_w, src_fps, want_w, want_fps):
    pid = f"ad12cmd{src_w}"
    seen = {}
    monkeypatch.setattr(ingest, "video_info", lambda p: {"width": src_w, "fps": src_fps, "duration": 5})

    async def fake_ffmpeg(cmd, *, timeout, what):
        seen["cmd"] = cmd
    monkeypatch.setattr(ingest, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(P, "record", lambda *a, **k: seen.setdefault("recipe", a[3]))
    src = "C:/media/{width} odd {out} name.mp4"          # braces in a path are not placeholders
    out = await ingest.build_proxy(pid, src)
    assert seen["cmd"] == _legacy_proxy_cmd(src, out, want_w, want_fps)
    r = seen["recipe"]
    assert r["version"] == P.PROXY_RECIPE_VERSION
    assert r["params"] == {"width": want_w, "fps": f"{want_fps:g}", "gop": f"{round(want_fps)}"}
    v = proxy_clock.clock_validation(r, proxy_clock.VALIDATED_CLOCKS[0]["ffmpeg_build"])
    assert v["state"] == ("validated" if (want_w, want_fps) == (480, 10.0) else "unvalidated")


def test_the_recipe_is_the_validated_template_and_its_version_is_pinned():
    assert P.PROXY_RECIPE == proxy_clock.VALIDATED_RECIPE_TEMPLATE
    # Edit a token and this fails: bump PROXY_RECIPE_VERSION with the new hash.
    assert (P.PROXY_RECIPE_VERSION, proxy_clock.template_sha256(P.PROXY_RECIPE)) == (
        "clipper-proxy-recipe-1", "218b32e9a1c5ddc004b6512bfa35e1d887629a4d0140861e5836e24c55bf688b")
    assert P.PROXY_RECIPE_VERSION != proxy_clock.ADDRESSING_VERSION


def test_the_thumbnail_grab_is_unchanged():
    out = Path("C:/x/thumb.jpg")
    assert ingest._frame_cmd("C:/v.mp4", 12.3456, out, quality=3) == _legacy_frame_cmd(
        "C:/v.mp4", 12.3456, out, 3)


async def test_a_failed_record_neither_fails_the_ingest_nor_leaves_the_old_record(monkeypatch):
    pid = "ad12recfail"
    storage.ensure_dirs(pid)
    storage.atomic_write_json(P._path(pid, P.PROVENANCE_FILE), {"recipe": {}, "ffmpeg_build": {}})
    monkeypatch.setattr(ingest, "video_info", lambda p: {"width": 1920, "fps": 60, "duration": 5})

    async def fake_ffmpeg(cmd, *, timeout, what):
        return None
    monkeypatch.setattr(ingest, "_ffmpeg", fake_ffmpeg)

    def boom(*a, **k):
        raise RuntimeError("ffprobe exploded")
    monkeypatch.setattr(P, "record", boom)
    assert await ingest.build_proxy(pid, "C:/src.mp4")
    assert not P._path(pid, P.PROVENANCE_FILE).exists()
    assert P.read_provenance(pid)["state"] == "provenance_missing"


# ── identity: full hash once, fingerprint afterwards ─────────────────────────

BUILD = dict(proxy_clock.VALIDATED_CLOCKS[0]["ffmpeg_build"], exe="ffmpeg.exe")
_RECIPE = P.proxy_recipe("S", "O", 480, 10.0)[1]


@pytest.fixture
def pure(monkeypatch):
    """record() without ffprobe/ffmpeg: fixed stream fields, counted full hashes."""
    hashed = []
    real_sha = P.sha256_file
    monkeypatch.setattr(P, "ffmpeg_build", lambda exe=None: dict(BUILD))
    monkeypatch.setattr(P, "probe", lambda p: {"streams": [{"codec_type": "video", "r_frame_rate": "60/1",
                                                            "avg_frame_rate": "60/1", "time_base": "1/15360",
                                                            "start_time": "0.000000", "start_pts": 0}],
                                               "format": {"start_time": "0.000000"}})
    monkeypatch.setattr(P, "sha256_file", lambda p, *a, **k: hashed.append(Path(p).name) or real_sha(p, *a, **k))
    return hashed


def test_record_hashes_both_files_in_full_and_a_rebuild_reuses_the_source_hash(tmp_path, pure):
    pid = "ad12cache"
    storage.ensure_dirs(pid)
    src, prx = tmp_path / "source.mp4", tmp_path / "proxy.mp4"
    src.write_bytes(bytes(range(256)) * 20000)            # > 2 MiB: head and tail differ from the middle
    prx.write_bytes(b"p" * 5000)
    doc = P.record(pid, src, prx, _RECIPE, build=BUILD)
    assert pure == ["source.mp4", "proxy.mp4"]
    assert doc["source_identity"]["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert doc["proxy_identity"]["sha256"] == hashlib.sha256(prx.read_bytes()).hexdigest()
    assert doc["recipe_version"] == P.PROXY_RECIPE_VERSION
    assert doc["addressing_version_at_build"] == proxy_clock.ADDRESSING_VERSION

    pure.clear()                                           # a retry rebuilds the proxy
    again = P.record(pid, src, prx, _RECIPE, previous=P.read_raw(pid), build=BUILD)
    assert pure == ["proxy.mp4"]
    assert again["source_identity"]["sha256"] == doc["source_identity"]["sha256"]
    assert again["source_identity"]["sha256_how"] == P.CACHE_REUSE

    b = bytearray(src.read_bytes())                         # same size, one byte in the middle:
    b[len(b) // 2] ^= 1                                     # the fingerprint cannot see it...
    src.write_bytes(bytes(b))
    pure.clear()
    third = P.record(pid, src, prx, _RECIPE, previous=P.read_raw(pid), build=BUILD)
    # ...but the write moved mtime, so the cache is not trusted and the file is rehashed.
    assert "source.mp4" in pure
    assert third["source_identity"]["sha256"] == hashlib.sha256(bytes(b)).hexdigest()


def test_reading_checks_the_fingerprint_and_never_rehashes(tmp_path, pure, monkeypatch):
    pid = "ad12noreh"
    storage.ensure_dirs(pid)
    src, prx = tmp_path / "s.mp4", tmp_path / "p.mp4"
    src.write_bytes(b"s" * 3000)
    prx.write_bytes(b"p" * 3000)
    P.record(pid, src, prx, _RECIPE, build=BUILD)
    monkeypatch.setattr(P, "sha256_file", lambda *a: pytest.fail("a read must not rehash"))
    r = P.read_provenance(pid, source_path=src, proxy_path=prx)
    assert r["state"] == "recorded" and r["clock"]["state"] == "validated"
    prx.write_bytes(b"q" * 3001)
    r = P.read_provenance(pid, proxy_path=prx)
    assert r["state"] == "identity_changed" and r["clock"]["state"] != "validated"


def test_the_clock_is_revalidated_on_read_not_taken_from_the_record(tmp_path, pure, monkeypatch):
    pid = "ad12reval"
    storage.ensure_dirs(pid)
    (tmp_path / "s").write_bytes(b"s")
    (tmp_path / "p").write_bytes(b"p")
    P.record(pid, tmp_path / "s", tmp_path / "p", _RECIPE, build=BUILD)
    assert P.read_raw(pid)["clock_at_build"]["state"] == "validated"
    monkeypatch.setattr(proxy_clock, "VALIDATED_CLOCKS", ())
    assert P.read_provenance(pid)["clock"]["state"] == "unvalidated"


# ── legacy projects, stale, retry ────────────────────────────────────────────

def _legacy_project(pid: str, media: Path) -> None:
    from services.clipper import ANALYSIS_VERSION

    storage.ensure_dirs(pid)
    storage.write_artifact(pid, "meta", {"analysis_version": ANALYSIS_VERSION,
                                         "source": {"filesize": media.stat().st_size},
                                         "proxy": {"width": 480, "fps": 10}})
    storage.write_artifact(pid, "signals", {"scenes": [1.0]})
    storage.write_artifact(pid, "faces", {"samples": [], "times": [1.0, 2.0]})
    p = storage.paths(pid)
    p["proxy"].write_bytes(b"proxy")
    p["audio"].write_bytes(b"audio")


def test_a_legacy_project_is_provenance_missing_and_its_legacy_read_is_untouched(tmp_path):
    pid = "ad12legacy"
    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(4096))
    _legacy_project(pid, media)
    faces_before = storage.paths(pid)["faces"].read_bytes()
    prov = P.read_provenance(pid, proxy_path=storage.paths(pid)["proxy"])
    assert prov["state"] == "provenance_missing" and prov["clock"]["state"] == "provenance_missing"
    r = proxy_clock.address_slot(500 * 1024, {}, {}, [], clock=prov["clock"],
                                 source_nb_frames=None, proxy_nb_frames=None)
    assert r["state"] == "refused" and r["reason"].startswith("provenance_missing")
    assert P.read_frames(pid) == {"state": "provenance_missing"}
    assert storage.read_artifact(pid, "faces") == {"samples": [], "times": [1.0, 2.0]}
    assert storage.paths(pid)["faces"].read_bytes() == faces_before
    assert storage.stale_artifacts(pid, media) is None


def test_no_recipe_addressing_or_reader_version_makes_an_analysis_stale(tmp_path, monkeypatch):
    pid = "ad12nostale"
    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(4096))
    _legacy_project(pid, media)
    storage.atomic_write_json(P._path(pid, P.PROVENANCE_FILE), {
        "recipe_version": "clipper-proxy-recipe-0", "addressing_version_at_build": "proxy-clock-0",
        "recipe": {"version": "clipper-proxy-recipe-0"}, "ffmpeg_build": {}})
    storage.atomic_write_json(P._path(pid, P.FRAMES_FILE), {"reader_version": "old", "frames": []})
    assert storage.stale_artifacts(pid, media) is None
    monkeypatch.setattr(proxy_clock, "ADDRESSING_VERSION", "proxy-clock-99")
    monkeypatch.setattr(P, "PROXY_RECIPE_VERSION", "clipper-proxy-recipe-99")
    monkeypatch.setattr(P, "READER_VERSION", "frame-grab-99")
    assert storage.stale_artifacts(pid, media) is None


@pytest.mark.parametrize("provenance", ["none", "foreign_versions"])
async def test_retry_resumes_where_it_did_before(client, tmp_path, provenance):
    from database import async_session
    from models import JobModel, ProjectModel, TranscriptModel

    pid = f"ad12rt{provenance[:4]}"
    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(4096))
    _legacy_project(pid, media)
    if provenance == "foreign_versions":
        storage.atomic_write_json(P._path(pid, P.PROVENANCE_FILE), {
            "recipe_version": "clipper-proxy-recipe-0", "addressing_version_at_build": "x",
            "recipe": {}, "ffmpeg_build": {}})
    async with async_session() as session:
        session.add(ProjectModel(id=pid, title="ad12", source_kind="file", video_path=str(media),
                                 status="failed", processing_mode="clipping"))
        session.add(JobModel(id=f"{pid}j", project_id=pid, type="clipper_analyze", status="failed",
                             error="boom"))
        # OW1 (next-24 §1 (3)): with no transcript the resume is transcribe.
        session.add(TranscriptModel(project_id=pid, segments=[{"start": 0, "end": 1, "text": "a"}]))
        await session.commit()
    r = await client.post(f"/api/clipper/projects/{pid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["resumed_at"] == "clipper_analyze"


# ── stored frames: the decoded PTS ───────────────────────────────────────────

def test_parse_showinfo_takes_the_one_written_frame():
    tb = "[Parsed_showinfo_0 @ 000001a2] config in time_base: 1/10240, frame_rate: 10/1\n"
    one = "[Parsed_showinfo_0 @ 000001a2] n:   0 pts:  12288 pts_time:1.2 duration:1024\n"
    # -frames:v 1 on 8.1.1 still shows the next frame (n:1) to the filter; it is not written.
    lookahead = one + one.replace("n:   0 pts:  12288", "n:   1 pts:  13312")
    assert P.parse_showinfo(tb + one) == {"pts": 12288, "time_base": "1/10240"}
    assert P.parse_showinfo(tb + lookahead) == {"pts": 12288, "time_base": "1/10240"}
    assert P.parse_showinfo(tb) is None                     # no frame
    assert P.parse_showinfo(one) is None                    # no time_base
    assert P.parse_showinfo(tb + one + one) is None         # two n:0: which one was written?
    assert P.parse_showinfo(tb + tb + one) is None


async def test_a_failed_or_unreadable_grab_is_its_own_row_never_decoded(monkeypatch, tmp_path):
    pid = "ad12rows"
    calls = iter([RuntimeError("rc=1"), None, {"pts": 1024, "time_base": "1/10240"}])

    def fake(cmd, *, timeout, what):
        assert "-copyts" in cmd and "showinfo" in cmd
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        Path(cmd[-1]).write_bytes(b"jpeg")
        return nxt
    monkeypatch.setattr(P, "run_showinfo", fake)
    written = await ingest.sample_frames(pid, str(tmp_path / "proxy.mp4"), [0.5, 0.7, 0.1])
    assert [Path(w).name for w in written] == ["frame_00001.jpg", "frame_00002.jpg"]
    doc = P.read_frames(pid)
    assert (doc["requested"], doc["decoded"], doc["pts_unknown"], doc["failed"]) == (3, 1, 1, 1)
    assert [f["state"] for f in doc["frames"]] == ["failed", "pts_unknown", "decoded"]
    assert [f["t_requested"] for f in doc["frames"]] == [0.5, 0.7, 0.1]
    assert doc["frames"][2]["slot"] == 1 and doc["frames"][1]["slot"] is None
    assert doc["proxy"] == {"provenance": "provenance_missing", "sha256": None, "identity_check": None}
    assert doc["reader_version"] == P.READER_VERSION


@real
async def test_build_proxy_and_sample_frames_on_real_media(tmp_path, monkeypatch):
    pid = "ad12real"
    src = tmp_path / "source.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=60:d=4", "-c:v", "libx264", "-preset", "ultrafast",
                    "-bf", "0", "-pix_fmt", "yuv420p", str(src)], check=True)
    proxy = await ingest.build_proxy(pid, str(src))
    raw = P.read_raw(pid)
    assert raw["source_identity"]["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert raw["proxy_identity"]["sha256"] == hashlib.sha256(Path(proxy).read_bytes()).hexdigest()
    assert raw["proxy_identity"]["stream"]["r_frame_rate"] == "10/1"
    assert raw["recipe"]["params"] == {"width": 480, "fps": "10", "gop": "10"}
    build = raw["ffmpeg_build"]
    assert build["exe_sha256"] == hashlib.sha256(Path(build["exe"]).read_bytes()).hexdigest()
    prov = P.read_provenance(pid, source_path=src, proxy_path=proxy)
    assert prov["state"] == "recorded" and prov["domain"]["state"] == "ok"
    same = all(build[k] == v for k, v in proxy_clock.VALIDATED_CLOCKS[0]["ffmpeg_build"].items())
    assert prov["clock"]["state"] == ("validated" if same else "unvalidated")

    monkeypatch.setattr(P, "sha256_file", lambda *a: pytest.fail("sample_frames must not rehash"))
    times = [0.0, 1.234, 2.05, 3.5, 60.0]
    written = await ingest.sample_frames(pid, proxy, times)
    doc = P.read_frames(pid)
    assert doc["requested"] == 5 and doc["decoded"] == 4 and doc["failed"] == 1
    assert doc["proxy"] == {"provenance": "recorded", "sha256": raw["proxy_identity"]["sha256"],
                            "identity_check": P.CACHE_REUSE}
    decoded = [f for f in doc["frames"] if f["state"] == "decoded"]
    assert [f["t_requested"] for f in decoded] == times[:4]
    slots = _gray(proxy)                                     # every proxy frame, slot order
    for f, path in zip(decoded, written):
        assert f["proxy_time_base"] == "1/10240" and f["slot"] is not None
        assert abs(f["slot"] / 10 - f["t_requested"]) <= 0.1 + 1e-9
        legacy = tmp_path / f"legacy_{f['file']}"
        subprocess.run(_legacy_frame_cmd(proxy, f["t_requested"], legacy, 4), check=True)
        assert Path(path).read_bytes() == legacy.read_bytes(), f       # byte-identical to the old grab
        # The recorded PTS is the frame IN the JPEG (not the look-ahead showinfo also prints):
        # its content is closest to that slot's, by a margin.
        mad = [float(abs(s.astype(int) - _gray(Path(path))[0].astype(int)).mean()) for s in slots]
        best = sorted(range(len(mad)), key=mad.__getitem__)
        assert best[0] == f["slot"] and mad[best[1]] > 2 * mad[best[0]], (f, mad[best[0]], mad[best[1]])
    assert decoded[0]["slot"] == 0


def _gray(media: Path):
    import numpy as np

    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(media), "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 270, 480)
