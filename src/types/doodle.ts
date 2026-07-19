// ClipForge — Auto Story Doodle Video types.
//
// Mirrors server/services/doodle/storage.py storyboard.json schema exactly
// (see PRPs/auto-story-doodle.md). Keep in sync with the backend contract —
// do not add fields here that the backend doesn't produce.

export type DoodleMode = "topic" | "script";

export type DoodleNiche =
  | "ancient_humans"
  | "history"
  | "weird_facts"
  | "psychology"
  | "space"
  | "science"
  | "mystery"
  | "animals"
  | "custom";

export type DoodleStatus =
  | "created"
  | "scripting"
  | "script_ready"
  | "voicing"
  | "voice_ready"
  | "rendering"
  | "done"
  | "failed";

export type DoodleFrameInterval = 2 | 3 | 4 | "auto";

export type DoodleAspectRatio = "16:9" | "9:16" | "1:1";

// "none" is the default — SRT captions are still exported, nothing is burned.
// Legacy values "minimal" / "tiktok_bold" are normalized server-side.
export type DoodleSubtitleStyle = "none" | "minimal_bottom" | "youtube_clean" | "tiktok_big";

export interface DoodleRenderInfo {
  status: "rendering" | "done" | "failed";
  path: string | null;
  error: string | null;
}

export type DoodleMotionStyle = "subtle" | "zoom_in" | "zoom_out" | "pan" | "none";

export type DoodleRenderQuality = "high" | "medium";

// "manual_flow" (default): user generates images in Google Flow and drags
// them in. "comfyui_local": free local SDXL Turbo generation via ComfyUI
// running on the user's own dual-GPU rig (see local-image-gen.tsx).
export type DoodleImageProviderMode = "manual_flow" | "comfyui_local";

export interface DoodleSettings {
  target_duration_seconds: number;
  frame_interval_seconds: DoodleFrameInterval;
  aspect_ratio: DoodleAspectRatio;
  resolution: string; // "1920x1080" | "1080x1920" | "1080x1080"
  voice: string;
  voice_speed: number;
  subtitle_style: DoodleSubtitleStyle;
  burn_subtitles: boolean;
  motion_style: DoodleMotionStyle;
  motion_intensity: number;
  openai_model: string | null;
  render_quality: DoodleRenderQuality;
  use_gpu: boolean;
  allow_placeholders: boolean;
  image_provider?: DoodleImageProviderMode;
}

export interface DoodleScene {
  index: number;
  narration: string;
  subtitle: string;
  estimated_duration: number;
  image_prompt: string;
  flow_filename: string;
  image_path: string | null;
  audio_path: string | null;
  audio_duration: number | null;
}

export interface DoodleStoryboard {
  id: string;
  title: string;
  description: string;
  tags: string[];
  topic: string;
  niche: string;
  mode: DoodleMode;
  status: DoodleStatus;
  error: string | null;
  settings: DoodleSettings;
  scenes: DoodleScene[];
  final_voiceover_path: string | null;
  total_audio_duration: number | null;
  export_path: string | null;
  // Per-subtitle-mode render outputs (keyed by mode) — each style has its
  // own status/path so one failed style never fails the project.
  renders?: Record<string, DoodleRenderInfo>;
  created_at: string;
  updated_at: string;
  // Computed server-side on GET /projects/{id}.
  missing_images?: number[];
  // Local ComfyUI image-generation progress, written by the worker.
  image_generation?: DoodleImageGeneration;
}

export interface DoodleProjectSummary {
  id: string;
  title: string;
  topic: string;
  niche: string;
  status: DoodleStatus;
  scene_count: number;
  images_uploaded: number;
  missing_images: number;
  created_at: string;
  total_audio_duration: number | null;
  export_path: string | null;
  settings: DoodleSettings;
}

export interface DoodleVoice {
  id: string;
  label: string;
  lang: string;
}

export interface DoodleVoicesResponse {
  available: boolean;
  reason: string | null;
  voices: DoodleVoice[];
}

export interface DoodleImageProvider {
  id: string;
  label: string;
  enabled: boolean;
  default?: boolean;
}

export interface DoodleJobResponse {
  id: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  progress_message: string | null;
  error: string | null;
}

export interface DoodleCreateProjectPayload {
  mode: DoodleMode;
  topic?: string;
  script_text?: string;
  niche: string;
  custom_niche?: string;
  target_duration_seconds: number;
  frame_interval_seconds: DoodleFrameInterval;
  aspect_ratio: DoodleAspectRatio;
  voice: string;
  voice_speed?: number;
  subtitle_style?: DoodleSubtitleStyle;
  burn_subtitles?: boolean;
  motion_style?: DoodleMotionStyle;
  motion_intensity?: number;
  openai_model?: string | null;
  render_quality?: DoodleRenderQuality;
  use_gpu?: boolean;
}

export interface DoodleBulkUploadResult {
  matched: number;
  unmatched: string[];
}

// --- Local ComfyUI image generation (free, dual-GPU) ---

export interface DoodleImageGenerationFailure {
  index: number;
  error: string;
}

export interface DoodleImageGeneration {
  status: "idle" | "running" | "done" | "failed";
  model: string;
  generated: number;
  failed: DoodleImageGenerationFailure[];
  updated_at: string;
}

export interface DoodleComfyGpuStatus {
  index: number;
  url: string;
  alive: boolean;
  queue_pending: number;
  error: string | null;
}

export interface DoodleComfyStatus {
  gpus: DoodleComfyGpuStatus[];
  any_alive: boolean;
  model: string | null;
  model_file_found: boolean;
  hint: string | null;
}
