"use client";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertCircle, Download } from "lucide-react";
import { formatDuration } from "@/components/doodle/constants";
import type { DoodleStoryboard } from "@/types/doodle";

interface Props {
  projectId: string;
  storyboard: DoodleStoryboard;
  onRetry: () => void;
  retrying: boolean;
}

export function ExportPanel({ projectId, storyboard, onRetry, retrying }: Props) {
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

  if (storyboard.status === "done" && storyboard.export_path) {
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

  return null;
}
