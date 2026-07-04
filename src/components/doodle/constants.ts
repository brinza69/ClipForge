// Shared constants for the Auto Story Doodle Video feature. Keep in one
// place so the form, list, and detail page render identical labels.

export const MANUAL_FLOW_WARNING =
  "Manual Flow Mode: no automatic image API cost. Generate images in Flow, then drag and drop them here.";

export const NICHES: { value: string; label: string }[] = [
  { value: "ancient_humans", label: "Ancient Humans" },
  { value: "history", label: "History" },
  { value: "weird_facts", label: "Weird Facts" },
  { value: "psychology", label: "Psychology" },
  { value: "space", label: "Space" },
  { value: "science", label: "Science" },
  { value: "mystery", label: "Mystery" },
  { value: "animals", label: "Animals" },
  { value: "custom", label: "Custom" },
];

export const DURATION_PRESETS: { value: number; label: string }[] = [
  { value: 30, label: "30 sec" },
  { value: 60, label: "1 min" },
  { value: 180, label: "3 min" },
  { value: 300, label: "5 min" },
  { value: 480, label: "8 min" },
  { value: 600, label: "10 min" },
  { value: -1, label: "Custom" },
];

export const FRAME_INTERVALS: { value: string; label: string }[] = [
  { value: "2", label: "2s" },
  { value: "3", label: "3s (default)" },
  { value: "4", label: "4s" },
  { value: "auto", label: "Auto" },
];

export const ASPECT_RATIOS: { value: string; label: string }[] = [
  { value: "16:9", label: "16:9 — YouTube" },
  { value: "9:16", label: "9:16 — Shorts / TikTok / Reels" },
  { value: "1:1", label: "1:1 — Square" },
];

// Subtitle modes. "none" is the default; "minimal_bottom" is the recommended
// option when you do want burned-in captions. TikTok big is opt-in only.
export const SUBTITLE_STYLES: { value: string; label: string }[] = [
  { value: "none", label: "None (default)" },
  { value: "minimal_bottom", label: "Minimal bottom (recommended)" },
  { value: "youtube_clean", label: "YouTube clean" },
  { value: "tiktok_big", label: "TikTok big" },
];

export const SUBTITLE_MODE_LABELS: Record<string, string> = {
  none: "No Subtitles",
  minimal_bottom: "Minimal Subtitles",
  youtube_clean: "YouTube Clean",
  tiktok_big: "TikTok Big",
};

export const MOTION_STYLES: { value: string; label: string }[] = [
  { value: "subtle", label: "Subtle (default)" },
  { value: "zoom_in", label: "Zoom in" },
  { value: "zoom_out", label: "Zoom out" },
  { value: "pan", label: "Pan" },
  { value: "none", label: "None" },
];

export const RENDER_QUALITIES: { value: string; label: string }[] = [
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
];

export function estimatedFrameCount(durationSeconds: number, frameInterval: string): number {
  const interval = frameInterval === "auto" ? 3 : Number(frameInterval) || 3;
  if (!durationSeconds || durationSeconds <= 0) return 0;
  return Math.ceil(durationSeconds / interval);
}

export function nicheLabel(value: string): string {
  return NICHES.find((n) => n.value === value)?.label || value;
}

export const STATUS_LABELS: Record<string, string> = {
  created: "Created",
  scripting: "Writing script",
  script_ready: "Script ready",
  voicing: "Voicing",
  voice_ready: "Voice ready",
  rendering: "Rendering",
  done: "Done",
  failed: "Failed",
};

export const STATUS_BADGE_CLASS: Record<string, string> = {
  created: "border-border/40 text-muted-foreground",
  scripting: "border-amber-500/40 text-amber-400",
  script_ready: "border-sky-500/40 text-sky-400",
  voicing: "border-amber-500/40 text-amber-400",
  voice_ready: "border-sky-500/40 text-sky-400",
  rendering: "border-amber-500/40 text-amber-400",
  done: "border-emerald-500/40 text-emerald-400",
  failed: "border-destructive/40 text-destructive",
};

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
