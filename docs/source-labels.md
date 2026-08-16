# Source labels — ground truth for the detectors

Fill in the three columns marked `?`. Everything blocked on "we have no
labelled sources" reads this file: the content classifier's weights, the
facecam aspect gate, the face-cluster seed, and per-segment region detection.

Contact sheets for every row are in the conversation that created this file —
eight frames spread evenly across each source, and for the eight ingested ones
they are the SAME frames the detector sees.

Nothing here needs pixel coordinates. Say which corner and I measure the rect.

## How to fill it

- **content_type** — one of: `gaming`, `podcast`, `interview`, `irl`,
  `commentary`, `talking_head`, `tutorial`, `sports`, `low_dialogue`,
  `unknown`.

  **A live stream is usually several of these, one after another**, and that
  matters more than getting any single one right. Write one line per stretch
  with rough minute ranges:

  ```
  0-60    irl
  60-200  gaming
  200-264 talking_head
  ```

  One line is fine when the source really is one thing throughout. Rough is
  fine — "about the first hour" is the useful part, not the exact boundary.

  Why the ranges rather than a single answer: `content_type` is a column on the
  PROJECT, so it picks ONE weight row in `scoring.PROFILES` for the whole
  source. On a stream that is an hour of gym and three hours of Minecraft, even
  a perfect classifier would be wrong for a quarter of the clips — the problem
  is granularity, not accuracy, and no amount of tuning fixes it. These ranges
  are the evidence for making it per-segment. The episode structure that would
  carry it already exists (`services/clipper/episodes.py`).
- **facecam** — `none` · `1: bottom-left` · `2: top-left + top-right` ·
  `fullscreen` (the camera IS the frame) · `cuts` (edited; the camera changes
  at every cut, there is no fixed inset).
- **layout_changes** — `yes` / `no`. Does the arrangement change part-way
  through — a different game, a different room, a segment shot another way?

Partial is useful. Three rows unblock the classifier, which is the piece that
decides the weights for everything else.

## The sources

One block each. Replace the `?` lines; leave a block alone if you have not got
to it. The last three are downloaded but not yet ingested — they are here
because labelling them costs the same look.

```
39c89ae2e16e   IShowSpeed — EARLY STREAM, 4h24m, live
  content   ?-?  ?
  facecam   ?
  changes   ?

2c8af11153a3   moistcr1tikal — Just Chatting, 3h43m, live
  content   ?-?  ?
  facecam   ?
  changes   ?

slice4h00test  IShowSpeed Minecraft — the 4h slice
  content   ?-?  ?
  facecam   ?
  changes   ?

2d3375ee3420   IShowSpeed Minecraft — 12 min
  content   ?-?  ?
  facecam   ?
  changes   ?

0c9685df852b   IShowSpeed — 12 min, believed to be the gym segment
  content   ?-?  ?
  facecam   ?
  changes   ?

f81b86d27877   go ghost — 22m, edited
  content   ?-?  ?
  facecam   ?
  changes   ?

ee0e599b3ecb   Turul apartamentului — 42m, edited, Romanian
  content   ?-?  ?
  facecam   ?
  changes   ?

6b3844793c6a   Jensen Huang — 63m, edited
  content   ?-?  ?
  facecam   ?
  changes   ?

7dcTiO0491E    Kai Cenat — Streamer University, 1h52m, live, 4K60
  content   ?-?  ?
  facecam   ?
  changes   ?

4FfnxFDr1rE    IShowSpeed — IRL World Cup, 3h03m, live
  content   ?-?  ?
  facecam   ?
  changes   ?

bRnJmeYk6X4    Jynxzi — Late Night Stream, 3h33m, live
  content   ?-?  ?
  facecam   ?
  changes   ?
```

A filled block looks like this — several `content` lines when the stream moves
through several things, one when it does not:

```
39c89ae2e16e   IShowSpeed — EARLY STREAM, 4h24m, live
  content   0-60     irl
  content   60-200   gaming
  content   200-264  talking_head
  facecam   1: bottom-left
  changes   yes
```

## What each column unblocks

| column | what it fixes |
|---|---|
| `content_type` | The classifier answers `talking_head` for almost everything, including Fortnite and a two-person interview — and the confidences are all under 0.57, which now reads as the honest answer to a question that has no single one. It selects the weight row in `scoring.PROFILES`, so a wrong answer silently scores every clip against the wrong profile. The RANGES are the more valuable half: they are the evidence for moving `content_type` off the project and onto the segment. |
| `facecam` | `_WEBCAM_ASPECT = (1.1, 1.9)` is landscape-only, drawn around one stream's insets, so a portrait facecam is undetectable no matter how confidently it is found — measured at 29 hits in 40 frames and thrown away. Also feeds the face-cluster seed (`_snap_edge`'s window), which is what makes the detected height flip between 68 and 78 on one source. |
| `layout_changes` | Region detection runs ONCE for a whole source. On the 4-hour slice it found one facecam on a stream that has two, because the first hour is shot completely differently. Knowing which sources change tells us whether per-segment detection is worth building. |

## Notes worth checking while you look

- `39c89ae2e16e` is where today's facecam defect was measured. The inset is
  bottom-left with chat down the left edge; confirming the corner is enough.
- `0c9685df852b` and the first hour of `slice4h00test` are BELIEVED to be a gym
  segment. Session 4's handoff rests on that. If the frames show something
  else, that is itself a finding.
