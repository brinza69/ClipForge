// ClipForge — Core Type Definitions

export type SourceType = "youtube" | "twitch" | "vimeo" | "direct" | "local" | "unknown";

export type ProjectStatus =
  | "pending"
  | "fetching_metadata"
  | "metadata_ready"
  | "downloading"
  | "downloaded"
  | "transcribing"
  | "transcribed"
  | "scoring"
  | "processing"
  | "ready"
  | "failed"
  | "cancelled";

export type ClipStatus = "candidate" | "approved" | "exporting" | "exported" | "rejected";

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type JobType = "fetch_metadata" | "download" | "transcribe" | "score" | "reframe" | "export" | "full_pipeline";

export interface Project {
  id: string;
  title: string;
  source_url: string | null;
  source_type: SourceType;
  status: ProjectStatus;
  duration: number | null;
  width: number | null;
  height: number | null;
  thumbnail_url: string | null;
  thumbnail_path: string | null;
  channel_name: string | null;
  estimated_size: number | null;
  video_path: string | null;
  filesize: number | null;
  total_storage: number;
  clip_count: number;
  exported_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProjectMetadata {
  title: string;
  channel_name: string | null;
  duration: number | null;
  duration_formatted: string | null;
  source_type: string;
  width: number | null;
  height: number | null;
  fps: number | null;
  thumbnail_url: string | null;
  thumbnail_path: string | null;
  estimated_size: number | null;
  estimated_size_formatted: string | null;
  upload_date: string | null;
  description: string | null;
  formats_available: FormatInfo[] | null;
}

export interface FormatInfo {
  format_id: string;
  resolution: string;
  ext: string;
  fps: number | null;
  filesize: number | null;
  filesize_formatted: string | null;
  vcodec: string;
  acodec: string;
}

export interface Clip {
  id: string;
  project_id: string;
  title: string;
  thumbnail_path?: string | null;
  start_time: number;
  end_time: number;
  duration: number;
  momentum_score: number;
  hook_strength: number;
  narrative_completeness: number;
  curiosity_score: number;
  emotional_intensity: number;
  caption_readability: number;
  confidence: number;
  transcript_text: string;
  transcript_segments: any[];
  hook_text?: string;
  explanation?: string;
  status: ClipStatus;
  error?: string;
  reframe_data?: any;
  reframe_mode?: string;
  caption_preset_id?: string;
  caption_font_size?: number | null;
  caption_text_color?: string | null;
  caption_highlight_color?: string | null;
  caption_outline_color?: string | null;
  caption_y_position?: string | null;
  hook_font_size?: number | null;
  hook_text_color?: string | null;
  hook_bg_color?: string | null;
  hook_y_position?: string | null;
  hook_box_size?: number | null;
  hook_box_width?: number | null;
  hook_duration_seconds?: number | null;
  hook_x?: number | null;
  hook_y?: number | null;
  subtitle_x?: number | null;
  subtitle_y?: number | null;
  export_resolution?: string | null;
  split_mode?: string | null;
  split_parts_count?: number | null;
  part_label_font_size?: number | null;
  part_label_box_size?: number | null;
  part_label_text_color?: string | null;
  part_label_bg_color?: string | null;
  part_label_x?: number | null;
  part_label_y?: number | null;
  export_path: string | null;
  created_at: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  clip_id: string | null;
  type: JobType;
  status: JobStatus;
  progress: number;
  progress_message: string;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  confidence: number;
  words?: TranscriptWord[];
}

export interface TranscriptWord {
  word: string;
  start: number;
  end: number;
  probability: number;
}

export interface Transcript {
  id: string;
  project_id: string;
  language: string;
  segments: TranscriptSegment[];
  full_text: string;
  word_count: number;
}

export interface StorageInfo {
  media_size: number;
  exports_size: number;
  cache_size: number;
  temp_size: number;
  thumbnails_size: number;
  total_data_size: number;
  disk_total: number;
  disk_used: number;
  disk_free: number;
}

export interface SystemInfo {
  gpu_available: boolean;
  gpu_name: string | null;
  whisper_model: string;
  whisper_device: string;
  data_dir: string;
  exports_dir: string;
  disk_free_gb: number;
  disk_total_gb: number;
}
