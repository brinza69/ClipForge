import type { JobStatus } from "./clipper";
import type { CaptionDisplayView } from "./clipper-caption-display";

// What the backend reads off its own job rows about the latest attempts (D2r-3,
// codex-verdict-closure-4). Split out at clipper.ts's 500-line limit; the two
// `*AttemptFields` interfaces are extended by ClipperClip / ClipperProject.

/** Why `_publish_preview` refused a finished render: the clip was edited while
 * it rendered, or an export published after the preview started. */
export type PreviewDiscard = "inputs_changed" | "newer_export" | "newer_preview";

/** The NEWEST `clipper_preview` job of a clip — never "the last failed", so a
 * later successful preview replaces an earlier failure on the card. */
export interface PreviewAttempt {
  job_id: string;
  status: JobStatus;
  error: string | null;
  discarded: PreviewDiscard | null;
  created_at: string;
}

/** The newest Clipper PIPELINE job (ingest / transcribe / analyze / score).
 * Previews and exports never enter it, so a clip's render cannot pose as an
 * analysis failure. */
export interface AnalysisAttempt {
  job_id: string;
  type: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
}

/** The NEWEST `clipper_export` job of a clip. A cancelled export leaves the clip
 * `failed` (R4b, so Export stays available); this is what lets the card say
 * "cancelled" rather than "failed" (O3). */
export interface ExportAttempt {
  job_id: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
}

export interface ClipAttemptFields {
  last_preview?: PreviewAttempt | null;
  last_export?: ExportAttempt | null;
  /** False when a rescore kept this clip from an earlier selection run: its
   * rank belongs to that run, so the card must not show it as a second "#1" (O2). */
  from_current_run?: boolean;
  /** How the captions of the current plan / export / preview are displayed (BURST). Absent on an
   * older server: that is "not reported", never "clean". */
  caption_display?: CaptionDisplayView | null;
}

export interface ProjectAttemptFields {
  /** Not an attempt reading — here for clipper.ts's 500-line limit. The maximum clip
   * length the SERVER applies to this project (`serialize.effective_max_clip_s`), so the
   * editor never applies a limit of its own (O4, codex-verdict-next-15 R1). */
  max_clip_s_effective?: number;
  analysis_attempt?: AnalysisAttempt | null;
  /** The predicate `/retry` itself applies: the latest pipeline attempt failed
   * or was cancelled, and none is queued or running. */
  retry_allowed?: boolean;
}
