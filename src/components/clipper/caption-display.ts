// The caption warning (codex-verdict-next-17 §3, next-20 §2): words for the facts the server puts in
// `clip.caption_display`. The server decides the level (`serialize.caption_display_view`); nothing
// here re-judges it. Two rules hold throughout:
// - a report that is missing, old or unreadable is NEVER shown as clean;
// - a time is always said on the clock it was measured on. A plan time is not an MP4 time: the
//   export may have removed silence before it.
import type {
  CaptionCard,
  CaptionDisplayView,
  CaptionRenderBlock,
} from "@/types/clipper-caption-display";

export interface CaptionWarning {
  /** `warn`: what the viewer gets is off. `info`: not checked, or checked on an older version. */
  tone: "warn" | "info";
  title: string;
  lines: string[];
}

const MAX_LISTED = 3;

const FROM = {
  export: "In the current export",
  preview: "In the last preview",
  plan: "In the current plan (no up-to-date render yet)",
} as const;

const STALE_WHY: Record<string, string> = {
  clip_not_exported: "the clip changed after it",
  plan_changed: "the captions were edited after it",
  preview_cleared: "the clip changed after it",
};

function secs(t: number | null | undefined): string {
  return typeof t === "number" && Number.isFinite(t) ? `${t.toFixed(2)} s` : "?";
}

function quote(text: string): string {
  const t = text.trim();
  return `“${t.length > 28 ? `${t.slice(0, 27)}…` : t}”`;
}

/** Where a card is, on its own clock. */
export function cardPlace(c: CaptionCard): string {
  if (c.clock === "plan") {
    const inExport =
      typeof c.export_at === "number" && Number.isFinite(c.export_at)
        ? `≈${secs(c.export_at)} in the export`
        : "its time in the export is unknown";
    return `${quote(c.text)} at ${secs(c.start)} in the plan (${inExport})`;
  }
  return `${quote(c.text)} at ${secs(c.start)} in the export`;
}

function listed(cards: CaptionCard[]): string[] {
  const shown = cards.slice(0, MAX_LISTED).map((c) => `• ${cardPlace(c)}`);
  return cards.length > MAX_LISTED ? [...shown, `• …and ${cards.length - MAX_LISTED} more`] : shown;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The export's report is about an older version: say so, whatever else is shown. */
function staleLines(view: CaptionDisplayView): string[] {
  const out: string[] = [];
  for (const [name, block] of [["export", view.export], ["preview", view.preview]] as const) {
    if (block?.state === "stale") {
      const why = STALE_WHY[block.stale_reason ?? ""] ?? "it no longer matches the clip";
      out.push(`The last ${name}'s caption check is out of date: ${why}. Render it again to check.`);
    }
  }
  return out;
}

function limited(view: CaptionDisplayView, from: "export" | "preview" | "plan"): CaptionWarning {
  let unshown: CaptionCard[];
  let short: CaptionCard[] | null;
  let shortCount: number;
  let missingAll = false;
  let overlaps = 0;
  let empty = 0;
  let unsettled = false;
  if (from === "plan") {
    unshown = (view.plan.unshown_cards ?? []).map((c) => ({ ...c, clock: c.clock ?? "plan" }));
    short = (view.plan.short_cards ?? []).map((c) => ({ ...c, clock: c.clock ?? "plan" }));
    shortCount = short.length;
  } else {
    const block: CaptionRenderBlock = view[from];
    const facts = block.facts;
    unshown = facts?.unshown_cards ?? [];
    short = facts?.short_cards ?? null;
    shortCount = facts?.short_count ?? short?.length ?? 0;
    missingAll = block.missing === "all";
    overlaps = facts?.overlapping_pairs ?? 0;
    empty = facts?.empty_events ?? 0;
    // A stored plan from before `settle` is reported, never re-timed: its overlaps stay.
    unsettled = facts?.plan_settled === false;
  }
  const min = secs(view.min_chunk_s);
  const lines: string[] = [`${FROM[from]}:`];
  if (missingAll) {
    lines.push("no caption card has any time on screen, so the captions do not appear at all.");
  }
  if (unshown.length > 0) {
    lines.push(`${plural(unshown.length, "caption card is", "caption cards are")} not shown:`, ...listed(unshown));
  }
  if (shortCount > 0) {
    lines.push(`${plural(shortCount, "card is", "cards are")} on screen for less than ${min}${short?.length ? ":" : "."}`);
    if (short?.length) lines.push(...listed(short));
  }
  if (overlaps > 0) {
    lines.push(`${plural(overlaps, "pair of cards overlaps", "pairs of cards overlap")} on screen.`);
  }
  if (empty > 0) {
    lines.push(`${plural(empty, "caption event has", "caption events have")} no time on screen.`);
  }
  if (unsettled && (overlaps > 0 || shortCount > 0)) {
    lines.push("These captions were saved before each card got its own display time; they are shown as saved.");
  }
  const title = missingAll
    ? "Captions missing"
    : unshown.length > 0
      ? "Some captions are not shown"
      : overlaps > 0
        ? "Some captions overlap"
        : "Some captions flash by";
  return { tone: "warn", title, lines: [...lines, ...staleLines(view)] };
}

function unverified(view: CaptionDisplayView, from: "export" | "preview"): CaptionWarning {
  const block = view[from];
  let why: string;
  if (block.state === "unreadable" && block.why === "mp4_missing") {
    why = "its video file is missing, so nothing about its captions is checked.";
  } else if (block.state === "unreadable") {
    why = "its caption report cannot be read, so nothing about the captions is checked.";
  } else if (block.facts?.ass_agrees === false) {
    why = "the subtitle file that was written does not match the planned cards.";
  } else {
    why = "nothing proves the subtitles were burned into the video.";
  }
  return { tone: "warn", title: "Captions not verified", lines: [`${FROM[from]}: ${why}`, ...staleLines(view)] };
}

/** The warning for one clip, or null when there is nothing to say. */
export function captionWarning(view: CaptionDisplayView | null | undefined): CaptionWarning | null {
  if (!view || view.schema !== "caption_display_v1") return null;
  const from = view.level_from ?? "export";
  if (view.level === "limited") return limited(view, from);
  if (view.level === "unverified" && from !== "plan") return unverified(view, from);
  if (view.level === "unknown") {
    // Neutral (codex-verdict-next-23 §2): an absent report says nothing about WHEN the render was
    // made; the reason is given only when the server knows it.
    const what = from === "preview" ? "preview" : "export";
    const why = view[from === "preview" ? "preview" : "export"]?.why === "sidecar_missing"
      ? " (its report file is missing)"
      : "";
    return {
      tone: "info",
      title: "Captions not checked",
      lines: [
        `The caption report of the current ${what} is not available${why}, so it cannot be checked.`,
        "Render it again to check it.",
        ...staleLines(view),
      ],
    };
  }
  const stale = staleLines(view);
  return stale.length ? { tone: "info", title: "Caption check out of date", lines: stale } : null;
}

/** What the EDITOR says: the warning, or an explicit state where the card stays silent. A missing
 * view (an older server) or an unknown schema is its own answer, never the silence of `verified`. */
export function editorCaptionNote(view: CaptionDisplayView | null | undefined): CaptionWarning | null {
  if (!view || view.schema !== "caption_display_v1") {
    return {
      tone: "info",
      title: "Caption check unavailable",
      lines: ["The server did not send a caption report this app can read, so nothing is checked."],
    };
  }
  return captionWarning(view);
}

/** What the CARD says: only what is wrong with the captions a viewer gets. "Not checked" and "out
 * of date" are the editor's (codex-verdict-next-23 §2); no note is never a "verified" label. */
export function cardCaptionNote(view: CaptionDisplayView | null | undefined): CaptionWarning | null {
  const w = captionWarning(view);
  return w && w.tone === "warn" ? w : null;
}

const NOT_BURNED_NONE = "Captions not burned (no_card_representable)";

/** The clip's text warnings without the one the structured note already says for the same render:
 * `no_card_representable` while the note reports every card missing. Other "not burned" reasons
 * and older texts stay (next-23 §2: one visible note per problem). */
export function visibleWarnings(
  warnings: string[] | null | undefined,
  view: CaptionDisplayView | null | undefined,
): string[] {
  const list = warnings ?? [];
  const from = view?.level_from;
  const covered =
    view?.schema === "caption_display_v1" &&
    view.level === "limited" &&
    (from === "export" || from === "preview") &&
    view[from]?.missing === "all";
  return covered ? list.filter((w) => !w.startsWith(NOT_BURNED_NONE)) : list;
}
