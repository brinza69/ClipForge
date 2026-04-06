"use client";

import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, THUMBNAIL_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { ClipCard } from "@/components/clips/clip-card";
import {
  STATUS_LABELS, STATUS_COLORS, SOURCE_TYPE_LABELS,
  formatDuration, formatBytes, getScoreColor,
} from "@/lib/constants";
import { toast } from "sonner";
import { motion } from "framer-motion";
import {
  ArrowLeft, Clock, Download, FileText, Play, Scissors, 
  Settings, Video, Search, ChevronRight, Zap, RefreshCw, Trash2, 
  AlertCircle, Loader2, Monitor, HardDrive
} from "lucide-react";
import Link from "next/link";
import type { Project, Job } from "@/types";

export default function ProjectPage() {
  const params = useParams();
  const projectId = params.id as string;
  const queryClient = useQueryClient();

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
    refetchInterval: 3000,
  });

  const { data: clips } = useQuery({
    queryKey: ["clips", projectId],
    queryFn: () => api.clips.list(projectId),
    enabled: !!project && ["ready", "transcribed", "scored", "scoring"].includes(project.status),
    refetchInterval: 5000,
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.jobs.list({ project_id: projectId }),
    enabled: !!project,
    refetchInterval: (query: any) => {
      const data = query?.state?.data as Job[] | undefined;
      return data?.some((j: Job) => j.status === "running" || j.status === "queued" || (project && project.status === "scoring"))
        ? 1000
        : false;
    }
  });

  const { data: transcript } = useQuery({
    queryKey: ["transcript", projectId],
    queryFn: () => api.clips.transcript(projectId),
    enabled: !!project && !["pending", "fetching_metadata", "metadata_ready", "downloading"].includes(project.status),
    retry: false,
  });

  const actionMutation = useMutation({
    mutationFn: ({ action }: { action: string }) =>
      api.projects.action(projectId, action),
    onSuccess: (_, { action }) => {
      toast.success(`Action started: ${action}`);
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.projects.delete(projectId),
    onSuccess: () => {
      toast.success("Project deleted");
      window.location.href = "/";
    },
  });

  if (isLoading || !project) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  const activeJob = jobs?.find(
    (j: Job) => j.status === "running" || j.status === "queued",
  );
  const isProcessing = !!activeJob;
  const thumbnailSrc = project.thumbnail_path
    ? THUMBNAIL_URL(project.thumbnail_path)
    : project.thumbnail_url;

  const showMetadataPreview = ["metadata_ready", "pending", "fetching_metadata", "cancelled"].includes(project.status);
  const showClips = clips && clips.length > 0;

  const isYouTube = project.source_url?.includes("youtube.com") || project.source_url?.includes("youtu.be");
  const ytVideoId = isYouTube ? project.source_url?.match(/(?:v=|\/)([0-9A-Za-z_-]{11}).*/)?.[1] : null;

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link href="/" className="hover:text-foreground transition-colors">Dashboard</Link>
        <ChevronRight className="h-3.5 w-3.5" />
        <span className="text-foreground font-medium truncate max-w-sm">{project.title}</span>
      </div>

      {/* Project Header */}
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Thumbnail */}
        <div className="w-full lg:w-80 flex-shrink-0">
          <div className="relative aspect-video overflow-hidden rounded-xl border border-border/40 bg-muted/20">
            {ytVideoId ? (
              <iframe
                src={`https://www.youtube.com/embed/${ytVideoId}?modestbranding=1&rel=0`}
                className="h-full w-full"
                allowFullScreen
              />
            ) : thumbnailSrc ? (
              <img src={thumbnailSrc} alt={project.title} className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full items-center justify-center">
                <Monitor className="h-12 w-12 text-muted-foreground/20" />
              </div>
            )}
            {project.duration && !ytVideoId && (
              <div className="absolute bottom-2 right-2 rounded-md bg-black/70 px-2 py-0.5 text-xs font-medium text-white">
                {formatDuration(project.duration)}
              </div>
            )}
          </div>
        </div>

        {/* Info */}
        <div className="flex-1 space-y-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{project.title}</h1>
            {project.channel_name && (
              <p className="mt-1 text-sm text-muted-foreground">{project.channel_name}</p>
            )}
          </div>

          {/* Metadata chips */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="outline" className={STATUS_COLORS[project.status]}>
              {STATUS_LABELS[project.status]}
            </Badge>
            {project.source_type && (
              <Badge variant="secondary" className="text-[10px]">
                {SOURCE_TYPE_LABELS[project.source_type]}
              </Badge>
            )}
            {project.duration && (
              <span className="flex items-center gap-1 text-muted-foreground">
                <Clock className="h-3 w-3" /> {formatDuration(project.duration)}
              </span>
            )}
            {project.width && project.height && (
              <span className="flex items-center gap-1 text-muted-foreground">
                <Monitor className="h-3 w-3" /> {project.width}×{project.height}
              </span>
            )}
            {project.estimated_size && (
              <span className="flex items-center gap-1 text-muted-foreground">
                <HardDrive className="h-3 w-3" /> ~{formatBytes(project.estimated_size)}
              </span>
            )}
          </div>

          {/* Progress for active jobs or background status */}
          {(activeJob || ["transcribing", "scoring", "downloading"].includes(project.status)) && (
            <Card className="border-primary/20 bg-primary/5 p-4 mb-6">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                  {activeJob?.progress_message || `System is ${project.status}...`}
                </span>
                <span className="text-xs text-muted-foreground">
                  {activeJob ? `${Math.round(activeJob.progress * 100)}%` : "Processing..."}
                </span>
              </div>
              <Progress value={activeJob ? activeJob.progress * 100 : null} className="h-2" />
            </Card>
          )}

          {/* Action Buttons */}
          <div className="flex flex-wrap gap-2">
            {isProcessing && (
               <Button
                 variant="destructive"
                 disabled={actionMutation.isPending}
                 onClick={() => {
                   if (confirm("Are you sure you want to cancel the active process?")) {
                     actionMutation.mutate({ action: "cancel" });
                   }
                 }}
                 className="gap-2"
               >
                 <Trash2 className="h-4 w-4" /> Cancel Process
               </Button>
            )}
            
            {!isProcessing && showMetadataPreview && (
              <>
                <Button
                  onClick={() => actionMutation.mutate({ action: "download_process" })}
                  disabled={isProcessing}
                  className="gap-2 bg-primary shadow-lg shadow-primary/20"
                >
                  <Zap className="h-4 w-4" /> Download & Process
                </Button>
                <Button
                  variant="outline"
                  onClick={() => actionMutation.mutate({ action: "download_only" })}
                  disabled={isProcessing}
                  className="gap-2"
                >
                  <Download className="h-4 w-4" /> Download Only
                </Button>
                <Button
                  variant="outline"
                  onClick={() => actionMutation.mutate({ action: "audio_only" })}
                  disabled={isProcessing}
                  className="gap-2"
                >
                  <FileText className="h-4 w-4" /> Audio + Transcript Only
                </Button>
              </>
            )}
            {!isProcessing && project.status === "cancelled" && (
                <Button
                  onClick={() => actionMutation.mutate({ action: "download_process" })}
                  disabled={isProcessing}
                  className="gap-2 bg-primary shadow-lg shadow-primary/20"
                >
                  <Zap className="h-4 w-4" /> Restart & Process
                </Button>
            )}
            {project.status === "downloaded" && (
              <>
                <Button
                  onClick={() => actionMutation.mutate({ action: "transcribe" })}
                  disabled={isProcessing}
                  className="gap-2 bg-primary shadow-lg shadow-primary/20"
                >
                  <FileText className="h-4 w-4" /> Transcribe
                </Button>
                <Button
                  variant="outline"
                  onClick={() => actionMutation.mutate({ action: "download_process" })}
                  disabled={isProcessing}
                  className="gap-2"
                >
                  <Zap className="h-4 w-4" /> Full Reprocess
                </Button>
              </>
            )}
            {project.status === "transcribed" && (
              <Button
                onClick={() => actionMutation.mutate({ action: "score" })}
                disabled={isProcessing}
                className="gap-2"
              >
                <Scissors className="h-4 w-4" /> Find Clips
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                if (confirm("Delete this project and all its files?")) {
                  deleteMutation.mutate();
                }
              }}
              className="text-destructive hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      <Separator className="bg-border/30" />

      {/* Transcript Section */}
      {transcript && (
        <div>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <FileText className="h-5 w-5 text-primary" /> Transcript
          </h2>
          <Card className="max-h-64 overflow-y-auto border-border/30 bg-card/40 p-4">
            <div className="space-y-2 text-sm leading-relaxed text-foreground/80">
              {transcript.segments.slice(0, 100).map((seg: any, i: number) => (
                <p key={i}>
                  <span className="mr-2 text-[10px] font-mono text-primary/60">
                    {formatDuration(seg.start)}
                  </span>
                  {seg.text}
                </p>
              ))}
              {transcript.segments.length > 100 && (
                <p className="text-muted-foreground">
                  ... and {transcript.segments.length - 100} more segments
                </p>
              )}
            </div>
          </Card>
        </div>
      )}

      {/* Clip Candidates */}
      {showClips && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Scissors className="h-5 w-5 text-primary" /> Clip Candidates
            </h2>
            <span className="text-sm text-muted-foreground">
              {clips.filter((c: any) => c.status !== "rejected").length} clips
            </span>
          </div>

          <motion.div
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
            initial="hidden"
            animate="visible"
            variants={{
              hidden: {},
              visible: { transition: { staggerChildren: 0.05 } },
            }}
          >
            {clips
              .filter((c: any) => c.status !== "rejected")
              .slice(0, 10)
              .map((clip: any, index: number) => (
                <motion.div
                  key={clip.id}
                  variants={{
                    hidden: { opacity: 0, y: 12 },
                    visible: { opacity: 1, y: 0 },
                  }}
                >
                  <ClipCard clip={clip} projectId={projectId} rank={index + 1} videoPath={project.video_path} />
                </motion.div>
              ))}
          </motion.div>
        </div>
      )}

      {/* Failed state */}
      {project.status === "failed" && (
        <Card className="border-destructive/30 bg-destructive/5 p-6 text-center">
          <AlertCircle className="mx-auto h-12 w-12 text-destructive mb-3" />
          <h2 className="text-xl font-bold mb-2">Processing Failed</h2>
          <p className="text-muted-foreground mb-4">
            {jobs?.find((j: Job) => j.status === "failed")?.error || "An error occurred while processing this project."}
          </p>
          <div className="flex justify-center gap-3">
            <Button
              variant="outline"
              onClick={() => actionMutation.mutate({ action: "download_process" })}
              className="gap-2"
            >
              <Zap className="h-4 w-4" /> Retry Full Process
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
            >
              <Trash2 className="h-4 w-4 mr-2" /> Delete
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
