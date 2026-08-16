# Reasoning upgrade — requirement-by-requirement status

Traceability for `claude_ai_stream_clipper_reasoning_prompt.md`, section by
section. Written 2026-08-14.

Status has four values and they mean different things:

- **Done** — built, and *measured on the real source*, not only unit-tested.
- **Done, mocks only** — built and unit-tested, never fired on real data.
- **Partial** — some of it exists; the gap is named.
- **Not built** — nothing.

Design detail lives in `docs/story-engine.md`; the state of the world and the
known problems live in `docs/handoff-clipper-session-4.md`.

---

## Priority list from the brief

| | requirement | status |
|---|---|---|
| **P0** | Anchor detection | Done |
| | Payoff reasoning | Done |
| | Backward context reconstruction | Done |
| | Forward reaction / resolution | Done — reused what existed |
| | Context debt | Done |
| | Archetypes | Done |
| | Candidate variants | Done |
| **P1** | Comparative ranking | Done |
| | Story memory | **Done** — atoms + threads + episodes (2026-08-16) |
| | Story threads | Done |
| | Promises / callbacks | Done, mocks only |
| | Semantic dedupe | Done |
| | Hook latency | Done |
| | Second efficiency | **Done** — `dead_air.py` (2026-08-16) |
| **P2** | Multimodal Pass D | **Not built** |
| | Diarization | **Not built** |
| | Human pairwise feedback | **Not built** |
| | Boundary learning dataset | **Not built** |
| **P3** | Game-specific detectors | **Not built** |
| | Post-publish learning | **Not built** |
| | Advanced learned ranker | Exists, dormant — needs 40 labels |

**P0 is complete. P1 is 7 of 7 as of 2026-08-16** — story memory and second
efficiency were the two open ones. P2 and P3 remain untouched apart from the
frontend inspection panel (§34), which is built.

---

## Section by section

### §1 Event atoms — **Done**
`services/clipper/atoms.py`. One natural utterance, 2–8s, carrying its own
audio/visual/semantic signals. Built with no model: a 12-hour stream is ~8,600
atoms and §29 forbids a call per two seconds. 15 KB per 12 minutes.
Boundaries are sentences, not a clock grid.

Measured: 63 atoms over the 12-minute slice, none outside the length bound.

### §2 Stream understanding — **Done** (2026-08-16)
Atoms, threads, chunked prompting, and now episodes: `episodes.py` cuts the
stream into stretches on the clock and labels each with the words that tell it
apart from the others, by TF-IDF over the buckets. `anchor_prompt` puts the
stretches that CLOSED before the current chunk in front of the model as
background, with the instruction not to clip from them.

No model call — the threads and atoms already held everything a summary needs.

Two designs failed first and both are recorded in the module: cutting episodes
where no thread had run for two minutes gave TWO episodes for four hours, and
labelling by "the words the most arcs share" returned "don't, it's, right,
can't". Measured on the 4-hour source, the finished labels line up with what is
known to be in it — minute 3-23 reads "control, failure, push, workout" over
the gym segment, 203-223 reads "drown, grown, sense, bubbles" over the argument
the two best clips were cut from.

### §3 Story threads — **Done**
`services/clipper/threads.py`, by lexical chaining over atoms. Consumed by
diversity as a third axis. 9 real arcs on the 12-minute slice after the
single-atom rule (43 → 9).

### §4 Promises / callbacks — **Done, mocks only**
`services/clipper/promises.py` sweeps for statements that create an
expectation; open ones are handed to anchor detection in later chunks. A
linked anchor gets the `CALLBACK` archetype and pays `callback_debt`.

**No callback has fired on real data.** Six plausible setups were found on the
real source; the model linked none, and it may be right to decline. Needs a
source with a confirmed callback.

### §5 Event graph — **Deliberately minimal**
`threads.edges()` derives two relations: `SETUP_FOR` from a named callback and
`SAME_STORY` from two payoffs in one thread. Nothing else.

The relations worth having already exist as targeted mechanisms something
reads. A general edge store would restate them in a vocabulary nothing
queries, and an unread structure is a failure this codebase hit four times.
`CAUSES`, `ESCALATES`, `CONTRADICTS` would each need a model per pair or a
guess. A test asserts nothing else is invented. §5 itself says not to
over-engineer a graph database.

### §6 Anchor events — **Done**
`llm_select.detect_anchors`, prompt `anchor_v1`, chunked, validated by
`story.normalise_anchor`. An anchor with no payoff time is dropped, never
guessed.

### §7 Payoff-first reasoning — **Done**
The prompt asks for the payoff first and works backwards to what a viewer must
already know.

### §8 Backward context reconstruction — **Done**
`story.latest_complete_start`. Measured on the real source: balanced variants
open **21–87 seconds before their payoff** (mean 52) — setup a spike detector
cannot reach.

### §9 Context debt — **Done**
`story.context_debt`, and it is read: `context_completeness` gives it the
largest of its five terms. Validated on the same run — 0.00–0.07 on variants
that keep the setup, 0.53 on tight ones that cut it. Since §25 it is evidence,
not a word count.

### §10 Forward reaction / resolution — **Done, reused**
`candidate_boundaries._reaction_end`, `_trim_tail` and `_drop_dangling_tail`
already did this well. Story variants are handed to the same path. Nothing was
rewritten to make room.

### §11 Clip archetypes — **Done**
15 archetypes with `ARCHETYPE_SHAPE`, the shape each must satisfy. The rubric
for every archetype present in a batch goes into the judge prompt, so a FUNNY
clip is not marked down for lacking stakes.

Caught here: the shapes were defined one commit before anything read them.

### §12 Candidate edit variants — **Done**
`story.variants_from_anchor` — 2–4 per anchor (`balanced`, `tight`,
`hook_first`, `story_short`), each carrying why it was cut that way. They
compete; dedupe and the scorer decide.

### §13 Hook reasoning — **Done**
`hook_latency` is separate from audio energy and read by the `hook` sub-score,
which ramps down on it. A whisper can be a hook.

### §14 Explicit semantic properties — **Partial**
Present: `hook_strength`, `hook_latency`, `setup_ratio`, `payoff_strength`,
`reaction_score`, `novelty`, `context_debt`, `dead_air_ratio`,
`boundary_confidence`, plus the three LLM verdicts.

**Missing:** `stakes_clarity`, `escalation`, `surprise`, `emotional_change`,
`information_density`, `quoteability`, `visual_legibility`, `visual_payoff`,
`second_efficiency`. The two visual ones need Pass D.

### §15 Second efficiency / dead-second detector — **Done** (2026-08-16)
`dead_air.py`. A silence is cut only when it holds no words (Whisper's word
timings veto the RMS floor), is longer than a beat (`PAUSE_KEEP_S`, reused
rather than re-decided), and is not at an edge the boundary rules own. A beat
survives on each side so the two sides do not sound spliced.

Cut with select/aselect inside the SAME encode, and the captions are remapped
because libass positions absolutely. Off by default (`trim_silence`, which had
sat in the settings dict for months with nothing reading it).

Measured on 920 candidates: 298 get a cut, ~3.1s each. A listener called the
result "audible, but fine" — a jump cut rather than a splice.

### §16 Features as evidence — **Done**
The ~60-key vector is kept and used for detection, filtering and confidence.
Atom lines carry the same measurements at utterance grain, so a model reads
"he said this AND the room got loud AND the picture cut" as one fact.

### §17 Cheap filtering first — **Done**
The hierarchy is cheap local analysis → aggregation → candidates → heuristic
score → LLM shortlist. Existing gates (duration, dead air, dedupe) run before
any model.

### §18 Comparative ranking — **Done**
`judge_v2_comparative` asks for a ranked shortlist, not absolute scores, and
derives the score from position. Measured: score range **20.1 → 63.8**, stdev
**3.90 → 15.67**, distinct LLM values 8 → 10.

A candidate the judge leaves out is scored as left out — the first run
shortlisted 9 of 42 and the other 33 kept an unblended heuristic ~58, so a
clip it declined to rank beat one it ranked fourth.

### §19 Three judging perspectives — **Done**
`story_editor`, `cold_viewer` and `critic` in one call. The cold viewer is
weighted higher; that is who decides a short.

### §20 Semantic dedupe + diversity — **Done**
`dedupe.same_story` groups two cuts sharing a payoff within 6s — time overlap
and shared words miss this. Diversity spreads across timeline, thread and
archetype, all under one quality ceiling so it never rescues a weak clip.

### §21 Pass D — multimodal review — **Not built**
Nothing sees the *edited clip*. This is the largest remaining gap in the
brief: a good moment can still be a bad clip because the payoff fell outside
the crop or a subtitle covers the kill feed. Observed on a real export — a
caption landed over the Minecraft inventory.

### §22 Pass D decisions (APPROVE / REJECT / REVISE) — **Not built**

### §23 Speaker diarization — **Not built**
Measured why the free path fails: mouth-region frame differencing at source
resolution separates speech from silence (ratio 2.27) but **cannot attribute**
— correlation between the two facecams *during speech* is +0.432, and the
difference is unimodal. Both faces move whenever anyone talks. Diarization
also only half-solves it: it gives "speaker A", not "A is the left camera".

### §24 Content-specific detectors — **Not built** (P3 by the brief)

### §25 Timeline retrieval / RAG — **Done**
`atoms.search` — token overlap weighted by rarity, bounded to atoms before the
reference. No embeddings, no index, no new dependency.

Its consumer is `story.resolve_backrefs`, which turns context debt into
evidence. Measured: "blast furnace look to your left" → the atom at 171s
(0.98); "piss counter drink a lot of water" → 399s (1.00). The stream has
exactly one real back-reference and it correctly resolves to nothing — it
points outside the stream.

### §26 Human pairwise feedback — **Not built**
Feedback recording exists (`services/clipper/feedback.py`); pairwise A-vs-B
capture does not.

### §27 Boundary learning — **Not built**

### §28 Post-publish metrics — **Not built** (P3)

### §29 Cost control — **Done**
Hierarchical: local analysis is free, nomination is cheap, judging is frontier
and bounded to a shortlist. Every stage checkpoints to disk (`atoms`,
`promises`, `threads`, `graph` joined `signals`, `faces`, `regions`,
`segments`, `candidates`, `meta`), so a restart does not redo the stream.

Measured with tiktoken on 19 real transcripts (90–395 tokens/min): **3.7 cents
for a 12-hour gaming stream, 11.1 for a talk-heavy one.** Frontier output
dominates at $10/1M against $2.50 input, which is why the judge is asked for
scores and eight-word reasons, never prose.

### §30 Prompt versioning — **Done**
`anchor_v1`, `judge_v2_comparative`, `promises_v2`, `story_v1`, `atoms_v1`,
`threads_v1`, `episodes_v1`, `segment_type_v1`. Persisted in the `reasoning`
column.

`promises_v2` is the version doing its job: v1 asked for statements that create
an expectation and got goals — 8 of the 12 setups on the 4-hour run were "I'm
going to the gym", which no one would clip. v2 requires a `stake` and quotes
those false positives back at the model.

### §31 Structured output — **Done**
JSON with a tolerant parser (code fences, prefaces) and per-field validation.
Anything unparseable is dropped, never guessed.

### §32 Failure modes / reject_reason — **Done**
A closed list of eleven: `no_payoff`, `no_story`, `context_debt`,
`late_hook`, `dead_open`, `weak_ending`, `all_energy_no_meaning`,
`all_setup_no_payoff`, `fans_only`, `needs_outside_knowledge`,
`transcript_broken`. Closed so they can be counted; invented ones are
discarded. Persisted per clip.

### §33 Debuggability — **Done**
New `clips.reasoning` JSON column, exposed through the API, carrying the
anchor, payoff, required context, archetype, edit variant, thread, context
debt, hook latency, the three verdicts, reject reasons and prompt versions.

### §34 Frontend inspection UI — **Done** (2026-08-16)
`ReasoningPanel`, opened from a "why" button that appears only on clips that
have reasoning. Shows the anchor, the payoff, what a cold viewer must already
know, the archetype, the winning variant, the three verdicts and the prompt
versions. Timestamps are on the source clock, which is what makes them useful.

Looking at it caught a bug no type could: hook latency is in seconds and had to
be scaled to fill a 0..1 bar, and the first version printed the scaled value
with an "s" after it — a 5-second hook read "0.50s".

### §35 Testing — **Done**
421 passing. New suites: `test_clipper_story.py`, `test_clipper_atoms.py`,
`test_clipper_threads.py`, `test_clipper_llm_select.py`,
`test_transcriber_resilience.py`. No test requires a live LLM — the passes are
driven with mocked answers, including the exact exception shapes seen on real
data.

### §36 Golden cases — **Not built**

### §37 Backwards compatibility — **Done**
`clipper_reasoning_version` is `"legacy"` by default; `"story_v1"` is opt-in
and additionally gated behind `clipper_llm_select`. Every new signal reads as
zero when absent, so the legacy path scores exactly as before — there is a
test for that.

### §38 Pipeline logic — **Partial**
The *logical* separation and the checkpoints the brief asks for exist: atoms,
promises, threads and graph are separate artifacts written before the next
step reads them, and any of them can be reused on a re-run.

**Still one job**, which the brief explicitly allows. What changed on
2026-08-16 is the part that actually cost anything: anchors are checkpointed
with a fingerprint of the prompt version, reasoning mode, engines and source
length, so a re-run reuses them and a configuration change recomputes them. The
judge is not checkpointed — it mutates the candidate list in place and returns
a count, so caching it means changing its contract rather than wrapping it.

### §39 Rules not to be broken — **Respected**
Checkpoints per stage; expensive analysis reads the proxy; the original is
opened only at export; the final render is still one fused filtergraph and one
encode; new work integrates with the existing cancel/progress/error handling.

### §40 Implementation strategy — **Followed**
Inspect → plan → implement → test → verify on real data → document. Where the
brief's structure did not fit the codebase it was adapted and the reason
recorded — the event graph (§5) is the clearest case.

---

## What the brief did not ask for, and was found anyway

Running this on real sources surfaced defects unrelated to the reasoning
upgrade. They are in the session-4 handoff in full; briefly:

- Transcription lost a whole 4-hour file to one bad chunk.
- The judge saw only the first 25 minutes of a 4-hour source.
- Three audio features were pinned and measured nothing.
- The gaming layout branch was unreachable in production.
- A layout plan carried no resolution, so swapping the source produced silent
  garbage.
- **The dynamic multi-shot editor is not wired into export at all** — every
  clip the pipeline produces is a static split screen. This is the biggest
  gap in the product and it predates this brief.

---

## Honest summary

**P0 complete, P1 seven of seven, P2 and P3 untouched.**

Everything marked Done was verified by running it on a real source, except
callback linking, which has only ever fired against mocks.

The reasoning now does what the brief asked: it starts from the payoff, asks
what a viewer must already know, reconstructs the earliest start that carries
it, keeps the reaction, judges candidates against each other from three
perspectives, and can explain every pick.

What it still cannot do is **see**. Pass D is the missing half — a moment
chosen perfectly can still ship as a bad clip, and nothing in the system
currently looks at the rendered frame.
