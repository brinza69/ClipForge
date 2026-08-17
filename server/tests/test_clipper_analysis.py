"""
Unit tests for the analysis chain: candidates, scoring, dedupe, layout.

Pure functions only — no ffmpeg, no cv2 image decoding, no DB, no network.
Everything is driven from a synthetic transcript and a synthetic signals blob,
which is exactly the shape `build_signals()` writes to analysis/signals.json.

The most important test here is `test_features_cover_every_consumer`: scoring.py
reads its features by name through a `.get(key, 0.0)` accessor, so a key that
extract_features stops emitting does NOT raise — it silently reads 0.0 and every
clip is mis-scored in the same direction. That failure is invisible in
production, so it gets an explicit assertion.
"""

from __future__ import annotations

import inspect
import math
import re

import pytest

from services.clipper import (
    candidate_boundaries, candidates, dedupe, layout, ranker, scoring, segmentation,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def _transcript() -> dict:
    """Eight punctuated sentences with word-level timings, ~60s of speech."""
    sentences = [
        "So this is the part where everything went wrong.",
        "I had about two seconds to react, and I completely froze.",
        "Why did nobody tell me the timer was already running?",
        "Because it turns out the setting was inverted the whole time.",
        "That is the most embarrassing thing I have done on stream.",
        "Chat absolutely destroyed me for it, and honestly they were right.",
        "Let me show you exactly how to avoid it happening to you.",
        "First you open the settings menu, then you flip that one toggle.",
    ]
    segments, t = [], 2.0
    for text in sentences:
        toks = text.split()
        words, wt = [], t
        for tok in toks:
            words.append(
                {"word": tok, "start": round(wt, 3), "end": round(wt + 0.38, 3),
                 "probability": 0.93}
            )
            wt += 0.42
        dur = len(toks) * 0.42
        segments.append(
            {"start": round(t, 3), "end": round(t + dur, 3), "text": text,
             "confidence": 0.93, "words": words}
        )
        t += dur + 1.1
    return {"language": "en", "segments": segments, "_end": t}


def _signals(duration: float) -> dict:
    hop = 0.25
    n = int(duration / hop) + 1
    rms = [0.35 + 0.4 * ((i * 7) % 13) / 13.0 for i in range(n)]
    motion = [0.3 + 0.2 * ((i * 5) % 7) / 7.0 for i in range(int(duration / 0.5) + 1)]
    return {
        "duration": duration,
        "proxy_width": 480,
        "proxy_height": 270,
        "audio": {"hop_s": hop, "duration": duration, "rms": rms,
                  "peaks": [12.0, 28.5, 41.0], "silence": [[0.0, 2.0]],
                  "speech": [[2.0, max(2.5, duration - 3)]]},
        "scenes": [15.0, 33.0],
        "motion": {"hop_s": 0.5, "motion": motion},
        "faces": [{"t": float(i * 10), "boxes": [[10, 10, 60, 60]]}
                  for i in range(int(duration / 10) + 1)],
        "peaks": [12.0, 28.5, 41.0],
        "silence": [[0.0, 2.0]],
        "speech": [[2.0, max(2.5, duration - 3)]],
    }


@pytest.fixture
def chain():
    tr = _transcript()
    duration = tr.pop("_end") + 5
    sig = _signals(duration)
    windows = segmentation.semantic_windows(tr, sig, min_s=15, max_s=90)
    cands = candidates.generate_candidates(windows, sig, min_s=15, max_s=90, target_s=30)
    refined = [
        candidates.refine_boundaries(c, tr, sig, min_s=15, max_s=90) for c in cands
    ]
    return {"transcript": tr, "signals": sig, "duration": duration,
            "windows": windows, "candidates": refined}


# ── segmentation + candidates ────────────────────────────────────────────────


def test_windows_are_semantic_not_fixed_size(chain):
    """Every window must record which signal closed it — a fixed-size chunker
    could not populate this."""
    assert chain["windows"], "no windows built from a normal transcript"
    for win in chain["windows"]:
        assert win["reasons"], "a window closed without recording why"
        assert win["end"] > win["start"]


def test_candidates_respect_duration_bounds(chain):
    for cand in chain["candidates"]:
        span = cand["end"] - cand["start"]
        assert 15 - 1e-6 <= span <= 90 + 1e-6, f"{span}s is outside the requested bounds"


def test_boundaries_never_land_mid_word(chain):
    """Starting mid-word is the single most obvious way a clip looks broken."""
    words = segmentation.word_list(chain["transcript"])
    for cand in chain["candidates"]:
        straddling = [
            w for w in words
            if w["start"] < cand["start"] - 1e-6 < w["end"]
        ]
        assert not straddling, f"clip starts inside the word {straddling[:1]}"


# ── the tail release ─────────────────────────────────────────────────────────
#
# Regression cover for a defect nothing measurable caught: all three clips of
# the first export anyone watched ended on the exact millisecond of their final
# word's timestamp, which a listener hears as a truncated sentence.


def _spoken(times: list[tuple[float, float]]) -> list[dict]:
    return [{"word": f"w{i}", "start": a, "end": b, "probability": 0.9}
            for i, (a, b) in enumerate(times)]


def test_release_pads_into_silence():
    words = _spoken([(1.0, 1.5), (1.6, 2.0)])
    out = candidate_boundaries._keep_release(2.0, words, ceiling=30.0, limit=30.0)
    assert out == pytest.approx(2.0 + candidate_boundaries.TAIL_PAD_S)


def test_release_never_crosses_the_next_word():
    """When speech continues there is no silence to borrow."""
    words = _spoken([(1.0, 1.5), (1.6, 2.0), (2.2, 2.6)])
    out = candidate_boundaries._keep_release(2.0, words, ceiling=30.0, limit=30.0)
    assert out == pytest.approx(2.2 - candidate_boundaries.RELEASE_GAP_S)
    assert out < 2.2


def test_release_respects_the_media_end_and_the_duration_cap():
    words = _spoken([(1.0, 1.5), (1.6, 2.0)])
    assert candidate_boundaries._keep_release(
        2.0, words, ceiling=2.1, limit=30.0) == pytest.approx(2.1)
    assert candidate_boundaries._keep_release(
        2.0, words, ceiling=30.0, limit=2.15) == pytest.approx(2.15)


def test_release_leaves_a_deliberate_pause_alone():
    """`_trim_tail` may have chosen a longer tail; padding must not fight it."""
    words = _spoken([(1.0, 1.5), (1.6, 2.0)])
    end = 2.0 + candidate_boundaries.TAIL_PAD_S + 0.5
    assert candidate_boundaries._keep_release(
        end, words, ceiling=30.0, limit=30.0) == end


def test_no_clip_ends_on_the_last_words_timestamp(chain):
    """The defect itself: a cut landing exactly where Whisper stops hearing."""
    words = segmentation.word_list(chain["transcript"])
    # refine_boundaries takes its ceiling from the last word, so a clip that
    # runs to the end of the transcript has nothing left to pad into.
    ceiling = words[-1]["end"]
    for cand in chain["candidates"]:
        end = cand["end"]
        if end >= ceiling - 1e-6:
            continue
        last = max((w["end"] for w in words if w["end"] <= end + 1e-6), default=None)
        if last is None:
            continue
        following = next((w["start"] for w in words if w["start"] >= end - 1e-6), None)
        room = (following - last) if following is not None else ceiling - last
        if room < candidate_boundaries.TAIL_PAD_S + candidate_boundaries.RELEASE_GAP_S:
            continue  # speech resumes too soon to pad; the cut is right where it is
        assert end > last + 1e-3, (
            f"clip ends on the timestamp of {last}s, truncating the final word")


def test_refine_does_not_mutate_its_input(chain):
    cand = dict(chain["candidates"][0])
    before = (cand["start"], cand["end"])
    candidates.refine_boundaries(cand, chain["transcript"], chain["signals"],
                                 min_s=15, max_s=90)
    assert (cand["start"], cand["end"]) == before


def test_payoff_is_inside_the_clip(chain):
    for cand in chain["candidates"]:
        if "payoff_t" in cand:
            assert cand["start"] <= cand["payoff_t"] <= cand["end"] + 1e-6


# ── the feature contract ─────────────────────────────────────────────────────


def test_features_cover_every_consumer(chain):
    """scoring.py and ranker.FEATURE_ORDER both read features BY NAME through a
    defaulting accessor. A dropped key degrades silently instead of raising, so
    the coverage is asserted here rather than discovered in a bad export."""
    cand = chain["candidates"][0]
    feats = candidates.extract_features(
        cand, chain["transcript"], chain["signals"], chain["duration"]
    )

    scoring_keys = set(re.findall(r'g\("([a-z_]+)"\)', inspect.getsource(scoring)))
    missing_scoring = scoring_keys - set(feats)
    assert not missing_scoring, f"scoring.py reads features nobody emits: {sorted(missing_scoring)}"

    missing_ranker = set(ranker.FEATURE_ORDER) - set(feats)
    assert not missing_ranker, f"ranker reads features nobody emits: {sorted(missing_ranker)}"


def test_features_are_all_finite(chain):
    feats = candidates.extract_features(
        chain["candidates"][0], chain["transcript"], chain["signals"], chain["duration"]
    )
    bad = {k: v for k, v in feats.items() if not isinstance(v, float) or not math.isfinite(v)}
    assert not bad, f"non-finite features would poison the ranker: {bad}"


def test_features_survive_empty_signals(chain):
    """A degenerate source (silent, unreadable proxy) must degrade, not crash."""
    feats = candidates.extract_features(
        chain["candidates"][0], chain["transcript"], {}, chain["duration"]
    )
    assert all(math.isfinite(v) for v in feats.values())


# ── scoring ──────────────────────────────────────────────────────────────────


def test_every_profile_is_normalised():
    for name, weights in scoring.PROFILES.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6, f"profile {name} weights sum to {total}, not 1"
        assert set(weights) <= set(scoring.SUB_SCORES), f"profile {name} has unknown sub-scores"


def test_scores_clamp_on_degenerate_input():
    cand = {"start": 0.0, "end": 30.0, "text": ""}
    for feats in ({}, dict.fromkeys(scoring.SUB_SCORES, 0.0), {"audio_rms_mean": 1e9}):
        out = scoring.score_candidate(cand, feats, profile="gaming", platform="tiktok")
        assert 0.0 <= out["overall"] <= 100.0
        assert set(out["sub_scores"]) == set(scoring.SUB_SCORES)
        for name, value in out["sub_scores"].items():
            assert 0.0 <= value <= 100.0, f"{name} escaped the clamp with {value}"
        assert isinstance(out["reason"], str) and out["reason"]


def test_platform_fit_peaks_inside_the_band():
    """TikTok's band is tighter than YouTube Shorts', so a 30s clip must not
    score the same on both."""
    tiktok_mid = scoring.platform_fit_score(30.0, "tiktok")
    tiktok_long = scoring.platform_fit_score(120.0, "tiktok")
    assert tiktok_mid > tiktok_long
    assert scoring.platform_fit_score(55.0, "youtube_shorts") > scoring.platform_fit_score(
        55.0, "tiktok"
    )


def test_unknown_profile_falls_back_rather_than_raising():
    out = scoring.score_candidate(
        {"start": 0.0, "end": 25.0}, {}, profile="not_a_profile", platform="tiktok"
    )
    assert 0.0 <= out["overall"] <= 100.0


# ── dedupe ───────────────────────────────────────────────────────────────────


def test_text_similarity_bounds():
    a = "the round changed in one single second"
    assert dedupe.text_similarity(a, a) == pytest.approx(1.0, abs=1e-6)
    assert dedupe.text_similarity(a, "completely unrelated vocabulary here") < 0.35
    assert dedupe.text_similarity("", "") == 0.0


def test_overlapping_candidates_collapse_to_one_winner():
    base = {"text": "he jumped and immediately fell off the edge again", "overall": 80.0}
    cands = [
        {**base, "start": 10.0, "end": 40.0, "overall": 80.0},
        {**base, "start": 11.0, "end": 41.0, "overall": 60.0},   # ~97% overlap
        {"text": "completely different topic about settings menus", "start": 200.0,
         "end": 230.0, "overall": 70.0},
    ]
    out = dedupe.deduplicate(cands, overlap_threshold=0.4, text_threshold=0.62,
                             target_count=5)
    winners = [c for c in out if not c.get("is_alternative")]
    alts = [c for c in out if c.get("is_alternative")]

    assert len(out) == 3, "nothing may be silently dropped"
    assert len(winners) == 2 and len(alts) == 1
    assert alts[0]["overall"] == 60.0, "the weaker twin should be the alternative"
    assert all(a["dedupe_group"] for a in alts), "an alternative must stay retrievable"


def test_rank_positions_are_contiguous_from_one():
    cands = [
        {"text": f"distinct topic number {i} with its own words", "start": i * 100.0,
         "end": i * 100.0 + 30.0, "overall": 90.0 - i}
        for i in range(5)
    ]
    out = dedupe.deduplicate(cands, overlap_threshold=0.4, text_threshold=0.62,
                             target_count=5)
    ranks = sorted(c["rank_position"] for c in out if not c.get("is_alternative"))
    assert ranks == list(range(1, len(ranks) + 1))


# ── layout ───────────────────────────────────────────────────────────────────


def _regions(webcam=None, conf=0.9):
    return {
        "webcam": webcam,
        "gameplay": {"x": 0, "y": 0, "w": 480, "h": 270},
        "chat": None,
        "hud": [],
        "confidence": {"webcam": conf, "gameplay": 0.9},
        "frame_width": 480,
        "frame_height": 270,
    }


def test_a_gaming_source_with_a_facecam_gets_the_split_layout():
    """The content type has to REACH plan_layout. It did not: neither pipeline
    caller passed it, the candidate dict never carried it and neither did
    regions.json, so `gaming` was always False and every gaming export came out
    as a tracking crop with no face band. Verified on a real export before the
    fix — layout was 'talking_head' on a source with two facecams."""
    plan = layout.plan_layout(
        {"start": 0.0, "end": 30.0},          # no content_type here, as in the pipeline
        _regions(webcam={"x": 0, "y": 0, "w": 122, "h": 68}),
        [{"t": float(i), "boxes": [[20, 15, 30, 30]]} for i in range(20)],
        1920, 1080, mode="auto", content_type="gaming",
    )
    assert plan["layout"] == "face_top_game_bottom", plan["layout"]
    assert plan["face_rect"], "a gaming split layout with no face band is the bug"


def test_a_layout_plan_from_another_resolution_is_not_reused():
    """A plan is crops in SOURCE pixels, valid only for the dimensions it was
    computed against. Swap the source — a re-download, an HD replacement of a
    proxy-resolution cut — and the old rects still apply cleanly and silently
    produce nonsense: measured, a plan built for 854x480 used against
    1920x1080 cropped the top-left corner as the facecam and a narrow strip as
    the gameplay."""
    from workers.clipper_render_jobs import _plan_fits

    # The exact plan from the broken export: every rect sits comfortably
    # inside a 1920x1080 frame, so a bounds check cannot tell it is wrong.
    small = {"src_w": 854, "src_h": 480,
             "face_rect": {"x": 0, "y": 0, "w": 216, "h": 134},
             "game_rect": {"x": 218, "y": 0, "w": 414, "h": 480}}
    assert _plan_fits(small, 854, 480)
    assert not _plan_fits(small, 1920, 1080), (
        "a 854x480 plan fits inside 1080p geometrically — only the recorded "
        "frame can tell them apart"
    )


def test_a_plan_records_the_frame_it_was_measured_in():
    plan = layout.plan_layout({"start": 0.0, "end": 30.0}, _regions(), [],
                              1920, 1080, mode="auto")
    assert plan["src_w"] == 1920 and plan["src_h"] == 1080


def test_a_plan_from_before_the_frame_was_recorded_still_bounds_checks():
    """Older stored plans carry no src_w/src_h; they must not all be thrown
    away, but a plan larger than the source is still detectable."""
    from workers.clipper_render_jobs import _plan_fits

    legacy = {"game_rect": {"x": 492, "y": 0, "w": 934, "h": 1080}}
    assert _plan_fits(legacy, 1920, 1080)
    assert not _plan_fits(legacy, 854, 480)


def test_the_pipeline_callers_pass_the_content_type():
    """Both call sites, asserted by name — the failure above is invisible at
    runtime, so it has to be caught here."""
    from workers import clipper_build, clipper_render_jobs

    for mod in (clipper_build, clipper_render_jobs):
        src = inspect.getsource(mod)
        assert "plan_layout(" in src, f"{mod.__name__} no longer plans layouts"
        call = src[src.index("plan_layout("):]
        assert "content_type=" in call[:600], (
            f"{mod.__name__} calls plan_layout without content_type"
        )


def test_the_face_band_is_sharpened_and_the_gameplay_lane_is_not():
    """A stream composites a small webcam into its 1080p canvas, so the face
    band is a 2.5x enlargement and the softest thing on screen. The gameplay
    lane is barely enlarged and sharpening it would only harden the
    compression noise in a dark scene."""
    graph = layout.build_filtergraph({
        "layout": "face_top_game_bottom",
        "face_rect": {"x": 12, "y": 0, "w": 436, "h": 272},
        "game_rect": {"x": 492, "y": 0, "w": 934, "h": 1080},
        "face_pct": 0.35,
    })
    face_lane, game_lane = graph.split(";")[0], graph.split(";")[1]
    assert "unsharp" in face_lane, "the face band is the lane that needs it"
    assert "unsharp" not in game_lane, "the gameplay lane must not be sharpened"


def test_a_lane_that_is_barely_enlarged_is_left_alone():
    """Below the threshold the lane has its own detail; sharpening is noise."""
    graph = layout.build_filtergraph({
        "layout": "face_top_game_bottom",
        # 1000px wide into a 1080 lane — 1.08x, nothing to recover.
        "face_rect": {"x": 0, "y": 0, "w": 1000, "h": 620},
        "game_rect": {"x": 0, "y": 0, "w": 934, "h": 1080},
        "face_pct": 0.35,
    })
    assert "unsharp" not in graph


def test_the_face_band_never_reaches_outside_the_facecam():
    """Growing the facecam rect to the band's aspect drags in whatever the
    streamer parked next to it. Measured on a real export: a 488x272 facecam
    became 488x304 and sliced the stats bar underneath in half."""
    webcam = {"x": 0, "y": 0, "w": 488, "h": 272}
    plan = layout.plan_layout(
        {"start": 0.0, "end": 30.0},
        _regions(webcam={"x": 0, "y": 0, "w": 122, "h": 68}),
        [{"t": float(i), "boxes": [[20, 15, 30, 30]]} for i in range(20)],
        1920, 1080, mode="auto", content_type="gaming", face_pct=0.35,
    )
    face = plan["face_rect"]
    assert face, "gaming split with no face band"
    assert face["x"] >= webcam["x"] and face["y"] >= webcam["y"]
    assert face["x"] + face["w"] <= webcam["x"] + webcam["w"] + 2
    assert face["y"] + face["h"] <= webcam["y"] + webcam["h"] + 2, (
        f"the band reaches {face['y'] + face['h']}px, past the facecam's "
        f"{webcam['y'] + webcam['h']}px — that is the stats bar"
    )


def test_no_webcam_falls_back_and_never_leaves_an_empty_face_band():
    plan = layout.plan_layout(
        {"start": 0.0, "end": 30.0, "content_type": "gaming"},
        _regions(webcam=None), [], 1920, 1080, mode="auto",
    )
    assert plan["layout"] in ("fullscreen_game", "fullscreen_crop")
    assert plan["face_rect"] is None, "a fallback layout must not claim a face band"


def test_tiny_facecam_warns_instead_of_shipping_mush():
    # 40px wide in a 480px frame -> 160px in a 1920px source, under the 220 floor.
    plan = layout.plan_layout(
        {"start": 0.0, "end": 30.0, "content_type": "gaming"},
        _regions(webcam={"x": 10, "y": 10, "w": 40, "h": 30}),
        [{"t": 1.0, "boxes": [[15, 15, 25, 20]]}],
        1920, 1080, mode="auto",
    )
    assert plan["warnings"], "a too-small facecam must be called out"


def test_face_pct_is_clamped():
    for requested, in ((0.01,), (9.0,)):
        plan = layout.plan_layout(
            {"start": 0.0, "end": 30.0}, _regions(), [], 1920, 1080,
            mode="auto", face_pct=requested,
        )
        assert 0.15 - 1e-9 <= plan["face_pct"] <= 0.6 + 1e-9


# ── a re-score must not put an exported moment on the board twice ────────────


def test_a_re_score_does_not_re_propose_an_exported_moment():
    """Exported clips are preserved across a re-analysis, deliberately. But
    dedupe only ever sees the fresh candidates, so nothing stopped the new set
    proposing the same moment again. Observed after three exports and a
    re-score: 11 winners for a requested 8, one moment on the board 3 times."""
    from workers.clipper_build import drop_moments_already_exported

    kept = [{"start": 117.8, "end": 157.5}]
    ranked = [
        {"start": 117.8, "end": 157.5},   # the same window
        {"start": 134.8, "end": 157.5},   # entirely inside it
        {"start": 100.3, "end": 139.3},   # heavily overlapping
        {"start": 381.3, "end": 404.4},   # a different moment
        {"start": 633.3, "end": 666.7},   # a different moment
    ]
    fresh = drop_moments_already_exported(ranked, kept, 0.4)
    spans = [(c["start"], c["end"]) for c in fresh]
    assert (117.8, 157.5) not in spans
    assert (134.8, 157.5) not in spans, "a window inside the export is the same moment"
    assert (381.3, 404.4) in spans and (633.3, 666.7) in spans


def test_nothing_exported_means_nothing_dropped():
    from workers.clipper_build import drop_moments_already_exported

    ranked = [{"start": 0.0, "end": 30.0}, {"start": 60.0, "end": 90.0}]
    assert drop_moments_already_exported(ranked, [], 0.4) == ranked


# ── features have to actually vary ───────────────────────────────────────────
#
# A feature pinned to one value is not neutral: it is weight spent on an
# offset. Measured on the co-stream's 57 candidate windows before these fixes,
# audio_peak_ratio read exactly 1.000 on every single one (30% of
# audio_energy) and audio_dynamic_range had a stdev of 0.027 with a median of
# 0.998 (another 20%). audio_energy therefore carried 12% of the gaming
# profile's weight with a stdev of 3.4.


def _two_halves() -> dict:
    """A source whose first half is loud and peaky and second half is not.

    The shared `chain` fixture cannot show this: its rms is a periodic
    sequence, so every window of the same length has an identical distribution
    and these features are legitimately constant on it. Testing against it
    would test the fixture.
    """
    hop, half = 0.25, 600                       # 150s each side
    rms = [0.9 if (i // 8) % 2 == 0 else 0.1 for i in range(half)]  # swinging
    rms += [0.4] * half                                             # flat
    peaks = [round(i * 1.5, 2) for i in range(1, 100)]              # first half only
    span = [[0.0, 300.0]]
    return {
        "duration": 300.0,
        "audio": {"hop_s": hop, "rms": rms, "peaks": peaks,
                  "silence": [], "speech": span},
        "peaks": peaks, "silence": [], "speech": span,
        "motion": {"hop_s": 0.5, "motion": [0.3] * 600},
    }


def test_the_audio_features_respond_to_the_audio():
    """Each of these was pinned before: audio_peak_ratio read exactly 1.000 on
    all 57 real windows, audio_dynamic_range had sd 0.027 at a median of 0.998,
    and peak_prominence divided by a max that the source normalisation had
    already pinned to 1.0."""
    sv = segmentation.signal_view(_two_halves())
    loud = candidates._audio(10.0, 60.0, 50.0, sv, 40.0)
    flat = candidates._audio(200.0, 250.0, 50.0, sv, 230.0)

    for key in ("audio_peak_ratio", "audio_dynamic_range", "peak_prominence"):
        assert loud[key] != flat[key], f"{key} cannot tell the two halves apart"
        assert not (loud[key] == 1.0 and flat[key] == 1.0), f"{key} saturates"

    assert loud["audio_dynamic_range"] > flat["audio_dynamic_range"]
    assert loud["peak_prominence"] > flat["peak_prominence"]
    assert loud["audio_peak_ratio"] > flat["audio_peak_ratio"]


def test_peak_ratio_is_relative_to_the_source_not_a_fixed_rate():
    """The old divisor assumed one peak per 8 seconds. On a source that peaks
    every 1.5s that cleared on every window, so the feature said 1.000 always."""
    sv = segmentation.signal_view(_two_halves())
    at_source_rate = candidates._audio(10.0, 60.0, 50.0, sv, 40.0)
    assert at_source_rate["audio_peak_ratio"] < 0.99, (
        f"a window at roughly the source's own peak rate reads "
        f"{at_source_rate['audio_peak_ratio']:.3f} — the fixed divisor is back"
    )


# ── speech comes from the transcript, not the noise floor ────────────────────


def test_speech_ratio_measures_words_not_noise(chain):
    """The audio spans answer 'is the track above the silence floor', which on
    a stream with constant game audio is always yes. Measured on the co-stream:
    94% of the VOD called speech, 24 of 56 windows at exactly 1.000, against a
    transcript covering 34%."""
    cand = chain["candidates"][0]
    sig = dict(chain["signals"])
    # A source that is never silent — the gaming case.
    span = (0.0, chain["duration"])
    sig["speech"] = [list(span)]
    sig["silence"] = []
    feats = candidates.extract_features(cand, chain["transcript"], sig, chain["duration"])
    assert feats["speech_ratio"] < 0.95, (
        "speech_ratio still reads the noise floor: "
        f"{feats['speech_ratio']:.3f} on a transcript with real gaps"
    )
    assert feats["speech_ratio"] > 0.0


def test_speech_ratio_falls_back_when_there_are_no_word_timings(chain):
    """No timings means the audio estimate is the only one there is."""
    cand = dict(chain["candidates"][0])
    cand["words"] = []
    feats = candidates.extract_features(cand, {"segments": []}, chain["signals"],
                                        chain["duration"])
    assert 0.0 <= feats["speech_ratio"] <= 1.0


# ── facecam insets ───────────────────────────────────────────────────────────
#
# Synthetic frames, not decoded ones, but the geometry is the co-stream's:
# two 1080p60 facecams, IShowSpeed top-left flush to the corner and KaiCenat
# top-right, over a 480x270 analysis proxy. Ground truth read off the real
# frames is (0,0,122,69) and (355,5,93,63).


def _scene(insets, fw=480, fh=270, frames=20):
    """Dark noisy 'gameplay' with bright, still rectangles composited on it.

    A real inset differs from its surroundings in both level and how much it
    changes frame to frame, which is what the edge search keys on.

    THE GAMEPLAY'S PER-FRAME LEVEL IS LOAD-BEARING. Without it this scene is
    the opposite of a real one: pure noise over 129,600 pixels averages to the
    same number every frame, so the "game" here held perfectly still while the
    inset was the only thing that moved. A real game swings from a cave to
    daylight to a menu and a locked-off webcam does not, which is the whole
    basis of `scene_independence`. Drop the level and every facecam in this
    file reads as a phantom — the fixture would be testing itself.
    """
    import numpy as np

    rng = np.random.default_rng(7)
    out = []
    for _ in range(frames):
        level = int(rng.integers(0, 120))
        frame = (rng.integers(0, 60, size=(fh, fw)) + level).astype("uint8")
        for x, y, w, h in insets:
            patch = np.full((h, w), 190, dtype="uint8")
            patch[h // 4:h // 2, w // 4:w // 2] = rng.integers(150, 230)
            frame[y:y + h, x:x + w] = patch
        out.append(frame)
    return out


def _face_at(insets, seen, frames=20):
    """A face box in the middle of each inset, present in `seen` frames."""
    from services.clipper.content_geom import make_rect

    per_frame = []
    for i in range(frames):
        boxes = []
        for j, (x, y, w, h) in enumerate(insets):
            if i < seen[j]:
                boxes.append(make_rect(x + w * 0.35, y + h * 0.30, w * 0.30, h * 0.35))
        per_frame.append(boxes)
    return per_frame


def test_both_facecams_of_a_co_stream_are_found():
    """The old detector returned at most one, so a layout built from it framed
    the second streamer as though he were the game."""
    from services.clipper import content_type

    insets = [(0, 0, 122, 68), (356, 6, 92, 62)]
    rects, confs = content_type._find_webcams(
        _scene(insets), _face_at(insets, seen=[14, 13]), 480, 270)
    assert len(rects) == 2, f"expected two facecams, got {rects}"
    assert all(c > 0.0 for c in confs)
    found = sorted((r["x"], r["x"] + r["w"]) for r in rects)
    assert found[0][0] <= 6 and abs(found[0][1] - 122) <= 12
    assert abs(found[1][0] - 356) <= 14 and abs(found[1][1] - 448) <= 14


def test_a_facecam_seen_in_a_third_of_frames_still_counts():
    """Measured on the real frames the two facecams landed 14 and 13 hits out
    of 40. The old gate wanted half, which neither could ever have cleared."""
    from services.clipper import content_type

    insets = [(0, 0, 122, 68)]
    rects, _ = content_type._find_webcams(
        _scene(insets), _face_at(insets, seen=[7]), 480, 270)
    assert rects, "a facecam present in 35% of frames was discarded"


def test_a_face_that_flashes_past_is_not_a_facecam():
    from services.clipper import content_type

    insets = [(0, 0, 122, 68)]
    rects, _ = content_type._find_webcams(
        _scene(insets), _face_at(insets, seen=[2]), 480, 270)
    assert not rects, "two sightings are noise, not an inset"


def test_a_composited_rect_covering_half_the_frame_is_still_too_big():
    """The area bound, on the FINAL rect — the fallback padding would sail
    through a check made on the seed. This case is genuinely a composite; it is
    rejected for its size, which is the only thing wrong with it."""
    from services.clipper import content_type

    huge = [(240, 0, 240, 270)]
    rects, _ = content_type._find_webcams(
        _scene(huge), _face_at(huge, seen=[18]), 480, 270)
    assert not rects, "a region covering a third of the frame is not an inset"


def test_a_wide_shot_with_people_in_it_is_not_a_facecam():
    """The gym-camera project has no inset at all: one wide IRL camera with
    people in frame. Deriving a rect from the faces reported its own right half
    as a facecam, area 0.36 and aspect 0.78 — outside both bounds.

    This test used to build that case by COMPOSITING a still rectangle where
    the people were, which is the one thing a wide shot does not contain, and
    it passed because the rect came out too big rather than because it was not
    an inset. Widen the aspect band — which the labelled sources needed — and
    it would have started passing a phantom. So the scene here has no
    composite at all: one continuous picture, faces in part of it. What
    rejects it is `scene_independence`, on purpose.
    """
    from services.clipper import content_type
    from services.clipper.content_geom import make_rect

    frames = _scene([])
    boxes = [[make_rect(353, 74, 58, 86)] if i < 18 else [] for i in range(20)]
    rects, _ = content_type._find_webcams(frames, boxes, 480, 270)
    assert not rects, (
        f"a window onto one continuous scene is not a second camera: {rects}")


def test_a_facecam_flush_to_the_frame_reaches_the_frame():
    """There is no border to find at a flush edge, so the strongest step in
    range is interior texture. Measured, that cost the left facecam its first
    24 columns."""
    from services.clipper import content_type

    insets = [(0, 0, 122, 68)]
    rects, _ = content_type._find_webcams(
        _scene(insets), _face_at(insets, seen=[14]), 480, 270)
    assert rects and rects[0]["x"] == 0 and rects[0]["y"] == 0


# ── a layout is only a layout if it lasts ────────────────────────────────────
#
# Detecting regions per stretch buys the second facecam on a source whose
# camera changes, and costs this: a stretch where the streamer reacts to a
# video containing a webcam reports that video as an inset. Measured on
# Jynxzi's 60-80 minute stretch, where it clears `scene_independence` at 0.96
# against a 1.0 bar — the closest call in the corpus, and one that cannot be
# fixed by moving the bar, because his REAL camera sits at 0.89.


def _stretch(idx, cams):
    return {"start": idx * 1200.0, "end": (idx + 1) * 1200.0,
            "frame_width": 480, "frame_height": 270,
            "webcam": cams[0] if cams else None, "webcams": list(cams),
            "confidence": {"webcam": 0.7 if cams else 0.0}}


def _cam(x, y, w=158, h=158):
    return {"x": x, "y": y, "w": w, "h": h}


def test_counting_people_does_not_borrow_the_facecam_bar():
    """Two questions, two constants, and they were one until they diverged.

    `_FACECAM_MIN_RATE` asks "is this rectangle part of the layout", where a
    long sample legitimately dilutes the rate. `_SCENE_MIN_RATE` asks "is
    somebody there", where it does not. Lowering the first from 0.15 to 0.10 —
    right on its own terms, 8/9 to 9/9 on the facecam scoreboard — moved the
    second with it, and on the SAME artifacts with only the code changed
    Jynxzi's scene count went 0 to 1, the source flipped `gaming` to
    `talking_head`, and the content classifier went 6/11 to 5/11.
    """
    import inspect

    from services.clipper import content_type

    assert content_type._SCENE_MIN_RATE == 0.15
    src = inspect.getsource(content_type._scene_faces)
    assert "_SCENE_MIN_RATE" in src
    assert "_FACECAM_MIN_RATE" not in src, (
        "counting people in the scene is reading the facecam gate again")


def test_a_facecam_seen_in_one_stretch_of_many_is_content_not_a_camera():
    from services.clipper.content_type import _drop_transient_webcams

    ranges = [_stretch(i, [_cam(0, 110 + i)]) for i in range(6)]
    ranges[3]["webcams"].append(_cam(240, 0, 240, 136))
    _drop_transient_webcams(ranges)

    assert [len(r["webcams"]) for r in ranges] == [1, 1, 1, 1, 1, 1]
    assert ranges[3]["webcams"][0]["x"] == 0
    assert ranges[3]["webcam"]["x"] == 0, "the best-of pointer went stale"


def test_a_second_facecam_on_half_the_stretches_survives():
    """Minecraft's co-streamer is on camera for five stretches of twelve. One
    against five is a co-stream, not a phantom — only a SINGLE appearance is
    treated as content."""
    from services.clipper.content_type import _drop_transient_webcams

    ranges = [_stretch(i, [_cam(0, 0, 122, 78)]) for i in range(10)]
    for i in (1, 3, 5, 7, 9):
        ranges[i]["webcams"].append(_cam(370, 0, 80, 68))
    _drop_transient_webcams(ranges)

    assert sum(len(r["webcams"]) for r in ranges) == 15


def test_a_brief_facecam_with_nothing_to_outvote_it_is_kept():
    """Kai Cenat's inset exists for four minutes of 112. A source whose only
    facecam is brief has no settled layout for it to contradict, and dropping
    it would be the rule deciding a source has no camera because it looked
    away."""
    from services.clipper.content_type import _drop_transient_webcams

    ranges = [_stretch(i, []) for i in range(6)]
    ranges[2]["webcams"] = [_cam(0, 0, 186, 134)]
    ranges[2]["webcam"] = ranges[2]["webcams"][0]
    _drop_transient_webcams(ranges)

    assert ranges[2]["webcams"], "the only facecam in the source was dropped"


def test_region_detection_looks_at_more_frames_than_the_classifier(tmp_path):
    """`_MAX_FRAMES = 40` answers "how stable is this layout", which a handful
    of frames can settle. It cannot answer "is there an inset here", which is
    what region detection asks.

    Measured on the 4-hour co-stream: 400 frames sampled, 40 looked at, and the
    co-streamer's facecam landed 3 of those 40 — rate 0.075, under any usable
    bar. Not rare, under-sampled. `_pick` takes every step-th frame, so at 400
    frames and a budget of 40 it keeps one in ten and whatever falls between
    the strides does not exist. One facecam at 40, 100 and 200 frames; both at
    400.
    """
    import cv2
    import numpy as np

    from services.clipper import content_type

    assert content_type._REGION_FRAMES > content_type._MAX_FRAMES

    paths = []
    for i in range(120):
        p = tmp_path / f"f{i:04d}.jpg"
        cv2.imwrite(str(p), np.full((270, 480, 3), 40 + i % 5, dtype=np.uint8))
        paths.append(str(p))

    assert len(content_type._load_frames(paths)[0]) == content_type._MAX_FRAMES
    assert len(content_type._load_frames(paths, content_type._REGION_FRAMES)[0]) == 120


def test_a_facecam_flush_to_the_BOTTOM_reaches_the_bottom():
    """The same rule downward, which is where it was silently switched off.

    `_snap_edge` concludes "the inset runs into the frame" only when its window
    reaches the limit, and `_FACECAM_REACH_CAP` truncates the window — so a
    facecam whose face sits further than 0.30 of the frame from the edge it is
    against never got that answer. Both labelled bottom-left sources lost 25
    and 46 rows of a 270-row proxy to it, which is 100 to 184 px of the
    streamer's torso on a 1080p source, cut out of the face band.
    """
    from services.clipper import content_type

    insets = [(0, 88, 182, 182)]
    rects, _ = content_type._find_webcams(
        _scene(insets), _face_at(insets, seen=[16]), 480, 270)
    assert rects, "a bottom-left inset was not found at all"
    assert rects[0]["y"] + rects[0]["h"] >= 269, (
        f"the bottom edge stopped short of the frame: {rects[0]}")


def test_one_face_detector_not_two():
    """content_type used to run its own untuned cascade. On the co-stream it
    measured 0 faces in 40 frames where the tuned one finds 27 — which is why
    regions.json reported no webcam on a source with two of them."""
    from services.clipper import content_type, signals

    src = inspect.getsource(content_type)
    assert "CascadeClassifier" not in src, "a second face detector came back"
    assert callable(signals.detect_faces)


# ── menu-heavy windows rank lower ────────────────────────────────────────────
#
# Numbers below are the real ones, measured on data/clipper/2d3375ee3420 (a
# 12-minute 1080p60 Minecraft co-stream, 1441 samples at a 0.5s hop):
# clean gameplay windows scored 0.000-0.012 and menu stretches 0.478-0.697.
# The synthetic series here reproduces that two-orders-of-magnitude split.


def _ui_signals(quiet: float, busy: float, busy_from: int, busy_to: int,
                n: int = 240) -> dict:
    series = [quiet] * n
    for i in range(busy_from, min(n, busy_to)):
        series[i] = busy
    return segmentation.signal_view({"motion": {"hop_s": 0.5, "motion": [0.4] * n,
                                                "ui": series}})


def test_a_menu_stretch_reads_as_menu_and_gameplay_does_not():
    # busy from index 100 to 160 -> 50s to 80s at a 0.5s hop
    sv = _ui_signals(0.021, 0.180, 100, 160)
    assert candidates._ui_ratio(sv, 0.0, 40.0) < 0.05, "clean gameplay flagged as menu"
    assert candidates._ui_ratio(sv, 52.0, 78.0) > 0.80, "a menu stretch went unnoticed"


def test_a_source_with_no_menus_never_reports_one():
    """The measure is relative to the source's own floor, so a source with no
    panels at all must return 0 rather than amplifying its own noise."""
    sv = _ui_signals(0.020, 0.023, 100, 160)   # spread 0.003, all noise
    for a, b in ((0.0, 40.0), (52.0, 78.0)):
        assert candidates._ui_ratio(sv, a, b) == 0.0


def test_a_menu_window_loses_visual_energy_against_identical_motion():
    """A menu is high-contrast and the cursor keeps moving, so every other
    signal in scoring.py calls it alive. This is the only thing that does not."""
    base = {"motion_mean": 0.6, "motion_peak": 0.9, "scene_cut_rate": 0.2,
            "duration": 35.0}
    cand = {"start": 0.0, "end": 35.0}
    clean = scoring.score_candidate(cand, dict(base, game_ui_ratio=0.0),
                                    profile="gaming", platform="tiktok")
    menu = scoring.score_candidate(cand, dict(base, game_ui_ratio=1.0),
                                   profile="gaming", platform="tiktok")
    assert menu["sub_scores"]["visual_energy"] < clean["sub_scores"]["visual_energy"] * 0.4
    assert menu["overall"] < clean["overall"]


def test_a_missing_ui_feature_costs_nothing():
    """Absent must behave as 'this source has no menus', not as a penalty —
    every pre-existing caller omits the key."""
    base = {"motion_mean": 0.6, "motion_peak": 0.9, "scene_cut_rate": 0.2,
            "duration": 35.0}
    cand = {"start": 0.0, "end": 35.0}
    absent = scoring.score_candidate(cand, base, profile="gaming", platform="tiktok")
    zero = scoring.score_candidate(cand, dict(base, game_ui_ratio=0.0),
                                   profile="gaming", platform="tiktok")
    assert absent["overall"] == zero["overall"]


def test_the_menu_reason_does_not_claim_the_screen_was_still():
    """'There is little on-screen motion' is a lie about an inventory screen."""
    sub = {name: 80.0 for name in scoring.SUB_SCORES}
    sub["visual_energy"] = 5.0
    reason = scoring.explain(sub, {"start": 0.0, "end": 35.0}, {"game_ui_ratio": 0.9})
    assert "menu" in reason.lower()
    assert "little on-screen motion" not in reason.lower()


def test_emitted_rects_are_even_and_inside_the_source():
    plan = layout.plan_layout(
        {"start": 0.0, "end": 30.0, "content_type": "gaming"},
        _regions(webcam={"x": 300, "y": 10, "w": 120, "h": 90}),
        [{"t": 1.0, "boxes": [[310, 20, 90, 70]]}],
        1920, 1080, mode="auto",
    )
    for key in ("face_rect", "game_rect"):
        rect = plan.get(key)
        if not rect:
            continue
        assert rect["w"] % 2 == 0 and rect["h"] % 2 == 0, f"{key} has odd dims — H.264 refuses them"
        assert rect["w"] > 0 and rect["h"] > 0
        assert rect["x"] >= 0 and rect["y"] >= 0
        assert rect["x"] + rect["w"] <= 1920 and rect["y"] + rect["h"] <= 1080


def test_filtergraph_is_a_single_pass_ending_in_a_labelled_pad():
    plan = layout.plan_layout(
        {"start": 0.0, "end": 30.0, "content_type": "gaming"},
        _regions(webcam={"x": 300, "y": 10, "w": 120, "h": 90}),
        [{"t": 1.0, "boxes": [[310, 20, 90, 70]]}],
        1920, 1080, mode="auto",
    )
    graph = layout.build_filtergraph(plan)
    assert graph.rstrip().endswith("[v]"), "render.py appends the subtitles filter to [v]"
    assert graph.count("vstack") <= 1, "two stacks would mean two composition passes"


def test_smoothing_reduces_jitter():
    jitter = [
        {"t": i * 0.5, "rect": {"x": 100 + (40 if i % 2 else -40), "y": 100,
                                "w": 400, "h": 300}}
        for i in range(12)
    ]

    def travel(kfs):
        return sum(
            abs(b["rect"]["x"] - a["rect"]["x"]) for a, b in zip(kfs, kfs[1:])
        )

    smoothed = layout.smooth_keyframes(
        jitter, max_pan_px_per_s=60.0, max_zoom_per_s=0.15, ema=0.35
    )
    assert travel(smoothed) < travel(jitter), "smoothing must actually damp movement"


# ── clip endings ────────────────────────────────────────────────────────────
#
# Measured on a real stream: every top clip ended one word into a phrase —
# "...of water bro let's" with "go" 15.8s later, "...trail chamber oh" with
# "my god" 5.5s later. The cut was in the right PLACE (speech genuinely stops
# there); it kept an orphan word that reads as a mistake.

def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_a_pause_does_not_end_a_sentence_on_a_word_nobody_stops_after():
    # "let's" then 1.4s of silence: the thought is plainly unfinished.
    words = [_w("we", 0.0, 0.3), _w("should", 0.3, 0.6), _w("let's", 0.6, 0.9),
             _w("go", 2.3, 2.6)]
    sents = segmentation.sentences_from_words(words)
    assert len(sents) == 1, "a pause closed the sentence on 'let's'"


def test_a_long_enough_silence_ends_it_anyway():
    # He did stop. Whatever the word, 3s of nothing settles the question.
    words = [_w("we", 0.0, 0.3), _w("should", 0.3, 0.6), _w("let's", 0.6, 0.9),
             _w("go", 4.5, 4.8)]
    assert len(segmentation.sentences_from_words(words)) == 2


def test_punctuation_still_outranks_the_guard():
    words = [_w("stop.", 0.0, 0.4), _w("Then", 1.9, 2.2), _w("go", 2.2, 2.5)]
    assert len(segmentation.sentences_from_words(words)) == 2


def test_continues_ignores_case_and_trailing_punctuation():
    assert segmentation._continues("Let's")
    assert segmentation._continues("the,")
    assert not segmentation._continues("chamber")
    assert not segmentation._continues("")


def test_an_orphan_final_word_is_dropped_when_silence_follows():
    words = [_w("trail", 0.0, 0.4), _w("chamber", 0.4, 0.9), _w("oh", 0.9, 1.2),
             _w("my", 6.7, 6.9), _w("god", 6.9, 7.2)]
    out = candidate_boundaries._drop_dangling_tail(1.2, words, start=0.0, lo=0.5)
    assert out == pytest.approx(0.9), "clip still ends on 'oh'"


def test_a_word_that_completes_a_thought_is_kept():
    words = [_w("trail", 0.0, 0.4), _w("chamber", 0.4, 0.9),
             _w("oh", 6.7, 6.9)]
    assert candidate_boundaries._drop_dangling_tail(0.9, words, start=0.0, lo=0.5) == 0.9


def test_no_drop_when_the_next_word_follows_immediately():
    # Running into the next phrase is not a dangling tail — it is a tight cut.
    words = [_w("we", 0.0, 0.4), _w("should", 0.4, 0.8), _w("let's", 0.8, 1.1),
             _w("go", 1.2, 1.5)]
    assert candidate_boundaries._drop_dangling_tail(1.1, words, start=0.0, lo=0.5) == 1.1


def test_the_minimum_duration_wins_over_a_tidy_ending():
    words = [_w("okay", 0.0, 0.4), _w("let's", 0.4, 0.7), _w("go", 9.0, 9.3)]
    assert candidate_boundaries._drop_dangling_tail(0.7, words, start=0.0, lo=0.6) == 0.7


# ── duration must not outvote content ────────────────────────────────────────


def test_platform_fit_carries_no_weight():
    """It was the largest single contributor to how the board is ordered — 2.10
    of 16.22 points of spread over 1073 real candidates, 13% of all
    discrimination — and it is almost binary: 798 at 100, 120 at 0. What it
    contributed was "is the duration in band", not a judgement about the clip.

    Worse, the generator and the band disagreed: `clipper_max_clip_s` is 90 s
    against a TikTok band ending at 45, so 26% of candidates were produced
    exactly as asked and then docked for it. A 50 s clip lost 6 points to a 44 s
    one while `payoff` contributes 1.14 in total.
    """
    from services.clipper.scoring import PROFILES

    for name, weights in PROFILES.items():
        assert weights.get("platform_fit", 0) == 0, (
            f"{name} still lets duration outvote content")


def test_platform_fit_is_still_computed_and_reported():
    """Zeroed, not deleted. "60s, TikTok prefers under 45" is worth showing in
    the breakdown; it just does not get a vote."""
    from services.clipper.scoring import SUB_SCORES, platform_fit_score

    assert "platform_fit" in SUB_SCORES
    assert platform_fit_score(30.0, "tiktok") > platform_fit_score(80.0, "tiktok")


def test_the_freed_weight_is_redistributed_rather_than_lost():
    """`_normalise()` divides by the real sum, so zeroing one row entry spreads
    its share proportionally instead of shrinking every score."""
    from services.clipper.scoring import PROFILES

    for name, weights in PROFILES.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-6, name
