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

## Comparative ranking (`judge_v2_comparative`)

Absolute scoring compressed: eight distinct values across 46 candidates, and
on a quiet source everything between 5 and 40. The judge now asks for a ranked
shortlist and derives the score from position, plus three verdicts each —
`story_editor`, `cold_viewer` (weighted higher; that is who decides a short)
and `critic`, which names reject reasons from a closed list of eleven.

`ARCHETYPE_SHAPE` feeds this prompt, so each candidate is judged against the
shape its own kind of clip needs.

| | before | after |
|---|---|---|
| score range | 20.1 | **63.8** |
| stdev | 3.90 | **15.67** |
| llm values | 8 distinct, 5–40 | 10 distinct, 0–90.7 |
| story-built winners | 1 of 8 | **5 of 8**, five different archetypes |

**A candidate the judge leaves out is scored as left out.** The first run
shortlisted 9 of 42 and the other 33 kept an unblended heuristic ~58, so a
clip the judge declined to rank beat one it ranked fourth.

## Known limitations

- **Validated on one run of one source.** The numbers above are a single
  measurement; the models are not deterministic.
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

**P1** — stream memory and episodes, story threads, promises/callbacks,
semantic dedupe, second-efficiency trimming. *(Comparative ranking is done.)*

**Unprioritised in the spec but not built** — event atoms as a first-class
structure (§1). Anchors are found straight from the transcript, which works,
but it means the event graph (§5) and timeline retrieval (§25) have no
foundation to sit on. Also missing: a frontend inspection panel (§34) and
golden cases (§36).

**P2** — multimodal Pass D over rendered previews, diarization, pairwise human
feedback, the boundary-learning dataset.

**P3** — game-specific detectors, post-publish metrics, the advanced learned
ranker.

## Semantic dedupe and archetype diversity (§20)

`dedupe.same_story` groups two candidates that share a payoff within 6s. Time
overlap and shared words miss this: a tight cut and a story-rich cut of one
joke have different openings and can share almost no text while being the same
clip. It only ever *adds* groupings — a candidate with no story never matches,
so legacy pairings are unchanged.

`_diversify` now spreads by archetype as well as along the timeline, so a
stream with a clutch, a story and an argument does not return eight rage
clips. Timeline spread is tried first, and both share the existing
`MAX_DIVERSITY_SACRIFICE` ceiling: diversity stops redundancy, it does not
rescue a weak clip.

## Promises and callbacks (§4)

`promises.py` sweeps the transcript once for statements that create an
expectation — prediction, promise, bet, challenge, goal — and checkpoints them
to `analysis/promises.json`. Anchor detection in a later chunk receives the
ones still open, so a payoff can be linked to a setup from earlier in the
stream. A linked anchor gets the `CALLBACK` archetype.

Built without the event-atom substrate (§1, §5) on purpose: callbacks need
only the setups and a link, and building the substrate first would have spent
the whole budget before anything detected one. §40 asks for that trade.

**A callback's setup can never be in the window.** It is minutes or hours
away, so it is not required context to *include* — it is context the clip
*owes*. `callback_debt` measures that and falls when the payoff line restates
the setup, which is the case where a callback works standalone at all.

Bounds: a setup goes stale after 45 minutes, and a payoff within 90 seconds of
its setup is one moment, handled better by the ordinary context path.

### Verified, and not verified

The sweep works — six plausible setups from the 12-minute slice. Linking,
validation, tagging and the debt are unit-tested against mocked answers.

**No callback has fired on real data.** A 12-minute transcript is one chunk and
in-chunk setups are excluded from the recall list, so the path cannot fire on
this source. Forcing the chunking small put the setups in front of the model
and it still linked none — and it may be right: *"we're gonna give you
diamond"* is not really paid off by mining one. Verifying this needs a source
with a confirmed callback; tuning the prompt further against a source with no
positive case would be fitting to nothing.

## Event atoms (§1)

`atoms.py` turns the stream into units the reasoning can point at: one natural
utterance, 2–8s, with the Pass A signals for its own span attached. Built with
no model — a 12-hour stream is ~8,600 atoms and the cost rule forbids a call
per two seconds. 15 KB of artifact per 12 minutes, so under a megabyte for a
full stream.

**They are read, not just stored.** `detect_anchors` sends atom lines instead
of transcript lines, so a model sees *"he said this AND the room got loud AND
the picture cut"* as one fact. Features as evidence (§16) at the grain of one
utterance.

Marking is **relative to the source**. An absolute `peaks >= 1` tagged 94% of
lines because this stream runs about one peak every three seconds — the marks
became the noise they exist to cut through. Bars are the source's own p80, and
`hook` is not printed (its vocabulary is "wait", "look", "why" — 22% of atoms).
94% → 57%.

That is the third time an absolute threshold measured nothing on a busy source,
after `audio_peak_ratio` and `game_ui_ratio`. **Compare a moment against its own
stream.**

## Timeline retrieval (§25)

`atoms.search` — token overlap weighted by rarity across the stream, bounded
to atoms *before* the reference. No embeddings, no index, no new dependency.

Its consumer is `story.resolve_backrefs`, which turns `context_debt` from a
word count into evidence: *"remember what he said"* costs nothing when he said
it eight seconds ago and the viewer just heard it, and costs full price when
the referent is an hour back. `unresolved_refs` then names what is missing
instead of guessing. With no atoms it returns `[]` and the word list answers as
before.

Measured: "blast furnace look to your left" → the atom at 171s (0.98), "piss
counter drink a lot of water" → 399s (1.00). On the whole stream there is
exactly one real back-reference — *"one from yesterday and today"* — and it
resolves to nothing, correctly: it points outside the stream.

That measurement also caught `_BACKREFS` carrying ordinary connectives
("before", "again"), which produced three false back-references out of four.

## Narrative threads (§3)

`threads.py` finds arcs by lexical chaining over atoms — an atom joins a
thread when it shares enough rare vocabulary with what that thread has been
about *recently*. No model. Diversity now tries three axes in order of how
clearly each means "something else": a different **stretch**, then a different
**story**, then a different **kind**.

Without it, a stream that spends an hour on one boss could return a board that
is entirely that boss: every clip in a different ten-minute bucket, every one a
different archetype, all of them the same fight.

**A single atom is not an arc.** Chaining first produced 43 threads over 12
minutes, 30 of them singletons — as a diversity axis that is one bucket per
moment. Below two atoms a moment now belongs to no thread. 43 → 9, and the
nine are real: Minecraft-admins (18–74s), blast furnace (170–188s), trial
chamber (240–379s), piss counter (390–405s), fortress (612–706s).

## The event graph (§5) — deliberately under-built

`threads.edges()` derives **two** relations: `SETUP_FOR` from a named callback,
`SAME_STORY` from two payoffs in one thread. Nothing else.

The relations worth having already exist as targeted mechanisms that something
already reads — `promises`/`callback_to` are SETUP_FOR and RESOLVES,
`dedupe.same_story` is SAME_STORY by payoff. A general edge store would restate
them in a vocabulary nothing queries, and **an unread structure is the failure
this codebase has hit three times** (ARCHETYPE_SHAPE, context_debt,
hook_latency were all defined before anything read them). CAUSES, ESCALATES and
CONTRADICTS would each need a model per pair or a guess. A test asserts nothing
else is invented.

## Next step

**A source with a real callback**, to find out whether the linking works at
all. Every other piece here was validated by running it; that one has only
been validated by mocks.

After that the story engine is as far as it goes without P2: multimodal Pass D
over rendered previews (§21–22), diarization (§23), pairwise human feedback
(§26) and the boundary-learning dataset (§27).
