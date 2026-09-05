# pilotf81b — two source observations, at frame resolution

**Agent, 2026-09-05**, through `scripts/dump_source_transition.py` on the proxy
(10 fps, so one frame is 0.1 s and that is the finest interval available).

Codex limited the new work to exactly two moments, as enough to cover the two
distinct situations: **content added over the existing frame**, and **complete
replacement of the source's frame**. Both belong to the SOURCE, not to a clip —
`de3ce372ba3b` and the overlapping part of `54a7e6d31dc8` must read this same
record, intersected with their own windows.

---

## 1. The photographs appear — content added over the frame

| | |
|---|---|
| **absent, verified** | f12109 (1210.80 s), f12110 (1210.90 s) |
| **present, first frame** | **f12111 (1211.00 s)** |
| **appearance interval** | (1210.90, 1211.00] — one proxy frame, the finest this source allows |
| **animated entrance** | f12111 to f12115 (1211.00–1211.40): the pair slides DOWN from beyond the top edge, partially cropped by it at f12111, settled by f12115 |
| **settled geometry** | x ≈ 0.665–0.985, y ≈ 0.05–0.30 of the frame, **approximate** — read off a 480×270 proxy; the margins were not resolved to better than about ±0.01 |
| **what the region is** | TWO photographs of a third person, side by side, forming a before/after comparison |
| **disappearance** | **UNKNOWN.** The clip's end is not a demonstration of it; nothing was inspected after f12115 for this purpose |

**The content to keep is the PAIR.** A face detected inside one photograph is
not the thing: the comparison is between them, and a framing that keeps one and
loses the other has lost the point. The speaker's own face average has no
authority over this region.

**During the entrance the region MOVES.** For roughly 0.4 s the insert is at a
different place in every frame, and part of it is outside the frame entirely.
Any constant region for an interval that contains the entrance either includes
the swept area or clips the first frames.

---

## 2. The cut to the watch — complete replacement of the frame

| | |
|---|---|
| **speaker, verified** | f2744 (274.30), f2745 (274.40), f2746 (274.50) |
| **watch, first frame** | **f2747 (274.60 s)** |
| **cut interval** | (274.50, 274.60] — one proxy frame |
| **what the region is** | an Apple Watch on a wrist on a table, filling the frame; **no person is in shot at all** |
| **the shot moves** | the watch sits at a different place in each of f2747, f2748, f2749 — the camera is handheld and still settling |
| **watch geometry** | f2748 x ≈ 0.43–0.67, y ≈ 0.22–0.74; f2749 x ≈ 0.37–0.60, same rows. **Approximate**, and moving |
| **end of the shot** | **UNKNOWN** — not inspected |

The target here is the watch. `dynamic_plan["subject"]["face"]` is a clip-wide
average of the speaker's face and is meaningless for these frames; a region
built from it would frame nothing.

---

## What these two settle, and what they do not

They settle that the source contains both kinds of second region, and where each
begins to the frame. They do NOT settle where either ends, and the tables say
`UNKNOWN` rather than reaching for the clip boundary.

And they do not license `alive=True`. "An insert is present" must not be
translated back into the old rectangle beside the face chased by whatever moved
— that link is what would preserve the defect under a better observation.
Codex's own check is the reason: at 1213.90 s in source, with both photographs
on screen, the export frames **the building on the left** and cuts the man out;
and 5.71 s into `de3ce372ba3b` the export frames the face and **loses the
photographs**, on a shot labelled `game_tight`. So the insert exists and the
`game` camera is not presenting it. My earlier suggestion that the 14.9 s of
off-subject on `54a7e6d31dc8` "may well be framing something worth framing" is
withdrawn: the first verified frame shows the opposite.
