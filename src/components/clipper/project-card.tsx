"use client";

// One clip project on the AI Stream Clipper landing page.
//
// The status label/colour maps live in this file rather than a shared
// constants module: the card is the only place a project *summary* status is
// rendered, and a third file for eleven strings is the speculative abstraction
// CLAUDE.md rule 8 rules out.

import Link from "next/link";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Trash2, Film, CheckCircle2, Upload, Loader2 } from "lucide-react";
import {
  CONTENT_TYPE_LABELS,
  formatTimecode,
  type ClipperProjectSummary,
  type ProjectStatus,
} from "@/types/clipper";

const STATUS_LABELS: Record<ProjectStatus, string> = {
  pending: "Queued",
  fetching_metadata: "Reading metadata",
  metadata_ready: "Metadata ready",
  downloading: "Downloading",
  downloaded: "Downloaded",
  transcribing: "Transcribing",
  transcribed: "Transcribed",
  scoring: "Scoring",
  ready: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATUS_CLASS: Record<ProjectStatus, string> = {
  pending: "border-border/60 text-muted-foreground",
  fetching_metadata: "border-primary/40 text-primary",
  metadata_ready: "border-primary/40 text-primary",
  downloading: "border-primary/40 text-primary",
  downloaded: "border-primary/40 text-primary",
  transcribing: "border-primary/40 text-primary",
  transcribed: "border-primary/40 text-primary",
  scoring: "border-primary/40 text-primary",
  ready: "border-emerald-500/40 text-emerald-400",
  failed: "border-destructive/40 text-destructive",
  cancelled: "border-border/60 text-muted-foreground",
};

/** Anything not terminal is still moving — the card shows a spinner for it. */
const IN_FLIGHT: ReadonlySet<ProjectStatus> = new Set<ProjectStatus>([
  "pending",
  "fetching_metadata",
  "metadata_ready",
  "downloading",
  "downloaded",
  "transcribing",
  "transcribed",
  "scoring",
]);

interface Props {
  project: ClipperProjectSummary;
  onDelete: (id: string) => void;
}

export function ClipperProjectCard({ project, onDelete }: Props) {
  const href = `/ai-stream-clipper/${project.id}`;
  const running = IN_FLIGHT.has(project.status);
  const dims =
    project.width && project.height ? `${project.width}×${project.height}` : null;

  return (
    <Card className="gap-0 border-border/40 bg-card/60 p-0 transition-colors hover:ring-primary/40">
      <Link href={href} className="block">
        <div className="relative aspect-video w-full bg-muted/40">
          {project.thumbnail_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- remote VOD
            // thumbnails from arbitrary hosts; next/image buys nothing here.
            <img
              src={project.thumbnail_url}
              alt=""
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <Film className="h-6 w-6 text-muted-foreground/50" />
            </div>
          )}
          {project.duration != null && project.duration > 0 && (
            <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-medium text-white">
              {formatTimecode(project.duration)}
            </span>
          )}
        </div>
      </Link>

      <div className="space-y-2.5 p-3">
        <div className="flex items-start justify-between gap-2">
          <Link
            href={href}
            className="block min-w-0 truncate text-sm font-semibold transition-colors hover:text-primary"
          >
            {project.title || "Untitled clip project"}
          </Link>
          <Badge variant="outline" className={STATUS_CLASS[project.status]}>
            {running && <Loader2 className="h-3 w-3 animate-spin" />}
            {STATUS_LABELS[project.status] ?? project.status}
          </Badge>
        </div>

        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-muted-foreground">
          {project.channel_name && (
            <span className="max-w-[45%] truncate">{project.channel_name}</span>
          )}
          {dims && <span>{dims}</span>}
          {project.fps != null && <span>{Math.round(project.fps)} fps</span>}
          {project.content_type && (
            <span className="text-foreground/70">
              {CONTENT_TYPE_LABELS[project.content_type]}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <Film className="h-3 w-3" /> {project.clip_count} candidates
          </span>
          {project.approved_count > 0 && (
            <span className="flex items-center gap-1 text-emerald-400">
              <CheckCircle2 className="h-3 w-3" /> {project.approved_count}
            </span>
          )}
          {project.exported_count > 0 && (
            <span className="flex items-center gap-1 text-primary">
              <Upload className="h-3 w-3" /> {project.exported_count}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Link href={href} className="flex-1">
            <Button variant="outline" size="sm" className="w-full">
              {project.status === "ready" ? "Review clips" : "Open"}
            </Button>
          </Link>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => onDelete(project.id)}
            title="Delete project and all its files"
          >
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
          </Button>
        </div>
      </div>
    </Card>
  );
}
