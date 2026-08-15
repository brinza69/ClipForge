// AI Stream Clipper — shared types.
//
// These mirror the backend shapes in server/routers/clipper.py exactly. The
// backend returns raw dicts (the project convention for disk/JSON-backed
// features), so this file is the single place the contract is written down on
// the client side.
//
// All calls go through the Next proxy at /worker-api/clipper/... — never
// straight to the backend port. See CLAUDE.md rule 5.

export const CLIPPER_API = "/worker-api/clipper";

// ── Enumerations ─────────────────────────────────────────────────────────────

export type SourceKind = "url" | "upload" | "library";

export type ContentType =
  | "gaming"
  | "podcast"
  | "interview"
  | "irl"
  | "commentary"
  | "talking_head"
  | "tutorial"
  | "sports"
  | "low_dialogue"
  | "unknown";

export type LayoutMode =
  | "auto"
  | "face_top_game_bottom"
  | "game_top_face_bottom"
  | "pip"
  | "fullscreen_game"
  | "fullscreen_crop"
  | "split_screen"
  | "talking_head";

export type TargetPlatform = "tiktok" | "youtube_shorts" | "instagram_reels" | "facebook_reels";

export type ClipStatus =
  | "candidate"
  | "approved"
  | "rejected"
  | "exporting"
  | "exported"
  | "failed";

export type ProjectStatus =
  | "pending"
  | "fetching_metadata"
  | "metadata_ready"
  | "downloading"
  | "downloaded"
  | "transcribing"
  | "transcribed"
  | "scoring"
  | "ready"
  | "failed"
  | "cancelled";

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

/** The 16 score components from the architecture doc, in display order. */
export const SUB_SCORE_KEYS = [
  "hook",
  "clarity",
  "setup_efficiency",
  "payoff",
  "emotion",
  "novelty",
  "audio_energy",
  "visual_energy",
  "reaction",
  "caption_suitability",
  "platform_fit",
  "context_completeness",
  "retention",
  "edit_confidence",
  "technical",
  "safety",
] as const;

export type SubScoreKey = (typeof SUB_SCORE_KEYS)[number];

export const SUB_SCORE_LABELS: Record<SubScoreKey, string> = {
  hook: "Hook strength",
  clarity: "Standalone clarity",
  setup_efficiency: "Setup efficiency",
  payoff: "Payoff strength",
  emotion: "Emotion",
  novelty: "Novelty",
  audio_energy: "Audio energy",
  visual_energy: "Visual energy",
  reaction: "Reaction quality",
  caption_suitability: "Caption suitability",
  platform_fit: "Platform fit",
  context_completeness: "Context completeness",
  retention: "Retention potential",
  edit_confidence: "Edit confidence",
  technical: "Technical quality",
  safety: "Content-safety confidence",
};

// ── Settings ─────────────────────────────────────────────────────────────────

export interface ClipperSettings {
  // Output
  clip_count: number;
  min_clip_s: number;
  max_clip_s: number;
  platform: TargetPlatform;
  fps: "source" | 30 | 60;
  language: string; // "auto" | "ro" | "en" | ...

  // Editing preferences
  caption_preset_id: string;
  caption_position: "bottom" | "center" | "top";
  caption_highlight: boolean;
  headline_enabled: boolean;
  headline_auto: boolean;
  emoji_enabled: boolean;
  profanity_mask: boolean;
  trim_silence: boolean;
  jump_cuts: boolean;
  auto_zoom: boolean;
  reaction_zoom: boolean;
  facecam_emphasis: boolean;
  include_chat: boolean;
  watermark_text: string;
  min_score: number;

  // Layout
  layout_mode: LayoutMode;
  face_pct: number; // 0..1 share of the canvas given to the facecam
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface LayoutPlan {
  layout: LayoutMode;
  face_rect: Rect | null;
  game_rect: Rect | null;
  chat_rect: Rect | null;
  keyframes: { t: number; rect: Rect }[];
  warnings: string[];
  face_pct: number;
}

export interface CaptionChunk {
  text: string;
  start: number;
  end: number;
}

export interface CaptionPlan {
  chunks: CaptionChunk[];
  style: Record<string, unknown>;
  x_pct: number;
  y_pct: number;
  scale: number;
  preset_id: string;
}

// ── Entities ─────────────────────────────────────────────────────────────────

export interface SourceMetadata {
  title: string;
  channel_name: string | null;
  duration: number | null;
  duration_formatted: string | null;
  thumbnail_url: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  estimated_size: number | null;
  estimated_size_formatted: string | null;
  source_type: string;
  webpage_url: string | null;
  extractor: string | null;
  is_live: boolean | null;
  was_live: boolean | null;
  error?: string;
  error_code?: string;
  suggestion?: string;
}

// Why a clip was picked, as the backend recorded it. Written by the story
// engine (`reasoning_version = "story_v1"`); legacy clips carry only `reasons`
// and the judge's verdict, and everything here is optional for that reason.
export interface ClipStory {
  anchor_t?: number;
  payoff_t?: number;
  hook_t?: number;
  reaction_end?: number;
  archetypes?: string[];
  why?: string;
  edit_reason?: string;
  required_context?: { t?: number; fact?: string }[];
  unresolved_refs?: { text?: string; resolved?: boolean }[];
  context_debt?: number;
  hook_latency?: number;
  thread_id?: string;
  story_version?: string;
  callback_to?: { t?: number; text?: string; kind?: string } | null;
  callback_debt?: number;
}

export interface ClipVerdict {
  story_editor?: string;
  cold_viewer?: string;
  critic?: string;
  reject_reasons?: string[];
  prompt_version?: string;
}

export interface ClipReasoning {
  reasons?: string[];
  story?: ClipStory;
  variant?: string;
  llm_score?: number;
  llm_rank?: number;
  llm_reason?: string;
  llm_verdict?: ClipVerdict;
}

export interface ClipperClip {
  id: string;
  project_id: string;
  title: string;
  start_time: number;
  end_time: number;
  duration: number;
  overall_score: number | null;
  sub_scores: Partial<Record<SubScoreKey, number>> | null;
  score_reason: string | null;
  headline_text: string | null;
  transcript_text: string | null;
  content_type: ContentType | null;
  layout_plan: LayoutPlan | null;
  caption_plan: CaptionPlan | null;
  warnings: string[] | null;
  dedupe_group: string | null;
  is_alternative: boolean;
  rank_position: number | null;
  ranker_version: string | null;
  reasoning: ClipReasoning | null;
  status: ClipStatus;
  export_path: string | null;
  preview_path: string | null;
  thumbnail_path: string | null;
}

export interface ClipperProject {
  id: string;
  title: string;
  source_url: string | null;
  source_type: string;
  source_kind: SourceKind | null;
  status: ProjectStatus;
  channel_name: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  thumbnail_url: string | null;
  content_type: ContentType | null;
  content_type_confidence: number | null;
  content_type_override: ContentType | null;
  rights_confirmed: boolean | null;
  clipper_settings: ClipperSettings | null;
  analysis_version: string | null;
  created_at: string;
  updated_at: string;
  // Present on the detail endpoint only
  clips?: ClipperClip[];
  active_job?: ClipperJob | null;
  error?: string | null;
}

export interface ClipperProjectSummary extends ClipperProject {
  clip_count: number;
  approved_count: number;
  exported_count: number;
}

export interface ClipperJob {
  id: string;
  project_id: string;
  clip_id: string | null;
  type: string;
  status: JobStatus;
  progress: number;
  progress_message: string;
  error: string | null;
}

export interface CaptionPreset {
  id: string;
  name: string;
  font_family: string;
  font_size: number;
  text_color: string;
  highlight_color: string;
  uppercase: boolean;
  position: string;
}

export interface RankerStatus {
  enabled: boolean;
  version: string | null;
  trained_at: string | null;
  training_examples: number;
  min_training_examples: number;
  active: boolean; // true when the learned model is actually being used
  metrics: { ndcg_at_5?: number; precision_at_5?: number; auc?: number; n?: number } | null;
}

export interface PerformanceMetrics {
  platform: string;
  post_url: string;
  views?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  saves?: number;
  avg_watch_time_s?: number;
  completion_rate?: number;
  followers_gained?: number;
  published_at?: string;
}

// ── Pipeline stages (mirrors the worker's progress messages) ─────────────────

export const PIPELINE_STAGES = [
  "Validating source",
  "Reading metadata",
  "Downloading",
  "Creating proxy",
  "Extracting audio",
  "Transcribing",
  "Detecting scenes",
  "Detecting faces and regions",
  "Detecting content type",
  "Building semantic segments",
  "Creating candidates",
  "Scoring candidates",
  "Removing duplicates",
  "Preparing layouts",
  "Generating previews",
  "Ready for review",
] as const;

// ── Defaults ─────────────────────────────────────────────────────────────────

export const DEFAULT_SETTINGS: ClipperSettings = {
  clip_count: 8,
  min_clip_s: 15,
  max_clip_s: 90,
  platform: "tiktok",
  fps: 30,
  language: "auto",
  caption_preset_id: "bold_impact",
  caption_position: "bottom",
  caption_highlight: true,
  headline_enabled: true,
  headline_auto: true,
  emoji_enabled: false,
  profanity_mask: false,
  trim_silence: true,
  jump_cuts: false,
  auto_zoom: true,
  reaction_zoom: true,
  facecam_emphasis: true,
  include_chat: false,
  watermark_text: "",
  min_score: 0,
  layout_mode: "auto",
  face_pct: 0.35,
};

export const CONTENT_TYPE_LABELS: Record<ContentType, string> = {
  gaming: "Gaming",
  podcast: "Podcast",
  interview: "Interview",
  irl: "IRL stream",
  commentary: "Commentary",
  talking_head: "Talking head",
  tutorial: "Tutorial",
  sports: "Sports",
  low_dialogue: "Low dialogue",
  unknown: "Unknown",
};

export const LAYOUT_LABELS: Record<LayoutMode, string> = {
  auto: "Auto-detect",
  face_top_game_bottom: "Face top / gameplay bottom",
  game_top_face_bottom: "Gameplay top / face bottom",
  pip: "Facecam picture-in-picture",
  fullscreen_game: "Full-screen gameplay",
  fullscreen_crop: "Full-screen crop",
  split_screen: "Split screen",
  talking_head: "Talking head",
};

/** Format seconds as m:ss (or h:mm:ss past an hour). */
export function formatTimecode(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}
