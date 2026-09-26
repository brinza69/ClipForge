import type { JobStatus } from "./clipper";

// What the backend reads off its own job rows about the latest attempts (D2r-3,
// codex-verdict-closure-4). Split out at clipper.ts's 500-line limit; the two
// `*AttemptFields` interfaces are extended by ClipperClip / ClipperProject.

/** Why `_publish_preview` refused a finished render: the clip was edited while
 * it rendered, or an export published after the preview started. */
export type PreviewDiscard = "inputs_changed" | "newer_export";

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

export interface ClipAttemptFields {
  last_preview?: PreviewAttempt | null;
}

export interface ProjectAttemptFields {
  analysis_attempt?: AnalysisAttempt | null;
  /** The predicate `/retry` itself applies: the latest pipeline attempt failed
   * or was cancelled, and none is queued or running. */
  retry_allowed?: boolean;
}
