"use client";

import { useRef, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { THUMBNAIL_URL, VIDEO_URL } from "@/lib/api";
import {
  formatDuration, getScoreColor, getScoreGradient,
  CLIP_STATUS_LABELS, MOMENTUM_SCORE_LABELS, MOMENTUM_SCORE_COLORS,
} from "@/lib/constants";
import type { Clip } from "@/types";
import {
  Download, X, Check, Clock, Zap,
  BarChart3, Sparkles, Loader2, Scissors
} from "lucide-react";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

function getExportUrl(exportPath: string) {
  if (!exportPath) return "";
  const parts = exportPath.replace(/\\/g, "/").split("/exports/");
  return parts.length > 1 ? `${WORKER_URL}/exports/${parts[1]}` : "";
}

interface ClipCardProps {
  clip: Clip;
  projectId: string;
  rank?: number;
  videoPath?: string | null;
}

export function ClipCard({ clip, projectId, rank, videoPath }: ClipCardProps) {
  const queryClient = useQueryClient();
  const videoRef = useRef<HTMLVideoElement>(null);
  const isHovering = useRef(false);

  const videoSrc = VIDEO_URL(projectId, videoPath);

  const startPreview = useCallback(() => {
    isHovering.current = true;
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = clip.start_time;
    video.style.opacity = "1";
    video.play().catch(() => {});
  }, [clip.start_time]);

  const stopPreview = useCallback(() => {
    isHovering.current = false;
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.style.opacity = "0";
  }, []);

  const handleMouseEnter = startPreview;
  const handleMouseLeave = stopPreview;

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current;
    if (!video || !isHovering.current) return;
    if (video.currentTime >= clip.end_time) {
      video.currentTime = clip.start_time;
    }
  }, [clip.start_time, clip.end_time]);

  const exportMutation = useMutation({
    mutationFn: () => api.clips.export(clip.id),
    onSuccess: () => {
      toast.success("Export started");
      queryClient.invalidateQueries({ queryKey: ["clips", projectId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const rejectMutation = useMutation({
    mutationFn: () => api.clips.reject(clip.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["clips", projectId] });
    },
  });

  const scoreEntries = [
    { key: "hook_strength", value: clip.hook_strength },
    { key: "curiosity_score", value: clip.curiosity_score },
    { key: "emotional_intensity", value: clip.emotional_intensity },
    { key: "narrative_completeness", value: clip.narrative_completeness },
    { key: "caption_readability", value: clip.caption_readability },
  ];

  return (
    <Card className="group relative overflow-hidden border-border/30 bg-card/50 transition-all duration-300 hover:border-primary/20 hover:bg-card/70">
      {/* Thumbnail + Video Preview */}
      {clip.thumbnail_path && (
        <div
          className="relative aspect-[9/16] w-full overflow-hidden bg-black/70"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          onTouchStart={startPreview}
          onTouchEnd={stopPreview}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={THUMBNAIL_URL(clip.thumbnail_path)}
            alt={clip.title}
            className="h-full w-full object-cover"
          />
          <video
            ref={videoRef}
            src={videoSrc}
            muted
            playsInline
            preload="none"
            onTimeUpdate={handleTimeUpdate}
            className="absolute inset-0 h-full w-full object-cover opacity-0 transition-opacity duration-200"
          />
        </div>
      )}
      
      {/* Rank Badge */}
      {rank !== undefined && (
        <div className="absolute top-3 right-3 z-10 flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground shadow-lg">
          #{rank}
        </div>
      )}

      {/* Score Header */}
      <div className="relative overflow-hidden px-4 pt-4 pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            {/* Score Circle */}
            <div className={`relative flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${getScoreGradient(clip.momentum_score)} shadow-lg`}>
              <span className="text-lg font-black text-white">
                {Math.round(clip.momentum_score)}
              </span>
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <Sparkles className="h-3 w-3 text-primary" />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Overall Score
                </span>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {formatDuration(clip.start_time)} → {formatDuration(clip.end_time)}
                <span className="ml-1.5 text-foreground/60">
                  ({formatDuration(clip.duration)})
                </span>
              </p>
            </div>
          </div>

          {clip.status === "exported" && (
            <Badge variant="secondary" className="bg-emerald-500/15 text-emerald-400 text-[10px]">
              <Check className="mr-1 h-3 w-3" /> Exported
            </Badge>
          )}
          {clip.status === "exporting" && (
            <Badge variant="secondary" className="bg-blue-500/15 text-blue-400 text-[10px]">
              <Loader2 className="mr-1 h-3 w-3 animate-spin" /> Exporting
            </Badge>
          )}
        </div>
      </div>

      {/* Title & Transcript Preview */}
      <div className="px-4 pb-3">
        <h4 className="line-clamp-2 text-sm font-medium leading-snug pr-8">
          {clip.title}
        </h4>
        
        {clip.hook_text && (
          <div className="mt-2 rounded-md border border-yellow-500/30 bg-black/70 p-2">
            <span className="text-[9px] font-bold uppercase tracking-wider text-yellow-300/90">Hook</span>
            <p className="text-[11px] font-bold text-white mt-0.5 leading-tight">
              {clip.hook_text}
            </p>
          </div>
        )}

        <p className="mt-2 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
          {clip.transcript_text.slice(0, 150)}...
        </p>

        {clip.explanation && (
          <p className="mt-2 text-[10px] text-primary/80 font-medium italic">
            Why chosen: {clip.explanation}
          </p>
        )}
      </div>

      {/* Score Breakdown */}
      <div className="space-y-1.5 px-4 pb-3">
        {scoreEntries.map(({ key, value }) => (
          <div key={key} className="flex items-center gap-2 text-[10px]">
            <span className="w-20 text-muted-foreground">
              {MOMENTUM_SCORE_LABELS[key]}
            </span>
            <div className="flex-1 h-1 rounded-full bg-muted/40 overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${value}%`,
                  backgroundColor: MOMENTUM_SCORE_COLORS[key],
                }}
              />
            </div>
            <span className={`w-6 text-right font-mono ${getScoreColor(value)}`}>
              {Math.round(value)}
            </span>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1.5 border-t border-border/20 px-3 py-2.5">
        <Button
          size="sm"
          variant="secondary"
          className="h-7 flex-1 gap-1.5 text-xs bg-primary/20 text-primary hover:bg-primary/30"
          onClick={() => window.location.href = `/editor/${clip.id}`}
        >
          <Scissors className="h-3 w-3" /> Edit
        </Button>
        {clip.status === "exported" && clip.export_path ? (
          <a href={`/worker-api/exports/${clip.id}/download`}>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 gap-1.5 text-xs text-emerald-400 hover:text-emerald-300"
            >
              <Download className="h-3 w-3" />
            </Button>
          </a>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1.5 text-xs text-muted-foreground"
            onClick={() => exportMutation.mutate()}
            disabled={exportMutation.isPending || clip.status === "exporting"}
          >
            {exportMutation.isPending || clip.status === "exporting" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Download className="h-3 w-3" />
            )}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1.5 text-xs text-muted-foreground hover:text-destructive"
          onClick={() => rejectMutation.mutate()}
        >
          <X className="h-3 w-3" />
        </Button>
      </div>
    </Card>
  );
}
