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

## 3. `b23c14c41495` — the watch phase, and where it starts

Codex placed the boundary at the first presentation gesture, before the frame
at 228.8 s. Read densely between 223.0 and 226.0 s:

| | |
|---|---|
| **the watch is VISIBLE** | from at least f2230 (222.90 s), white, on the speaker's left wrist at the lower left of frame — visible is not presented, and the distinction is the point |
| **the left forearm is at rest** | f2240 (223.90 s) |
| **raised into frame** | f2243 (224.20 s), as he says "20K steps" |
| **gesture boundary** | **[223.90, 224.20]** — recorded as an interval. Where the arm "starts" rising is a judgement about a continuous movement, not a measurement, and a single number would state a precision nothing supports |
| **he takes it off** | around f2309 (230.90 s): both hands at the wrist, no subtitle on screen; the caption at 231.5 s says "let me take my watch off I want y'all to see it" |
| **the display fills the frame** | 239.4 and 240.0 s: no face in shot at all, and at 240.0 the detector returns one box on the watch face — the only non-subtitle text in the clip |
| **end of the phase** | **UNKNOWN.** The clip ends at 241.8 s with the watch still held up; that is where the CLIP stops, not where the phase does |

### CORRECTION, 2026-09-08 — the times in the table above are one frame early

Every time in this section was read from `CAP_PROP_POS_MSEC` taken BEFORE the
decode, where it names the PREVIOUS frame. The two properties disagree by one
frame at the same instant:

    set(POS_FRAMES, 2243)  ->  POS_FRAMES 2243, POS_MSEC 224200
    read()                 ->  POS_FRAMES 2244, POS_MSEC 224300

and f2243's presentation time is index/fps = 224.3 s. So the FRAME INDICES above
are right and the seconds beside them are each 0.1 s early. Fixed in
`source_caption_observation.observe` and the three scripts that mirror it.

THE BOUNDARIES WERE NOT SHIFTED BY 100 ms. Codex: "unele sunt alegeri
editoriale, iar baza fiecăreia trebuie identificată." Each was re-observed on a
frame with a corrected label, and all three land on the numbers they already
had:

| | basis | corrected |
|---|---|---|
| **gesture** | f2240 at rest (224.00), f2242 ALREADY RISING (224.20) — the white cuff is up at the lower centre-right. The interval narrows by one frame, from [f2240, f2243] to **[224.00, 224.20]**, and its later end is still 224.20 | **224.20** |
| **taken off** | f2309 (230.90): the watch prominent on the wrist, the other hand moving to it | **230.90** |
| **display fills the frame** | f2394 (239.40): the watch held to the lens, screen legible, the face reduced to a dark sliver behind it | **239.40** |

That the numbers survive is a coincidence of where the frames fall, not a reason
the correction did not matter. It DID matter once, at the other end: f2425 was
carried as a construction frame of the final phase on a label of 242.40, and its
true time is 242.5 s against a clip that ends at 242.42. It is not in the clip,
and the screen region was partly built from it.
`build_phase_regions._in_clip` now refuses any such frame, and the withdrawal
list names f2424 alone.

So the demonstration has three stages, not one: the watch worn and referred to
(224.2), taken off and offered (230.9), and held to the lens as the subject of
the frame (239.4). A single region for the whole stretch would have to hold a
wrist at the lower left and a watch face at the centre, which are not the same
rectangle.

---

## What these three settle, and what they do not

They settle that the source contains both kinds of second region, and where each
begins — to the frame for the first two, to a 0.3 s interval for the gesture,
which is a continuous movement and has no frame where it "starts". They do NOT
settle where any of them ends, and the tables say `UNKNOWN` rather than reaching
for the clip boundary.

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
