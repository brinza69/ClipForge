// What the server says about how a clip's captions are DISPLAYED (BURST, codex-verdict-next-17 §3 /
// next-20 §2): `caption_display` on every clip card, built by `serialize.caption_display_view`
// (contract: data/claude-master-20260924/B/BURST-UI-contract.md). The server decides `level`; the UI
// only words it. A missing or old report is never "clean": `not_measured` / `unknown` are answers.

/** Whose clock a card's times are on. `plan`: clip-relative seconds BEFORE dead air is removed.
 * `export`: the delivered file's clock, after `drop_spans`. They differ when silence was trimmed. */
export type CaptionClock = "plan" | "export";

export interface CaptionCard {
  index?: number;
  text: string;
  start: number | null;
  end: number | null;
  /** `no_time`: the plan had no display time for it. `under_one_tick`: the export's remap left it
   * shorter than one subtitle tick. Either way it is NOT on screen. */
  why?: string;
  clock?: CaptionClock;
  /** A plan-clock card's position in the export; null when it cannot be converted. */
  export_at?: number | null;
}

export interface CaptionPlanFacts {
  state: "no_plan" | "unreadable" | "not_settled" | "clean" | "limited";
  clock?: "plan";
  short_cards?: CaptionCard[];
  unshown_cards?: CaptionCard[];
  limits?: unknown[];
}

export type CaptionRenderState =
  | "none"
  | "not_measured"
  | "not_burned"
  | "unreadable"
  | "verified"
  | "unverified"
  | "limited"
  | "stale";

export interface CaptionRenderFacts {
  short_count: number | null;
  short_cards: CaptionCard[] | null;
  unshown_cards: CaptionCard[];
  /** False: the written subtitle file is not the planned cards. Never a verified success. */
  ass_agrees: boolean | null;
  overlapping_pairs: number | null;
  empty_events: number | null;
  plan_limits: number | null;
  plan_settled: boolean | null;
}

export interface CaptionRenderBlock {
  state: CaptionRenderState;
  current: boolean;
  /** For a `stale` block: the state it had when it was current. */
  found_state?: CaptionRenderState;
  stale_reason?: string | null;
  outcome?: string | null;
  reason?: string | null;
  missing?: "all" | "some" | "none" | null;
  /** Why a known render reads `not_measured` / `unreadable`: `sidecar_missing`, `mp4_missing`. */
  why?: string | null;
  facts?: CaptionRenderFacts | null;
  identity?: Record<string, unknown> | null;
}

export type CaptionLevel =
  | "unverified"
  | "limited"
  | "unknown"
  | "verified"
  | "not_burned"
  | "not_rendered";

export interface CaptionDisplayView {
  schema: "caption_display_v1";
  min_chunk_s: number;
  level: CaptionLevel;
  level_from: "export" | "preview" | "plan" | null;
  /** The effective caption policy the level was judged under; `suppress` = our layer is off on
   * purpose, so the plan's limits do not raise the level (BURST R3). */
  policy?: { action: string; scope?: string } | null;
  plan: CaptionPlanFacts;
  export: CaptionRenderBlock;
  preview: CaptionRenderBlock;
}
