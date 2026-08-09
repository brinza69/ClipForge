# Style spec — synthesised from nine measured reference profiles

Companion to `style-spec.json`. Same content, with the reasoning visible.

Source: the nine profiles in this directory plus `README.md`. Every number below traces
to at least one profile, cited by clip id.

---

## How the nine were weighted

They were **not averaged**. The nine split into three tiers by how much they can tell us
about a locked-off 1920x1080 gameplay stream cut to 1080x1920:

**Tier 1 — structural analogues.** `uRU9SzlVClg` and `-dHfHZgtXJw` are locked-off stream
captures cut between discrete crop windows on one continuous source. `8cO8UWyjGyc` is two
subjects inside one source frame with the crop alternating between them — the technique the
renderer was designed around, and the only reference whose *shape* matches "two cameras
inside one frame". Where these three agree, that is the spec.

**Tier 2 — informative but differently built.** `_LQ379ZhspI` (two stream feeds intercut),
`9L2Yrs6jwb4` (reaction host plus skit), `q-SaGj-pDh0` (handheld selfie vlog),
`Cfpc04Tpc4k` (handheld b-roll). They contribute where the question is source-agnostic —
caption sizing, colour semantics, cut-on-speech — and are discounted where it is not.

**Tier 3 — different medium.** `8-eCvn1gWIg` is a produced studio piece with background
removal and a second shoot. `jOUyn64mSsk` is a raw 27.5 s single take with no cuts, no
captions and no effects. Both are well measured; neither says much about what to do with a
stream VOD. `jOUyn64mSsk` is worth one line of humility though: 38.7M views with literally
zero editing craft. The edit is not always what carries a clip.

Weighting is stated inline everywhere it changes an answer.

---

## The consensus, in ten lines

1. Everything comes from one continuous source. All cuts in all three Tier-1 clips are
   same-scene reframes (`same_scene_reframe_pct = 100` on each). Nothing cuts away.
2. Only 2–4 crop rectangles are ever used, they are saved and reused *exactly*, and moving
   between them is an instant hard cut — never an animated push.
3. Cut roughly every 1.8 s, ~30 cuts/min. Shots run 0.5–3.6 s.
4. Cuts land on speech events: onsets, boundaries, speaker changes, pauses.
5. Framing follows the voice — reframe to whoever or whatever is being talked about.
6. The crop is frozen inside a shot.
7. Captions are 1–2 all-caps words per card, ~0.4 s each, running essentially continuously,
   in a heavy condensed grotesque at 6–7.5% of frame height.
8. Each card enters with a centre-anchored scale pop: 0.85–0.91 → ~1.05 at +83 ms → 1.00
   by ~130 ms.
9. Editor-added effects are near zero. Two of the three Tier-1 clips have literally none.
10. The hook is verbal. Open cold mid-sentence, caption burnt in on frame 1, first cut
    inside 0.9 s, shortest shot of the clip in the first 1.2 s. No reference has a title card.

---

## Shot grammar

**Target shot length 1.8 s; range 0.6–3.0 s; ~30 cuts/min.**

The Tier-1 rates are 31.5 (`uRU9SzlVClg`), 36.1 (`8cO8UWyjGyc`) and 24.7 (`-dHfHZgtXJw`)
cuts/min — median 31.5, mean 30.8. Their average shot lengths are 1.713 / 1.454 / 2.208 s.
Their p10–p90 spreads are 0.65–2.66, 0.527–2.95 and 0.933–3.633 s.

For context the full set runs 0 to 45.3 cuts/min, which is why weighting matters: taking
all nine would drag the target down to ~26 and widen the range to something meaningless.

**Reframe vs cutaway: 100:0.** All three Tier-1 clips measure 100% same-scene reframe. The
renderer cannot cut away in one pass, so this constraint is free. For contrast the other
six run 0% (`Cfpc04Tpc4k`), 6.7% (`q-SaGj-pDh0`), 20% (`_LQ379ZhspI`), 50% (`8-eCvn1gWIg`)
and 66.7% (`9L2Yrs6jwb4`) — every one of those cutaways needs footage we do not have.

### The framing ladder

Two references published an explicit ladder, both as **zoom factors** (larger = tighter).
The spec's coordinate system is the inverse — **crop factor** relative to the widest 9:16
window of a 1920x1080 frame, 608x1080 = 1.00, smaller = tighter. Reconciled:

| rung | crop factor | rectangle | evidence |
|---|---|---|---|
| WIDE | 1.00 | 608x1080 | `-dHfHZgtXJw` shots 8–11 (base; later shots return at s=1.0003–1.0014); `8cO8UWyjGyc` widest framing at 6.70 s |
| MEDIUM | 0.88 | 536x950 | `-dHfHZgtXJw` 1.10–1.13x → 0.885–0.909 (388 and 158 ORB inliers); `8cO8UWyjGyc` 1.15x → 0.870 |
| TIGHT | 0.76 | 462x820 | `8cO8UWyjGyc` measures this one *directly* as 0.755 and 0.758 in units of its wide crop, and separately as 1.32–1.34x; `-dHfHZgtXJw` shot 7 lands here at 1.21–1.32x but flags ±10% |
| PUNCH | 0.71 | 432x766 | `-dHfHZgtXJw` shots 1–3 at ~1.40x → 0.714; `8cO8UWyjGyc` tight end 1.38x → 0.725 |
| *(optional)* EXTREME | 0.67 | 408x724 | `_LQ379ZhspI` only — BEAST-TIGHT is ~1.5x BEAST-WIDE, from subject measurement after ORB gave a false match |

`8cO8UWyjGyc` is the useful one here because it gives crop factors natively rather than as
ratios: its two working windows sit at scale 0.755 and 0.758 in units of the wide crop, and
1/1.32 = 0.758, so the two routes cross-check.

Rectangles are round-to-even of `608*f` and `1080*f`. That drifts the aspect by ≤0.2%
(462/820 = 0.5634 vs 0.5625) — invisible after `scale=1080:1920`, and both dimensions must
stay even for H.264 anyway.

**Use three rungs by default.** `-dHfHZgtXJw` uses four zoom levels across eight distinct
framings, `8cO8UWyjGyc` three to four, and `uRU9SzlVClg` uses only *two* for the whole clip
while still being the closest structural analogue we have.

### Rules — signals, not vibes

- **Cut on speech onset.** `8cO8UWyjGyc`: cuts land on speech boundaries with audio running
  continuously across every one. `_LQ379ZhspI` quantifies it: mean level rise 11.5 dB at its
  10 cut points against 6.9 dB (sd 4.9) at 129 random non-cut times — about 0.9 sd, i.e.
  cuts sit on onsets and carry no added whoosh.
- **Cut on speaker change, but not every one.** `9L2Yrs6jwb4`: all 3 cuts land exactly on a
  speaker change, but only 3 of 6 speaker turns get a cut. Gate the candidate on
  `min_shot_s`.
- **Cut on a pause.** `uRU9SzlVClg`'s 14.517 s cut is confirmed by a 167 ms silence gap
  straddling it (−47.9 dB RMS). `-dHfHZgtXJw` ducks to −54.8 dBFS into its 15.117 s cut.
  Any speech gap ≥150 ms is a free cut point.
- **Framing follows the voice, not the picture.** `8cO8UWyjGyc` alternates its two windows
  across a question-and-reaction exchange. For the target: facecam on the speaker's own
  speech, gameplay window on the thing being described.
- **Tighter on energy, wider on the payoff.** `-dHfHZgtXJw` opens at its tightest rung
  mid-shout with audio at its loudest of the whole clip (−6.8 dBFS RMS in the first 100 ms)
  and settles to f=1.00 for the calm middle. `8cO8UWyjGyc` holds its widest framing for the
  4.93 s payoff. Map the rung to windowed speech RMS percentile; reserve the tightest rung
  for the top decile.
- **Front-load the shortest shot.** `uRU9SzlVClg`'s first shot (0.65 s) is the shortest in
  the clip; `8cO8UWyjGyc`'s shortest (0.317 s) sits inside the first 1.2 s.
- **First cut inside 0.9 s.** 0.350 / 0.650 / 0.867 s on the Tier-1 clips. This one is
  target-weighted, not a nine-clip consensus — Tier 2/3 run 1.1 to 2.8 s.
- **Reuse rectangles byte-identically.** `8cO8UWyjGyc` returns to a window within 1 px and
  0.05% scale; `-dHfHZgtXJw` returns to exact identity seconds apart (shot 8 vs shot 11,
  s=0.9996, rot=+0.026°). Recomputing per shot would add a wobble neither reference has.

---

## Captions

**Size:** cap height 7.0% of frame height, range 5.2–8.0%. Tier-1 gives 7.55%
(`8cO8UWyjGyc`, 145 px at 1920) and 7–8% (`-dHfHZgtXJw`, 100–120 px at 1440), with
`uRU9SzlVClg` lower at 5.8% — it can afford to be, because it sits on an opaque card that
buys legibility a stroke has to buy with size. Wider set for context: 6.8%
(`_LQ379ZhspI`), 5.2% (`9L2Yrs6jwb4`), 4.4% (`8-eCvn1gWIg`), 4.0% (`q-SaGj-pDh0`).

**Vertical position: 62.5%**, from `8cO8UWyjGyc` — chosen because it is the only reference
with two subjects inside one source frame, which is the target's structure, and it places
the text deliberately clear of both faces (which sit at 21–24% and 32–35%). The other two
Tier-1 clips disagree hard: 50.0% and 73.5%. This is a real disagreement, not a rounding
question — see below. The defensible **rule** is: put the text centre in the largest
vertical band containing neither the facecam head box nor the top of the game HUD, clamped
to 55–75%.

Horizontally centred, and this one *is* unanimous — 540/1080 exactly on `uRU9SzlVClg`,
48.9–50.7% of width on `-dHfHZgtXJw`, cx 534–543 on `9L2Yrs6jwb4`, 50.0% on `_LQ379ZhspI`,
cx 547.5 on `q-SaGj-pDh0`.

**Words per card: 1.4 mean, hard max 2.** Six of seven captioned clips land in 1.0–1.9
(1.6 / 1.4 / 1.33 / 1.2 / 1.0). The only outlier is `q-SaGj-pDh0` at 3.5, a selfie vlog
that builds multi-word cards word by word. Caveat worth carrying: every one of these is a
read count off contact sheets, not OCR — `uRU9SzlVClg` counted 45 words over 29 legible
cards of 40 detected, `_LQ379ZhspI` 46 of 77.

**Card duration ~0.42 s.** `8cO8UWyjGyc` 0.45 s mean, `uRU9SzlVClg` 0.43, `9L2Yrs6jwb4`
0.47, `_LQ379ZhspI` 0.36 with gaps p10/p50/p90 = 0.15/0.30/0.65 s. Coverage is near
continuous: 96.4% of frames on `uRU9SzlVClg`, 273 of 274 sampled on `_LQ379ZhspI`.

**Colour tracks who is speaking** — the strongest cross-clip finding in the set. All four
references with 2+ speakers do it, and each carries a decisive proof in the form of a card
coloured for a speaker who is *off screen* at that moment:

| clip | scheme | proof |
|---|---|---|
| `8cO8UWyjGyc` | yellow `#FDF400` kid / near-white `#F1F6FB` Kai | yellow card over a Kai shot, 3.72–4.25 s |
| `_LQ379ZhspI` | cyan `#00FFFF` MrBeast / yellow `#FFFF00` Rug | yellow "LETS GO!" over a MrBeast shot, 23.5–24.0 s |
| `9L2Yrs6jwb4` | white host / magenta `#F740F8` A / yellow `#FAFD08` B | magenta "SEPTEMBER" at n=396 while A is out of frame |
| `8-eCvn1gWIg` | white lead / pink `#FD609C` date / yellow `#FDDF27` panel | white "YOURSELF" at 9.52 s and pink "ALL THE" at 18.38 s both over the wrong shot |

This needs diarisation. Without it, fall back to the single-speaker rule — white base plus
one accent reserved for a *semantic category*. `uRU9SzlVClg` is the model: white for speech,
pure red for bracketed action annotations (`*LOOKS AT CHAT*`), used only from 2.20–6.05 s,
and no frame ever mixes the two. Do **not** colour by shot: that is the one thing all four
multi-speaker clips actively disprove.

**Outline: hard black stroke at ~10% of cap height** plus a soft dark shadow — four of seven
clips (`8cO8UWyjGyc` ~8%, `9L2Yrs6jwb4` ~10%, `8-eCvn1gWIg` ~12%, `_LQ379ZhspI` ~17%). At a
7.0% cap height on a 1920-tall frame that is a 13 px stroke. But note the awkwardness: both
Tier-1 clips sit *outside* the plurality — `uRU9SzlVClg` uses an opaque auto-sized black
card with no stroke at all (max channel < 28 across the card), `-dHfHZgtXJw` uses a thin
white inner outline with a coloured neon glow. So ship the stroke as default and the opaque
card as a first-class named preset.

**Entry animation: the pop, and it is genuinely measured twice.**

- start 0.91 → peak **1.05 at 83 ms** → settle **1.00 at 130 ms**, centre-anchored, per card.

`uRU9SzlVClg` measured it on 8 separate card starts, identical each time: 0.909
(range 0.907–0.912) → 1.050 (range 1.047–1.055) at frame +5 = +83 ms → exactly 1.000 by
frame +8 = +133 ms, no undershoot, bbox centre pinned at (270.0, 76.0) throughout — which is
what proves it is a uniform scale about the card centre rather than a per-word width reveal.
`8cO8UWyjGyc` independently: 0.88 at +2 f, 1.034 at +4, peak 1.055 at +5–6, 1.00 by +8 f,
with two clean discriminators against a full-frame ramp (the picture's own push is only
1–2%/s, two orders too small for a 17% swing; and during the "GIRLFRIEND" card the picture
pans ~3.6 px/frame while the caption centre stays put).

Second preset worth having, from `9L2Yrs6jwb4`: the same peak value at the same time, but it
keeps ringing — 0.849 start, crosses 1.0 at 50 ms, peak 1.048 at 83–100 ms, undershoot 0.956
at 183 ms, settled 233–250 ms.

**No karaoke highlighting.** `highlight_active_word` is false in all seven captioned
profiles. At 1–2 words per card there is nothing to highlight.

**Two things that generalise across source types and are cheap:** write action beats and SFX
in asterisks as their own cards in the accent colour (`uRU9SzlVClg`, `-dHfHZgtXJw`,
`8-eCvn1gWIg` — three clips, three different media), and transcribe verbatim including
hesitations with a trailing hyphen on interrupted words (`uRU9SzlVClg`: `SO-`,
`BEN DA DON-`, `I DONT WANT-`). Do not clean the transcript.

---

## Effects

**Default: none.** This is a measurement, not minimalism. `uRU9SzlVClg` lists it exhaustively
— no zoom lines, no flash frames, no emoji or stickers, no meme cutaways, no arrows, no
border, no grain, no vignette, no chromatic aberration, no progress bar, no speed ramp — and
`8cO8UWyjGyc` calls its speaker-coded pop-in captions "the only graphic treatment". The two
cleanest references are two of the three most relevant.

**No push-in inside a shot** (`push_amount = 0`), on the evidence of `uRU9SzlVClg`
(s=1.0000, |dx|,|dy| < 0.4 px across 17.1 s, 0.00 %/s), `-dHfHZgtXJw` (−0.23 to +1.13 %/s
within every shot over spans up to 4.25 s), `_LQ379ZhspI` (all 11 shots pixel-static) and
`8-eCvn1gWIg`'s source A ("measurably zero push-in"). Contested — see disagreements.

**No shake** (`shake_px = 0`) — the best-supported value in the whole spec. Not one of the
nine adds synthetic shake. The two profiles reporting `shake_used: true` (`Cfpc04Tpc4k`,
`q-SaGj-pDh0`) both attribute it to a physical handheld camera in the source, and
`Cfpc04Tpc4k` warns explicitly that a crop-only renderer should keep the source move and cut
on it rather than synthesise one. `8-eCvn1gWIg` disproves shake actively: a ±12 px
translation search returns near-zero error gain on every shot.

**No speed ramps** — `speed_ramp_used` is false on all nine. The renderer's inability to
speed-ramp costs nothing.

**Hard cuts, no transitions** by default (`uRU9SzlVClg`, `8cO8UWyjGyc`, and `_LQ379ZhspI`'s
"hard cuts only — no transitions of any kind"). `-dHfHZgtXJw` is the counterexample: a blur
or flash on every one of its 10 cuts.

**Optional, one or two per clip:** a red full-frame emphasis flash on the loudest onset —
*not* on a cut. `9L2Yrs6jwb4` measures it precisely: mean R jumps 152 → 222 in one frame
while G and B dip, then decays exponentially over 19 frames (0.317 s), framing identical
either side. `-dHfHZgtXJw` fires red flashes at 16.850 and 22.017 s, both proven effects not
cuts. Pair it with a sub-bass hit if wanted — `9L2Yrs6jwb4`'s 20–60 Hz band jumps from
−53/−31 dB to −13 dB co-timed with the flash.

**Do not model flashes as a dip to `#FFFFFF`.** `Cfpc04Tpc4k` measured both of its gold
light-leak flashes: ~0.30 s total with a ~0.07 s peak; flash A peaks at mean luma 209.5/255,
max 235, saturation rising 14 → 71 (a saturated gold that never clips); flash B peaks at
234.6/255, max 251, saturation collapsing to 1.9 — near-white but still ~4 counts short of
pure 255, entering and exiting through orange.

**No music bed** — eight of nine, each disproved by a measured floor drop rather than
assumed. Only `8-eCvn1gWIg` (the studio piece) has a real one; `-dHfHZgtXJw` fades one in for
its last third only. **No SFX on cuts** — `8cO8UWyjGyc` (onset strength at cuts only at the
84th–93rd percentile, and the clip's biggest transient lands 80 ms *after* the cut, so it is
a shout), `_LQ379ZhspI`, and `-dHfHZgtXJw` explicitly.

---

## Hook

Open cold, mid-sentence, with no title card and no establishing shot. **Not one of the nine
has a title card.** Caption must already be burnt in on frame 1–2: `uRU9SzlVClg` has "SO-" at
t=0.05 s, `-dHfHZgtXJw` has "CHAT" on frame 1, `8cO8UWyjGyc`'s frame 0 is already caught
mid-pop, `_LQ379ZhspI` is running by the second frame.

Open on a tight rung, not the wide one (`-dHfHZgtXJw` at ~1.40x with the loudest audio in
the clip; `_LQ379ZhspI` at ~1.8x). Land the first cut inside 0.9 s. Make one of the first
two shots the shortest in the clip. Fire 4–5 cards in the first 1.8 s.

The hook device itself is **verbal** — `uRU9SzlVClg` states it outright ("the hook is purely
verbal": drop the viewer into a story in progress, name a person, use a sub-second first
shot), and `8cO8UWyjGyc`'s is a question-plus-reaction beat. Two references use an opening
scale ramp instead (`_LQ379ZhspI` 1.8x → 1.0x over 0.55 s, measured via caption cap height
at 1.58 / 1.45 / 1.32 / 1.15 / 1.12 / 1.00x; `8-eCvn1gWIg` a −24% punch-out in 0.2 s) and it
is renderable as a stepped crop — but neither is a locked-off stream capture, so treat it as
garnish over the verbal hook, not a substitute for it.

---

## Where the nine genuinely disagree

This section is as valuable as the consensus. Fourteen items in the JSON; the substantive ones:

**1. Push-in inside a shot.** The sharpest conflict, and it splits Tier 1 two-to-one *against*
the clip the renderer was designed around. Zero push: `uRU9SzlVClg`, `-dHfHZgtXJw`,
`_LQ379ZhspI`, `8-eCvn1gWIg` source A. Constant slow push: `8cO8UWyjGyc`, +0.8 to +1.2 %/s on
*every* shot plus a tracking pan on 3 of 8. Constant slow *pull*: `9L2Yrs6jwb4`, −2.5 to
−3.2 %/s, which its profile calls "close to a house value". Violent push: `8-eCvn1gWIg`
source B, +46 %/s over 0.22 s. There is no consensus value. `push_amount = 0` survives because
it is what the locked-off captures do — which is what the target is — not because it wins a vote.

**2. Caption vertical position.** The three Tier-1 clips span 23.5 points of frame height:
`-dHfHZgtXJw` 50.0% (five clean cards at 49.72 / 50.14 / 49.93 / 50.03 / 50.35, spread 0.6%),
`8cO8UWyjGyc` 62.5%, `uRU9SzlVClg` 73.5%. The wider set fills the gaps rather than clustering:
50.2%, 51.1%, 52.9%, 77.9%. The only shared property is *below mid-frame* — and none is near
the 43% the old §3 recipe claimed.

*Note on the README:* its summary list reads "50.0 / 50.0 / 50.2 / 51.1 / 53 / 62.5 / 73.5"
and omits `_LQ379ZhspI`'s own measured 77.9% while carrying a second 50.0. The profile is the
authority; that line looks like a transcription slip.

**3. Caption entry animation.** Six behaviours over seven clips. The pop is the plurality at
3 of 7 and the only repeated one, but it is not a majority, and the alternatives are
genuinely different designs rather than noise around a mean: `8-eCvn1gWIg`'s 1-frame snap
(0.84 → 1.00 in ~33 ms, no overshoot); `q-SaGj-pDh0`'s per-word 0.34 → 1.00 over 200–233 ms
anchored bottom-centre; `-dHfHZgtXJw`'s horizontal motion-blur smear-in at *constant 1.00
scale* (the ±2% wobble is bloom shrinking as the blur resolves, not a ramp); `_LQ379ZhspI`'s
hard swap at full size.

Also worth noting: `-dHfHZgtXJw`'s profile explicitly says it does *not* follow the
1.05-at-+83 ms convention that three of its siblings share. It knew about the convention and
measured itself out of it.

**4. Caption legibility treatment.** Hard black stroke (4 clips, 8–17% of cap height),
opaque black card with no stroke (`uRU9SzlVClg`), neon glow (`-dHfHZgtXJw`), soft shadow only
(`q-SaGj-pDh0`). Both Tier-1 clips are outside the plurality.

**5. Colour semantics.** Speaker-coded in all four multi-speaker clips; emphasis-coded in the
three single-speaker ones. Not really a style conflict — a conflict about available input.
Curiosity worth recording: `-dHfHZgtXJw` is the only clip where colour is *proven* to carry no
information, because its profile establishes that the second party (Ronaldo) never picks up
and never speaks. That is the joke of the clip.

**6. Cap height** — 4.0% to 7.55%, nearly 2x, with the Tier-1 clips at the top and
`uRU9SzlVClg` at 5.8%. The card-vs-stroke explanation is an inference, not a measurement.

**7. Ladder top end** — 1.38x, ~1.40x, or ~1.5x, and `uRU9SzlVClg`'s punch-in could not be
pinned at all: its two crops show different parts of the room so they cannot be registered,
and texture autocorrelation only brackets it at 2–3x. `-dHfHZgtXJw`'s shot-7 rung carries
±10% because the physically rotating prize wheel contaminates ORB and masking it removes most
of the usable features. Treat f=0.71 as a floor with ±0.05 of slack.

---

## What the profiles could not settle

Stated plainly, with what would settle each, rather than filled with an invented number.

- **Facecam-vs-gameplay switching cadence.** *None of the nine contains a gameplay view.*
  `8cO8UWyjGyc` is the closest structural match — two windows in one locked-off source,
  alternating on dialogue — but both of its windows contain a person. Nothing here says how
  long to hold a gameplay crop, when to leave it, or what share of runtime should be gameplay.
  The renderer needs that number. **To settle:** profile 3–5 gameplay-stream Shorts the same
  way, measuring (a) runtime share per window, (b) whether a switch to gameplay is triggered
  by the speaker referring to the action or by motion in the gameplay region, and (c) whether
  the gameplay window ever takes the tightest rung.
- **Loudness target.** Integrated LUFS runs −7.4 to −23.4; LRA 1.7 to 23.9. The Tier-1 three
  alone give −23.4, −19.0 and −13.0. Four of nine clip at or above 0 dBFS true peak, three sit
  well below. There is no house value. If a normalisation target is needed, take it from
  platform guidance and label it as such — not from these profiles.
- **Typeface.** Undetermined in *every* profile. `8cO8UWyjGyc`, `_LQ379ZhspI` and
  `8-eCvn1gWIg` list it explicitly under "could not determine"; the rest describe a class.
  Any font we pick is a substitution and should be labelled one. **To settle:** render
  candidates at the measured cap heights and cross-correlate glyph outlines against
  full-resolution frame crops.
- **The sub-0.5 s shot.** p10 across Tier 1 is 0.527 / 0.650 / 0.933 s, so a 0.6 s floor is
  defensible — but `8cO8UWyjGyc`'s shortest real shot is 0.317 s and it is deliberate.
  Whether sub-0.5 s shots are a hook-only device or usable throughout cannot be settled from
  three clips.
- **Whether `-dHfHZgtXJw`'s same-framing cuts remove source time.** Undetermined by its own
  profile — the camera is locked, so framing cannot tell. Its prize-wheel rotation clock bounds
  the 7.717 s cut at under 0.15 s of removed time; no clock exists at 0.350 or 23.350 s. This
  is the difference between a cut and a pure transition effect, and it moves that clip's rate.
- **`uRU9SzlVClg`'s exact cut count.** Its profile flags 11.717 s as the weakest of nine (small
  head jump, gift-box clock stepping only 0.24 px, supported by a one-frame shoulder appearance
  and an I-frame placed one frame later). If it or 6.700 s is spurious, the count drops to 8
  and the rate to 28.0 cuts/min.

---

## Mapping onto the renderer's primitives

Where a reference does something the renderer cannot do, the substitute is named.

**Direct 1:1**

- *Framing ladder* → `sendcmd` setting all four `crop` params at once per cut, then a fixed
  `scale=1080:1920,setsar=1`. The output scale never changes; the ladder lives entirely in the
  crop rectangle. Round both crop dimensions to even.
- *Hard camera switch* → one `sendcmd` at the cut time. Exactly what `uRU9SzlVClg` reduces to
  ("two static crop rectangles, a cut list, and the caption layer") and what `-dHfHZgtXJw`
  describes ("a hard cut to a new crop, never an animated push").
- *Caption pop* → ASS `\t` chain (already implemented).
- *Per-word scale-in* → per-word spans plus `\t`, which is already how `caption_overlays`
  builds its span list. Caveat: `q-SaGj-pDh0`'s anchor is bottom-centre, and ASS scales about
  the `\an` alignment point, so that needs `\an2`-style alignment rather than the `\an5` the
  current prefix uses.
- *Speaker-coded colour* → `\c` per card. Colour is per card, never per shot.
- *Push-in / tracking pan* → crop w/h stepped every few frames, or a per-frame expression on
  crop x/y. Available and correct if `8cO8UWyjGyc`'s behaviour is wanted; off by default. Note
  that stepping `w` without stepping `x` re-anchors at the top-left — centre-anchored push
  needs x and y stepped by half the delta in the same command.
- *Red emphasis flash* → `eq` with `eval=frame` and expressions on `gamma_r`/`gamma_g`/
  `gamma_b`, one frame at full strength then exponential decay over ~0.32 s, matching
  `9L2Yrs6jwb4`'s R 152→222 with G/B dipping over 19 frames. Not a brightness spike to white.

**Achievable approximations**

- *Motion-blur smear caption entry* (`-dHfHZgtXJw`) → libass supports `\blur`, so
  `\blur8` + `\t(0,100,\blur0)` gives a card resolving from unreadable to sharp over 100 ms at
  constant scale. That is the measured behaviour minus the horizontal directionality — libass
  blur is isotropic.
- *Neon glow caption* → thin white `\3c` outline plus a coloured `\4c` shadow with `\blur`, no
  offset. Softer bloom than the reference, right structure.
- *Opaque black card* (`uRU9SzlVClg`) → ASS `BorderStyle=3` with `\bord` for padding. Corners
  will be square where the reference's are rounded; the box scales with `\fscx/\fscy` so the
  pop applies to card and text together, which is exactly what the reference measures.
- *Gold light-leak flash* (`Cfpc04Tpc4k`) → the luma/saturation envelope is an `eq` ramp over
  ~0.30 s with a ~0.07 s peak, capped below clipping. The streaky vertical banding with the
  image faintly visible through it is a screen-blended overlay needing a second input — not
  achievable. Substitute: the envelope alone, which gets colour and timing and loses texture.
- *Drawn shape annotations* (`q-SaGj-pDh0`'s red circle, `8-eCvn1gWIg`'s title lockup) → ASS
  vector drawing (`\p1`) in the subtitle pass. Raster stickers and photos are not reachable.

**Not achievable — and what to do instead**

- *Whip / motion-blur transitions* (`-dHfHZgtXJw`, 183–217 ms on 6 of its cuts). Directional
  blur needs `tblend`/`tmix` or a `boxblur` that cannot be time-gated cleanly alongside
  `sendcmd`. **Substitute:** hard-cut (what `uRU9SzlVClg`, `8cO8UWyjGyc` and `_LQ379ZhspI` all
  do), or a 2–3 frame `eq` brightness dip across the cut, which reads as a beat without
  pretending to be a blur.
- *Cutaway to external footage.* Not possible, and not needed — the Tier-1 three are 100%
  same-scene reframe. **Substitute:** a reframe to the other camera window.
- *Composited raster overlays* — `9L2Yrs6jwb4`'s SUBSCRIBE pill and cursor (its profile flags
  this as the one thing needing a second input), `_LQ379ZhspI`'s circular profile photo,
  `q-SaGj-pDh0`'s FIFA trophy PNG and emoji sticker, `-dHfHZgtXJw`'s broken-heart sticker and
  LXST watermark, `8-eCvn1gWIg`'s branded lockup and watermark. **Partial substitute:** ASS
  vector drawing for simple shapes only.
- *Background removal* (`8-eCvn1gWIg`'s cutout onto a flat grey plate with white outer glow and
  contact shadow). Not achievable and not worth substituting — that clip is a studio piece with
  a second shoot.
- *Speed ramp / freeze-frame* (`8-eCvn1gWIg`'s 0.18 s hold). Not achievable without audio
  desync. No reference speed-ramps, so only the single freeze is lost.

**Operational**

- **Output 60 fps.** The Tier-1 three are genuine 60 (`uRU9SzlVClg` mpdecimate keeps
  1010/1028, `-dHfHZgtXJw` 1457/1457, `8cO8UWyjGyc` zero exact duplicates). Two references are
  30-in-60 containers (`Cfpc04Tpc4k` 445/836 unique, `8-eCvn1gWIg` 48% near-zero diffs in an
  alternating cadence) — their cut times land on a 30 fps grid and should not be copied
  frame-for-frame.
- **Extract with `select='eq(n,K)'`, never `ffmpeg -ss <t> -i`.** Input seeking is not
  frame-accurate on AV1 and silently returns before/after pairs from the same shot;
  `8cO8UWyjGyc` and `q-SaGj-pDh0` both hit this.
- **Scene detection will not see our own cuts.** `uRU9SzlVClg`'s nine cuts produced 2 hits at
  threshold 0.10 and *zero* at 0.20/0.35; `-dHfHZgtXJw`'s ten produced 6 and zero. A locked-off
  source leaves the background pixel-identical across a jump cut. Verify renders with
  caption-band-masked per-frame MAD normalised by a local median, and **locate the band by
  scanning, never by assuming** — measured bands sat at 40–66%, 45–56.7% and 70.4–77.1% of
  frame height on different clips.
- **Caption swaps look like cuts to any detector**, in our output as much as in the references
  (`_LQ379ZhspI` picked up six false hits at threshold 0.10, `8cO8UWyjGyc` two,
  `q-SaGj-pDh0` eleven). Mask the band before measuring anything.

---

## Where the current implementation is already right

**`caption_overlays._pop_tags` — 0.91 → 1.05 at 83 ms → 1.00 at 130 ms: CORRECT**, and better
supported than its own comment claims. `uRU9SzlVClg` measures 0.909 → 1.050 at +83 ms → 1.000
by +133 ms over 8 separate card starts with no undershoot; `8cO8UWyjGyc` independently gets
peak 1.055 at +5–6 frames and 1.00 by +8 f (0.13 s). All four constants match.

> **One correction.** The comment in `caption_overlays.py` credits `_LQ379ZhspI`. That is
> wrong — `_LQ379ZhspI`'s profile states its cards hard-swap at full size with no visible pop
> or scale entry at all, and that the only caption scaling anywhere in the clip is the opening
> 0–0.55 s full-frame composite ramp. The measurement belongs to `uRU9SzlVClg`, corroborated
> by `8cO8UWyjGyc`. The numbers stand; the citation should be repointed.

**`dynamic_edit.DEFAULT_STYLE`:**

- `push_amount = 0` — **correct**, on `uRU9SzlVClg`, `-dHfHZgtXJw`, `_LQ379ZhspI` and
  `8-eCvn1gWIg` source A. Contradicted by `8cO8UWyjGyc` (+0.8–1.2 %/s) and `9L2Yrs6jwb4`
  (−2.5 to −3.2 %/s). Right for the target because the target is a locked-off capture — which
  is what all four supporting clips are.
- `shake_px = 0` — **correct**, and the best-supported value in the spec. Seven of nine report
  `shake_used: false`; the two that report true attribute it to a physical handheld camera in
  the source; `8-eCvn1gWIg` disproves it actively with a ±12 px translation search returning
  near-zero error gain on every shot.
- `snap_amount = 0` — **correct by default**, with one named exception worth exposing. No
  Tier-1 clip snaps. The exception is an *opening* snap, not a per-shot one: `_LQ379ZhspI`
  ramps 1.8x → 1.0x over the first 0.55 s and `8-eCvn1gWIg` snaps out −24% in 0.2 s. If snap is
  exposed at all, gate it to t < 0.6 s.

**And one value that needs adjusting.** `target_shot_s = 1.25` and `max_shot_s = 2.40` are
faster than any Tier-1 clip: their average shot lengths are 1.454 / 1.713 / 2.208 s and their
p90s are 2.95 / 2.66 / 3.633 s. The code comment cites "31–47 cuts/min" from the superseded
mechanical table; the measured Tier-1 band is 24.7–36.1. Suggest `target_shot_s = 1.8` and
`max_shot_s = 3.0`. `min_shot_s = 0.60` is well placed against p10 values of
0.527 / 0.650 / 0.933.
