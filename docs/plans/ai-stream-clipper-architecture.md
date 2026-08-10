# AI Stream Clipper — Architecture

Companion to `ai-stream-clipper-repository-audit.md`. This document is the **contract**: every
module below is implemented exactly to the signatures given here, so the pieces compose without
re-negotiation.

Route: **`/ai-stream-clipper`** · API prefix: **`/api/clipper`** · artifact root:
**`data/clipper/{project_id}/`**

---

## 1. User flow

```mermaid
flowchart TD
    A[Paste URL / upload file / pick library item] --> B{URL policy check}
    B -->|rejected| B1[Structured error + suggestion]
    B -->|ok| C[Metadata preview: thumb, title, channel, duration, WxH, fps, size]
    C --> D[Confirm rights + configure output/editing/layout]
    D --> E[Start analysis]
    E --> F[Progress screen: 18 named stages, survives reload]
    F -->|failed| F1[Failure reason + Retry / Cancel]
    F --> G[Review dashboard: ranked candidate cards]
    G --> H{Per card}
    H -->|Approve| I[Approved]
    H -->|Reject| J[Rejected + reason stored]
    H -->|Edit| K[Clip editor]
    K --> K1[Trim / transcript / captions / headline / crop / layout]
    K1 --> L[Preview frame or proxy render]
    L --> K
    I --> M[Export 1080x1920]
    K --> M
    M --> N[Exported file + sidecar metadata]
    N --> O[Optionally attach performance metrics later]
    O --> P[(Feedback store)]
    J --> P
    I --> P
    K1 --> P
```

## 2. Processing pipeline (hierarchical — brief §8)

```mermaid
flowchart LR
    subgraph Ingest
      S1[validate + policy] --> S2[metadata]
      S2 --> S3[download / copy]
      S3 --> S4[proxy 480p low-fps]
      S3 --> S5[mono 16k audio]
      S4 --> S6[thumbnails + sparse frames]
    end

    subgraph PassA[Pass A - cheap global scan]
      A1[audio RMS + peaks + silence]
      A2[scene-change timeline]
      A3[motion timeline]
      A4[face-presence sampling]
      A5[transcript w/ word timestamps]
    end

    subgraph PassB[Pass B - semantic segmentation]
      B1[sentence + pause + speaker-turn boundaries]
      B2[scene + action boundaries]
      B3[semantic windows 15-90s]
    end

    subgraph PassC[Pass C - candidates + scoring]
      C1[candidate generation]
      C2[boundary refinement + alternatives]
      C3[feature extraction]
      C4[heuristic sub-scores by profile]
    end

    subgraph PassD[Pass D - expensive review, top-N only]
      D1[LLM judgement on top-N transcripts]
      D2[headline generation]
    end

    subgraph PassE[Pass E - render prep, selected only]
      E1[content-type + region detection]
      E2[layout plan + crop keyframes]
      E3[caption plan]
      E4[render spec]
    end

    S5 --> A1
    S4 --> A2
    S4 --> A3
    S4 --> A4
    S5 --> A5
    A1 & A2 & A3 & A4 & A5 --> B1 --> B2 --> B3
    B3 --> C1 --> C2 --> C3 --> C4
    C4 --> DD[dedupe + diversity] --> D1 --> D2
    D2 --> E1 --> E2 --> E3 --> E4 --> R[(export)]
```

**The rule that makes this affordable:** Pass A and B read only the **proxy** and the
**transcript**. Pass D runs on at most `top_n_llm` candidates (default 8) and only if an LLM
engine is configured. Pass E runs only for clips the user actually renders. The full-resolution
source is opened exactly once per exported clip.

## 3. Job orchestration

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as routers/clipper.py
    participant Q as JobQueue (SQLite)
    participant W as workers/clipper_pipeline.py
    participant DB as SQLite

    UI->>API: POST /projects/{id}/analyze
    API->>DB: status = fetching_metadata
    API->>Q: enqueue(clipper_ingest)
    API-->>UI: {job_id}
    UI->>API: GET /api/jobs/{job_id}/stream (SSE)
    Q->>W: handler(job_id, project_id, metadata, queue)
    W->>Q: update_progress(0.05,"Validating source")
    Q-->>UI: SSE frame
    W->>W: ingest -> proxy -> audio
    W->>Q: enqueue(clipper_transcribe)
    W->>Q: enqueue(clipper_analyze)
    W->>Q: enqueue(clipper_score)
    W->>DB: write clips + artifacts
    W->>Q: complete_job
    Q-->>UI: SSE terminal frame, stream closes
    UI->>API: GET /projects/{id} -> ranked candidates

    Note over UI,Q: Cancel = POST /api/jobs/{id}/cancel (existing endpoint)
    Note over W,DB: Every stage writes artifacts to disk before advancing,<br/>so retry resumes from the last completed stage.
```

Stage names surfaced to the user (brief §26) are the exact strings written by
`update_progress`: *Validating source · Reading metadata · Downloading · Creating proxy ·
Extracting audio · Transcribing · Detecting scenes · Detecting faces and regions · Detecting
content type · Building semantic segments · Creating candidates · Scoring candidates · Removing
duplicates · Preparing layouts · Generating previews · Ready for review · Rendering export ·
Completed.*

## 4. Data model

```mermaid
erDiagram
    PROJECTS ||--o{ CLIPS : "has candidates"
    PROJECTS ||--o| TRANSCRIPTS : "has"
    PROJECTS ||--o{ JOBS : "has"
    CLIPS ||--o{ CLIP_FEEDBACK : "emits"
    CLIPS ||--o{ JOBS : "export jobs"

    PROJECTS {
        string id PK
        string title
        string source_url
        string source_type
        string source_kind
        string status
        float  duration
        int    width
        int    height
        float  fps
        string video_path
        json   clipper_settings
        string content_type
        float  content_type_confidence
        string content_type_override
        string analysis_version
        bool   rights_confirmed
    }
    CLIPS {
        string id PK
        string project_id FK
        string title
        float  start_time
        float  end_time
        float  duration
        float  overall_score
        json   sub_scores
        text   score_reason
        json   layout_plan
        json   caption_plan
        string headline_text
        string content_type
        json   warnings
        string dedupe_group
        bool   is_alternative
        int    rank_position
        json   feature_vector
        string ranker_version
        string status
        string export_path
        string preview_path
    }
    CLIP_FEEDBACK {
        string id PK
        string clip_id FK
        string project_id FK
        string event_type
        json   payload
        datetime created_at
    }
    TRANSCRIPTS {
        string id PK
        string project_id FK
        string language
        json   segments
        text   full_text
    }
```

On-disk artifacts (**not** in SQLite):

```
data/clipper/{project_id}/
  source/            downloaded original (or a symlink/copy of an upload)
  proxy/proxy.mp4    ~480px wide, 10 fps, no audio  -- every analysis pass reads this
  audio/speech.wav   mono 16 kHz PCM                -- transcription + energy
  frames/            sparse sampled JPEGs
  thumbs/            per-candidate thumbnails
  analysis/
    signals.json     {audio_rms[], peaks[], silence[], scenes[], motion[]}
    faces.json       sampled face boxes over time
    regions.json     detected webcam / gameplay / chat / HUD rectangles
    segments.json    Pass B semantic windows
    candidates.json  pre-dedupe candidates with features
    meta.json        analysis_version, model versions, timings
  previews/{clip_id}.mp4
  exports/{clip_id}.mp4 + {clip_id}.json sidecar
```

## 5. Feedback and model-training loop

```mermaid
flowchart TD
    G[Clip generated] --> P[Previewed]
    P --> A{User action}
    A -->|Approve| EV1[event: approved]
    A -->|Reject| EV2[event: rejected + reason]
    A -->|Edit boundaries| EV3[event: start_changed / end_changed<br/>+ edit distance in seconds]
    A -->|Edit crop or layout| EV4[event: crop_changed / layout_changed]
    A -->|Edit captions or headline| EV5[event: caption_changed / headline_changed]
    A -->|Export| EV6[event: exported]
    EV1 & EV2 & EV3 & EV4 & EV5 & EV6 --> FS[(clip_feedback)]
    POST[User pastes post URL + views/likes/watch-time] --> EV7[event: performance_recorded] --> FS
    FS --> FE[feature extraction: frozen feature_vector + label]
    FE --> TR[train baseline pairwise ranker]
    TR --> MV[model version + offline eval:<br/>precision@K, NDCG, approval rate, mean edit distance]
    MV -->|promote| RK[ranker.json used at scoring time]
    MV -->|reject| KEEP[keep heuristic weights]
    RK --> SC[future scoring runs blend heuristic + learned]
```

**Label definition:** positive = exported (strongest), then approved; negative = rejected.
Unreviewed clips are excluded, not treated as negatives. **Guardrail:** the learned model is
only used when it beats the heuristic baseline on held-out NDCG@5 *and* at least
`min_training_examples` (default 40) labelled clips exist. Otherwise the heuristic weights stand
and the UI says so.

## 6. Gaming layout pipeline

```mermaid
flowchart TD
    F[Sampled frames from candidate window] --> D1[Gaming detection:<br/>edge density, colour saturation,<br/>UI-corner stability, motion profile,<br/>gaming vocabulary in transcript]
    D1 -->|not gaming| GEN[General reframing path]
    D1 -->|gaming| W1[Webcam-region search:<br/>rectangular border detection +<br/>face detection inside candidate rects +<br/>temporal stability across samples]
    W1 --> W2{Usable webcam?}
    W2 -->|yes, area and resolution ok| L1[Layout: face top / gameplay bottom<br/>default 35% / 65%, configurable]
    W2 -->|too small or unstable| L2[Warn: low-res facecam]
    L2 --> L1
    W2 -->|none found| L3[Fallback: full-screen dynamic gameplay crop]
    L1 --> C1[Gameplay crop chosen to preserve<br/>centre-of-action + HUD keep-out rects]
    L3 --> C1
    C1 --> S1[Temporal smoothing:<br/>EMA on crop centre, max pan rate,<br/>max zoom rate, deadzone]
    S1 --> CAP[Caption placement:<br/>avoid faces, HUD, chat, source subtitles]
    CAP --> SPEC[Layout plan JSON -> single fused ffmpeg filtergraph]
    GEN --> G1[Face/subject track -> safe-area crop] --> S1
```

**The one fused encode.** A gaming export becomes a single ffmpeg invocation of the shape:

```
[0:v] trim/crop(face_rect)   -> scale 1080x672  -> [top]
[0:v] trim/crop(game_rect)   -> scale 1080x1248 -> [bot]
[top][bot] vstack -> subtitles=captions.ass -> yuv420p -> libx264
```

Never two passes. Same rationale as `remix_pipeline._stage_match_and_caption`.

---

## 7. Module contracts

All modules live under `server/services/clipper/`. Every function is pure-ish: it takes paths and
dicts and returns dicts; none of them touch the DB or the job queue. That keeps them unit-testable
without a database, which is how the existing `test_tiktok_transform.py` storage tests work.

### `urlguard.py`
```python
class UrlRejected(Exception):
    def __init__(self, code: str, message: str, suggestion: str = "") -> None: ...

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS   = {80, 443}

def check_url(url: str, *, allow_private: bool = False) -> dict:
    """Return {'url','host','scheme','port','source_type','addresses'} or raise UrlRejected.
    Rejects: bad scheme, credentials in URL, non-allowed port, unresolvable host, and any host
    whose resolved A/AAAA set includes loopback / private / link-local / CGNAT / multicast /
    reserved / unspecified addresses."""

def is_blocked_ip(ip: str) -> bool: ...
MAX_REDIRECTS = 3
```

### `storage.py`
```python
def project_dir(project_id: str) -> Path: ...
def paths(project_id: str) -> dict[str, Path]:
    """Keys: root, source_dir, proxy_dir, proxy, audio_dir, audio, frames_dir, thumbs_dir,
    analysis_dir, previews_dir, exports_dir, signals, faces, regions, segments, candidates, meta"""
def ensure_dirs(project_id: str) -> None: ...
def write_artifact(project_id: str, name: str, data: dict | list) -> Path:   # atomic
def read_artifact(project_id: str, name: str) -> dict | list | None: ...
def artifact_exists(project_id: str, name: str) -> bool: ...
def delete_project(project_id: str) -> None: ...
def dir_size_bytes(project_id: str) -> int: ...
ARTIFACT_NAMES: frozenset[str]   # allowlist for the HTTP artifacts endpoint
```

### `ingest.py`
```python
async def probe_source(url: str) -> dict:
    """URL policy + yt-dlp metadata. Returns metadata dict or {'error','error_code','suggestion'}."""

async def ingest_source(project_id: str, *, url: str | None, local_path: str | None,
                        max_duration_s: float, min_free_bytes: int,
                        on_progress=None, is_cancelled=None) -> dict:
    """Download or copy -> returns {'video_path','duration','width','height','fps','filesize'}."""

async def build_proxy(project_id: str, video_path: str, *,
                      width: int = 480, fps: int = 10) -> str: ...
async def extract_audio(project_id: str, video_path: str) -> str:      # mono 16 kHz wav
async def extract_thumbnail(project_id: str, video_path: str, t: float, name: str) -> str: ...
async def sample_frames(project_id: str, proxy_path: str, times: list[float]) -> list[str]: ...
def check_disk_space(min_free_bytes: int) -> None:  # raises RuntimeError
```

### `signals.py` — Pass A
```python
def audio_timeline(wav_path: str, *, hop_s: float = 0.25) -> dict:
    """{'hop_s', 'rms': [..], 'peaks': [t,..], 'silence': [[s,e],..], 'speech': [[s,e],..]}
    Computed with ffmpeg astats/silencedetect — no full decode into Python."""

def scene_timeline(proxy_path: str, *, threshold: float = 0.30) -> list[float]:
    """Scene-change timestamps via ffmpeg's scdet/select filter."""

def motion_timeline(proxy_path: str, *, hop_s: float = 0.5) -> dict:
    """{'hop_s','motion':[0..1,..]} via frame differencing on the proxy."""

def face_presence(proxy_path: str, times: list[float]) -> list[dict]:
    """[{'t':float,'boxes':[[x,y,w,h],..]}] using cv2 Haar cascades shipped with opencv-python."""

def build_signals(project_id: str, proxy_path: str, wav_path: str, duration: float) -> dict:
    """Runs the above, writes analysis/signals.json, returns it."""
```

### `segmentation.py` — Pass B
```python
def semantic_windows(transcript: dict, signals: dict, *,
                     min_s: float, max_s: float) -> list[dict]:
    """[{'start','end','text','words':[..],'reasons':[..]}]
    Boundaries from: sentence punctuation, pauses > 0.6 s, scene changes, silence edges,
    audio-peak onsets. Never fixed-size chunks."""
```

### `candidates.py` — Pass C
```python
def generate_candidates(windows: list[dict], signals: dict, *,
                        min_s: float, max_s: float, target_s: float) -> list[dict]: ...

def refine_boundaries(cand: dict, transcript: dict, signals: dict, *,
                      min_s: float, max_s: float) -> dict:
    """Snap start to a natural boundary, never mid-word; extend to include the payoff and the
    following reaction; trim trailing dead air but keep meaningful pauses. Returns the candidate
    with 'start','end' updated and 'alternatives': [{'start','end','why'}]."""

def extract_features(cand: dict, transcript: dict, signals: dict, duration: float) -> dict:
    """The frozen numeric feature vector used by both scoring and the ranker."""
```

### `scoring.py`
```python
SUB_SCORES = ("hook","clarity","setup_efficiency","payoff","emotion","novelty",
              "audio_energy","visual_energy","reaction","caption_suitability",
              "platform_fit","context_completeness","retention","edit_confidence",
              "technical","safety")

PROFILES: dict[str, dict[str, float]]   # content_type -> per-sub-score weights

def score_candidate(cand: dict, features: dict, *, profile: str,
                    platform: str) -> dict:
    """{'overall': 0..100, 'sub_scores': {name: 0..100}, 'reason': str}"""

def explain(sub_scores: dict, cand: dict) -> str:
    """One or two factual sentences citing the top contributing signals. No hype."""
```

### `dedupe.py`
```python
def text_similarity(a: str, b: str) -> float:      # token Jaccard x trigram cosine
def overlap_ratio(a: dict, b: dict) -> float:
def deduplicate(cands: list[dict], *, overlap_threshold: float,
                text_threshold: float, target_count: int) -> list[dict]:
    """Assigns 'dedupe_group', marks losers 'is_alternative', then applies a diversity pass
    (spread across the source timeline, mixed content/emotion) and sets 'rank_position'."""
```

### `content_type.py`
```python
CONTENT_TYPES = ("gaming","podcast","interview","irl","commentary","talking_head",
                 "tutorial","sports","low_dialogue","unknown")

def detect_content_type(frames: list[str], signals: dict, transcript: dict) -> dict:
    """{'content_type','confidence','evidence':[str,..]}"""

def detect_regions(frames: list[str]) -> dict:
    """{'webcam': rect|None, 'gameplay': rect|None, 'chat': rect|None, 'hud': [rect,..],
        'confidence': {..}}   rect = {'x','y','w','h'}  in source pixel coords."""
```

### `layout.py`
```python
LAYOUTS = ("face_top_game_bottom","game_top_face_bottom","pip","fullscreen_game",
           "fullscreen_crop","split_screen","talking_head")

def plan_layout(cand: dict, regions: dict, faces: list[dict], src_w: int, src_h: int, *,
                mode: str = "auto", face_pct: float = 0.35,
                include_chat: bool = False) -> dict:
    """{'layout','face_rect','game_rect','chat_rect','keyframes':[{'t','rect'}],
        'warnings':[str,..],'safe_zones':{...}}
    Falls back to fullscreen_game when no usable webcam exists — never emits an empty face band."""

def smooth_keyframes(kfs: list[dict], *, max_pan_px_per_s: float,
                     max_zoom_per_s: float, ema: float) -> list[dict]: ...

def build_filtergraph(plan: dict, out_w: int = 1080, out_h: int = 1920) -> str:
    """The single fused ffmpeg -filter_complex string. No second encode."""
```

### `captions.py`
```python
def build_caption_plan(cand: dict, transcript: dict, *, preset_id: str,
                       max_words: int, position: str, layout: dict) -> dict:
    """{'chunks':[{'text','start','end'}], 'style':{...}, 'x_pct','y_pct','scale'}
    Reuses captioner_events._group_words for phrase grouping and the
    captioner_presets safe-zone constants; nudges Y when the caption box would collide with a
    face, HUD or chat rect from the layout plan."""

def caption_plan_to_overlays(plan: dict) -> list[dict]:
    """Overlay dicts in exactly the shape caption_overlays.build_overlays_ass expects."""
```

### `headline.py`
```python
async def generate_headline(cand: dict, *, engine: str | None, language: str) -> dict:
    """{'text','source': 'llm'|'heuristic'}  3-10 words, no fabricated claims.
    Falls back to a deterministic extraction from the clip's own strongest sentence when no LLM
    engine is configured — never blocks the pipeline on an optional provider."""
```

### `render.py`
```python
def build_render_cmd(src: str, cand: dict, plan: dict, ass_path: str, out: str, *,
                     fps: int, crf: int, preset: str) -> list[str]:
    """One ffmpeg argv list. Seeks with -ss before -i, crops/scales/stacks, burns subtitles,
    forces even dimensions, yuv420p, +faststart. Pure function -> unit-testable."""

async def render_clip(...) -> dict     # runs it, returns {'path','size','duration'}
async def render_preview(...) -> dict  # low-res, short, for the editor
```

### `feedback.py` / `ranker.py`
```python
# feedback.py
EVENT_TYPES = ("generated","previewed","approved","rejected","deleted","exported",
               "start_changed","end_changed","crop_changed","layout_changed",
               "caption_changed","headline_changed","score_overridden","posted",
               "performance_recorded")
async def record(session, clip_id, project_id, event_type, payload) -> None: ...
async def training_rows(session) -> list[dict]:   # [{'features':{..},'label':float}]

# ranker.py
FEATURE_ORDER: tuple[str, ...]     # frozen, versioned
def train(rows, *, epochs, lr, l2) -> dict         # logistic regression, pure numpy
def predict(model, features) -> float              # 0..1
def evaluate(model, rows) -> dict                  # {'ndcg@5','precision@5','auc','n'}
def load_model() -> dict | None
def save_model(model) -> Path
MIN_TRAINING_EXAMPLES = 40
```

---

## 8. Config surface (all `CLIPFORGE_`-prefixed)

| Setting | Default | Meaning |
| --- | --- | --- |
| `clipper_enabled` | `True` | Feature flag — gates router + handler registration. |
| `clipper_max_source_duration_s` | `21600` (6 h) | Reject longer sources. |
| `clipper_max_upload_bytes` | `21474836480` (20 GB) | Upload cap. |
| `clipper_min_free_bytes` | `10737418240` (10 GB) | Refuse to start if disk is tighter. |
| `clipper_proxy_width` | `480` | Analysis proxy width. |
| `clipper_proxy_fps` | `10` | Analysis proxy fps. |
| `clipper_default_clip_count` | `8` | Requested clips. |
| `clipper_min_clip_s` / `_max_clip_s` / `_target_clip_s` | `15` / `90` / `35` | Duration window. |
| `clipper_overlap_threshold` | `0.4` | Dedupe overlap. |
| `clipper_text_similarity_threshold` | `0.62` | Dedupe text. |
| `clipper_top_n_llm` | `8` | Pass D budget. |
| `clipper_llm_engine` | `""` | Empty = skip Pass D entirely. |
| `clipper_export_crf` / `_preset` / `_fps` | `18` / `slow` / `30` | Export quality. |
| `clipper_face_pct` | `0.35` | Default facecam share of the canvas. |
| `clipper_retention_days` | `0` | 0 = never auto-purge. |
| `clipper_ranker_enabled` | `True` | Use the learned ranker when it qualifies. |

## 9. Non-goals for this delivery

Recorded so the boundary is explicit, not implied:
speaker diarisation · natural-language moment search · OCR/killfeed/scoreboard parsing · live
chat-replay ingestion · intro/outro template rendering · multi-tenant campaigns · automated
platform metric scraping · a trained vision model for reaction magnitude.
