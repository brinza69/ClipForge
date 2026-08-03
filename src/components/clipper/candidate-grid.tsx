"use client";

// The ranked review board.
//
// Sorting and filtering are client-side because the whole candidate set for a
// project is small (tens of rows) and already in memory — a round trip per
// sort would only add latency.
//
// Near-duplicates are hidden by default rather than deleted: dedupe keeps them
// as `is_alternative`, and "Show N near-duplicates" reveals them. The user can
// always see what was filtered out on their behalf.

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { CandidateCard } from "@/components/clipper/candidate-card";
import { ScoreBreakdown } from "@/components/clipper/score-breakdown";
import { Button } from "@/components/ui/button";
import { errorDescription, readApiError } from "@/lib/api-error";
import { CLIPPER_API, type ClipperClip, type ClipperProject } from "@/types/clipper";

type SortKey = "score" | "timeline" | "duration";

const SORTS: { id: SortKey; label: string }[] = [
  { id: "score", label: "Best first" },
  { id: "timeline", label: "In order" },
  { id: "duration", label: "Longest" },
];

const FILTERS = [
  { id: "all", label: "All" },
  { id: "candidate", label: "Unreviewed" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "exported", label: "Exported" },
] as const;

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors ${
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border/50 text-muted-foreground hover:bg-accent hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

export function CandidateGrid({
  project,
  clips,
  onRefresh,
}: {
  project: ClipperProject;
  clips: ClipperClip[];
  onRefresh: () => void;
}) {
  const [sort, setSort] = useState<SortKey>("score");
  const [filter, setFilter] = useState<string>("all");
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [scoreClip, setScoreClip] = useState<ClipperClip | null>(null);
  const [bulk, setBulk] = useState(false);

  const winners = clips.filter((c) => !c.is_alternative);
  const alternatives = clips.filter((c) => c.is_alternative);

  const shown = (showAlternatives ? clips : winners)
    .filter((c) => (filter === "all" ? true : c.status === filter))
    .sort((a, b) => {
      if (sort === "timeline") return a.start_time - b.start_time;
      if (sort === "duration") return b.duration - a.duration;
      return (b.overall_score ?? 0) - (a.overall_score ?? 0);
    });

  const act = async (
    clip: ClipperClip,
    action: "approve" | "reject" | "export" | "preview",
  ) => {
    const url =
      action === "preview"
        ? `${CLIPPER_API}/clips/${clip.id}/regenerate`
        : `${CLIPPER_API}/clips/${clip.id}/${action}`;
    const body = action === "preview" ? JSON.stringify({ what: "preview" }) : undefined;
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body,
      });
      if (!r.ok) {
        const e = await readApiError(r, `Could not ${action} that clip`);
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      if (action === "export") toast.success("Export queued — it renders in the background.");
      if (action === "preview") toast.success("Preview render queued.");
      onRefresh();
    } catch (err) {
      toast.error(`Could not ${action} that clip`, {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const bulkAct = async (action: "approve" | "export") => {
    const targets = shown.filter((c) => c.status === "candidate" || action === "export");
    if (targets.length === 0) return;
    setBulk(true);
    try {
      for (const clip of targets) {
        // Serial on purpose: an export enqueues a full 1080x1920 encode, and
        // firing twenty at once just fills the queue with work the user may
        // change their mind about.
        await act(clip, action);
      }
      toast.success(`${action === "approve" ? "Approved" : "Queued"} ${targets.length} clips`);
    } finally {
      setBulk(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {SORTS.map((s) => (
          <Chip key={s.id} active={sort === s.id} onClick={() => setSort(s.id)}>
            {s.label}
          </Chip>
        ))}
        <span className="mx-1 h-4 w-px bg-border/50" />
        {FILTERS.map((f) => (
          <Chip key={f.id} active={filter === f.id} onClick={() => setFilter(f.id)}>
            {f.label}
          </Chip>
        ))}

        {alternatives.length > 0 && (
          <>
            <span className="mx-1 h-4 w-px bg-border/50" />
            <button
              type="button"
              onClick={() => setShowAlternatives((v) => !v)}
              className="text-[11px] font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              {showAlternatives ? "Hide" : "Show"} {alternatives.length} near-duplicate
              {alternatives.length === 1 ? "" : "s"}
            </button>
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            disabled={bulk}
            onClick={() => void bulkAct("approve")}
          >
            Approve all shown
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onRefresh}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Refresh
          </Button>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        {shown.length} of {winners.length} clips
        {project.duration ? ` from a ${Math.round(project.duration / 60)} min source` : ""}. Scores
        rank clips within this source — click one to see what produced it.
      </p>

      {shown.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No clips match that filter.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((clip) => (
            <CandidateCard
              key={clip.id}
              clip={clip}
              onAction={act}
              onShowScore={setScoreClip}
            />
          ))}
        </div>
      )}

      <ScoreBreakdown
        clip={scoreClip}
        open={scoreClip !== null}
        onOpenChange={(v) => !v && setScoreClip(null)}
      />
    </div>
  );
}
