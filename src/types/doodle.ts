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

export type DoodleSubtitleStyle = "youtube_clean" | "tiktok_bold" | "minimal";

export type DoodleMotionStyle = "subtle" | "zoom_in" | "zoom_out" | "pan" | "none";

export type DoodleRenderQuality = "high" | "medium";

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
  created_at: string;
  updated_at: string;
  // Computed server-side on GET /projects/{id}.
  missing_images?: number[];
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
