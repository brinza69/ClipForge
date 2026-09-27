import assert from "node:assert/strict";
import test from "node:test";
import {
  captionWarning, cardCaptionNote, cardPlace, editorCaptionNote, visibleWarnings,
} from "./caption-display.ts";

// Shapes as `serialize.caption_display_view` returns them (B/BURST-UI-contract.md).
const none = { state: "none", current: false };
const facts = (over = {}) => ({
  short_count: 0, short_cards: [], unshown_cards: [], ass_agrees: true, overlapping_pairs: 0,
  empty_events: 0, plan_limits: 0, plan_settled: true, ...over,
});
const view = (over = {}) => ({
  schema: "caption_display_v1", min_chunk_s: 0.12, level: "verified", level_from: "export",
  plan: { state: "clean", clock: "plan", short_cards: [], unshown_cards: [], limits: [] },
  export: { state: "verified", current: true, missing: "none", facts: null },
  preview: none, ...over,
});
const text = (w) => [w.title, ...w.lines].join("\n");

test("a verified export says nothing", () => {
  assert.equal(captionWarning(view()), null);
});

test("an old export with no report is 'not checked', never silence (next-20 §2)", () => {
  const w = captionWarning(view({ level: "unknown", export: { state: "not_measured", current: true } }));
  assert.ok(w);
  assert.equal(w.tone, "info");
  assert.match(text(w), /caption report of the current export is not available, so it cannot be checked/);
  assert.doesNotMatch(text(w), /before/, "an absent report says nothing about WHEN (next-23 §2)");
});

test("every card with no display time: the captions are missing", () => {
  const cards = [{ text: "LAST", start: 9.997, end: 10.0, why: "no_time", clock: "plan", export_at: 9.997 }];
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "all", facts: facts({ unshown_cards: cards }) },
  }));
  assert.equal(w.tone, "warn");
  assert.equal(w.title, "Captions missing");
  assert.match(text(w), /do not appear at all/);
  assert.match(text(w), /“LAST” at 10\.00 s in the plan \(≈10\.00 s in the export\)/);
});

test("some cards not shown are listed on the export's clock", () => {
  const cards = [{ text: "one", start: 1.0, end: 1.001, why: "under_one_tick", clock: "export", export_at: 1.0 }];
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "some", facts: facts({ unshown_cards: cards }) },
  }));
  assert.equal(w.title, "Some captions are not shown");
  assert.match(text(w), /1 caption card is not shown/);
  assert.match(text(w), /“one” at 1\.00 s in the export/);
});

test("a plan time is never shown as an export time (next-20 §2: clocks)", () => {
  assert.equal(cardPlace({ text: "x", start: 12.3, end: 12.3, clock: "plan", export_at: 11.8 }),
    "“x” at 12.30 s in the plan (≈11.80 s in the export)");
  assert.equal(cardPlace({ text: "x", start: 12.3, end: 12.3, clock: "plan", export_at: null }),
    "“x” at 12.30 s in the plan (its time in the export is unknown)");
});

test("a burst that is only short says so, with the minimum", () => {
  const short = [{ text: "SO WHAT'S STOPPING", start: 28.9, end: 28.99, clock: "export" }];
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "none", facts: facts({ short_count: 1, short_cards: short }) },
  }));
  assert.equal(w.title, "Some captions flash by");
  assert.match(text(w), /1 card is on screen for less than 0\.12 s:/);
  assert.match(text(w), /“SO WHAT'S STOPPING” at 28\.90 s in the export/);
});

test("a count without a list is still said", () => {
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "none", facts: facts({ short_count: 3, short_cards: null }) },
  }));
  assert.match(text(w), /3 cards are on screen for less than 0\.12 s\./);
});

test("a written file that does not match the cards is not verified (ass_agrees=false)", () => {
  const w = captionWarning(view({
    level: "unverified",
    export: { state: "unverified", current: true, missing: "none", facts: facts({ ass_agrees: false }) },
  }));
  assert.equal(w.title, "Captions not verified");
  assert.match(text(w), /does not match the planned cards/);
});

test("an unreadable report is not verified either", () => {
  const w = captionWarning(view({ level: "unverified", export: { state: "unreadable", current: true } }));
  assert.match(text(w), /cannot be read/);
});

test("the plan's own limits speak before anything is rendered", () => {
  const w = captionWarning(view({
    level: "limited", level_from: "plan", export: none,
    plan: { state: "limited", clock: "plan", short_cards: [],
            unshown_cards: [{ text: "b10", start: 5.12, end: 5.12, why: "no_time" }], limits: [{}] },
  }));
  assert.match(text(w), /In the current plan \(no up-to-date render yet\):/);
  assert.match(text(w), /“b10” at 5\.12 s in the plan/);
});

test("an export made before an edit is out of date, even when a newer preview is fine", () => {
  const w = captionWarning(view({
    level: "verified", level_from: "preview",
    preview: { state: "verified", current: true, missing: "none" },
    export: { state: "stale", current: false, found_state: "verified", stale_reason: "plan_changed" },
  }));
  assert.equal(w.tone, "info");
  assert.match(text(w), /last export's caption check is out of date: the captions were edited after it/);
});

test("more than three cards are summarised", () => {
  const cards = Array.from({ length: 5 }, (_, i) => ({ text: `b${i}`, start: 5, end: 5, clock: "export" }));
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "some", facts: facts({ unshown_cards: cards }) },
  }));
  assert.match(text(w), /…and 2 more/);
});

test("no view, or an unknown schema, claims nothing", () => {
  assert.equal(captionWarning(null), null);
  assert.equal(captionWarning(undefined), null);
  assert.equal(captionWarning({ ...view(), schema: "caption_display_v2" }), null);
});

test("overlapping cards of a plan saved before settle are said, with why (real export d2b16fcfac95)", () => {
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "none",
              facts: facts({ overlapping_pairs: 7, plan_settled: false }) },
  }));
  assert.equal(w.title, "Some captions overlap");
  assert.match(text(w), /7 pairs of cards overlap on screen\./);
  assert.match(text(w), /saved before each card got its own display time/);
});

test("empty caption events are said", () => {
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "none", facts: facts({ empty_events: 1 }) },
  }));
  assert.match(text(w), /1 caption event has no time on screen\./);
});

test("a limited level never renders as a header with nothing under it", () => {
  const w = captionWarning(view({
    level: "limited",
    export: { state: "limited", current: true, missing: "none", facts: facts({ overlapping_pairs: 1 }) },
  }));
  assert.ok(w.lines.length > 1, "more than the 'In the current export:' header");
});

// ── next-23 §2: where each note shows, one note per problem, neutral wording ──

test("the card keeps only warnings: 'not checked' and 'out of date' are the editor's", () => {
  const unknown = view({ level: "unknown", export: { state: "not_measured", current: true } });
  assert.equal(cardCaptionNote(unknown), null);
  assert.equal(editorCaptionNote(unknown).title, "Captions not checked");
  const stale = view({ export: { state: "stale", current: false, stale_reason: "plan_changed" } });
  assert.equal(cardCaptionNote(stale), null);
  assert.equal(editorCaptionNote(stale).title, "Caption check out of date");
  const missing = view({ level: "limited", export: { state: "limited", current: true, missing: "all",
    facts: facts({ unshown_cards: [{ text: "x", start: 1, end: 1, why: "no_time", clock: "plan" }] }) } });
  assert.equal(cardCaptionNote(missing).title, "Captions missing");
});

test("the editor never shows an unreadable report or schema as the silence of 'verified'", () => {
  assert.equal(editorCaptionNote(null).title, "Caption check unavailable");
  assert.equal(editorCaptionNote({ ...view(), schema: "caption_display_v9" }).title, "Caption check unavailable");
  assert.equal(editorCaptionNote(view()), null, "verified says nothing");
});

test("a missing report file is named, only when the server knows it", () => {
  const w = captionWarning(view({ level: "unknown",
    export: { state: "not_measured", current: true, why: "sidecar_missing" } }));
  assert.match(text(w), /not available \(its report file is missing\)/);
});

test("an exported row whose video file is gone is not verified, and says why", () => {
  const w = captionWarning(view({ level: "unverified",
    export: { state: "unreadable", current: true, why: "mp4_missing" } }));
  assert.equal(w.title, "Captions not verified");
  assert.match(text(w), /its video file is missing/);
});

test("the old 'not burned (no_card_representable)' text is not repeated under 'Captions missing'", () => {
  const nb = "Captions not burned (no_card_representable): no caption card kept any display time";
  const other = "Captions not burned (empty_plan): the stored plan has no cards";
  const missing = view({ level: "limited", export: { state: "limited", current: true, missing: "all",
    facts: facts() } });
  assert.deepEqual(visibleWarnings([nb, other, "Layout: kept"], missing), [other, "Layout: kept"]);
  // no structured note for it: the text stays
  assert.deepEqual(visibleWarnings([nb], view()), [nb]);
  assert.deepEqual(visibleWarnings([nb], null), [nb]);
  const some = view({ level: "limited", export: { state: "limited", current: true, missing: "some",
    facts: facts() } });
  assert.deepEqual(visibleWarnings([nb], some), [nb]);
  assert.deepEqual(visibleWarnings(null, missing), []);
});
