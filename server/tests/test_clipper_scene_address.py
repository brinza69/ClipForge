"""Addressing step 3 (3a + 3b), pure: no ffmpeg, no media.

3a: the scene pass keeps every selected frame's integer PTS and still returns the
legacy `scenes` list. 3b: scenes_addressed, its state table, the (e) counts,
the isolation verdict and the windowed source reader. Contract:
B/addressing-lot3-addendum.md + codex-verdict-next-9.md §2. The real-ffmpeg half
is test_clipper_scene_address_real.py.
"""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction

import numpy as np
import pytest

from services.clipper import ANALYSIS_VERSION, proxy_clock as C, scene_address as S, storage

TB = "1/10240"
HDR = "[Parsed_showinfo_1 @ 0000015186b08080] "


def _pt(p: int, tb: str = TB) -> str:
    n, d = (int(x) for x in tb.split("/"))
    return f"{p * n / d:.6g}"


def outputs(pts: list, tb: str = TB, *, info_pts: list | None = None, tbs: int = 1) -> tuple[str, str]:
    """(metadata stdout, showinfo stderr) of a scene pass that selected frames with these PTS."""
    out = "".join(f"frame:{k:<4} pts:{p:<7} pts_time:{_pt(p, tb)}\nlavfi.scene_score=0.7{k}\n"
                  for k, p in enumerate(pts))
    err = "".join(f"{HDR}config in time_base: {tb}, frame_rate: 10/1\n" for _ in range(tbs))
    err += f"{HDR}config out time_base: 0/0, frame_rate: 0/0\n"
    err += "".join(f"{HDR}n:{k:>4} pts:{p:>7} pts_time:{_pt(p, tb):<7} duration:   1024\n"
                   for k, p in enumerate(pts if info_pts is None else info_pts))
    return out, err


# ── 3a: parse, legacy equality, the (e) rows ─────────────────────────────────

def test_rows_keep_integer_pts_and_the_legacy_list_is_the_old_expression():
    out, err = outputs([18432, 44032, 69632])
    got = S.parse_scene_output(out, err)
    assert got["mismatch"] is None and got["time_base"] == TB
    assert got["times"] == [1.8, 4.3, 6.8] == S.legacy_times(out)
    assert [(r["id"], r["proxy_pts"], r["slot"], r["legacy_index"]) for r in got["rows"]] == \
        [(0, 18432, 18, 0), (1, 44032, 43, 1), (2, 69632, 68, 2)]


@pytest.mark.parametrize("info_pts,tbs,why", [
    ([18432], 1, "metadata 2 frames, showinfo 1"),
    ([18432, 44033], 1, "frame 1"),
    ([18432, 44032], 2, "2 input time_bases"),
    ([18432, 44032], 0, "0 input time_bases"),
])
def test_a_disagreement_is_decode_mismatch_and_keeps_the_legacy_list(info_pts, tbs, why):
    out, err = outputs([18432, 44032], info_pts=info_pts, tbs=tbs)
    got = S.parse_scene_output(out, err)
    assert got["rows"] is None and why in got["mismatch"]
    assert got["times"] == [1.8, 4.3]        # the old montage does not move


def test_zero_negative_duplicate_and_rounding_collision_stay_in_the_denominator():
    # -1024 (negative: the legacy regex never reads it), 0 (t <= 0 filter), 10240 and 10241
    # (1.0 and 1.0000977: one legacy time after rounding), 20480 twice (a duplicate PTS).
    pts = [-1024, 0, 10240, 10241, 20480, 20480]
    out, err = outputs(pts)
    got = S.parse_scene_output(out, err)
    assert got["times"] == [1.0, 2.0] == S.legacy_times(out)
    rows = got["rows"]
    assert len(rows) == 6                                   # N: every selected frame is a row
    assert [r["legacy_index"] for r in rows] == [None, None, 0, 0, 1, 1]
    assert rows[0]["legacy_excluded"] == "pts_time not read by the legacy expression"
    assert rows[1]["legacy_excluded"].startswith("t_le_zero")
    assert rows[3]["slot"] is None and rows[2]["slot"] == 10  # the float is not the key
    doc = S.assemble({"state": "ok", "times": got["times"], "rows": rows, "time_base": TB},
                     {"state": "provenance_missing", "clock": C.clock_validation(None, None)},
                     lambda: {"state": "provenance_missing"}, _never)
    c = doc["counts"]
    assert (c["detected"], c["legacy"]) == (6, {"selected": 6, "eligible": 4, "unique": 2, "excluded": 2,
                                                "merged": 2})
    assert c["interval"] + c["refused"] == 6 and len(got["times"]) == c["legacy"]["unique"]


def test_rows_that_do_not_rebuild_the_legacy_list_are_a_mismatch():
    out, err = outputs([18432])
    got = S.parse_scene_output(out + "pts_time:9.5\n", err)   # a legacy time with no selected row
    assert got["rows"] is None and "rows rebuild 1 legacy times, stdout has 2" in got["mismatch"]
    assert got["times"] == [1.8, 9.5]


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _fake_run(monkeypatch, result):
    def run(cmd, **kw):
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(S.subprocess, "run", run)


def test_scene_pass_states_and_scene_timeline_returns_only_times(tmp_path, monkeypatch):
    from services.clipper import signals
    prx = tmp_path / "p.mp4"
    assert S.scene_pass(str(prx))["state"] == "proxy_missing"
    prx.write_bytes(b"x")
    _fake_run(monkeypatch, _Proc(1, "", "boom"))
    got = S.scene_pass(str(prx))
    assert (got["state"], got["times"], got["rows"]) == ("decode_failed", [], None)
    _fake_run(monkeypatch, subprocess.TimeoutExpired("ffmpeg", 3600))
    assert S.scene_pass(str(prx))["state"] == "decode_failed"
    out, err = outputs([18432, 44032], info_pts=[18432])
    _fake_run(monkeypatch, _Proc(0, out, err))
    got = S.scene_pass(str(prx))
    assert (got["state"], got["times"]) == ("decode_mismatch", [1.8, 4.3])
    out, err = outputs([18432, 44032])
    _fake_run(monkeypatch, _Proc(0, out, err))
    assert S.scene_pass(str(prx))["state"] == "ok"
    assert signals.scene_timeline(str(prx)) == [1.8, 4.3]


# ── 3b: isolation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mafd,want", [
    ([1, 40, 1], True), ([20, 40, 20], True), ([30, 40, 1], False), ([1, 40, 30], False),
    ([0, 0, 0], False), (None, None), ([1, 40], None),
])
def test_isolation_verdict_only_from_three_mafds(mafd, want):
    assert S.isolation_verdict(mafd) is want


def test_isolated_1_is_not_a_verdict():
    assert C.interval_for_event({}, {}, isolated=1)["reason"].startswith("isolation_not_a_verdict")


def _grab(n: int, emitted: int, info: list, tb: str = TB, w: int = 4, h: int = 2, values=(0, 0, 80, 80, 80)):
    raw = b"".join(bytes([values[i]]) * (w * h) for i in range(emitted))
    err = f"{HDR}config in time_base: {tb}, frame_rate: 10/1\n" + "".join(
        f"{HDR}n:{k:>4} pts:{p:>7} pts_time:x duration: 1024\n" for k, p in info)
    return S.bind_grab(raw, err, n, w, h, TB)


def test_bind_grab_binds_emitted_frames_and_ignores_one_lookahead_report():
    n = 50
    four = [(i, (n - 2 + i) * 1024) for i in range(4)]
    g = _grab(n, 4, four + [(4, (n + 2) * 1024)])            # showinfo saw a 5th frame
    assert g["pts"] == {48: 48 * 1024, 49: 49 * 1024, 50: 50 * 1024, 51: 51 * 1024}
    assert g["mafd"] == [0.0, 80.0, 0.0] and g["isolated"] is True


@pytest.mark.parametrize("emitted,info,tb,why", [
    (4, [(i, (48 + i) * 1024) for i in range(4)] + [(4, 0), (5, 0)], TB, "showinfo reported"),
    (4, [(0, 48 * 1024), (2, 49 * 1024), (3, 50 * 1024), (4, 51 * 1024)], TB, "showinfo reported"),
    (4, [(0, 48 * 1024), (1, 50 * 1024), (2, 51 * 1024), (3, 52 * 1024)], TB, "not slot 49"),
    (4, [(i, (48 + i) * 1024) for i in range(4)], "1/90000", "time_base"),
    (3, [(i, (48 + i) * 1024) for i in range(2)], TB, "showinfo reported"),
    (5, [(i, (48 + i) * 1024) for i in range(5)], TB, "5 frames emitted"),
])
def test_bind_grab_refuses_a_gap_an_ambiguity_or_another_time_base(emitted, info, tb, why):
    g = _grab(50, emitted, info, tb=tb)
    assert g["isolated"] is None and g["pts"] == {} and why in g["reason"]
    assert g["reason"].startswith("isolation_unknown")


def test_bind_grab_short_keeps_bound_pts_but_no_verdict_and_partial_bytes_refuse():
    g = _grab(50, 3, [(i, (48 + i) * 1024) for i in range(3)] + [(3, 51 * 1024)])
    assert g["isolated"] is None and g["mafd"] is None and set(g["pts"]) == {48, 49, 50}
    assert S.bind_grab(b"\0" * 9, f"{HDR}config in time_base: {TB}, x\n", 50, 4, 2, TB)["reason"] \
        .startswith("isolation_unknown (9 bytes")


def test_isolation_grab_before_the_stream_or_failing_is_unknown(monkeypatch):
    assert S.isolation_grab("p.mp4", 1, 4, 2, TB)["reason"].startswith("isolation_unknown (slot n-2")
    _fake_run(monkeypatch, _Proc(1, b"", b""))
    assert S.isolation_grab("p.mp4", 50, 4, 2, TB)["reason"] == "isolation_unknown (grab rc=1)"
    _fake_run(monkeypatch, subprocess.TimeoutExpired("ffmpeg", 120))
    assert S.isolation_grab("p.mp4", 50, 4, 2, TB)["isolated"] is None


# ── 3b: the windowed source reader ───────────────────────────────────────────

@pytest.mark.parametrize("payload,rc,why", [
    ({"streams": [{"time_base": "1/90000"}], "packets": []}, 0, "pts_unknown (source time_base 1/90000"),
    ({"streams": [{"time_base": "1/15360"}], "packets": [{"pts": 0}]}, 0, "window_unreadable (packet"),
    ({}, 1, "window_unreadable (ffprobe rc=1)"),
])
def test_source_reader_refuses_another_time_base_or_a_packet_without_pts(monkeypatch, payload, rc, why):
    _fake_run(monkeypatch, _Proc(rc, json.dumps(payload), ""))
    with pytest.raises(ValueError) as exc:
        S.read_source_packets("s.mp4", 50, "1/15360")
    assert str(exc.value).startswith(why)


def test_source_reader_sorts_and_restricts_the_read(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return _Proc(0, json.dumps({"streams": [{"time_base": "1/15360"}],
                                    "packets": [{"pts": 512, "duration": 256}, {"pts": 256, "duration": 256}]}))
    monkeypatch.setattr(S.subprocess, "run", run)
    assert S.read_source_packets("s.mp4", 50, "1/15360") == ([256, 512], [256, 256], (1, 15360))
    assert seen["cmd"][seen["cmd"].index("-read_intervals") + 1] == "2.900%6.000"


# ── 3b: the state table ──────────────────────────────────────────────────────

SRC_FPS, SRC_TBD, NSRC, NPRX = Fraction(60), 15360, 3600, 600
STEP = 256


def prov(**over) -> dict:
    p = {"state": "recorded", "recorded_at": "2026-09-26T00:00:00+00:00",
         "identity_check": {"source": "cache_reuse", "proxy": "cache_reuse"},
         "clock": {"state": "validated", "clock": "test clock"}, "domain": {"state": "ok"},
         "ffmpeg_build": {"exe_sha256": "e"},
         "proxy_identity": {"sha256": "p", "fingerprint": {"size": 1},
                            "stream": {"r_frame_rate": "10/1", "avg_frame_rate": "10/1", "time_base": TB,
                                       "start_time": "0.000000", "start_pts": "0", "nb_frames": str(NPRX),
                                       "width": 4, "height": 2},
                            "format": {"start_time": "0.000000"}},
         "source_identity": {"sha256": "s", "fingerprint": {"size": 2}, "path": "src.mp4",
                             "stream": {"r_frame_rate": "60/1", "avg_frame_rate": "60/1",
                                        "time_base": f"1/{SRC_TBD}", "start_time": "0.000000",
                                        "start_pts": "0", "nb_frames": str(NSRC)},
                             "format": {"start_time": "0.000000"}}}
    for k, v in over.items():
        if "__" in k:
            a, b, c = k.split("__")
            p[a][b][c] = v
        else:
            p[k] = v
    return p


def _never(row, ctx):
    raise AssertionError("a refused project must not address rows")


def scene(pts: list, state: str = "ok", tb: str = TB) -> dict:
    out, err = outputs(pts, tb)
    parsed = S.parse_scene_output(out, err)
    return {"state": state, "reason": None if state == "ok" else "why", "threshold": 0.3,
            "times": parsed["times"], "rows": parsed["rows"] if state == "ok" else None,
            "time_base": parsed["time_base"] if state == "ok" else None}


def _ok_counts(doc):
    c = doc["counts"]
    assert c["interval"] + c["refused"] == c["detected"] == len(doc["rows"])
    return c


@pytest.mark.parametrize("state", ["proxy_missing", "decode_failed", "decode_mismatch"])
def test_no_row_states_have_detected_null_and_keep_scenes(state):
    sc = scene([18432, 44032], state=state)
    doc = S.assemble(sc, prov(), lambda: prov(), _never)
    assert doc["state"] == state and doc["rows"] is None and doc["counts"]["detected"] is None
    assert doc["counts"]["legacy"]["unique"] == len(sc["times"])


@pytest.mark.parametrize("before,after,state,why", [
    ({"state": "provenance_missing", "clock": C.clock_validation(None, None)},
     {"state": "provenance_missing", "clock": C.clock_validation(None, None)},
     "provenance_missing", "provenance_missing"),
    (prov(clock={"state": "unvalidated", "reasons": ["ffmpeg_build_not_validated (libavutil differs)"]}), None,
     "clock_unvalidated", "clock_unvalidated (ffmpeg_build_not_validated"),
    ({"state": "identity_changed", "reason": "proxy fingerprint differs"}, None,
     "identity_changed", "identity_changed (proxy fingerprint differs)"),
    (prov(identity_check={"source": "not_checked", "proxy": "cache_reuse"}), None,
     "identity_changed", "identity_changed (not checked"),
    (prov(domain={"state": "refused", "reasons": ["source_vfr (r_frame_rate 60/1 != avg 59/1)"]}), None,
     "domain_refused", "domain_refused (source_vfr"),
    (prov(domain={"state": "refused"}), None, "domain_refused", "domain_refused (domain_not_ok)"),
    (prov(proxy_identity__stream__time_base=None), None, "domain_refused", "domain_refused (pts_unknown"),
    (prov(source_identity__stream__nb_frames="N/A"), None, "domain_refused", "nb_frames_unknown (source)"),
    (prov(proxy_identity__stream__nb_frames=None), None, "domain_refused", "nb_frames_unknown (proxy)"),
    (prov(), prov(recorded_at="later"), "identity_changed", "recorded after the step"),
    (prov(), {"state": "identity_changed"}, "identity_changed", "identity_changed after the step"),
])
def test_refused_project_states_refuse_every_row_and_keep_its_decode_facts(before, after, state, why):
    addresser = _never if after is None else _one_interval
    doc = S.assemble(scene([18432, 44032]), before, lambda: before if after is None else after, addresser, {})
    assert doc["state"] == state and why in doc["reason"]
    assert all(r["state"] == "refused" and r["refused_at"] == "project" and r["reason"] == doc["reason"]
               and "interval" not in r for r in doc["rows"])
    assert [r["proxy_pts"] for r in doc["rows"]] == [18432, 44032]
    c = _ok_counts(doc)
    assert c["refused"] == 2 and c["by_reason"] == {state: 2}


def _one_interval(row, ctx):
    return {"state": "interval", "reason": None, "interval": {"state": "interval"}}


def test_domain_refused_lists_every_reason():
    doc = S.assemble(scene([18432]), prov(domain={"state": "refused", "reasons": ["a (1)", "b (2)"]},
                                          source_identity__stream__nb_frames=None), prov, _never)
    assert doc["reasons"] == ["a (1)", "b (2)", "nb_frames_unknown (source)"]


def test_ok_with_no_selected_frame_is_detected_zero_not_null():
    doc = S.assemble(scene([]), prov(), prov, _never)
    assert doc["state"] == "ok" and doc["counts"]["detected"] == 0 and doc["rows"] == []


def test_a_row_without_a_known_state_breaks_the_count_and_voids_the_pass():
    doc = S.assemble(scene([18432]), prov(), prov, lambda r, c: {"state": "addressed", "reason": None})
    assert doc["state"] == "decode_mismatch" and doc["reason"].startswith("counts_inconsistent")
    assert doc["rows"] is None and doc["counts"]["detected"] is None


# ── 3b: address_row on a synthetic 60 fps source (the -7 rule, no ffmpeg) ────

def _wire(monkeypatch, *, iso=(1.0, 40.0, 1.0), src_tb=f"1/{SRC_TBD}", grab_pts=True):
    pts = [i * STEP for i in range(NSRC)]
    durs = [STEP] * NSRC

    def packets(path, n, registered, windowed=True):
        if src_tb != registered:
            raise ValueError(f"pts_unknown (source time_base {src_tb} != registered {registered})")
        return pts, durs, (1, SRC_TBD)

    def grab(path, n, w, h, tb):
        short = iso is None or n + 1 >= NPRX                # the proxy has no slot n+1
        mafd = None if short else list(iso)
        return {"isolated": S.isolation_verdict(mafd), "mafd": mafd,
                "reason": None if not short else "isolation_unknown (grab emitted 3 of slots)",
                "pts": {s: s * 1024 for s in range(n - 2, min(n + 2, NPRX))} if grab_pts else {}}
    monkeypatch.setattr(S, "read_source_packets", packets)
    monkeypatch.setattr(S, "isolation_grab", grab)
    monkeypatch.setattr(S, "provenance_now", lambda pid, path: prov())


def _address(slots: list[int], pts_extra: list[int] = ()) -> dict:
    return S.address_scenes("pid", "prx.mp4", scene([s * 1024 for s in slots] + list(pts_extra)), prov())


def test_every_cut_phase_lies_in_its_interval_with_both_bounds(monkeypatch):
    _wire(monkeypatch)
    for cut in range(600, 612):                      # every phase of two 6-frame slots
        n = next(k for k in range(NPRX) if 6 * k - 7 >= cut)
        doc = _address([n])
        (row,) = doc["rows"]
        assert row["state"] == "interval", row
        iv = row["interval"]
        assert set(iv) >= {"source_pts_after", "source_pts_through"} and not {"lo", "hi"} & set(iv)
        assert (iv["hypothesis"], iv["not_excluded"]) == ("single_cut", "two_cuts_between_samples")
        assert C.interval_contains(iv, cut * STEP)
        assert not C.interval_contains(iv, iv["source_pts_after"])                 # exclusive start
        assert C.interval_contains(iv, iv["source_pts_after"] + STEP)
        assert C.interval_contains(iv, iv["source_pts_through"])                   # inclusive end
        assert not C.interval_contains(iv, iv["source_pts_through"] + STEP)
        assert row["samples"]["current"]["source_index"] == 6 * n - 7
        assert doc["counts"]["interval"] == 1 and doc["provenance"]["identity_check"].startswith("cache_reuse")


@pytest.mark.parametrize("iso,why", [
    ((30.0, 40.0, 1.0), "needs_source_refinement"), ((1.0, 40.0, 30.0), "needs_source_refinement"),
    ((0.0, 0.0, 0.0), "needs_source_refinement"), (None, "isolation_unknown"),
])
def test_neighbours_that_also_changed_or_were_not_seen_get_no_interval(monkeypatch, iso, why):
    _wire(monkeypatch, iso=iso)
    (row,) = _address([100])["rows"]
    assert row["state"] == "refused" and row["reason"].startswith(why) and row["refused_at"] == "interval"
    assert row["isolation"]["mafd"] == (list(iso) if iso else None)


def test_address_refusals_keep_address_slot_reasons(monkeypatch):
    _wire(monkeypatch)
    doc = _address([1, NPRX], pts_extra=[100 * 1024 + 1])
    reasons = [r["reason"].split(" ")[0] for r in doc["rows"]]
    assert reasons == ["start_transient", "slot_not_in_proxy", "proxy_pts_off_grid"]
    assert doc["counts"]["by_reason"] == {"start_transient": 1, "slot_not_in_proxy": 1, "proxy_pts_off_grid": 1}
    assert [r["refused_at"] for r in doc["rows"]] == ["current", "current", "current"]


def test_another_source_time_base_or_an_untied_previous_slot_refuses(monkeypatch):
    _wire(monkeypatch, src_tb="1/90000")
    (row,) = _address([100])["rows"]
    assert row["reason"].startswith("pts_unknown (source time_base") and row["refused_at"] == "source_window"
    _wire(monkeypatch, grab_pts=False)
    (row,) = _address([100])["rows"]
    assert row["reason"].startswith("pts_unknown") and row["refused_at"] == "previous"


def test_the_last_slot_has_no_n_plus_1_and_is_isolation_unknown(monkeypatch):
    _wire(monkeypatch)
    (row,) = _address([NPRX - 1])["rows"]
    assert row["reason"] == "isolation_unknown (grab emitted 3 of slots)"
    assert row["samples"]["current"]["slot"] == NPRX - 1 and row["isolation"]["mafd"] is None


# ── no global bump ───────────────────────────────────────────────────────────

def test_no_global_version_bump_and_an_old_signals_json_is_not_stale(tmp_path):
    assert ANALYSIS_VERSION == "1"
    pid = "ad3oldsignals"
    media = tmp_path / "src.mp4"
    media.write_bytes(bytes(4096))
    storage.ensure_dirs(pid)
    storage.write_artifact(pid, "meta", {"analysis_version": ANALYSIS_VERSION,
                                         "source": {"filesize": media.stat().st_size},
                                         "proxy": {"width": 480, "fps": 10}})
    storage.write_artifact(pid, "signals", {"scenes": [1.0]})
    storage.write_artifact(pid, "faces", {"samples": [], "times": []})
    p = storage.paths(pid)
    p["proxy"].write_bytes(b"proxy")
    p["audio"].write_bytes(b"audio")
    assert "scenes_addressed" not in storage.read_artifact(pid, "signals")
    assert storage.stale_artifacts(pid, media) is None
    assert S.SCENE_READER_VERSION == "scene-select-showinfo-1"


def test_mafd_is_mean_absolute_difference_not_scene_score():
    a, b = np.zeros((2, 4), np.uint8), np.full((2, 4), 10, np.uint8)
    raw = a.tobytes() + b.tobytes() + b.tobytes() + a.tobytes()
    err = f"{HDR}config in time_base: {TB}, x\n" + "".join(
        f"{HDR}n:{k:>4} pts:{(48 + k) * 1024:>7} pts_time:x d\n" for k in range(4))
    g = S.bind_grab(raw, err, 50, 4, 2, TB)
    assert g["mafd"] == [10.0, 0.0, 10.0] and g["isolated"] is False     # m2 == 0: not a cut
