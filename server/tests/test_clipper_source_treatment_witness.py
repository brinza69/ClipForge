"""Regression 6 — the SC-addendum-v2 §1 split with its two lossless witnesses —
and regression 10, preview == export. SC batch 2.

Split out of test_clipper_source_treatment_render.py (rule 2), whose fixtures
and docstring (lossless synthetic source, per-frame marker, FFV1 twins) apply.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from services.clipper import source_treatment as st
from services.clipper.dynamic_render import build_dynamic_cmd, write_sendcmd
from services.clipper.ffmpeg_tools import ffmpeg_bin
from services.clipper.proxy_provenance import media_identity
from test_clipper_source_treatment_render import (FPS, H, K_FIRST, RECTS, STEP, W, Synth, frame,
                                                  line_at, marker, sources)  # noqa: F401

cv2 = pytest.importorskip("cv2")


# ── 6 the split, and its lossless witnesses ──────────────────────────────────

SPLIT_K = 42
#: hex, as the answer page writes a clip's prefix
SPLIT_CLIP = "5c2babcdef01"
ANSWER = ("SCB2 synthetic\r\n3. 5c2bab, linia sintetica: c — schimbarea la 1,4 s\r\n"
          "observații: -").encode("utf-8")


@pytest.fixture
def approved(monkeypatch, tmp_path, sources):
    """A STAND-IN approved record, for the synthetic source only: the real one is
    bound to b23c's source hash, which no synthetic file can have. Same shape,
    same checks — the resolver reads it through `QUESTIONS` like the real one."""
    ident = media_identity(sources["plain"])
    answer = tmp_path / "answer.txt"
    answer.write_bytes(ANSWER)
    record = {"label": "linia sintetica", "split_answer": "c", "offered": "synthetic",
              "answer_sha256": hashlib.sha256(ANSWER).hexdigest(), "clip_id": SPLIT_CLIP,
              "source_sha256": ident["sha256"], "line_id": "L2", "k_split": SPLIT_K,
              "pts_split": SPLIT_K * STEP, "before": "blur", "after": "erase"}
    assert Fraction(SPLIT_K * STEP, 15360) == Fraction("1.4")
    monkeypatch.setitem(st.QUESTIONS, "sc-split-synthetic", record)
    return answer


def split_override(answer: Path) -> list[dict]:
    return [{"k_from": K_FIRST, "k_to": SPLIT_K, "treatment": "blur",
             "split": {"line_id": "L2", "k_split": SPLIT_K, "pts_split": SPLIT_K * STEP,
                       "authorisation": {"question_id": "sc-split-synthetic",
                                         "answer_file": str(answer)}}}]


def _independent(k: int, treatment: str, doc: dict) -> np.ndarray:
    """Luma of the patch box at k, recomputed from the GENERATED source with
    OpenCV and the params record — not through the module under test."""
    x, y, w, h = doc["patch"]["x"], doc["patch"]["y"], doc["patch"]["w"], doc["patch"]["h"]
    x0, x1, y0, y1 = RECTS["L2"]
    ctx = 64 if treatment == "blur" else 14
    cx0, cy0, cx1, cy1 = max(0, x - ctx), max(0, y - ctx), min(W, x + w + ctx), min(H, y + h + ctx)
    Y = frame(k)[0][cy0:cy1, cx0:cx1].copy()
    box = (slice(y0 - cy0, y1 + 1 - cy0), slice(x0 - cx0, x1 + 1 - cx0))
    if treatment == "blur":
        Y[box] = cv2.GaussianBlur(Y, (0, 0), 16.0, borderType=cv2.BORDER_REFLECT)[box]
    else:
        kern = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        runs = [frame(j)[0][cy0:cy1, cx0:cx1] for j in range(36, 50)]
        hits = sum(((cv2.morphologyEx(f, cv2.MORPH_TOPHAT, kern) > 40) & (f > 170)).astype(np.int32)
                   for f in runs)
        inside = np.zeros(Y.shape, bool)
        inside[box] = True
        fp = (hits / len(runs) >= 0.5) & inside
        m = cv2.dilate(fp.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
        Y = cv2.inpaint(Y, (m.astype(bool) & inside).astype(np.uint8), 8, cv2.INPAINT_TELEA)
    return Y[y - cy0:y - cy0 + h, x - cx0:x - cx0 + w]


def test_6_the_split_resolves_only_as_answered_and_the_witnesses_agree(sources, tmp_path, approved):
    s = Synth(tmp_path, sources["plain"], clip_id=SPLIT_CLIP)
    d = s.decision(s.setting("erase", split_override(approved)))
    r = d["source_treatment"]
    assert r["state"] == "resolved", r
    l2 = [(g["k_from"], g["k_to"], g["treatment"]) for g in r["per_line"] if g["line_id"] == "L2"]
    assert l2 == [(36, SPLIT_K, "blur"), (SPLIT_K, 50, "erase")]
    patch = s.patch(d, "p")
    doc = json.loads(Path(patch["manifest_path"]).read_text(encoding="utf-8"))

    # Witness 1 — patch ↔ treatment, at the two frames either side of the seam.
    raw = np.fromfile(patch["file"], np.uint8)
    w, h = patch["w"], patch["h"]
    size = w * h * 3 // 2
    at = {k: raw[(k - K_FIRST) * size:(k - K_FIRST) * size + w * h].reshape(h, w)
          for k in (SPLIT_K - 1, SPLIT_K)}
    blur_41, erase_41 = _independent(SPLIT_K - 1, "blur", doc), _independent(SPLIT_K - 1, "erase", doc)
    blur_42, erase_42 = _independent(SPLIT_K, "blur", doc), _independent(SPLIT_K, "erase", doc)
    assert np.array_equal(at[SPLIT_K - 1], blur_41) and not np.array_equal(at[SPLIT_K - 1], erase_41)
    assert np.array_equal(at[SPLIT_K], erase_42) and not np.array_equal(at[SPLIT_K], blur_42)

    # Witness 2 — patch ↔ output: the split twin equals the all-blur control
    # before the seam inside the line, the all-erase control from it, and the
    # none twin everywhere nothing is treated. The treated k set so measured is
    # the manifest's.
    none = s.twin(d, "none")
    split = s.twin(d, "split", patch)
    db, de = s.decision(s.setting("blur")), s.decision(s.setting("erase"))
    blur, erase = s.twin(db, "blur", s.patch(db, "pb")), s.twin(de, "erase", s.patch(de, "pe"))
    measured = set()
    for i in range(len(none)):
        k = marker(none[i])
        want = (blur if (line_at(k) == "L1" or (line_at(k) == "L2" and k < SPLIT_K)) else
                erase if line_at(k) else none)[i]
        assert np.array_equal(split[i], want), f"k {k}"
        if not np.array_equal(split[i], none[i]):
            measured.add(k)
    manifest_k = {k for f in doc["frames"] if f["treatment"] for k in range(f["k_from"], f["k_to"])}
    assert measured == manifest_k & {marker(f) for f in none}


def test_6_a_split_nobody_answered_is_refused_and_never_rendered(sources, tmp_path):
    s = Synth(tmp_path, sources["plain"], clip_id=SPLIT_CLIP)
    over = [{"k_from": K_FIRST, "k_to": SPLIT_K, "treatment": "blur"}]
    d = s.decision(s.setting("erase", over))
    assert d["source_treatment"]["state"] == "refused"
    assert d["source_treatment"]["reason"] == "override_splits_a_line"
    with pytest.raises(st.SourceTreatmentRefused) as e:
        s.patch(d, "p")
    assert e.value.reason == "override_splits_a_line"
    assert not list(tmp_path.glob("**/*.yuv"))


# ── 10 preview == export ─────────────────────────────────────────────────────

def test_10_the_editor_still_equals_the_export_on_the_shared_frame(sources, tmp_path):
    """`_render_frame` (the editor still, which builds its own command) against
    the export command's FFV1 twin at the same frame, both at 1080x1920 and
    both treated by their own attempt's patch."""
    from workers.clipper_preview_frame import _render_frame

    s = Synth(tmp_path, sources["plain"])
    d = s.decision(s.setting("erase"))
    index = 30  # k 40, inside L2
    work = tmp_path / "still"
    work.mkdir()
    png = _render_frame(s.clip, s.project, d, str(s.src), work, index / FPS)
    still = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    out = tmp_path / "export.mkv"
    dyn = d["dyn"]
    cmd = build_dynamic_cmd(str(s.src), dyn, write_sendcmd(dyn, W, H, tmp_path / "e.cmd.txt"), None,
                            str(out), start=s.clip.start_time, duration=float(dyn["duration"]),
                            src_w=W, src_h=H, fps=FPS, has_audio=False, loudness=False,
                            source_patch=s.patch(d, "p"))
    cmd = cmd[:cmd.index("-c:v")] + ["-c:v", "ffv1", "-pix_fmt", "yuv420p", "-an", str(out)]
    subprocess.run(cmd, check=True)
    ref = tmp_path / "ref.png"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(out), "-vf", f"select=eq(n\\,{index})",
                    "-frames:v", "1", "-c:v", "png", str(ref)], check=True)
    exported = cv2.imread(str(ref), cv2.IMREAD_UNCHANGED)
    assert still.shape == exported.shape == (1920, 1080, 3)
    assert np.array_equal(still, exported)
    none_png = _render_frame(s.clip, s.project, s.decision(None), str(s.src), work, index / FPS)
    assert png != none_png  # the still is treated, not the untouched source
