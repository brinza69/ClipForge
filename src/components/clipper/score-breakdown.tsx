"use client";

// The 16 sub-scores behind a clip's rank score.
//
// This dialog exists so the number is never opaque. It shows every component
// that fed it, the sentence explaining the result, and which ranker produced
// it — and it states plainly that the score orders clips within one source and
// has not been validated against real performance data. Saying so is the
// honest position: no such validation has been run.

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SUB_SCORE_KEYS, SUB_SCORE_LABELS, type ClipperClip } from "@/types/clipper";

function barColor(value: number): string {
  if (value >= 70) return "bg-emerald-500";
  if (value >= 45) return "bg-amber-500";
  return "bg-rose-500";
}

export function ScoreBreakdown({
  clip,
  open,
  onOpenChange,
}: {
  clip: ClipperClip | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  if (!clip) return null;
  const subs = clip.sub_scores ?? {};

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base">
            Rank score {Math.round(clip.overall_score ?? 0)}
          </DialogTitle>
        </DialogHeader>

        {clip.score_reason && (
          <p className="rounded-lg border border-border/40 bg-muted/20 p-3 text-xs text-muted-foreground">
            {clip.score_reason}
          </p>
        )}

        <div className="space-y-2">
          {SUB_SCORE_KEYS.map((key) => {
            const value = Math.round(subs[key] ?? 0);
            return (
              <div key={key} className="space-y-1">
                <div className="flex justify-between text-[11px]">
                  <span className="text-muted-foreground">{SUB_SCORE_LABELS[key]}</span>
                  <span className="tabular-nums">{value}</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className={`h-full rounded-full ${barColor(value)}`}
                    style={{ width: `${Math.max(2, value)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        <p className="border-t border-border/40 pt-3 text-[11px] leading-relaxed text-muted-foreground">
          This score ranks clips <em>within this source</em>. It is a heuristic estimate, not a
          prediction of how a clip will perform, and it has not been validated against real
          performance data.
          {clip.ranker_version && (
            <>
              {" "}
              Produced by <code className="text-foreground/70">{clip.ranker_version}</code>.
            </>
          )}
        </p>
      </DialogContent>
    </Dialog>
  );
}
