"use client";

import Link from "next/link";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  STATUS_LABELS, STATUS_COLORS, SOURCE_TYPE_LABELS, SOURCE_TYPE_COLORS,
  formatDuration, formatBytes,
} from "@/lib/constants";
import { THUMBNAIL_URL } from "@/lib/api";
import type { Project } from "@/types";
import {
  Clock, Film, Scissors, Download, HardDrive,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface ProjectCardProps {
  project: Project;
}

export function ProjectCard({ project }: ProjectCardProps) {
  const { data: jobs } = useQuery({
    queryKey: ["jobs", project.id],
    queryFn: () => api.jobs.list({ project_id: project.id }),
    refetchInterval:
      ["downloading", "transcribing", "scoring", "processing", "fetching_metadata"].includes(
        project.status,
      )
        ? 2000
        : false,
  });

  const activeJob = jobs?.find(
    (j) => j.status === "running" || j.status === "queued",
  );
  const isProcessing = !!activeJob;
  const thumbnailSrc = project.thumbnail_path
    ? THUMBNAIL_URL(project.thumbnail_path)
    : project.thumbnail_url;

  return (
    <Link href={`/projects/${project.id}`}>
      <Card className="group relative overflow-hidden border-border/40 bg-card/60 transition-all duration-300 hover:border-primary/30 hover:bg-card/80 hover:shadow-lg hover:shadow-primary/5">
        {/* Thumbnail */}
        <div className="relative aspect-video w-full overflow-hidden bg-muted/30">
          {thumbnailSrc ? (
            <img
              src={thumbnailSrc}
              alt={project.title}
              className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <Film className="h-10 w-10 text-muted-foreground/30" />
            </div>
          )}

          {/* Duration badge */}
          {project.duration && (
            <div className="absolute bottom-2 right-2 rounded-md bg-black/70 px-2 py-0.5 text-xs font-medium text-white backdrop-blur-sm">
              {formatDuration(project.duration)}
            </div>
          )}

          {/* Source type badge */}
          <div className="absolute left-2 top-2">
            <Badge
              variant="secondary"
              className={`text-[10px] font-semibold ${SOURCE_TYPE_COLORS[project.source_type] || ""}`}
            >
              {SOURCE_TYPE_LABELS[project.source_type] || project.source_type}
            </Badge>
          </div>

          {/* Processing overlay */}
          {isProcessing && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-[2px]">
              <div className="space-y-2 text-center">
                <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                <p className="text-xs font-medium text-white">
                  {activeJob.progress_message || STATUS_LABELS[project.status]}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Content */}
        <div className="space-y-3 p-4">
          <div>
            <h3 className="line-clamp-1 text-sm font-semibold transition-colors group-hover:text-primary">
              {project.title}
            </h3>
            {project.channel_name && (
              <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                {project.channel_name}
              </p>
            )}
          </div>

          {/* Progress bar for active jobs */}
          {isProcessing && activeJob && (
            <Progress
              value={activeJob.progress * 100}
              className="h-1.5"
            />
          )}

          {/* Meta row */}
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <Badge
              variant="outline"
              className={`text-[10px] ${STATUS_COLORS[project.status] || ""}`}
            >
              {STATUS_LABELS[project.status] || project.status}
            </Badge>

            <div className="flex items-center gap-3">
              {project.clip_count > 0 && (
                <span className="flex items-center gap-1">
                  <Scissors className="h-3 w-3" />
                  {project.clip_count}
                </span>
              )}
              {project.total_storage > 0 && (
                <span className="flex items-center gap-1">
                  <HardDrive className="h-3 w-3" />
                  {formatBytes(project.total_storage)}
                </span>
              )}
            </div>
          </div>
        </div>
      </Card>
    </Link>
  );
}
