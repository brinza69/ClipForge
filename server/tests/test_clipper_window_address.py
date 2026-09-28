"""window_address on constructed stats rows: the tie, the composition, every refusal.

Pure: no ffmpeg. The rows are what the window encode's `-stats_enc_pre` writes
(CC1-C0 §2: `n == j`, `ptsi` = demuxer PTS after ts_offset, in the proxy tb).
The real-ffmpeg counterpart, with barcodes as the witness, is
test_clipper_window_address_real.py.
"""
from __future__ import annotations

import inspect
from fractions import Fraction

import pytest

from services.clipper import proxy_clock as C, window_address as A, window_address_rows as R

PTB = "1/10240"
STARTS = ["12.340", "0.020", "0.050", "0.080", "0.250", "12.345",
          "0.000", "5.020", "5.050", "5.080", "5.250", "16.050"]
BUILD = dict(A.VALIDATED_TIES[0]["ffmpeg_build"])
ARGV = ["-y", "-loglevel", "error", "-ss", "12.340", "-i", "p.mp4", "-t", "6.000", "-an", "-c:v",
        "libx264", "-preset", "ultrafast", "-crf", "24", *A.stats_options("s.txt"), "w.mp4"]
SRC_N = 60 * 30                                          # 30 s of 60 fps source, tb 1/15360
SRC = ([k * 256 for k in range(SRC_N)], [256] * SRC_N, (1, 15360))


def _rows(start: str, n: int = 59, *, skip_at: int | None = None, dup_at: int | None = None,
          enc0: int | None = None) -> list[dict]:
    """The rows of a steady window: frame j shows proxy slot first+j. `skip_at` drops the proxy
    frame before window frame j (a gap: ptsi and enc pts jump); `dup_at` repeats frame j-1."""
    off = A.ts_offset(start, (1, 10240))
    first = -(-Fraction(start) * 10 // 1)                              # first slot at or after start
    e0 = enc0 if enc0 is not None else round(first - Fraction(start) * 10)
    rows, slot, enc = [], int(first), e0
    for j in range(n):
        if j and j == skip_at:
            slot, enc = slot + 1, enc + 1
        ni = j + (1 if skip_at is not None and j >= skip_at else 0)
        if j and j == dup_at:
            rows.append({**rows[-1], "n": j, "pts": enc})
        else:
            rows.append({"n": j, "ni": ni, "tb": "1/10", "pts": enc, "tbi": PTB, "ptsi": slot * 1024 - off})
            slot += 1
        enc += 1
    return rows


def _text(rows: list[dict]) -> str:
    return "".join(f"{r['n']} {r['ni']} {r['tb']} {r['pts']} {r['tbi']} {r['ptsi']}\n" for r in rows)


def _prov(**over) -> dict:
    ident = {"sha256": "p", "fingerprint": {"size": 1}}
    return {"state": "recorded", "recorded_at": "t0", "ffmpeg_build": {"exe_sha256": "e"},
            "identity_check": {"source": "cache_reuse", "proxy": "cache_reuse"},
            "clock": {"state": "validated", "clock": "test"}, "domain": {"state": "ok"},
            "proxy_identity": {**ident, "stream": {"r_frame_rate": "10/1", "avg_frame_rate": "10/1",
                                                   "time_base": PTB, "start_time": "0", "start_pts": 0,
                                                   "nb_frames": 300}, "format": {"start_time": "0"}},
            "source_identity": {**ident, "sha256": "s", "path": "src.mp4",
                                "stream": {"r_frame_rate": "60/1", "avg_frame_rate": "60/1",
                                           "time_base": "1/15360", "start_time": "0", "start_pts": 0,
                                           "nb_frames": SRC_N}, "format": {"start_time": "0"}},
            **over}


def _seen(rows: list[dict], step: int, reads: int | None = None, **over) -> dict:
    n = len(rows) if reads is None else reads
    return {"opened": True, "backend": "FFMPEG", "opencv": "4.14.0", "decoder": {"avcodec": "61.19.100"},
            "fps": 10.0, "step": step, "stop": {"after_reads": n, "pos_frames": float(n)},
            "reads": [{"index": j, "pos_frames": float(j),
                       "pos_msec": float((rows[j]["pts"] - rows[0]["pts"]) * 100)} for j in range(n)],
            "sampled": list(range(0, n, step)), **over}


def _face(j: int, rows: list[dict], **over) -> dict:
    return {"t": j / 10, "boxes": [], "state": "empty", "reason": None, "frame_index": j,
            "decoded_t": (rows[j]["pts"] - rows[0]["pts"]) / 10, "address_basis": "opencv_ffmpeg_metadata",
            **over}


def _compose(rows, *, start="12.340", faces=None, seen_motion=None, seen_panels=None, before=None,
             after=None, build=None, recipe=None, packets=None, stats_text=None) -> dict:
    seen_motion = _seen(rows, 2) if seen_motion is None else seen_motion
    before = _prov() if before is None else before
    return A.compose(
        request={"start": start, "duration": "6.000"}, recipe=recipe or A.recipe_of(ARGV),
        stats_text=_text(rows) if stats_text is None else stats_text,
        faces=[_face(j, rows) for j in range(0, len(rows), 3)] if faces is None else faces,
        motion_count=len(seen_motion.get("sampled") or []), seen_motion=seen_motion,
        seen_panels=_seen(rows, 5) if seen_panels is None else seen_panels,
        before=before, read_after=lambda: before if after is None else after,
        build=BUILD if build is None else build, read_packets=packets or (lambda *a: SRC))


# ── the stats and the tie ────────────────────────────────────────────────────

def test_parse_rejects_what_is_not_a_stats_file():
    rows = _rows("12.340", 3)
    assert A.parse_enc_stats(_text(rows)) == rows
    for bad in (None, "", "\n", "0 0 1/10 1 1/10240\n", "0 x 1/10 1 1/10240 5\n"):
        assert A.parse_enc_stats(bad) is None


@pytest.mark.parametrize("start,off", [("12.340", 126362), ("0.020", 205), ("0.050", 512), ("0.080", 819),
                                       ("0.250", 2560), ("12.345", 126413), ("0.000", 0)])
def test_ts_offset_is_ffmpegs_near_inf_rescale_of_the_string(start, off):
    assert A.ts_offset(start, (1, 10240)) == off
    # The composer reads the argv STRING ffmpeg parsed, never the float it was printed from:
    # 12.345 as a double is not a whole number of microseconds.
    assert (Fraction(12.345) * 10**6).denominator != 1 and f"{12.345:.3f}" == "12.345"


@pytest.mark.parametrize("start", STARTS)
def test_the_twelve_phases_tie_every_frame_to_its_slot(start):
    rows = _rows(start)
    tie = A.tie_window(rows, start, PTB)
    first = int(-(-Fraction(start) * 10 // 1))
    assert tie["state"] == "ok" and tie["offset"] == A.ts_offset(start, (1, 10240))
    assert [f["slot"] for f in tie["frames"]] == list(range(first, first + 59))
    assert all(f["duplicate_of"] is None for f in tie["frames"])


def test_face_time_is_relative_to_the_first_window_frame_not_the_first_sample():
    rows = _rows("12.340", enc0=1)                          # start_pts 1024: 9 of the 12 phases
    tie = A.tie_window(rows, "12.340", PTB)
    assert A.frame_of(3, 0.3, tie) == (3, None)
    assert A.frame_of(3, 0.4, tie)[1].startswith("sample_frame_unknown")      # the MP4 clock: refused
    got = _compose(rows, faces=[_face(3, rows), _face(5, rows)])       # first sample is j=3, not 0
    assert [(f["state"], f["window_frame"]) for f in got["faces"]] == [("addressed", 3), ("addressed", 5)]
    for bad in (None, True, 3.0, -1, 59):
        assert A.frame_of(bad, 0.3, tie)[0] is None


def test_a_gap_is_a_pts_jump_and_only_the_pair_across_it_is_refused():
    rows = _rows("12.340", skip_at=20)
    got = _compose(rows)
    assert got["state"] == "ok"
    pairs = [m["pair"] for m in got["motion"]]
    bad = [p for p in pairs if p["state"] == "refused"]
    assert len(bad) == 1 and bad[0]["reason"].startswith("pair_not_steady") and bad[0]["slots"] == [142, 145]
    assert all(m["point"]["state"] == "addressed" for m in got["motion"])
    spans = [p for p in pairs if p["state"] == "span"]
    assert {p["proxy_duration"] for p in spans} == {"1/5"}      # never step/fps: the gap pair is not in it


def test_equal_ni_is_a_duplicate_and_a_span_across_it_is_not_steady():
    rows = _rows("12.340", dup_at=10)
    tie = A.tie_window(rows, "12.340", PTB)
    assert tie["frames"][10]["duplicate_of"] == 9 and tie["frames"][10]["slot"] == tie["frames"][9]["slot"]
    got = _compose(rows)
    assert got["motion"][5]["reason"].startswith("pair_not_steady")         # reads 8 -> 10
    assert got["motion"][5]["point"]["duplicate_of"] == 9


@pytest.mark.parametrize("mutate,state", [
    (lambda r: r[7].update(ptsi=r[7]["ptsi"] + 3), "window_tie_off_grid"),   # drop3's off-grid survivor
    (lambda r: r[7].update(tbi="1/12800"), "pts_unknown"),
    (lambda r: r[7].update(ni=-1), "window_tie_unavailable"),
    (lambda r: r[7].update(n=8), "window_tie_unavailable"),
    (lambda r: r[7].update(tb="1/20"), "window_tie_unavailable"),
])
def test_one_bad_row_refuses_every_sample(mutate, state):
    rows = _rows("12.340")
    mutate(rows)
    got = _compose(rows)
    assert got["state"] == state and got["reason"].startswith(state)
    assert all(r["state"] == "refused" and r["refused_at"] == "window" for r in got["faces"] + got["motion"])
    assert got["panels"]["state"] == "refused"


def test_missing_stats_is_a_refused_tie_not_missing_provenance_and_back():
    rows = _rows("12.340")
    assert _compose(rows, stats_text="")["state"] == "window_tie_unavailable"
    calls = []
    got = _compose(rows, before={"state": "provenance_missing", "clock": C.clock_validation(None, None)},
                   packets=lambda *a: calls.append(a))
    assert got["state"] == "provenance_missing" and calls == [] and got["tie"] == {"state": None}


def test_changed_identity_before_or_after_refuses_every_row():
    rows = _rows("12.340")
    before = _compose(rows, before={"state": "identity_changed", "reason": "proxy fingerprint differs"})
    assert before["state"] == "identity_changed"
    moved = _prov()
    moved["proxy_identity"] = {**moved["proxy_identity"], "fingerprint": {"size": 2}}
    after = _compose(rows, after=moved)
    assert after["state"] == "identity_changed" and "after the last address" in after["reason"]
    assert {r["refused_at"] for r in after["faces"] + after["motion"]} == {"window"}


@pytest.mark.parametrize("change", ["build", "recipe", "opencv", "avcodec"])
def test_a_build_recipe_or_decoder_change_is_unvalidated(change):
    rows = _rows("12.340")
    kw: dict = {}
    if change == "build":
        kw["build"] = {**BUILD, "exe_sha256": "0" * 64}
    elif change == "recipe":
        kw["recipe"] = A.recipe_of([t if t != "24" else "23" for t in ARGV])
    else:
        seen = _seen(rows, 2)
        if change == "opencv":
            seen["opencv"] = "4.15.0"
        else:
            seen["decoder"] = {"avcodec": "62.0.0"}
        kw["seen_motion"] = seen
    got = _compose(rows, packets=lambda *a: pytest.fail("no ffprobe on an unvalidated reader"), **kw)
    assert got["state"] == "window_reader_unvalidated"


@pytest.mark.parametrize("fields", [("opencv",), ("backend",), ("avcodec",), ("opencv", "backend", "avcodec")])
@pytest.mark.parametrize("how", ["absent", "null"])
def test_an_unknown_reader_component_is_never_the_validated_reader(fields, how):
    """next-26 §3 (C1r): a missing backend or avcodec used to validate and address every face."""
    rows = _rows("12.340")
    seen = _seen(rows, 2)
    for f in fields:
        if f == "avcodec":
            seen["decoder"] = {"avcodec": None} if how == "null" else {}
        elif how == "null":
            seen[f] = None
        else:
            del seen[f]
    got = _compose(rows, seen_motion=seen, packets=lambda *a: pytest.fail("no ffprobe on an unvalidated reader"))
    assert got["state"] == "window_reader_unvalidated" and all(f in got["reason"] for f in fields)
    assert {r["state"] for r in got["faces"] + got["motion"]} == {"refused"}
    assert got["coverage"]["motion"]["opened"] is True and got["coverage"]["motion"]["reads"] == len(rows)


def test_the_complete_validated_reader_addresses():
    got = _compose(_rows("12.340"))
    assert got["state"] == "ok" and got["reader"] == got["reader_panels"] == A.VALIDATED_TIES[0]["reader"]
    assert any(f["state"] == "addressed" for f in got["faces"]) and got["panels"]["state"] == "aggregate"


@pytest.mark.parametrize("change", ["backend_null", "backend_other", "decoder_absent"])
def test_the_motion_reader_does_not_certify_the_panels_reader(change):
    rows = _rows("12.340")
    panels = _seen(rows, 5)
    if change == "backend_null":
        panels["backend"] = None
    elif change == "backend_other":
        panels["backend"] = "MSMF"
    else:
        del panels["decoder"]
    got = _compose(rows, seen_panels=panels)
    assert got["state"] == "ok" and any(f["state"] == "addressed" for f in got["faces"])
    assert got["panels"]["state"] == "refused"
    assert got["panels"]["reason"].startswith("panels_reader_unvalidated")
    assert got["coverage"]["panels"]["reads"] == len(rows)


def test_the_recipe_placeholders_do_not_collide():
    same = [t if t != "6.000" else "12.340" for t in ARGV]          # start and duration equal strings
    assert A.recipe_of(same)["template_sha256"] == A.VALIDATED_TIES[0]["template_sha256"]


def test_a_source_that_cannot_be_read_refuses_the_window():
    def boom(*a):
        raise ValueError("source_window_unreadable (ffprobe rc=1)")
    got = _compose(_rows("12.340"), packets=boom)
    assert got["state"] == "source_window_unreadable"


def test_a_shifted_source_refuses_per_sample_not_per_window():
    pts, durs, tb = SRC
    shifted = ([p + (100 if k > 1000 else 0) for k, p in enumerate(pts)], durs, tb)
    got = _compose(_rows("12.340"), packets=lambda *a: shifted)
    assert got["state"] == "ok"
    reasons = {f["reason"].split(" ")[0] for f in got["faces"] if f["state"] == "refused"}
    assert reasons == {"vfr_local"} and any(f["state"] == "addressed" for f in got["faces"])


def test_the_first_motion_entry_is_initialisation_and_its_point_is_addressed():
    got = _compose(_rows("12.340"))
    m0 = got["motion"][0]
    assert m0["state"] == "motion_init" and m0["point"]["state"] == "addressed"
    assert got["counts"]["motion"] == {"motion_init": 1, "span": 29}


def test_a_stop_in_the_last_step_minus_one_frames_passes_the_count_but_not_the_coverage():
    rows = _rows("12.340", 60)
    got = _compose(rows, seen_motion=_seen(rows, 2, reads=59), seen_panels=_seen(rows, 5, reads=59))
    cov = got["coverage"]["motion"]
    assert cov["state"] == "incomplete" and cov["reads"] == 59 and cov["rows"] == 60
    assert cov["count_check_aux"] is True                    # the auxiliary check cannot see it
    early = _compose(rows, seen_motion=_seen(rows, 2, reads=22))
    assert early["coverage"]["motion"]["count_check_aux"] is False
    assert all(x is None or isinstance(x, bool) for x in (cov["count_check_aux"], cov["opened"]))


def test_a_capture_that_did_not_open_is_not_no_panels():
    rows = _rows("12.340")
    shut = {"opened": False, "opencv": "4.14.0", "backend": None, "decoder": {"avcodec": "61.19.100"},
            "step": 2, "reads": [], "sampled": [], "stop": {"after_reads": 0, "pos_frames": 0.0}}
    got = _compose(rows, seen_panels={**shut, "step": 5})
    assert got["panels"]["state"] == "refused" and got["panels"]["reason"].startswith("capture_not_opened")
    assert got["coverage"]["panels"]["state"] == "not_opened"
    # Both captures shut: the motion reader's backend is unknown, so the WINDOW is refused (C1r),
    # and the coverage still says what was read.
    both = _compose(rows, seen_motion=shut, seen_panels={**shut, "step": 5})
    assert both["state"] == "window_reader_unvalidated" and both["panels"]["state"] == "refused"
    assert both["coverage"]["motion"]["state"] == both["coverage"]["panels"]["state"] == "not_opened"
    assert _compose(rows, seen_panels={**_seen(rows, 5), "sampled": []})["panels"]["reason"] == "no_frame_read"


def test_a_read_whose_reported_frame_disagrees_is_refused_but_a_hidden_frame_is_not_seen():
    rows = _rows("12.340")
    seen = _seen(rows, 5)
    seen["reads"][10]["pos_msec"] += 100.0                   # OpenCV names another frame: refused
    got = _compose(rows, seen_panels=seen)
    assert got["panels"]["observed"] == 11 and got["panels"]["refused"] == 1
    # read 39 showing frame 38's pixels reports 39's index and time: the telemetry passes
    # it, so the output must never call the read verified.
    assert got["integrity"].startswith("unverified")


def test_tracking_is_kept_apart_from_the_raw_observation():
    rows = _rows("12.340")
    seed, target = _face(5, rows, state="detected", boxes=[[1, 2, 3, 4]]), _face(8, rows)
    target["motion"] = {"state": "tracked", "method": "lk_fb_v1", "age_s": 0.3, "reason": None,
                        "seed_frame_index": 5, "seed_decoded_t": seed["decoded_t"],
                        "target_frame_index": 8, "target_decoded_t": target["decoded_t"]}
    refused = _face(10, rows, motion={"state": "refused", "method": "lk_fb_v1", "reason": "support_collapse",
                                      "seed_frame_index": 5, "seed_decoded_t": 9.9,
                                      "target_frame_index": 10, "target_decoded_t": 1.0})
    unread = _face(13, rows, state="unreadable", frame_index=None, decoded_t=None, reason="frame_not_decoded")
    got = _compose(rows, faces=[seed, target, refused, unread])
    f = got["faces"]
    assert f[1]["observation"] == "empty" and f[1]["state"] == "addressed"
    assert f[1]["tracking"]["state"] == "tracked" and f[1]["tracking"]["seed"]["slot"] == f[0]["slot"]
    assert f[1]["tracking"]["target"]["slot"] == f[1]["slot"]
    assert f[2]["observation"] == "empty" and f[2]["tracking"]["reason"] == "support_collapse"
    assert f[2]["tracking"]["seed"]["state"] == "refused"               # its decoded time is wrong
    assert f[3]["observation"] == "unreadable" and f[3]["reason"].startswith("sample_unreadable")
    assert got["counts"]["faces_tracked"] == 1


def test_counts_rebuild_the_denominator():
    got = _compose(_rows("12.340"))
    c = got["counts"]
    assert sum(c["faces"].values()) == c["denominator"]["faces"] == len(got["faces"])
    assert sum(c["motion"].values()) == c["denominator"]["motion"] == len(got["motion"])
    nr = A.not_requested(25, 30)
    assert nr["state"] == "not_requested" and nr["counts"]["not_requested"] == 55


def test_a_span_is_not_a_cut_interval():
    got = _compose(_rows("12.340"))
    span = got["motion"][1]["pair"]
    assert span["state"] == "span" and "hypothesis" not in span and "source_index" not in span
    assert not C.interval_contains(span, span["source_pts_through"])
    a, b = got["motion"][0]["point"], got["motion"][1]["point"]
    ends = [{**p, "addressing_version": C.ADDRESSING_VERSION} for p in (a, b)]
    assert C.interval_for_event(*ends, isolated=True)["reason"].startswith("samples_not_adjacent")


def test_no_function_here_can_receive_audio_or_the_transcript():
    banned = {"cand", "candidate", "words", "peaks", "audio", "transcript", "segments"}
    for mod in (A, R):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__:
                assert not banned & set(inspect.signature(fn).parameters), name
    got = _compose(_rows("12.340"))
    flat = repr(got)
    assert "corrected" not in flat and "'t':" not in flat


# ── the production caller never sees the offset (addendum §4.3) ──────────────

async def test_dynamic_plan_is_unchanged_by_window_addressed(monkeypatch, tmp_path):
    """`_dynamic_plan` with today's window dict and with `window_addressed` added: the
    planner's inputs, the plan, `_rhythm` and the export argv are equal. Audio peaks,
    words and scenes stay on the candidate's source clock either way."""
    from services.clipper import dynamic_edit, dynamic_render, dynamic_rhythm, dynamic_window
    from workers import clipper_render_plan as plan_mod

    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"x")
    signals = {"proxy_width": 480, "proxy_height": 270, "scenes": [108.0, 117.5],
               "audio": {"peaks": [103.2, 111.0, 121.4]}}
    words = [{"word": f"w{k}", "start": 100.0 + k * 0.7, "end": 100.4 + k * 0.7} for k in range(40)]
    monkeypatch.setattr(plan_mod.storage, "paths", lambda _pid: {"proxy": proxy})
    monkeypatch.setattr(plan_mod.storage, "read_artifact", lambda _pid, _name: signals)
    monkeypatch.setattr(plan_mod, "_candidate", lambda clip: {
        "start": 100.0, "end": 130.0, "text": "t", "headline": "", "words": words})
    n = 121
    base = {"faces": [{"t": 100.0 + k * 0.25, "boxes": [[40 + (k % 5), 30, 60, 60]], "state": "detected",
                       "clock": "source_requested"} for k in range(n)],
            "motion": [float((k * 7) % 11) for k in range(60)], "focus": [200.0 + k for k in range(60)],
            "detail": [30.0 + k % 3 for k in range(60)], "ui": [0.0] * 60, "motion_hop": 0.2,
            "band": (0.25, 1.0, 0.0, 0.8), "hop": 0.25, "proxy_width": 480, "proxy_height": 270,
            "panels": [], "face_decoded_space": "reencoded_window"}
    seen: list = []
    real_plan, real_cut = dynamic_edit.plan_dynamic_edit, dynamic_rhythm.cut_boundaries
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit",
                        lambda *a, **k: seen.append(("plan", a, k)) or real_plan(*a, **k))
    monkeypatch.setattr(dynamic_rhythm, "cut_boundaries",
                        lambda *a, **k: seen.append(("rhythm", a, k)) or real_cut(*a, **k))

    class Clip:
        id, project_id = "c1", "p1"

    class Project:
        id = "p1"

    out = []
    for extra in ({}, {"window_addressed": _compose(_rows("100.000"))}):
        monkeypatch.setattr(dynamic_window, "analyse_window", lambda *a, _e=extra, **k: {**base, **_e})
        seen.clear()
        plan = await plan_mod._dynamic_plan(Clip(), Project(), 1920, 1080)
        assert plan is not None and len(plan["shots"]) >= 2
        work = {k: v for k, v in plan.items() if not k.startswith("_")}
        argv = dynamic_render.build_dynamic_cmd("src.mp4", work, "cmd.txt", "a.ass", "out.mp4",
                                                start=100.0, duration=30.0, src_w=1920, src_h=1080)
        out.append((repr(seen), plan, argv))
    assert out[0][0] == out[1][0]                        # the planner and the rhythm got the same inputs
    assert out[0][1] == out[1][1] and out[0][1]["_rhythm"] == out[1][1]["_rhythm"]
    assert out[0][2] == out[1][2]
    assert "window_addressed" not in out[1][1]


# ── the guards a clean window never trips ────────────────────────────────────

def test_a_read_whose_pos_frames_is_not_its_ordinal_is_refused():
    rows = _rows("12.340")
    seen = _seen(rows, 5)
    seen["reads"][15]["pos_frames"] = 16.0
    got = _compose(rows, seen_panels=seen)
    assert got["panels"]["refused"] == 1 and got["panels"]["observed"] == 11


def test_sampled_reads_that_do_not_match_the_values_refuse_every_motion_entry():
    rows = _rows("12.340")
    seen = _seen(rows, 2)
    seen["sampled"] = seen["sampled"][:-1]
    got = A.compose(request={"start": "12.340", "duration": "6.000"}, recipe=A.recipe_of(ARGV),
                    stats_text=_text(rows), faces=[], motion_count=30, seen_motion=seen,
                    seen_panels=_seen(rows, 5), before=_prov(), read_after=_prov, build=BUILD,
                    read_packets=lambda *a: SRC)
    assert {m["reason"].split(" ")[0] for m in got["motion"]} == {"motion_reads_inconsistent"}
    assert len(got["motion"]) == 30


def test_a_face_without_the_ffmpeg_address_basis_or_marked_unreadable_is_refused():
    rows = _rows("12.340")
    got = _compose(rows, faces=[_face(3, rows, address_basis=None),
                                _face(6, rows, state="unreadable", reason="decode_error")])
    assert got["faces"][0]["reason"].startswith("sample_frame_unknown (address basis")
    assert got["faces"][1]["reason"].startswith("sample_unreadable") and got["faces"][1]["observation"] == "unreadable"


def test_a_steady_pair_whose_source_does_not_advance_is_refused():
    tie = A.tie_window(_rows("12.340"), "12.340", PTB)
    pt = {"state": "addressed", "source_pts": 1000, "source_index": 3, "source_time_base": "1/15360"}
    assert R._pair((0, pt), (2, pt), 2, tie)["reason"].startswith("not_steady")
    assert R._pair((0, pt), (2, {**pt, "source_pts": 1512}), 2, tie)["state"] == "span"
