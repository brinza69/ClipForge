// What the engine DECIDED, and what Pass D thought of the result.
//
// Split out of `clipper.ts` when that file crossed the repo's 500-line limit.
// The seam is a real one: everything here describes a judgement about a clip —
// the story it was chosen for, the model's verdict, the review's findings —
// while `clipper.ts` describes the clip, the project and the settings.
//
// Re-exported from `clipper.ts`, so no existing import had to move. Import
// from either; they are the same types.
//
// `DEFAULT_SETTINGS` deliberately stayed behind: `server/tests/
// test_settings_parity.py` reads it out of `clipper.ts` by name.

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

// Pass D. `reasoning` says why the MOMENT was chosen; this says what is wrong
// with the CUT, and it only exists after an export, because that is when there
// is a shot list and a caption position to be wrong about.
export interface ClipFinding {
  kind: string;
  /** "revise" — something can act on it. "reject" — the clip is mostly dead. */
  severity: "revise" | "reject";
  /** Seconds from the start of the clip, so the reader can jump to it. */
  at: number;
  detail: string;
  value: number;
}

export interface ClipReview {
  version: string;
  verdict: "APPROVE" | "REVISE" | "REJECT";
  findings: ClipFinding[];
  /** How many frames were sampled. `0` means the review could not look. */
  sampled: number;
  warnings: string[];
}
