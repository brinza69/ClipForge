// Shapes for the blind review. Kept minimal on purpose: the page must not be
// able to render membership, so it must not be able to *name* it either. If a
// field appears here that says which board asked for a clip, the blind is gone.

export const REVIEW_API = "/worker-api/clipper/review";

export type ReviewQuestion = {
  key: string;
  type: "choice";
  options: string[];
};

export type ReviewRubric = {
  rubric_version: string;
  questions: ReviewQuestion[];
  reject_reasons: string[];
};

export type ReviewItem = {
  review_item_id: string;
  position: number | null;
  total: number;
  start: number | null;
  end: number | null;
  duration: number | null;
  preview_ready: boolean;
  transcript: string;
};

export type ReviewProgress = {
  answered: number;
  total: number;
  remaining: number;
};

export type ReviewNext = ReviewProgress &
  ({ done: true } | { done: false; item: ReviewItem });

export type BoardTally = {
  reviewed: number;
  yes: number;
  no: number;
  unsure: number;
  self_contained: number;
  boundaries_correct: number;
  precision: number | null;
};

export type ReviewResult = ReviewProgress & {
  session_id: string;
  historical: boolean;
  blinding_policy: string | null;
  rubric_version: string;
  seed: number;
  tally: { legacy: BoardTally; shadow: BoardTally };
  items: {
    review_item_id: string;
    clip_id: string;
    project_id: string;
    membership: "legacy" | "shadow" | "both";
    legacy_rank: number | null;
    shadow_rank: number | null;
  }[];
};

// The labels the reviewer reads. The API speaks the keys; only this map speaks
// Romanian, so a rubric change is a server change and a wording change is not.
export const QUESTION_LABELS: Record<string, string> = {
  worth_exporting: "Merită exportat?",
  self_contained: "Se înțelege singur?",
  hook: "Începutul captează?",
  start_boundary: "Marginea de început",
  end_boundary: "Marginea de final",
  technical_problem: "Problemă tehnică?",
};

export const OPTION_LABELS: Record<string, string> = {
  yes: "Da",
  no: "Nu",
  unsure: "Nu pot decide",
  weak: "Slab",
  acceptable: "Acceptabil",
  strong: "Puternic",
  early: "Prea devreme",
  correct: "Corect",
  late: "Prea târziu",
};

export const REASON_LABELS: Record<string, string> = {
  no_payoff: "Fără payoff",
  needs_context: "Context insuficient",
  weak_hook: "Hook slab",
  boundary: "Margini greșite",
  visual: "Vizual",
  audio: "Audio",
  duplicate: "Duplicat",
  other: "Altul",
};
