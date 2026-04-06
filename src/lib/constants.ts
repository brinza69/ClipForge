export const APP_NAME = "ClipForge";
export const APP_VERSION = "0.1.0";

// ── Source platform labels & colors ──────────────────────────────────────────
export const SOURCE_LABELS: Record<string, string> = {
  youtube: "YouTube",
  twitch: "Twitch VOD",
  vimeo: "Vimeo",
  direct: "Direct MP4",
  m3u8: "HLS Stream",
  generic: "Web Video",
  local: "Local File",
  unknown: "Unknown",
};

export const SOURCE_COLORS: Record<string, string> = {
  youtube: "bg-red-500/15 text-red-400 border-red-500/20",
  twitch: "bg-purple-500/15 text-purple-400 border-purple-500/20",
  vimeo: "bg-cyan-500/15 text-cyan-400 border-cyan-500/20",
  direct: "bg-amber-500/15 text-amber-400 border-amber-500/20",
  m3u8: "bg-orange-500/15 text-orange-400 border-orange-500/20",
  generic: "bg-blue-500/15 text-blue-400 border-blue-500/20",
  local: "bg-slate-500/15 text-slate-400 border-slate-500/20",
  unknown: "bg-gray-500/15 text-gray-400 border-gray-500/20",
};

// ── Project status ───────────────────────────────────────────────────────────
export const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  fetching_metadata: "Fetching Info",
  metadata_ready: "Ready to Process",
  downloading: "Downloading",
  downloaded: "Downloaded",
  transcribing: "Transcribing",
  transcribed: "Transcribed",
  scoring: "Finding Clips",
  processing: "Processing",
  ready: "Clips Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const STATUS_COLORS: Record<string, string> = {
  pending: "bg-slate-500/15 text-slate-400",
  fetching_metadata: "bg-blue-500/15 text-blue-400",
  metadata_ready: "bg-emerald-500/15 text-emerald-400",
  downloading: "bg-blue-500/15 text-blue-400",
  downloaded: "bg-teal-500/15 text-teal-400",
  transcribing: "bg-violet-500/15 text-violet-400",
  transcribed: "bg-violet-500/15 text-violet-400",
  scoring: "bg-amber-500/15 text-amber-400",
  processing: "bg-blue-500/15 text-blue-400",
  ready: "bg-emerald-500/15 text-emerald-400",
  failed: "bg-red-500/15 text-red-400",
  cancelled: "bg-gray-500/15 text-gray-400",
};

// ── Clip status ──────────────────────────────────────────────────────────────
export const CLIP_STATUS_LABELS: Record<string, string> = {
  candidate: "Candidate",
  approved: "Approved",
  exporting: "Exporting",
  exported: "Exported",
  rejected: "Rejected",
};

// ── Momentum Score labels & colors ───────────────────────────────────────────
export const SCORE_LABELS: Record<string, string> = {
  hook_strength: "Hook Strength",
  narrative_completeness: "Narrative",
  curiosity_score: "Curiosity",
  emotional_intensity: "Emotion",
  caption_readability: "Readability",
};

export const SCORE_COLORS: Record<string, string> = {
  hook_strength: "#FFD700",
  narrative_completeness: "#00D4FF",
  curiosity_score: "#FF6B35",
  emotional_intensity: "#FF3366",
  caption_readability: "#4ADE80",
};

// ── Supported ingestion sources ──────────────────────────────────────────────
// Robust but honest: these are the platforms we actively support.
// Any yt-dlp-compatible URL may also work via generic fallback.
export const SUPPORTED_SOURCES = [
  { id: "youtube", label: "YouTube", icon: "youtube", note: "Full support for public videos and playlists entries." },
  { id: "twitch",  label: "Twitch VODs", icon: "twitch", note: "Public VODs and highlights. Live streams: not supported." },
  { id: "vimeo",   label: "Vimeo", icon: "vimeo", note: "Public and unlisted videos." },
  { id: "direct",  label: "Direct MP4", icon: "link", note: "Any publicly accessible .mp4 URL." },
  { id: "m3u8",    label: "HLS/m3u8", icon: "link", note: "May work for publicly accessible HLS streams." },
  { id: "generic", label: "Other sites", icon: "globe", note: "Many sites supported via yt-dlp. Results vary." },
];

// Known failure reasons for graceful error display
export const INGESTION_ERRORS: Record<string, string> = {
  geo_blocked: "This video is geo-restricted and cannot be accessed from your location.",
  login_required: "This video requires a login. Try downloading it manually and using local upload.",
  drm_protected: "This video is DRM-protected and cannot be downloaded.",
  private_video: "This video is private or has been removed.",
  live_stream: "Live streams are not supported. Wait for the VOD to be available.",
  age_restricted: "This video is age-restricted. Try logging into yt-dlp with cookies.",
  unsupported_site: "This site is not supported by yt-dlp. Try a direct MP4 link or local upload.",
  network_error: "Network error. Check your connection and try again.",
  unknown: "An unexpected error occurred. Try again or use local file upload.",
};

// ── Formatting helpers ───────────────────────────────────────────────────────
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "--:--";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let b = Math.abs(bytes);
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

export function scoreColor(score: number): string {
  if (score >= 70) return "text-emerald-400";
  if (score >= 45) return "text-amber-400";
  if (score >= 25) return "text-orange-400";
  return "text-red-400";
}

export function scoreGradient(score: number): string {
  if (score >= 70) return "from-emerald-500 to-teal-500";
  if (score >= 45) return "from-amber-500 to-yellow-500";
  if (score >= 25) return "from-orange-500 to-red-500";
  return "from-red-500 to-rose-500";
}

/** Detect source platform from a pasted URL. */
export function detectSource(url: string): string {
  const u = url.toLowerCase().trim();
  if (u.includes("youtube.com") || u.includes("youtu.be")) return "youtube";
  if (u.includes("twitch.tv")) return "twitch";
  if (u.includes("vimeo.com")) return "vimeo";
  if (u.endsWith(".m3u8") || u.includes(".m3u8?")) return "m3u8";
  if (u.endsWith(".mp4") || u.endsWith(".webm") || u.endsWith(".mkv")) return "direct";
  if (u.startsWith("http")) return "generic";
  return "unknown";
}

// ── Backward-compat aliases for older components ─────────────────────────────
export const getScoreColor = scoreColor;
export const getScoreGradient = scoreGradient;
export const MOMENTUM_SCORE_LABELS = SCORE_LABELS;
export const MOMENTUM_SCORE_COLORS = SCORE_COLORS;
export const SOURCE_TYPE_LABELS = SOURCE_LABELS;
export const SOURCE_TYPE_COLORS = SOURCE_COLORS;