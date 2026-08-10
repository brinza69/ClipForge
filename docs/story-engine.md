# Story engine — `reasoning_version = "story_v1"`

The old chain was `interesting signals → window → features → score`. It finds
moments where something is loud. Measured on `data/clipper/2d3375ee3420`, that
put *"let's cook our food, let's cook our diamonds"* at **#2 of 46** and put
this at **#45**:

> "Look to your left — it's a blast furnace." / "What do you mean, look to my
> left?" / "You don't have that in the game." / "I'm lying, bro." / "Y'all just
> sat there and lied, bro."

story_v1 asks the question the other way round, payoff first, and serves one
principle:

```
LATEST COMPLETE START  +  EARLIEST SATISFYING END
```

The shortest cut that keeps the story. Not the shortest cut possible, and not
the widest context available.

## Turning it on

```jsonc
// project clipper_settings — both are required, llm_select gates everything
{ "llm_select": true, "reasoning_version": "story_v1" }
```

Defaults are `legacy` and `false`. With `llm_select` off, `reasoning_version`
does nothing.

## Implemented

| piece | where | note |
|---|---|---|
| Anchor detection | `llm_select.detect_anchors` | versioned prompt (`anchor_v1`), chunked |
| Payoff-first reasoning | `story.normalise_anchor` | an anchor with no payoff time is dropped, never guessed |
| Backward context | `story.latest_complete_start` | start = earliest required fact, not the first spike |
| Forward reaction | **reused** `candidate_boundaries._reaction_end` | already existed and is good |
| Context debt | `story.context_debt` | unresolved openers + backrefs, overridden by the model's own list |
| Hook latency | `story.hook_latency` | how long a cold viewer waits to learn why to stay |
| Archetypes | `story.ARCHETYPES` + `ARCHETYPE_SHAPE` | 15, each with the shape it must satisfy |
| Edit variants | `story.variants_from_anchor` | 2–4 per anchor, each carrying why it was cut that way |
| Debuggability | `clips.reasoning` JSON column | anchor, payoff, context, archetype, variant, judge verdict |

Both new properties reach the score: `context_completeness` gives
`context_debt` the largest of its five terms, and `hook` ramps down on
`hook_latency`. Absent reads as zero for both, so the legacy path is unchanged.

## Measured on the real source

Five anchors over the 12-minute slice, each with its archetype named. Balanced
variants open **21–87s before their payoff** (mean 52s) — setup a spike
detector cannot reach. The 230s anchor is the blast-furnace bit, labelled
`REACTION+FUNNY`, required context at 179s.

`context_debt` validates itself on the same run: **0.00–0.07** on variants that
keep the setup, **0.53** on the tight ones that cut it.

## Three faults this exposed, all fixed

1. **`merge_nominations` swallowed every story cut.** A story window on the
   same moment as a heuristic one is not a duplicate — it is a different
   *boundary*, and which wins belongs to dedupe *after* scoring. With the
   coverage check on, all 14 story windows died before being scored.
   `keep_overlaps=True` on the story path.
2. **The export filter ran after dedupe elected leaders.** A leader dropped for
   overlapping an export took its group with it and the runner-up was never
   promoted — 8 winners became 3. It runs before dedupe now. **Not specific to
   story_v1**: it shrank the board for anyone who exported and re-ran.
3. **Model context timestamps are coarse.** One landed 12.6s before the first
   word of the thing it described, so the window opened on twelve seconds of
   silence. `_snap_context_to_speech` moves it forward — same principle: a
   start with no speech after it carries no information.

## Known limitations

- **Story windows reach the board but do not dominate it.** Across runs, 0–1
  of 8 winners were story-built. They are scored fairly and sometimes lose to
  heuristic windows on the same moment, which is the design working — but it
  means the payoff-first path is not yet *driving* the result.
- **The models are not deterministic.** The same 46 candidates scored twice
  gave different orderings, and two runs of the full board gave different top
  threes. Any judgement about whether story_v1 is better needs several runs,
  not one.
- **Validated on one source.** A 12-minute Minecraft slice, English, one
  content type. Nothing here has met a podcast or an IRL stream.
- **No episode/thread memory.** Anchors are found per chunk with no memory
  across them, so a callback to something an hour earlier cannot be detected.
  That is the P1 work.

## Not built (from the upgrade spec)

**P1** — comparative ranking (A vs B rather than absolute scores), stream
memory and episodes, story threads, promises/callbacks, semantic dedupe,
second-efficiency trimming.

**P2** — multimodal Pass D over rendered previews, diarization, pairwise human
feedback, the boundary-learning dataset.

**P3** — game-specific detectors, post-publish metrics, the advanced learned
ranker.

## Next step

Comparative ranking (P1). The judge currently scores each candidate alone and
its answers compress hard — measured, eight distinct values across 46 clips,
and on a quiet source everything lands between 5 and 30. Asking *"if we could
publish only N moments from this stream, which N"* is both what the spec wants
and the fix for the compression.
