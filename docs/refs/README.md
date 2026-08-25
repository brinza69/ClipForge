# Reference profiles

One JSON per reference Short, measured 2026-08-07/08. Each is the output of an agent
that ran ffmpeg over the file and **looked at contact sheets** — every visual claim in
them comes from frames someone actually viewed, and anything undetermined says so in
`notes` rather than carrying a guessed value.

These replace the cut-rate table in `../dynamic-edit-recipe.md` §1. See "What this
corrects" below before trusting that table.

Schema (14 keys, identical across all nine): `id`, `duration_s`, `fps`, `resolution`,
`cut_times_s`, `avg_shot_s`, `cuts_per_min`, `shot_len_p10_p50_p90`, `framing_style`,
`caption`, `effects`, `hook`, `audio`, `notes`.

`framing_style.same_scene_reframe_pct` is 0-100: the share of cuts that reframe the
SAME footage rather than jumping to different footage. It is the single most useful
number here, because only same-scene reframing is reproducible from one input.

Note `-dHfHZgtXJw.json` starts with a dash — quote it or pass a full path, or CLI tools
will read it as a flag.

## What this corrects

The original §1 table came from a mechanical `select='gt(scene,0.35)'` pass. Measured
against the nine profiles it is wrong on five of nine, twice catastrophically:

| id | measured | mechanical | |
|---|---|---|---|
| `8-eCvn1gWIg` | 45.3 | 46.9 | ok |
| `8cO8UWyjGyc` | 36.1 | 30.9 | x1.2 |
| `uRU9SzlVClg` | 31.5 | 3.5 | **x9** |
| `q-SaGj-pDh0` | 30.0 | 12.0 | x2.5 |
| `-dHfHZgtXJw` | 24.7 | **0** | from nothing |
| `_LQ379ZhspI` | 21.9 | 21.9 | ok |
| `9L2Yrs6jwb4` | 17.6 | 11.7 | x1.5 |
| `Cfpc04Tpc4k` | 17.2 | 17.2 | ok |
| `jOUyn64mSsk` | 0 | 0 | ok |

§1's argument survives — the references still share no cut rate — but its shape changes.
The real spread is **17-45 cuts/min for eight of nine**, with one genuine single take.
They are far more homogeneous than "0 to 47" suggested, and the three clips filed as
"lightly cut" or "near single-take" were nothing of the sort.

## Why the mechanical pass failed

Both failure modes bite exactly where this project operates, so they are worth keeping:

**Jump cuts on a locked-off stream are invisible to scene detection.** The background is
pixel-identical across the cut. On `uRU9SzlVClg`, masked-MAD returned 2 hits at 0.10 and
zero at 0.20/0.35 while nine real cuts existed; on `-dHfHZgtXJw`, six and zero against
ten. This is the kind of cut `dynamic_edit.py` itself makes — scene detection will not
see our renders either.

**Codec and captions fire where there is no cut.** AV1 quality refreshes spike MAD while
only sharpening a static texture (high-frequency energy jumps ~1.5x, against 0.98-1.13x
at real cuts). Caption cards swapping do the same. On `-dHfHZgtXJw` three of thirteen
detected events were flashes, not cuts.

What worked instead, roughly in order of usefulness:

1. Per-frame mean-absolute-difference with the **caption band masked** — band located by
   a full-height text scan, never assumed. Measured bands sat at 46.8-52.8%, 51-65% and
   68-76% of frame height on different clips.
2. Normalising each frame's difference by a **local median over +/-40 frames** when the
   motion baseline is high.
3. **The stream's own overlays as clocks.** A gift-box alert stepped 43.7 px at a real
   cut versus 0.15-0.45 px at false ones; a physical prize wheel turning -0.75 deg/frame
   bounded removed time at a splice to under 0.15 s.
4. ORB/RANSAC similarity on labelled before/after pairs, plus subject-pose continuity
   stepped frame by frame at 60 fps.
5. Audio: a drop to digital silence (-54.8 dBFS) at a splice is impossible in live stream
   audio, so it marks an edit.

`ffmpeg -ss <t> -i` is **not frame-accurate** on these AV1 files — it silently yields
before/after pairs from the same shot. Use `select='eq(n,K)'`.

## Caption entry animation

Seven of the nine have captions, and they do six different things:

| behaviour | clips |
|---|---|
| per-card pop, peak ~1.05 at +5 frames (83 ms) | **3** — `8cO8UWyjGyc`, `9L2Yrs6jwb4`, `uRU9SzlVClg` |
| 1-frame snap, 0.84 -> 1.00, no overshoot | `8-eCvn1gWIg` |
| per-*word* scale-in, 0.34 -> 1.00 over 200-233 ms | `q-SaGj-pDh0` |
| horizontal motion-blur smear, no scale | `-dHfHZgtXJw` |
| none, cards hard-swap | `_LQ379ZhspI` |

The pop is the only repeated behaviour and the plurality. `uRU9SzlVClg` measured
0.909 -> 1.050 at +83 ms -> 1.000 by 133 ms, which is what `caption_overlays._pop_tags`
implements. `9L2Yrs6jwb4` adds an undershoot to 0.956 at 183 ms before settling ~240 ms —
a second preset worth having, not a correction.

**Do not average these.** Averaging six behaviours produces one that no reference has.

## Other cross-clip findings

**Caption colour tracks WHO IS SPEAKING**, in every clip where the question applies: four
references with 2+ speakers all do it, each confirmed by a card coloured for a speaker who
is off-screen at that moment. The three single-speaker clips use colour for emphasis
instead. Reproducing this needs diarisation.

**Caption vertical position** measured 50.0 / 50.2 / 51.1 / 53 / 62.5 / 73.5 / 77.9% of
frame height — four of seven cluster at 50-53%, the rest spread to the lower third.
None is near the 43% §3 claims. Read the vertical figure out of `caption.position`
carefully: several profiles state the horizontal centre first, so a naive "first
percentage in the string" gives 50.0% for `_LQ379ZhspI`, whose captions actually sit
at 77.9%.

**In-shot movement splits by source type, not by house style.** The two clips that most
resemble a stream VOD — `uRU9SzlVClg` and `-dHfHZgtXJw`, both locked-off captures cut
between discrete crop windows — hold the crop at exactly s=1.0000 with no ramp, and use
hard punch-ins on a ladder of roughly 1.00 / 1.10 / 1.25 / 1.40x. Clips built differently
ramp continuously (+0.8-1.2%/s, -2.5 to -3.2%/s, +46%/s on one panel). `push_amount = 0`
is right for our case, but for this reason rather than the one §8 gives.

**Loudness does not cluster.** Integrated LUFS spans -7.4 to -23.4 with LRA from 1.7 to
13.9 — §4's "-14 to -15" describes two clips, not the set.
