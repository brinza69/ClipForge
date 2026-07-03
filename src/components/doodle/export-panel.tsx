"use client";

// Shows every rendered subtitle-style output (storyboard.renders) with its
// own player / download / error state, falling back to the legacy single
// export_path for projects rendered before per-style outputs existed.

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertCircle, Download } from "lucide-react";
import { formatDuration, SUBTITLE_MODE_LABELS } from "@/components/doodle/constants";
import type { DoodleStoryboard } from "@/types/doodle";

interface Props {
  projectId: string;
  storyboard: DoodleStoryboard;
  onRetry: () => void;
  retrying: boolean;
}

export function ExportPanel({ projectId, storyboard, onRetry, retrying }: Props) {
  const renders = Object.entries(storyboard.renders || {});
  const doneRenders = renders.filter(([, r]) => r.status === "done" && r.path);
  const failedRenders = renders.filter(([, r]) => r.status === "failed");

  if (storyboard.status === "failed") {
    return (
      <Card className="p-4 space-y-3 border-destructive/40">
        <div className="flex items-start gap-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>{storyboard.error || "The pipeline failed."}</div>
        </div>
        <Button variant="outline" onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Retry"}
        </Button>
      </Card>
    );
  }

  // Legacy single-output projects (rendered before per-style outputs).
  if (doneRenders.length === 0 && storyboard.status === "done" && storyboard.export_path) {
    const src = `/worker-doodle/${projectId}/${storyboard.export_path}`;
    return (
      <Card className="p-4 space-y-3 border-border/40">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">Final Video</div>
        <video controls src={src} className="w-full rounded-md border border-border/40 max-h-[480px]" />
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">
            Duration {formatDuration(storyboard.total_audio_duration)}
          </span>
          <a href={src} download>
            <Button variant="outline" size="sm"><Download className="h-3.5 w-3.5" /> Download</Button>
          </a>
        </div>
      </Card>
    );
  }

  if (doneRenders.length === 0 && failedRenders.length === 0) return null;

  return (
    <Card className="p-4 space-y-4 border-border/40">
      <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
        Rendered Videos
      </div>

      {failedRenders.map(([mode, r]) => (
        <div key={mode} className="flex items-start gap-2 text-xs text-destructive border border-destructive/30 rounded-md px-3 py-2">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <div>
            <b>{SUBTITLE_MODE_LABELS[mode] || mode}</b> render failed: {r.error || "unknown error"}
          </div>
        </div>
      ))}

      <div className="grid gap-4 md:grid-cols-2">
        {doneRenders.map(([mode, r]) => {
          const src = `/worker-doodle/${projectId}/${r.path}`;
          return (
            <div key={mode} className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium">{SUBTITLE_MODE_LABELS[mode] || mode}</span>
                <a href={src} download>
                  <Button variant="outline" size="sm"><Download className="h-3.5 w-3.5" /> Download</Button>
                </a>
              </div>
              <video controls src={src} className="w-full rounded-md border border-border/40 max-h-[360px]" />
              <p className="text-[11px] text-muted-foreground">
                {r.path} · Duration {formatDuration(storyboard.total_audio_duration)}
              </p>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
