# Source labels — ground truth for the detectors

COMPLETE as of 2026-08-16 — all eleven sources labelled by eye, from
twenty-four frames each, by the person who owns them. Everything that was
blocked on "no labelled sources" reads this file: the content classifier's
weights, the facecam aspect gate, the face-cluster seed, and per-segment
region detection.

## What the finished set says

Four things, and each of them changes what is worth building:

1. **Seven of eleven sources change layout part-way through.** Region detection
   runs ONCE per source. It is therefore wrong for the majority of them by
   construction, and no threshold fixes that.

2. **Every long live source is several content types in a row.** All four run
   two or three. `content_type` is a column on the PROJECT and picks one weight
   row in `scoring.PROFILES`, so a perfect classifier would still be wrong for
   a third of the clips on these. The granularity is the bug.

3. **The facecam is in a different place in every single source that has one**:
   bottom-left, two in the top corners, left edge at mid-height, fullscreen,
   none, cut-to-cut, and one that exists for four minutes out of 112.
   `_WEBCAM_ASPECT = (1.1, 1.9)` and `corner_proximity` were both fitted to ONE
   of these. Two of the eleven — moistcr1tikal's mid-height inset and
   IShowSpeed's portrait one — are penalised or rejected for being where and
   what they are.

4. **`interview` and `podcast` have never been selected on any source**, and
   the set now contains a confirmed interview (Jensen Huang) and a confirmed
   two-person stretch (Jynxzi 55-80). `face_count` is the MODAL faces per frame
   and an edited interview cuts between speakers, so it reads 1 and `two_up`
   cannot fire. That is the mechanism, not a threshold.

And what already works, which is worth as much: `webcam: None` is the correct
answer on all three edited sources and it is what came back, and the 12-minute
Minecraft slice gets BOTH insets while the 4-hour source of the same stream
gets one — the cleanest evidence that the failure is global detection over a
changing stream rather than the detector.

Contact sheets are in the conversation that produced this file: twenty-four
frames per source, stamped with the minute, and for the eight ingested ones
they are the SAME frames the detector sees.

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
  content   0-35     talking_head
  content   35-253   gaming
  facecam   1: bottom-left  (fullscreen for the first ~35 min)
  chat      top-right
  changes   yes  — the camera goes from full-frame to an inset at ~35 min

2c8af11153a3   moistcr1tikal — Just Chatting, 3h43m, live
  content   0-223    commentary
  facecam   1: left edge, MID-HEIGHT — not in a corner
  changes   no  — browser plus inset, unchanged throughout
  note      Titled "Just Chatting" but he watches YouTube the whole stream:
            the screen is the material and he reacts over it. The detector's
            84x66 inset was RIGHT; the earlier suspicion that it had invented
            one came from assuming a Just Chatting camera fills the frame.
            `corner_proximity` is 25% of the facecam score and this inset sits
            in no corner, so it is scored down for being where it is.

slice4h00test  IShowSpeed Minecraft — the 4h slice
  content   0-35     irl
  content   35-240   gaming
  facecam   2: top-left + top-right
  changes   yes  — full-frame camera for 35 min, then the two-inset layout
  note      CONFIRMED by eye: this source really does have two facecams, and
            regions.json found ONE. Known problem #2 in the session-4 handoff
            was a suspicion until now; it is a fact.

2d3375ee3420   IShowSpeed Minecraft — 12 min
  content   0-12     gaming
  facecam   2: top-left + top-right
  changes   no
  note      The same layout as slice4h00test after minute 35. Detection finds
            BOTH insets on this short slice and only one on the 4-hour source —
            the cleanest possible evidence that the problem is global detection
            over a changing stream, not the detector itself.

0c9685df852b   IShowSpeed — 12 min, the gym segment
  content   0-12     irl
  facecam   fullscreen — the camera IS the frame, no game
  changes   no
  note      Confirmed: the session-4 handoff's note about this being the gym
            segment is right.

f81b86d27877   go ghost — 22m, edited
  content   0-22     talking_head
  facecam   cuts — edited, no fixed inset
  changes   yes (every cut)
  note      `webcam: None` is the RIGHT answer here, and that is what came
            back. One of the three cases the detector already gets right.

ee0e599b3ecb   Turul apartamentului — 42m, edited, Romanian
  content   0-42     irl
  facecam   cuts — edited, handheld, no fixed inset
  changes   yes (every cut)
  note      Classified `commentary` at 0.286 — the lowest confidence in the
            set, and wrong. Romanian: transcription handled it (8,903 words in
            42 minutes), the classifier did not.

6b3844793c6a   Jensen Huang — 63m, edited
  content   0-63     interview   (two people)
  facecam   cuts — edited, no fixed inset
  changes   yes (every cut)
  note      Classified `talking_head` at 0.507. `interview` has never once been
            selected on any source, and this is what one looks like. The cuts
            are why: an edited interview shows ONE face per frame, so a modal
            `face_count` reads 1 and `two_up` never fires.

7dcTiO0491E    Kai Cenat — Streamer University, 1h52m, live, 4K60
  content   0-112    irl / variety
  facecam   none for almost all of it — an inset appears around 94-98 min only
  changes   yes
  note      Faces per frame VARY a lot: him alone, him plus a guest, a room
            with several. No stable count, which is the case a MODAL
            `face_count` cannot describe at all.
            The brief inset is the sharpest argument for per-segment region
            detection in the whole set: four minutes of inset in 112, so a
            single global answer is wrong either way it goes.
            Also the wall of viewer webcams behind him is CONTENT, not a
            facecam — a face detector has dozens of small faces to trip on.

4FfnxFDr1rE    IShowSpeed — IRL World Cup, 3h03m, live
  content   0-183    irl
  facecam   fullscreen — a phone IRL stream; there is no inset, only the camera
  changes   no  — the place changes, the arrangement does not
  note      The first live source in the set that really is ONE thing end to
            end, and the clean negative case for `_find_webcams`: it should
            find nothing here, not invent an inset.

bRnJmeYk6X4    Jynxzi — Late Night Stream, 3h33m, live
  content   0-55     commentary
  content   55-80    talking_head   (two people on camera)
  content   80-205   gaming
  facecam   1: bottom-left  (fullscreen between 55 and 80 min)
  changes   yes
  note      Two people on camera for the middle stretch — the counterexample
            `face_count` needs. It is the MODAL faces-per-frame and reads 1 or
            0 on every source so far, which is why `podcast` and `interview`
            can never win their own vote.
            Second of four live sources whose CAMERA layout changes mid-stream,
            not just its content.
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
