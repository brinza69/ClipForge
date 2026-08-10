# AI Stream Clipper — Competitive Analysis

Research date: **2026-07-30**. Sources: the public marketing/doc pages of
[opus.pro](https://www.opus.pro/), [clipping.net](https://clipping.net/),
[clipping.net/blog/stream-clipper-guide](https://clipping.net/blog/stream-clipper-guide),
[clipping.net/docs](https://clipping.net/docs), plus OpusClip's public help centre for the
Virality Score.

Everything below is tagged so the reader always knows what kind of claim it is:

| Tag | Meaning |
| --- | --- |
| **[OBS]** | Publicly observable product behaviour — stated on the vendor's own public pages. |
| **[ASM]** | Our assumption / inference. Not stated by the vendor. May be wrong. |
| **[CF]** | A ClipForge implementation decision. Ours, not copied. |

No proprietary source code, branding, copy, visual assets, internal APIs or protected design
elements were inspected or reproduced. This analysis is built from public product descriptions
and general product concepts only.

---

## 0. Correction to the brief's premise

The task brief treats clipping.net as a *stream-clipping workflow tool* and asks us to mine it
for "clip review and submission workflows, performance tracking, status dashboards, creator /
team / campaign / project organisation".

**What the site actually is today [OBS]:** clipping.net presents as **BetaClipping**, a
*paid-clipping campaign marketplace*. Brands post campaigns with a per-100K-views rate and a
minimum-view threshold; independent "clippers" connect their own TikTok / Instagram Reels /
YouTube Shorts / X accounts, post clips, paste the link back for verification, and get paid per
verified view via PayPal or USDC/USDT. Its documented entities are Campaigns, Clips,
Submissions, Accounts and Teams; its documented roles are Clippers and Brands. No public API is
documented.

**Consequence [CF]:** clipping.net is *not* a useful architectural reference for an AI clipping
engine — it does no AI editing. It is still useful for two things, and we use it only for those:

1. Its **blog guide** on stream clipping is genuine editorial guidance about *what makes a stream
   clip work*. That feeds our scoring heuristics and default durations.
2. Its **marketplace shape** (submission → verification → per-view performance → payout) is the
   real-world downstream of a clip. That validates building a *performance-metric intake* layer
   (§30 of the brief) rather than assuming ClipForge owns distribution.

Where the brief asked for "campaign/creator/team organisation", we deliberately do **not** build
it: ClipForge is a single-user local studio (see §4 below). Building multi-tenant campaign
management would be speculative abstraction, which `CLAUDE.md` rule 8 forbids.

---

## 1. Feature matrix

| Capability | OpusClip | clipping.net (BetaClipping) | ClipForge today (pre-change) | ClipForge after this work |
| --- | --- | --- | --- | --- |
| Long video → many short clips | **[OBS]** yes | **[OBS]** n/a (humans clip manually) | no | **yes** |
| Link ingestion | **[OBS]** YouTube, Drive, Vimeo, Zoom, Rumble, Twitch, Facebook, LinkedIn, X, Loom, Riverside, StreamYard | n/a | yes (yt-dlp: YouTube/Twitch/Vimeo/direct/m3u8) | yes (reuses yt-dlp) |
| Local file upload | **[OBS]** yes | n/a | partial (utilities) | **yes** |
| Multimodal highlight detection | **[OBS]** "ClipAnything" — visual frames + audio + sentiment | n/a | no | **yes** (heuristic multimodal, §11) |
| Natural-language moment targeting | **[OBS]** yes | n/a | no | not built (documented gap) |
| Automatic captions | **[OBS]** yes, "over 97% accuracy", editable | n/a | yes (word-level, burned) | **yes** (reuses existing ASS engine) |
| Auto vertical reframing | **[OBS]** "ReframeAnything", AI object tracking, manual tracking option | n/a | pad-to-fit only | **yes** (subject-aware crop + manual override) |
| Speaker / subject tracking | **[OBS]** yes | n/a | no | partial (face-region tracking, no diarised speaker switching) |
| Gaming-specific layout | **[OBS]** genre support claimed incl. gaming | **[OBS]** guide advises "facecam + action, crop out unnecessary UI" | no | **yes** (dedicated split-layout engine) |
| Virality / quality score | **[OBS]** 0–99 Virality Score | n/a | dead columns on `clips` | **yes** (0–100 + sub-scores + reason) |
| Score explanation | **[OBS]** built from hook / flow / value / trend | n/a | no | **yes** (per-sub-score + text reason) |
| Editable AI output | **[OBS]** yes | n/a | no | **yes** |
| Batch clip generation | **[OBS]** yes | n/a | yes (parallel pipeline, different purpose) | **yes** |
| Brand templates | **[OBS]** font / colour / logo / intro+outro | n/a | caption templates + creator tag | partial (caption presets + watermark; no intro/outro) |
| Workflow automation / API | **[OBS]** API for CMS integration | **[OBS]** none documented | local REST API | local REST API |
| Post-publication performance | **[OBS]** not the focus | **[OBS]** core — per-view verified tracking | Buffer posting exists | **yes** (manual/entry intake, feeds ranker) |
| Payouts / marketplace | n/a | **[OBS]** PayPal + USDC/USDT | n/a | **not built — out of scope** |

---

## 2. Useful interaction patterns worth adopting

1. **Paste-link-first entry [OBS].** Both the reference product and the brief centre on a single
   URL field. **[CF]** Our source screen leads with the URL field, with upload and
   existing-media as peers, and shows a metadata preview (thumbnail / title / channel /
   duration / resolution / fps / size) *before* the user commits to a long analysis run. This is
   cheap for us because `services/downloader.fetch_metadata()` already does metadata-without-
   download.
2. **Ranked card grid with a score badge [OBS].** Clips come back as a ranked set, not a
   timeline. **[CF]** We use the same shape: a candidate card carries preview, title, score,
   reason, duration, source timestamps, detected layout, and per-card Approve / Reject / Edit /
   Export.
3. **Score as a *ranking* device, not a promise [OBS + ASM].** OpusClip's own help material
   frames the score as a likelihood, and third-party reviews report it is an unreliable absolute
   predictor. **[CF]** We show the score as a *relative rank within this source*, always with the
   sub-scores that produced it, and we never call it validated. See §15 of the brief.
4. **Editable everything [OBS].** Captions and reframing are both editable. **[CF]** We make
   boundaries, transcript text, caption style/position, crop rectangles and headline editable,
   and re-render only the edited clip.
5. **Review → submit → measure loop [OBS, from the marketplace].** **[CF]** We store approve /
   reject / edit / export events and allow performance numbers to be attached later.

## 3. Useful processing concepts

1. **One engine, many genres [OBS].** The reference engine is marketed as working across
   podcasts, vlogs, gaming, sports, interviews and tutorials. **[ASM]** That almost certainly
   means genre-conditioned weighting rather than one universal scorer. **[CF]** We implement
   explicit **score profiles** per content type (§15 of the brief) over a shared feature set.
2. **Visual + audio + sentiment fusion [OBS].** **[CF]** We fuse a transcript track, an audio
   energy/peak track, a motion track and a scene-change track, all computed from a low-res proxy.
3. **Hook / flow / value framing [OBS].** OpusClip publicly describes its score as combining hook
   strength, flow, value and trend alignment. **[CF]** We use a comparable but independently
   designed decomposition — hook, standalone clarity, setup efficiency, payoff, emotion,
   novelty, audio energy, visual energy, reaction quality, caption suitability, platform fit,
   context completeness, retention potential, edit confidence, technical quality, safety
   confidence — because the brief (§15) enumerates those explicitly. We do **not** attempt trend
   alignment: we have no trend data source, and faking it would be dishonest.
4. **Stream clips are short and start at the action [OBS].** The clipping.net guide states stream
   clips work best at **15–45 seconds** and advises "start at the action — no buildup needed",
   keep the streamer's reaction, add context text overlays, add captions for muted viewing, and
   frame mobile-first around "facecam + action, crop out unnecessary UI". **[CF]** These become
   concrete defaults: 15–45 s target window for stream/gaming profiles, a payoff-and-reaction
   boundary rule, an auto-headline for context, and a facecam-over-gameplay vertical layout.
5. **Category knowledge matters [OBS].** The guide notes a domain expert spots highlights others
   miss. **[ASM]** A generic model will under-perform on game-specific events (a clutch, an ace,
   a bad beat). **[CF]** We do not pretend to detect game-specific events. We detect *proxies*
   for them — audio energy spikes, facecam reaction magnitude, scene/motion discontinuity — and
   we say so in the docs.

## 4. What we deliberately do **not** copy

| Not copied | Why |
| --- | --- |
| Any branding, wording, colour, layout or visual asset from either product | Legal + `CLAUDE.md`; our UI follows ClipForge's existing shadcn/Tailwind conventions. |
| Multi-tenant campaigns / clipper marketplace / payouts | ClipForge is a **single-user local** studio with **no auth layer at all**. Adding tenancy would be a large speculative abstraction (rule 8). |
| A cloud credit/minutes meter | We run locally on the user's own GPUs. Compute is the user's, not metered. |
| "Trend alignment" as a score input | We have no legitimate trend data source. A fabricated trend score would be a made-up number. |
| Claimed caption accuracy percentages | We use faster-whisper locally; accuracy depends on the user's model choice and audio. We will not print a number we have not measured. |
| Platform scraping for performance metrics | Brief §30 and the platforms' terms. Performance data is entered by the user or imported from an official API. |
| Natural-language "find me moments about X" | Genuinely useful, but needs an embedding+LLM retrieval path we are not building in this slice. Listed as a gap, not silently dropped. |

## 5. Where ClipForge can be genuinely better

1. **Local and private.** Everything — download, transcription, vision, render — runs on the
   user's own machine. No video ever leaves the rig. Cloud clippers cannot offer that.
2. **Dual-GPU aware.** The rig already has a documented dual-GPU setup with VRAM auto-tuning and
   a two-lane job queue. Analysis (light) and render (heavy) already have separate lanes.
3. **No minutes meter.** A 6-hour VOD costs disk and wall-clock, not credits — so we can afford a
   genuinely hierarchical analysis pass over the *whole* stream rather than a sampled one.
4. **The render path is already better than a naive clipper.** `remix_pipeline` fuses
   speed-match + scale + caption burn into **one** ffmpeg encode specifically to avoid a second
   generation of compression loss. The clipper reuses that discipline.
5. **The caption engine already exists and is good.** Word-level ASS with karaoke highlighting,
   safe-zone constants tuned for TikTok's actual UI overlays, a font manager, and a live preview
   renderer. Most clippers ship worse captions than ClipForge already has.
6. **Honest scoring.** We ship the sub-scores and the reason string, so the user can see *why* a
   clip ranked where it did and overrule it — instead of a single opaque number.
7. **The feedback loop is real.** Because it is single-user and local, every approve/reject/edit
   is high-signal training data about *this* creator's taste, not an anonymised crowd average.

## 6. Gaming-specific opportunities

**[OBS]** the guide's core gaming advice is: facecam + action, crop out unnecessary UI, zoom on
reactions, balance audio across sources, and consider including chat.

**[CF]** Concrete opportunities we implement or plan:

- **Embedded-webcam detection** — find the streamer's webcam rectangle inside the source frame
  and promote it to the top third of the 9:16 canvas, rather than letter-boxing the whole 16:9
  frame.
- **Independent gameplay crop** — the gameplay region is cropped separately from the facecam, so
  the action stays large instead of being scaled down with the whole frame.
- **HUD-protection** — treat detected HUD regions (corners, minimap, killfeed strips) as
  keep-out zones for captions rather than covering them.
- **Empty-webcam fallback** — if no usable webcam exists, do **not** create a dead grey band;
  fall back to a full-screen dynamic gameplay crop.
- **Low-resolution warning** — an embedded webcam is often ~320×240 in the source; blowing it up
  to 1080 wide looks bad. We warn instead of silently shipping a blurry clip.
- **Audio-energy as an event proxy** — kill/round/objective sounds and streamer shouting are the
  cheapest reliable "something happened" signal in gaming footage.

**Not attempted:** per-title game event detection (killfeed OCR, scoreboard parsing,
game-specific HUD templates). It is a large per-game investment and we would rather ship an
honest generic detector than a fragile one that only works for one shooter.

## 7. Privacy, cost, performance, reliability

| Concern | Position |
| --- | --- |
| **Privacy** | All processing is local. The only optional outbound calls are the user's own configured LLM (for headline/description text) and their own Drive/Buffer credentials. Transcripts are never logged in full. |
| **Cost** | Zero marginal cost per clip except electricity. The expensive-model pass (§8 Pass D) is opt-in and bounded to the top-N candidate windows. |
| **Performance** | Hierarchical: never run an expensive model over every frame of a multi-hour stream. Analysis runs on a low-res proxy; only the *selected* clips touch the full-resolution source. Artifacts are cached on disk and reused across re-runs. |
| **Reliability** | Every stage is a job in the existing SQLite-backed queue, so progress survives a page reload and a backend restart (there is already a stuck-job recovery pass). Failures carry a structured code + suggestion, reusing the downloader's existing error-classification table. |
| **Legal** | We never bypass DRM, auth, private-video protection, paywalls or rate limits — the existing downloader already classifies and refuses those cases. The user must affirm they own or have rights to the content before analysis starts. |

---

## 8. Open gaps (honest list)

These are named in the brief but are **not** in this implementation. They are recorded here so
nobody mistakes silence for completion:

- Natural-language moment search ("find the part about X").
- Speaker diarisation and diarisation-driven split-screen interview layouts.
- OCR timelines (killfeed / scoreboard / on-screen text) as a ranking signal.
- Live chat-replay ingestion (Twitch/YouTube chat JSON) as a ranking signal.
- Intro/outro template rendering and full brand-kit management.
- Learned (rather than heuristic) vision models for reaction magnitude.
