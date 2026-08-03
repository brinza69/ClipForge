"use client";

// One ranked candidate.
//
// The score is labelled "Rank score" rather than anything predictive, and it is
// a button: clicking it opens the full sub-score breakdown. A number the user
// cannot interrogate is a number they cannot overrule.

import { useState } from "react";
import { Check, Download, Film, Loader2, Play, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  CLIPPER_API,
  CONTENT_TYPE_LABELS,
  LAYOUT_LABELS,
  formatTimecode,
  type ClipperClip,
} from "@/types/clipper";

function scoreTone(score: number): string {
  if (score >= 70) return "border-emerald-500/40 bg-emerald-500/10 text-emerald-400";
  if (score >= 45) return "border-amber-500/40 bg-amber-500/10 text-amber-400";
  return "border-rose-500/40 bg-rose-500/10 text-rose-400";
}

export function CandidateCard({
  clip,
  onAction,
  onShowScore,
}: {
  clip: ClipperClip;
  onAction: (clip: ClipperClip, action: "approve" | "reject" | "export" | "preview") => Promise<void>;
  onShowScore: (clip: ClipperClip) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (action: "approve" | "reject" | "export" | "preview") => {
    setBusy(action);
    try {
      await onAction(clip, action);
    } finally {
      setBusy(null);
    }
  };

  const score = Math.round(clip.overall_score ?? 0);
  const previewUrl = clip.preview_path
    ? `${CLIPPER_API}/clips/${clip.id}/preview-file`
    : null;

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {clip.rank_position != null && (
              <span className="text-[11px] font-semibold text-muted-foreground">
                #{clip.rank_position}
              </span>
            )}
            <h3 className="truncate text-sm font-medium">{clip.title}</h3>
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {formatTimecode(clip.start_time)} – {formatTimecode(clip.end_time)} ·{" "}
            {clip.duration.toFixed(1)}s
          </p>
        </div>
        <button
          type="button"
          onClick={() => onShowScore(clip)}
          title="See the 16 sub-scores behind this"
          className={`shrink-0 rounded-lg border px-2.5 py-1 text-sm font-bold tabular-nums transition-opacity hover:opacity-80 ${scoreTone(score)}`}
        >
          {score}
        </button>
      </div>

      {previewUrl ? (
        // No <track>: captions are burned into the rendered pixels, so a
        // sidecar text track would duplicate them.
        <video src={previewUrl} controls className="aspect-[9/16] w-full rounded-lg bg-black" />
      ) : (
        <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg border border-dashed border-border/40 bg-muted/10">
          <div className="text-center">
            <Film className="mx-auto h-6 w-6 text-muted-foreground/40" />
            <p className="mt-1 text-[11px] text-muted-foreground">No preview rendered yet</p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {clip.content_type && (
          <Badge variant="secondary" className="text-[10px]">
            {CONTENT_TYPE_LABELS[clip.content_type] ?? clip.content_type}
          </Badge>
        )}
        {/* Skipped when it would just repeat the content-type badge — a
            talking_head clip laid out as talking_head told the user nothing
            twice. */}
        {clip.layout_plan?.layout && clip.layout_plan.layout !== clip.content_type && (
          <Badge variant="secondary" className="text-[10px]">
            {LAYOUT_LABELS[clip.layout_plan.layout] ?? clip.layout_plan.layout}
          </Badge>
        )}
        {clip.status !== "candidate" && (
          <Badge
            className={`text-[10px] ${
              clip.status === "approved" || clip.status === "exported"
                ? "bg-emerald-500/15 text-emerald-400"
                : clip.status === "rejected"
                  ? "bg-rose-500/15 text-rose-400"
                  : ""
            }`}
          >
            {clip.status}
          </Badge>
        )}
      </div>

      {clip.transcript_text && (
        <p className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
          {clip.transcript_text}
        </p>
      )}

      {(clip.warnings?.length ?? 0) > 0 && (
        <div className="space-y-1">
          {clip.warnings!.slice(0, 2).map((w) => (
            <p key={w} className="text-[10px] text-amber-400/90">
              ⚠ {w}
            </p>
          ))}
        </div>
      )}

      <div className="mt-auto flex flex-wrap gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          className="flex-1"
          onClick={() => void run("preview")}
          disabled={busy !== null}
        >
          {busy === "preview" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <>
              <Play className="mr-1 h-3.5 w-3.5" /> Preview
            </>
          )}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void run("approve")}
          disabled={busy !== null}
          title="Approve"
        >
          <Check className="h-3.5 w-3.5 text-emerald-400" />
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void run("reject")}
          disabled={busy !== null}
          title="Reject"
        >
          <X className="h-3.5 w-3.5 text-rose-400" />
        </Button>
        <Button
          size="sm"
          onClick={() => void run("export")}
          disabled={busy !== null}
          title="Render the final 1080x1920 file"
        >
          {busy === "export" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
    </Card>
  );
}
