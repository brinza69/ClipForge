"use client";

// The caption warning on screen (BURST, codex-verdict-next-20 §2). The card shows the title and keeps
// every line in its tooltip; the editor shows them all. Nothing is drawn when the server has nothing
// to say, and a missing or old report is never drawn as a pass (see caption-display.ts).

import type { CaptionDisplayView } from "@/types/clipper-caption-display";
import { cardCaptionNote, editorCaptionNote } from "./caption-display";

export function CaptionDisplayNote({
  view,
  compact = false,
}: {
  view?: CaptionDisplayView | null;
  compact?: boolean;
}) {
  const w = compact ? cardCaptionNote(view) : editorCaptionNote(view);
  if (!w) return null;
  const color = w.tone === "warn" ? "text-amber-400/90" : "text-sky-300/80";
  const mark = w.tone === "warn" ? "⚠" : "ⓘ";
  if (compact) {
    return (
      <p
        className={`text-[10px] ${color}`}
        title={[w.title, ...w.lines].join("\n")}
        data-testid="caption-display-note"
      >
        {mark} {w.title}
      </p>
    );
  }
  return (
    <div
      className={`space-y-0.5 rounded border border-border/50 px-2 py-1.5 text-[11px] ${color}`}
      data-testid="caption-display-note"
    >
      <p className="font-medium">
        {mark} {w.title}
      </p>
      {w.lines.map((line, i) => (
        <p key={i} className={line.startsWith("•") ? "pl-2" : ""}>
          {line}
        </p>
      ))}
    </div>
  );
}
