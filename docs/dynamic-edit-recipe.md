# The dynamic multi-cam edit recipe

What the nine reference Shorts actually do, measured — and how each measurement
maps onto `services/clipper/dynamic_edit.py` and `dynamic_render.py`.

Written 2026-08-06. Every number below came from ffmpeg/ffprobe on the
downloaded files or from looking at contact sheets, not from memory. Where a
measurement is missing, it says so.

**Coverage, stated up front.** Cut rates were measured mechanically for all
nine. Full frame-by-frame profiles exist for only **two** — `Cfpc04Tpc4k` and
`_LQ379ZhspI` — because the agent fan-out that was producing them ran out of
session quota twice. Three more (`8cO8UWyjGyc`, `8-eCvn1gWIg`, `jOUyn64mSsk`)
were inspected by hand via contact sheets, which is enough for framing and
caption style but not for pixel-level claims. The remaining four have cut rates
and nothing else. Section 10 lists what is still open.

---

## 1. The references are not one style

The first thing the measurements killed was the assumption that the nine clips
share a cut rate. They do not, and averaging them produces a style none of them
has.

| id | dur | size | cuts/min | avg shot | median shot | what it is |
|---|---|---|---|---|---|---|
| `8-eCvn1gWIg` | 37.1s | 1080x1920 | 46.9 | 1.24s | 1.08s | produced studio skit, reaction cutaways |
| `8cO8UWyjGyc` | 11.6s | 1080x1920 | 30.9 | 1.66s | 1.08s | **two-camera stream switch** |
| `_LQ379ZhspI` | 27.4s | 1080x1920 | 21.9 | 2.49s | 2.40s | mixed |
| `Cfpc04Tpc4k` | 14.0s | 1080x1920 | 17.2 | 2.80s | 1.42s | handheld vlog, flash transitions |
| `q-SaGj-pDh0` | 30.0s | 1080x1440 | 12.0 | 4.29s | 3.38s | lightly cut |
| `9L2Yrs6jwb4` | 10.3s | 1080x1920 | 11.7 | 3.42s | 2.25s | lightly cut |
| `uRU9SzlVClg` | 17.2s | 1080x1920 | 3.5 | 8.58s | 14.20s | near single-take |
| `jOUyn64mSsk` | 27.5s | 1080x1440 | 0 | — | — | **raw single take, no cuts, no captions** |
| `-dHfHZgtXJw` | 24.3s | 1080x1440 | 0 | — | — | raw single take |

Method: `select='gt(scene,0.35)'` with hits closer than 0.4s collapsed. The
collapse is not optional — a flash transition and handheld shake both fire the
detector several times per cut. On `Cfpc04Tpc4k` the raw counts were 141 / 27 /
8 at thresholds 0.10 / 0.20 / 0.35 against 4 real cuts.

**Two of the nine have no edit at all.** They are stream moments cropped
vertical. That is worth stating plainly: part of what makes this content work is
the moment, not the montage.

## 2. The model to copy: `8cO8UWyjGyc`

Kai Cenat Live, 11.6s, 2.17M views — the clip that was named first as the
target. It is the one whose technique transfers to a stream VOD, because its
"cameras" are both already inside a single source frame:

- a wide shot of the guest at the spinning wheel,
- a close shot of Kai reacting in his chair.

The edit alternates between them roughly every second. **The cut is the effect.**
There is no zoom animation, no transition, no sticker — the whole energy comes
from switching subject on the beat of the conversation, including cutting to the
listener while the other person is still talking.

That is the mechanism `dynamic_edit.py` reproduces. On a stream VOD the two
subjects are the facecam and the gameplay.

### `_LQ379ZhspI` confirms it, with numbers

The one clip that got a full frame-by-frame profile and also uses the technique:
MrBeast and FaZe Rug on a phone call, each stream cam hard-cropped full-bleed to
9:16. **8 of its 10 cuts (80%) jump between the two sources on a speaker turn.**
Only 2 are a reframe of the same camera — one punch-in, one pull-out.

Two further findings from it that shaped the code:

- **Inside a shot the crop is completely static.** Background landmarks (a chair
  logo, a wall outlet, a floor mat) sit on identical pixels at the head and tail
  of both 5-second shots. No push, no drift, no shake, no speed ramp. Combined
  with `Cfpc04Tpc4k` — whose motion is a physically handheld phone, not a
  digital move — that is why `push_amount`, `snap_amount` and `shake_px` all
  default to **0**.
- **The subject is not centred.** Both cams put the face in the upper 40% and
  leave the bottom half of the frame to the caption.

Shot durations there: 2.40 / 0.67 / 0.68 / 5.48 / 4.80 / 2.02 / 2.43 / 1.00 /
2.60 / 1.93 / 3.38s. A three-cut flurry as the call connects, then long
pitch-and-response shots, then a steady back-and-forth. **There is no metronomic
interval to copy** — the cuts follow dialogue turns. Our planner approximates
that by cutting on speech pauses, which is the closest signal available without
speaker diarisation.

## 3. Captions

From `8cO8UWyjGyc` and `8-eCvn1gWIg` (the two that have them):

- ALL CAPS, heavy bold, thick black outline.
- **1-2 words per card.** Never a sentence.
- Vertically at roughly **43% of frame height** — mid-frame, not at the bottom.
- White base with the emphasised word in **yellow** (`8cO8UWyjGyc`); the studio
  clip uses cyan/pink/yellow to code who is speaking.
- No fade. Cards swap instantly.

Mapped to: `build_caption_plan(position="center", max_words=2)` — the
`SAFE_CAPTION_CENTER` constant already puts the block at 43.75% of 1920 — and
the `words` key added to each chunk, which makes `build_overlays_ass` emit one
event per word with the active word recoloured and scaled to 112%.

`Cfpc04Tpc4k`, `jOUyn64mSsk` and `-dHfHZgtXJw` have **no captions at all**, so
captions are a per-clip choice, not a law.

### Where the two profiled references genuinely disagree — do not average

| | `8cO8UWyjGyc` (hand-inspected) | `_LQ379ZhspI` (profiled) |
|---|---|---|
| vertical position | ~43% (mid-frame) | **77.8%** (lower third) |
| words per card | 1-2 | 1.4 average |
| colour rule | white, emphasis word yellow | **cyan/yellow keyed to WHO IS SPEAKING** |

That colour rule is the sharpest thing the profile turned up: at 24.0s a yellow
`LETS GO!` sits over MrBeast's face because Rug is the one shouting. Colour
tracks the voice, not the picture. We cannot reproduce it without speaker
diarisation, and this VOD has one speaker anyway — but it is the right idea for
any two-person clip, and it is why `--caption-pos` is a flag rather than a
constant.

### Measured caption geometry (`_LQ379ZhspI`)

Cap height 119px = **6.2% of frame height** (~160px font at 1080x1920). Single
line, never wraps. Black stroke ~18-22px — thick enough that adjacent letters'
strokes merge into one solid slab with the letters knocked out. Entry animation
is a **scale pop with overshoot**: 0.91x → 1.05x peak at ~83ms → 1.00x by
~130ms, anchored at the text centre, no fade or slide.

**We have that pop**, behind `--caption-pop`. `caption_overlays._pop_tags`
writes an ASS `\t` chain onto the event that lands the card — never onto the
later word-highlight spans, whose `\t` clock restarts and would re-fire it on
every word. Because the highlight already puts an inline `\fscx` on each token,
the chain has to multiply each run's resting scale (100% for the line, 112% for
the active word) rather than replace it.

Measured back off the render, white glyph bbox per frame at 100fps over black:

| | recipe | rendered |
|---|---|---|
| entry | 0.91x | **0.910x** |
| peak | 1.05x @ ~83ms | **1.045x @ 80ms** |
| settled | 1.00x by ~130ms | **1.000x by 130ms** |

The 80ms/1.045x peak is the sampling grid, not a discrepancy: at 100fps no frame
lands on 83ms. With the highlight on, the width steps in a single frame at each
word boundary (804→791→802px) instead of ramping — that step is the highlight
moving to a shorter or longer word, and it is what confirms the pop is not
replaying mid-card.

## 4. Audio

This is the most consistently reproduced element across the references.

| | `8cO8UWyjGyc` | `Cfpc04Tpc4k` | `_LQ379ZhspI` |
|---|---|---|---|
| loudness range | 3.1 LU | 2.5 LU | **1.7 LU** |
| true peak | -0.0 dBFS | +0.3 dBFS | -0.2 dBFS |
| integrated | — | -15.1 LUFS | **-14.5 LUFS** |

A loudness range of 2.5-3.1 LU is a compressor doing most of the work, and a
true peak at or over full scale means nobody left headroom. Normalising alone
would leave a clip sounding limp next to these, which is why
`build_dynamic_cmd` runs `acompressor` before `loudnorm` and asks loudnorm for
`LRA=4` at `I=-14`.

Neither reference has a music bed or sfx hits on the transitions —
`Cfpc04Tpc4k` drops to -77 dBFS between shouts, which no bed would allow. So:
**no added music.** The stream audio is the audio.

## 5. Effects

`Cfpc04Tpc4k` uses two full-frame blow-outs as transitions, each ~0.28s, peaking
at Y≈233/255 without clipping to white. Nothing else: no zoom lines, no
stickers, no meme cutaways, no vignette, no grain, no chromatic aberration, no
progress bar.

Mapped to: `_eq_filter` sums a gaussian per hit into `eq`'s brightness,
saturation and contrast with `eval=frame`. `flash_s` (default 0.18s) is the
half-amplitude width; the gaussian constant is `4*ln2 / flash_s²`.

## 6. Frame rate — a trap

`Cfpc04Tpc4k`'s container says 59.94 fps, but `mpdecimate` keeps only 445 of 836
frames: 31.8 effective fps, every frame duplicated. `_LQ379ZhspI` is the
opposite — 1572 of 1642 frames unique, genuine 60fps. **So frame rate is a
per-clip property, not a house style.** The renderer defaults to `--fps 30`
because a locked-off crop of a 60fps stream gains little from 60, but `--fps 60`
is the right call for fast gameplay.

There is a second trap here worth writing down: **burned-in captions inflate
scene-detection scores.** On `_LQ379ZhspI`, threshold 0.10 reported six extra
"cuts" that were caption cards swapping, not picture changes. If the clipper is
ever pointed at an already-captioned render, use 0.20-0.35 and de-duplicate
hits within 0.4s.

## 7. What the renderer cannot copy

Stated so nobody goes looking for it in the code:

- **Cutaways to other footage.** `8-eCvn1gWIg` cuts to a reaction panel filmed
  separately. One source file, one encode — there is nothing to cut to. The
  substitute is the second camera inside the same frame.
- **Background removal.** `8-eCvn1gWIg` isolates its subjects on white.
- **Speed ramps.** They would desynchronise the audio.
- **Sticker/emoji overlays.** Would need extra inputs in the filtergraph.

## 8. Parameters that came out of all this

`dynamic_edit.DEFAULT_STYLE`, with the reference each value came from:

| key | value | why |
|---|---|---|
| `target_shot_s` | 1.25 | median shot of the two fast references is 1.08s |
| `min_shot_s` / `max_shot_s` | 0.60 / 2.40 | p10-p90 of the fast references |
| `max_same_family` | 2 | the model clip never sits on one subject longer |
| `flash_s` | 0.18 | `Cfpc04Tpc4k` measured 0.28s; shortened because ours fire on audio peaks, which are more frequent than its two transitions |
| `saturation` / `contrast` | 1.16 / 1.07 | eyeballed against the references, NOT measured |
| `push_amount` / `snap_amount` / `shake_px` | **0** | both profiled references hold the crop perfectly still inside a shot |

Those three were 0.07 / 0.055 / 6.0 in the first version, on the assumption that
a locked-off VOD crop would read as frozen. The frame-by-frame profile of
`_LQ379ZhspI` said otherwise, so they are now 0 by default and available
per clip:

```bash
python scripts/render_dynamic_clip.py <project> --top 3 \
  --style '{"push_amount":0.07,"shake_px":6}'
```

## 10. Still open

- **Seven of nine references have no full profile.** Cut rates only. Re-run
  `ref-style-extraction` (the workflow script is saved under the session's
  `workflows/scripts/`) with `resumeFromRunId` — the two finished agents replay
  from cache for free.
- ~~The caption entry pop~~ — done, `--caption-pop`, verified against the
  measurement on a synthetic render (§3). **Never checked on the VOD itself**,
  because that machine had no `data/clipper/`.
- **Speaker-keyed caption colour** needs diarisation; irrelevant for a
  one-speaker VOD, correct for anything with two people.
- **Caption vertical position** is genuinely contested between the references
  (43% vs 78%). Currently 43% via `--caption-pos center`. Untested against
  retention.
- `saturation` / `contrast` (1.16 / 1.07) were **eyeballed, never measured.**

## 9. How it renders

One `-filter_complex`, one libx264 pass:

```
sendcmd -> crop -> scale=1080:1920:lanczos -> setsar -> eq -> subtitles
```

`crop`'s `w/h/x/y` are all runtime-settable in ffmpeg 8.1 (the `T` flag in
`ffmpeg -h filter=crop`), so the sendcmd script hard-switches the rectangle on
an exact frame; changing `w/h` reconfigures the link and `scale` follows. Crop's
`x/y` expressions are re-evaluated per frame and can see `t`, which is where the
shake lives at zero command cost. Because `x/y` are written against `out_w` and
`out_h`, a size command alone re-centres the crop — that is what makes a size
ramp a push toward the subject rather than toward the middle of the frame.

Two hard rules on every expression: no commas (a comma separates filters in a
filtergraph and arguments in a sendcmd entry, so `clip(v,lo,hi)` would silently
truncate the graph — range safety is baked into the constants instead), and
every dimension even (H.264 with yuv420p refuses odd crops).
