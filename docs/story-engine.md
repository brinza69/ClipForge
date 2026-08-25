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
| Episodes (§2) | `episodes.py` | what the stream was about before this chunk, no model call |
| Second efficiency (§15) | `dead_air.py` | wordless silences cut out of the middle, captions remapped |
| Inspection panel (§34) | `reasoning-panel.tsx` | the "why" button on a clip |

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
- **~~Validated on one source.~~** No longer true as of 2026-08-16: eleven
  sources are on disk and labelled by eye — gaming, Just Chatting, IRL, an
  interview, a Romanian vlog — and the detectors are scored against them in
  `source-labels.md`. What has NOT been re-measured on them is the story engine
  itself: every number in this file still comes from the Minecraft slices,
  because scoring the others needs the LLM passes turned on and a run per
  source.
- **~~No episode/thread memory.~~** Built 2026-08-16. Threads (§3), then
  episodes (§2): `anchor_prompt` now opens with what the stream has been about
  before the current chunk. See "Episodes" below.

## Not built (from the upgrade spec)

Rewritten 2026-08-16. `story-engine-spec-status.md` is the authority, section
by section; this is the summary.

**P1 — all seven done.** Stream memory and episodes, story threads,
promises/callbacks, semantic dedupe, comparative ranking, hook latency, and
second-efficiency trimming (`dead_air.py`, §15) were the last two to land.

**Event atoms (§1) are built** and are what threads, retrieval and episodes sit
on. The frontend inspection panel (§34) is built as well — the "why" button on
a clip.

**Still not built:** golden cases (§36).

**P2** — multimodal Pass D over rendered previews (§21-22), diarization (§23),
pairwise human feedback (§26), the boundary-learning dataset (§27). Pass D is
the largest remaining gap in the whole brief and it grew: the multi-shot editor
now makes visual decisions that nothing checks.

**P3** — game-specific detectors, post-publish metrics, the advanced learned
ranker.

## Episodes (§2)

`episodes.py` cuts the stream into stretches on the clock and labels each with
the words that tell it apart from the others, by TF-IDF over the buckets.
`anchor_prompt` puts the stretches that CLOSED before the current chunk in
front of the model as background, with the instruction not to clip from them; a
stretch the chunk is still inside is not offered, because calling a running
episode "background" would tell the model the moment it is reading is old news.

No model call. The threads and the atoms already held everything a summary
needs, and §29 forbids paying per moment.

Two designs failed first, both recorded in the module because each produced
working code that summarised nothing:

- Cutting an episode where no thread had run for two minutes gave **two**
  episodes for four hours, one spanning minute 8 to minute 240. Threads on a
  dense source overlap constantly, so there is no quiet moment to cut at.
- Labelling by "the words the most arcs share" returned *"don't, it's, right,
  can't, both, eight"*. The words every arc has identify nothing.

Measured on the 4-hour source: 123 threads and 1536 atoms become 12 stretches,
and the labels match what is known to be in it. Minute 3-23 reads "control,
failure, push, workout" over the gym segment; 63-83 reads "heal, golem, witch,
bell" and the iron-golem clip sits at minute 73; 203-223 reads "drown, grown,
sense, bubbles" and the two best clips on the board were cut from that argument
at minute 217.

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

## Verified on four hours

Everything above was measured on a 12-minute slice. A 4-hour run (927
candidates, 1536 atoms, 123 threads, 12 promises) broke three things a short
source cannot show, all fixed:

| fault | why 12 minutes hid it |
|---|---|
| the judge read `cands[:80]` in **timeline order** | 46 candidates fit in 80 |
| the recall window looked only at a chunk's **end** | 12 minutes is one short chunk |
| thread keywords were **alphabetical** | 9 threads, easy to eyeball |

**Callbacks fired for the first time.** Until this run they had only ever been
validated by mocks. The model linked *"I think I can go in a cave"* (207m) to a
payoff at 210m, and *"I'm hitting the cave, bro"* (205m) to one at 221m — six
callbacks across 927 candidates, from zero.

Fixing the judge's shortlist inverted the ordering: story-built candidates went
from 33.7–62.7 against a heuristic 10.1–84.8, to **35.5–81.0 against 18.2–66.5**,
with a story window at the top of the board.

### Timings, 4 hours of 480p source

| stage | time |
|---|---|
| ingest (proxy + audio) | 227s |
| transcribe (large-v3, GPU) | 1918s |
| analyse | 265s |
| score, both LLM passes | 164s |
| **total** | **~44 min** |

About **1 minute of processing per 4 minutes of stream**. Analyse came in at
4.4 minutes against my 20–25 estimate — the face pass is capped at 2000 samples
whatever the length, which is what keeps it flat.

The story engine is now as far as it goes without P2: multimodal Pass D over
rendered previews (§21–22), diarization (§23), pairwise human feedback (§26)
and the boundary-learning dataset (§27).

Two things worth knowing before the next round:

- **The promise detector finds goals, not stakes.** 8 of the 12 setups on the
  4-hour run were `goal` — "I'm going to go to the gym", "I'm hitting the cave".
  Those have no payoff anyone would clip. The spec's own examples are sharper
  ("if I lose this I'll shave my head"), and tightening the detector toward
  predictions and bets would raise the quality of the callbacks it finds.
- **Nobody has watched a clip yet.** Every property that can be measured has
  been; whether a clip *works* has not.
