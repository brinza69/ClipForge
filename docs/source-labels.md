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
  `unknown`. What the source is for MOST of its duration. If it is genuinely
  split, write both with a rough share: `gaming 80% + irl 20%`.
- **facecam** — `none` · `1: bottom-left` · `2: top-left + top-right` ·
  `fullscreen` (the camera IS the frame) · `cuts` (edited; the camera changes
  at every cut, there is no fixed inset).
- **layout_changes** — `yes` / `no`. Does the arrangement change part-way
  through — a different game, a different room, a segment shot another way?

Partial is useful. Three rows unblock the classifier, which is the piece that
decides the weights for everything else.

## The sources

| id | what it is | content_type | facecam | layout_changes |
|---|---|---|---|---|
| `39c89ae2e16e` | IShowSpeed — EARLY STREAM, 4h24m, live | ? | ? | ? |
| `2c8af11153a3` | moistcr1tikal — Just Chatting, 3h43m, live | ? | ? | ? |
| `slice4h00test` | IShowSpeed Minecraft — the 4h slice | ? | ? | ? |
| `2d3375ee3420` | IShowSpeed Minecraft — 12 min | ? | ? | ? |
| `0c9685df852b` | IShowSpeed — 12 min, believed to be the gym segment | ? | ? | ? |
| `f81b86d27877` | go ghost — 22m, edited | ? | ? | ? |
| `ee0e599b3ecb` | Turul apartamentului — 42m, edited, Romanian | ? | ? | ? |
| `6b3844793c6a` | Jensen Huang — 63m, edited | ? | ? | ? |
| `7dcTiO0491E` | Kai Cenat — Streamer University, 1h52m, live, 4K60 | ? | ? | ? |
| `4FfnxFDr1rE` | IShowSpeed — IRL World Cup, 3h03m, live | ? | ? | ? |
| `bRnJmeYk6X4` | Jynxzi — Late Night Stream, 3h33m, live | ? | ? | ? |

The last three are downloaded but not yet ingested, so they have no project id
and no analysis — they are here because labelling them costs the same look.

## What each column unblocks

| column | what it fixes |
|---|---|
| `content_type` | The classifier answers `talking_head` for almost everything, including Fortnite and a two-person interview. It selects the weight row in `scoring.PROFILES`, so a wrong answer silently scores every clip against the wrong profile. Two of its inputs (`face_count`, `speech_ratio`) cannot discriminate and need thresholds these labels would justify. |
| `facecam` | `_WEBCAM_ASPECT = (1.1, 1.9)` is landscape-only, drawn around one stream's insets, so a portrait facecam is undetectable no matter how confidently it is found — measured at 29 hits in 40 frames and thrown away. Also feeds the face-cluster seed (`_snap_edge`'s window), which is what makes the detected height flip between 68 and 78 on one source. |
| `layout_changes` | Region detection runs ONCE for a whole source. On the 4-hour slice it found one facecam on a stream that has two, because the first hour is shot completely differently. Knowing which sources change tells us whether per-segment detection is worth building. |

## Notes worth checking while you look

- `39c89ae2e16e` is where today's facecam defect was measured. The inset is
  bottom-left with chat down the left edge; confirming the corner is enough.
- `0c9685df852b` and the first hour of `slice4h00test` are BELIEVED to be a gym
  segment. Session 4's handoff rests on that. If the frames show something
  else, that is itself a finding.
