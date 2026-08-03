"use client";

// Analysis progress.
//
// The step list is derived from the job's progress_message, not from client
// state: the backend writes one of PIPELINE_STAGES on every update, so a hard
// refresh (or opening the page elsewhere) reconstructs the same view. Nothing
// about "where we are" is remembered here.
//
// Live updates come from the EXISTING /worker-api/jobs/{id}/stream SSE
// endpoint, falling back to a 3s poll if EventSource errors — no new transport
// was added for this feature.

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { PIPELINE_STAGES, type ClipperJob } from "@/types/clipper";

function stageIndex(message: string): number {
  if (!message) return 0;
  const m = message.toLowerCase();
  // Last match wins: later stages are more specific, and "Downloading… 40%"
  // must not re-match an earlier prefix.
  let found = 0;
  PIPELINE_STAGES.forEach((stage, i) => {
    if (m.includes(stage.toLowerCase())) found = i;
  });
  return found;
}

export function AnalysisProgress({
  job,
  onFinished,
}: {
  job: ClipperJob;
  onFinished: () => void;
}) {
  const [live, setLive] = useState<ClipperJob>(job);
  const startedAt = useRef<number>(Date.now());
  const [, tick] = useState(0);

  // Keep the elapsed counter honest without re-fetching.
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => setLive(job), [job]);

  useEffect(() => {
    let poll: ReturnType<typeof setInterval> | null = null;
    let es: EventSource | null = null;

    const startPolling = () => {
      if (!poll) poll = setInterval(onFinished, 3000);
    };

    try {
      es = new EventSource(`/worker-api/jobs/${job.id}/stream`);
      es.onmessage = (evt) => {
        try {
          const next = JSON.parse(evt.data) as ClipperJob;
          setLive((prev) => ({ ...prev, ...next }));
          if (["done", "failed", "cancelled"].includes(next.status)) onFinished();
        } catch {
          onFinished();
        }
      };
      es.onerror = () => {
        es?.close();
        es = null;
        startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      es?.close();
      if (poll) clearInterval(poll);
    };
  }, [job.id, onFinished]);

  const cancel = async () => {
    await fetch(`/worker-api/jobs/${job.id}/cancel`, { method: "POST" });
    onFinished();
  };

  const current = stageIndex(live.progress_message);
  const pct = Math.round((live.progress ?? 0) * 100);
  const elapsed = Math.floor((Date.now() - startedAt.current) / 1000);

  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            {live.progress_message || "Working…"}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            You can leave this page — the analysis keeps running on the worker, and what you see
            here is read back from it.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void cancel()}>
          <X className="mr-1.5 h-3.5 w-3.5" /> Cancel
        </Button>
      </div>

      <div className="space-y-1.5">
        <Progress value={pct} />
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>{pct}%</span>
          <span>
            {Math.floor(elapsed / 60)}m {elapsed % 60}s elapsed
          </span>
        </div>
      </div>

      <ol className="space-y-1">
        {PIPELINE_STAGES.map((stage, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <li
              key={stage}
              className={`flex items-center gap-2 text-xs ${
                active
                  ? "font-medium text-foreground"
                  : done
                    ? "text-muted-foreground"
                    : "text-muted-foreground/40"
              }`}
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                {done ? (
                  <Check className="h-3.5 w-3.5 text-emerald-500" />
                ) : active ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                ) : (
                  <span className="h-1.5 w-1.5 rounded-full bg-current opacity-40" />
                )}
              </span>
              {stage}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
