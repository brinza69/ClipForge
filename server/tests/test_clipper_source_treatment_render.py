"""The source-caption treatment EXECUTED: SC-addendum §4 regressions 1–5 and 7–10,
and regression 6 as the SC-addendum-v2 §1 split with its two lossless witnesses.

SC batch 2. Every regression runs the command `build_dynamic_cmd` builds through
the real ffmpeg, on a synthetic CFR source encoded LOSSLESSLY (x264 qp 0), and
reads the decoded frames of an FFV1 twin — never a lossy MP4 (SC-addendum §4).
The source is 9:16, so the dynamic graph's canvas is the frame itself and an
output pixel is the source pixel at the same place: "outside the mask" is a
statement about rows and columns, not a tolerance.

Every frame carries its own index k in eight 32x32 blocks at the top (outside
every rect), and its background moves with k, so a patch laid one frame off
changes pixels. Which source frame an output frame came from is READ off that
marker, never computed from a constant (SC-addendum §4 regression 8).

The fixture builders here are imported by the witness, record, gate and v3
test files; regressions 6 and 10 live in test_clipper_source_treatment_witness.py.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from models import ClipModel, ProjectModel
from services.clipper import source_treatment as st
from services.clipper import source_treatment_render as treat
from services.clipper.dynamic_render import build_dynamic_cmd, write_sendcmd
from services.clipper.ffmpeg_tools import ffmpeg_bin
from services.clipper.proxy_provenance import media_identity
from services.clipper.source_treatment_mask import IDENTITY_STREAM_KEYS
from test_clipper_source_treatment import _png, write_mask

cv2 = pytest.importorskip("cv2")

W, H, FPS, N = 360, 640, 30, 90
STEP, TB = 512, "1/15360"
PROJECT, CLIP = "scsynth01", "scsynthclip1"
#: Inclusive, even-aligned outward. L2 and L3 do not overlap, so a frame that
#: carried the old rect on a new line's frame would show outside the new one.
RECTS = {"L1": (48, 311, 480, 527), "L2": (40, 159, 480, 527),
         "L3": (200, 319, 480, 527), "L4": (48, 311, 480, 527)}
#: k 35 is a one-frame gap (F1 k 35964 [SC1-2]); L2 → L3 switch at k 50 with
#: no gap; L4 runs through the stream's last frame (the EOF trap).
RUNS = [("L1", 20, 35), ("L2", 36, 50), ("L3", 50, 62), ("L4", 80, 90)]
SPACING = {"L1": 10, "L2": 12, "L3": 14, "L4": 11}
K_FIRST = 4
#: A bright object moving through L3's rect (reported, not gated) — and one
#: outside every rect (must stay bit-identical).
PROTECT = {"label": "hand", "k_from": 50, "k_to": 62, "x0": 200, "x1": 260, "y0": 496, "y1": 513}
START = 10 / 30


def frame(k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:H, 0:W]
    y = (60 + (xx + 2 * yy + 3 * k) % 48).astype(np.uint8)
    u = (128 + (xx[::2, ::2] // 2 + k) % 16 - 8).astype(np.uint8)
    v = (128 + (yy[::2, ::2] // 2) % 12 - 6).astype(np.uint8)
    for i in range(8):  # the marker: bit i of k
        x0 = 8 + 42 * i
        y[8:40, x0:x0 + 32] = 235 if (k >> i) & 1 else 16
        u[4:20, x0 // 2:(x0 + 32) // 2] = v[4:20, x0 // 2:(x0 + 32) // 2] = 128
    ox = (10 + 4 * k) % (W - 30)  # outside every rect
    y[300:314, ox:ox + 14], u[150:157, ox // 2:ox // 2 + 7], v[150:157, ox // 2:ox // 2 + 7] = 240, 100, 160
    for lid, a, b in RUNS:
        if a <= k < b:
            x0, x1, y0, y1 = RECTS[lid]
            for x in range(x0 + 14, x1 - 14, SPACING[lid]):
                y[y0 + 12:y1 - 11, x:x + 3] = 235
                u[(y0 + 12) // 2:(y1 - 11) // 2, x // 2:x // 2 + 2] = 128
                v[(y0 + 12) // 2:(y1 - 11) // 2, x // 2:x // 2 + 2] = 128
    if PROTECT["k_from"] <= k < PROTECT["k_to"]:
        hx = 205 + 4 * (k - PROTECT["k_from"])
        y[500:510, hx:hx + 10] = 240
    return y, u, v


def encode_source(path: Path, *, audio: bool = False, late: bool = False) -> Path:
    """Lossless H.264, tb 1/15360, 30/1 CFR, tv/bt709. `late`: video starts
    at pts 1536 (0.1 s) — a nonzero start_pts on the clock."""
    raw = b"".join(p.tobytes() for k in range(N) for p in frame(k))
    base = path.with_name(path.stem + ".base.mp4") if late or audio else path
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
           # tagged by setparams, not by output options: those made ffmpeg 8
           # convert the untagged input's range (every pixel ±1, not lossless)
           "-vf", "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709",
           "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-bf", "0", "-g", "15",
           "-pix_fmt", "yuv420p", "-video_track_timescale", "15360", str(base)]
    subprocess.run(cmd, input=raw, check=True)
    if base != path:
        cmd = [ffmpeg_bin(), "-y", "-loglevel", "error"] + (["-itsoffset", "0.1"] if late else [])
        cmd += ["-i", str(base), "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3.2",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", str(path)]
        subprocess.run(cmd, check=True)
    return path


PARAMS_VALUES = {
    "blur": {"context_edge": "reflect", "context_px": 64, "sigma": [16, 8]},
    "erase": {"dilation": {"shape": "ellipse", "size": 17}, "method": "opencv_inpaint_telea",
              "footprint": {"luma_min": 170, "persist": 0.5, "tophat": 15, "tophat_min": 40},
              "opencv_version": ".".join(cv2.__version__.split(".")[:2]), "telea_radius": [8, 4],
              "temporal_smoothing": "none"}}


def params_record(root: Path, values: dict | None = None, version: str = "sc-synth-v1") -> Path:
    doc = {"schema": treat.PARAMS_SCHEMA, "immutable": True, "version": version,
           "values": PARAMS_VALUES if values is None else values}
    data = st.canonical_bytes(doc)
    path = root / "params" / f"{version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    Path(f"{path}.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {path.name}\n",
                                      encoding="utf-8")
    return path


def mask_doc(ident: dict, *, clip_id: str = CLIP, runs=RUNS, k_first: int = K_FIRST,
             states: dict | None = None, line_extra: dict | None = None,
             stream_extra: dict | None = None) -> dict:
    """The whole window [k_first, last frame]; `states` overrides a run's state,
    `line_extra` every line's fields, `stream_extra` the source stream's."""
    k_last = N - 1
    frames, at = [], k_first
    for lid, a, b in runs:
        if a > at:
            frames.append({"k_from": at, "k_to": a, "state": "none_observed", "basis": "viewed"})
        frames.append({"k_from": a, "k_to": b, "state": f"line:{lid}", "basis": "viewed"})
        at = b
    if at <= k_last:
        frames.append({"k_from": at, "k_to": k_last + 1, "state": "none_observed", "basis": "viewed"})
    for f in frames:
        f.update((states or {}).get((f["k_from"], f["k_to"]), {}))
    stream = ident["stream"]
    lines = []
    for lid, a, b in runs:
        x0, x1, y0, y1 = RECTS[lid]
        lines.append({"id": lid, "text": f"synthetic {lid}", "k_on": a, "k_off": b,
                      "rect": {"x0": x0, "x1": x1, "y0": y0, "y1": y1},
                      "uncertainty_px": {"x": 0, "y": 0}, "karaoke": False,
                      "glyph_png": {"file": f"g/{lid}.png", "sha256": hashlib.sha256(_png(lid)).hexdigest(),
                                    "w": x1 - x0 + 1, "h": y1 - y0 + 1, "origin": {"x": x0, "y": y0}},
                      "style": "plain_white_soft_shadow_one_line",
                      "footprint_rule": PARAMS_VALUES["erase"]["footprint"],
                      "dilation": PARAMS_VALUES["erase"]["dilation"]} | (line_extra or {}))
    return {
        "schema": st.MASK_SCHEMA,
        "conventions": {"k": "(pts-start_pts)/pts_step", "interval": "half-open [k_on,k_off)"},
        "source": {"sha256": ident["sha256"], "sha256_how": "full_hash", "size": ident["size"],
                   "stream": {k: stream[k] for k in IDENTITY_STREAM_KEYS}
                   | {"pix_fmt": "yuv420p", "color_range": "tv", "color_space": "bt709"}
                   | (stream_extra or {}),
                   "verified_at": "2026-09-27T00:00:00Z", "verified_by": "test"},
        "clock": {"time_base": TB, "pts_step": STEP, "start_pts": stream["start_pts"], "fps": "30/1",
                  "cfr_check": {"how": "synthetic", "frames": k_last - k_first + 1, "off_step": 0}},
        "clip": {"project_id": PROJECT, "clip_id": clip_id, "start_time": START, "end_time": 3.0},
        "window": {"k_first": k_first, "k_last": k_last, "margin_frames": 2,
                   "rule": "floor(start*fps)-2 .. ceil(end*fps)+2"},
        "lines": lines, "frames": frames,
        "inspection": {"strips": [], "brackets": [], "uncertain": [], "no_text": []},
        "agreement": {"frames_total": k_last - k_first + 1, "frames_agree": k_last - k_first + 1,
                      "disagreements": []},
        "provenance": {"author": "test", "method": "synthetic", "protect": [PROTECT]},
    }


class Synth:
    """One source + mask + params, and the decisions and twins built on them."""

    def __init__(self, root: Path, src: Path, *, clip_id: str = CLIP, start: float = START,
                 end: float = 3.0, doc: dict | None = None):
        self.root, self.src = root, src
        self.ident = media_identity(src)
        self.mask_path = write_mask(root, doc or mask_doc(self.ident, clip_id=clip_id))
        self.params_path = params_record(root)
        self.clip = ClipModel(id=clip_id, start_time=start, end_time=end, duration=end - start)
        self.project = ProjectModel(id=PROJECT, width=W, height=H)
        self.has_audio = bool(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
             "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip())

    def mask(self) -> dict:
        from services.clipper.source_treatment_mask import load_mask
        return load_mask(self.mask_path, source_identity=self.ident, clip_id=self.clip.id,
                         clip_start=self.clip.start_time, clip_end=self.clip.end_time)

    def setting(self, treatment: str = "erase", overrides=None) -> dict:
        out = {"treatment": treatment, "decided_by": "human", "mask_sha256": self.mask()["sha256"]}
        if overrides is not None:
            out["overrides"] = overrides
        return out

    def decision(self, setting: dict | None = None, *, burn: bool = False, drop=(),
                 dynamic: bool = True) -> dict:
        p = treat.load_params(self.params_path)
        resolved = st.resolve(None, setting, self.mask(), {"version": p["version"], "values": p["values"]})
        d = self.clip.end_time - self.clip.start_time
        return {"cfg": {}, "fps": FPS, "watermark": "", "drop": list(drop), "plan": {},
                "caption_y": None, "ass_path": None,
                "dyn": {"duration": d, "src_w": W, "src_h": H, "style": {"push_amount": 0.0},
                        "hits": [], "shots": [{"t0": 0.0, "t1": d, "composition": "fit",
                                               "rect": {"x": 0, "y": 0, "w": W, "h": H}}]}
                if dynamic else None,
                "caption_policy": {"action": "burn" if burn else "suppress", "decided_by": "human"},
                "layout_policy": {"regions": "no_second_camera", "decided_by": "agent"},
                "edit_profile": None, "creator_view": None, "regime_view": None, "rhythm_view": None,
                "render": {"preset": "ultrafast", "crf": 18},
                "source_treatment": resolved,
                "source_treatment_inputs": {"project": None, "clip": setting,
                                            "mask_path": str(self.mask_path),
                                            "params_path": str(self.params_path)}}

    def patch(self, decision: dict, name: str) -> dict | None:
        return treat.prepare(self.clip, self.project, decision, src=str(self.src),
                             scratch_root=self.root / name)

    def twin(self, decision: dict, name: str, patch: dict | None = None,
             out_w: int = W, out_h: int = H) -> np.ndarray:
        """The frames the export's own command produces, into FFV1 instead of x264."""
        out = self.root / f"{name}.mkv"
        dyn = decision["dyn"]
        cmd_path = write_sendcmd(dyn, W, H, self.root / f"{name}.cmd.txt")
        cmd = build_dynamic_cmd(str(self.src), dyn, cmd_path, None, str(out),
                                start=self.clip.start_time, duration=float(dyn["duration"]),
                                src_w=W, src_h=H, fps=FPS, out_w=out_w, out_h=out_h,
                                loudness=False, drop_spans=decision["drop"],
                                has_audio=self.has_audio, source_patch=patch)
        cmd = cmd[:cmd.index("-c:v")] + ["-c:v", "ffv1", "-pix_fmt", "yuv420p", "-an", str(out)]
        subprocess.run(cmd, check=True)
        return decode(out, out_w, out_h)


def decode(path: Path, w: int = W, h: int = H) -> np.ndarray:
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, w * h * 3 // 2)


def planes(f: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    q = (W // 2) * (H // 2)
    return f[:W * H].reshape(H, W), f[W * H:W * H + q].reshape(H // 2, W // 2), \
        f[W * H + q:].reshape(H // 2, W // 2)


def marker(f: np.ndarray) -> int:
    y = planes(f)[0]
    return sum(1 << i for i in range(8) if y[24, 8 + 42 * i + 16] > 128)


def line_at(k: int) -> str | None:
    return next((lid for lid, a, b in RUNS if a <= k < b), None)


def check_treated(none: np.ndarray, treated: np.ndarray, treated_k: set[int]) -> list[int]:
    """Frame by frame, by marker: a treated k differs, and ONLY inside its own
    line's rect (luma and chroma); every other frame is bit-identical."""
    assert len(none) == len(treated) and len(none) > 0
    seen = []
    for a, b in zip(none, treated):
        k = marker(a)
        assert marker(b) == k
        seen.append(k)
        if k not in treated_k:
            assert np.array_equal(a, b), f"k {k} is untreated and changed"
            continue
        x0, x1, y0, y1 = RECTS[line_at(k)]
        ya, ua, va = planes(a)
        yb, ub, vb = planes(b)
        dy = ya != yb
        assert dy.any(), f"k {k} was not treated"
        inside = np.zeros_like(dy)
        inside[y0:y1 + 1, x0:x1 + 1] = True
        assert not (dy & ~inside).any(), f"k {k} changed luma outside its rect"
        cin = inside[::2, ::2]
        assert not ((ua != ub) & ~cin).any() and not ((va != vb) & ~cin).any(), f"k {k} chroma"
    return seen


def treated_set(resolved: dict) -> set[int]:
    return {k for s in resolved["per_line"] for k in range(s["k_from"], s["k_to"])}


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("sc2-sources")
    return {"plain": encode_source(root / "plain.mp4"),
            "audio": encode_source(root / "audio.mp4", audio=True),
            "late": encode_source(root / "late.mp4", late=True)}


def copy_patch(s: Synth, name: str) -> dict:
    """Regression 1's control: a patch that treats NOTHING — the decoded source."""
    mask = s.mask()
    box = (40, 480, 320, 528)
    path = s.root / f"{name}.yuv"
    made = treat.write_patch(str(s.src), mask, [], PARAMS_VALUES, {"patch": box, "context": box}, path)
    off = treat.pts_offset(mask["clock"], mask["window"][0], treat.seek_arg(s.clip.start_time),
                           s.ident["format"]["start_time"])
    return {"file": str(path), "w": made["w"], "h": made["h"], "fps": "30/1",
            "overlay_filter": treat.overlay_prefix(time_base=TB, pts_step=STEP, pts_offset=off,
                                                   x=made["x"], y=made["y"])}


# ── the source fixture itself ────────────────────────────────────────────────

def test_the_fixture_source_is_lossless_cfr_and_marked(sources, tmp_path):
    s = Synth(tmp_path, sources["plain"])
    assert s.ident["stream"]["time_base"] == TB and s.ident["stream"]["r_frame_rate"] == "30/1"
    got = decode(sources["plain"])
    assert len(got) == N
    for k in (0, 35, 89):
        assert np.array_equal(got[k], np.concatenate([p.ravel() for p in frame(k)]))
        assert marker(got[k]) == k
    late = Synth(tmp_path / "late", sources["late"])
    assert late.ident["stream"]["start_pts"] == 1536


# ── 1 identity, 8 fractional start ───────────────────────────────────────────

@pytest.mark.parametrize("variant", ["plain", "audio", "late"])
@pytest.mark.parametrize("start", [START, 12.6 / 30, 12.5 / 30],
                         ids=["on-grid", "k0+0.6", "k0+0.5"])
def test_1_8_a_patch_that_treats_nothing_is_bit_identical_every_frame(sources, tmp_path, variant, start):
    """Treatment `none` through the patch path vs no patch: every frame of the
    twin bit-identical — tags, csp, EOF and the pts offset at a fractional seek
    (b23c: k0+0.6; 6053: k0+0.5) and a nonzero start_pts. The background moves
    with k, so a patch one frame off would change pixels."""
    s = Synth(tmp_path, sources[variant], start=start)
    d = s.decision(None)
    base = s.twin(d, "none")
    copy = s.twin(d, "copy", copy_patch(s, "copy"))
    assert len(base) == len(copy) > 70
    assert np.array_equal(base, copy)


@pytest.mark.parametrize("variant", ["plain", "late"])
@pytest.mark.parametrize("start", [12.6 / 30, 12.5 / 30], ids=["k0+0.6", "k0+0.5"])
def test_8_the_patch_lands_on_the_source_k_it_was_computed_for(sources, tmp_path, variant, start):
    s = Synth(tmp_path, sources[variant], start=start)
    d = s.decision(s.setting("erase"))
    seen = check_treated(s.twin(d, "none"), s.twin(d, "erase", s.patch(d, "p")),
                         treated_set(d["source_treatment"]))
    assert seen == sorted(seen) and len(set(seen)) == len(seen)


# ── 2 colour, 3 last frame, 4 gap, 5 line change, 7 protected ───────────────

@pytest.mark.parametrize("treatment", ["erase", "blur"])
def test_2_3_4_5_7_treated_frames_only_inside_their_rect(sources, tmp_path, treatment):
    s = Synth(tmp_path, sources["plain"])
    d = s.decision(s.setting(treatment))
    patch = s.patch(d, "p")
    manifest = json.loads(Path(patch["manifest_path"]).read_text(encoding="utf-8"))
    none, treated = s.twin(d, "none"), s.twin(d, treatment, patch)
    seen = check_treated(none, treated, treated_set(d["source_treatment"]))
    # 3: the last output frame is the stream's last frame, and it is treated
    assert seen[-1] == N - 1 and not np.array_equal(none[-1], treated[-1])
    # 4: the one-frame gap is present and untouched; its neighbours are treated
    i = seen.index(35)
    assert np.array_equal(none[i], treated[i])
    assert not np.array_equal(none[i - 1], treated[i - 1]) and not np.array_equal(none[i + 1], treated[i + 1])
    # 5: the switch frame carries only the new rect, the frame before only the old
    for k, other in ((49, "L3"), (50, "L2")):
        j = seen.index(k)
        x0, x1, y0, y1 = RECTS[other]
        own = RECTS[line_at(k)]
        cols = np.zeros(W, bool)
        cols[x0:x1 + 1] = True
        cols[own[0]:own[1] + 1] = False
        assert np.array_equal(planes(none[j])[0][:, cols], planes(treated[j])[0][:, cols])
    # 7: the moving object outside every rect is covered by "only inside the
    # rect" above; the one inside L3's rect is reported, not gated
    assert manifest["protect"] == [{"label": "hand", "treated_frames_touching": 12}]


def test_2_the_patch_itself_differs_from_the_source_only_on_treated_frames_inside_rects(sources, tmp_path):
    """The lossless patch, frame by frame, against the generated source."""
    s = Synth(tmp_path, sources["plain"])
    d = s.decision(s.setting("erase"))
    patch = s.patch(d, "p")
    raw = np.fromfile(patch["file"], np.uint8)
    w, h = patch["w"], patch["h"]
    size = w * h * 3 // 2
    assert raw.size == size * (N - K_FIRST + 1)  # the window + the spare trailing frame
    doc = json.loads(Path(patch["manifest_path"]).read_text(encoding="utf-8"))
    x, y = doc["patch"]["x"], doc["patch"]["y"]
    T = treated_set(d["source_treatment"])
    for i in range(N - K_FIRST + 1):
        k = min(K_FIRST + i, N - 1)
        fy = raw[i * size:i * size + w * h].reshape(h, w)
        src_y = frame(k)[0][y:y + h, x:x + w]
        if K_FIRST + i in T:
            assert not np.array_equal(fy, src_y)
        else:
            assert np.array_equal(fy, src_y), f"patch frame {i} (k {K_FIRST + i}) is not the source"


# ── 9 drop spans ─────────────────────────────────────────────────────────────

def test_9_the_treatment_precedes_select_and_markers_survive(sources, tmp_path):
    s = Synth(tmp_path, sources["plain"])
    d = s.decision(s.setting("erase"), drop=[(0.5, 1.0)])
    seen = check_treated(s.twin(d, "none"), s.twin(d, "erase", s.patch(d, "p")),
                         treated_set(d["source_treatment"]))
    assert 24 in seen and not any(26 <= k <= 38 for k in seen) and 41 in seen
    assert len(seen) < 75
