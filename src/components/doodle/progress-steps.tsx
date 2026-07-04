"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Loader2 } from "lucide-react";
import type { DoodleStoryboard } from "@/types/doodle";
import { formatDuration } from "@/components/doodle/constants";

interface Props {
  storyboard: DoodleStoryboard;
  estimatedFrames: number;
  jobProgress: number | null;   // 0-1, null when no job running
  jobMessage: string;
}

const STEP_DEFS = [
  "Topic", "Script", "Flow Prompts", "Kokoro Voice", "Images Uploaded", "Captions", "Render", "Export",
] as const;

function computeDoneSteps(sb: DoodleStoryboard, missingImages: number): boolean[] {
  const hasScenes = sb.scenes.length > 0;
  const scriptDone = sb.status !== "created" && sb.status !== "scripting" && hasScenes;
  const voiceDone = ["voice_ready", "rendering", "done"].includes(sb.status) || sb.total_audio_duration != null;
  const imagesDone = hasScenes && missingImages === 0;
  const renderDone = sb.status === "done" && !!sb.export_path;
  return [
    true,          // Topic — always "entered" once a project exists
    scriptDone,    // Script
    scriptDone,    // Flow Prompts (written alongside script)
    voiceDone,     // Kokoro Voice
    imagesDone,    // Images Uploaded
    voiceDone,     // Captions (srt written with voice)
    renderDone,    // Render
    renderDone,    // Export
  ];
}

export function ProgressSteps({ storyboard, estimatedFrames, jobProgress, jobMessage }: Props) {
  const missingImages = storyboard.missing_images?.length ?? 0;
  const done = computeDoneSteps(storyboard, missingImages);
  const activeIndex = done.findIndex((d) => !d);
  const isRunning = ["scripting", "voicing", "rendering"].includes(storyboard.status);

  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">Progress</div>
        <Badge variant="outline">{storyboard.status}</Badge>
      </div>

      <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
        {STEP_DEFS.map((label, i) => {
          const isDone = done[i];
          const isActive = i === activeIndex && isRunning;
          return (
            <div
              key={label}
              className={`rounded-md border p-2 text-center text-[11px] ${
                isDone ? "border-primary/40 bg-primary/5 text-primary"
                : isActive ? "border-amber-400/40 bg-amber-400/5 text-amber-500"
                : "border-border/40 text-muted-foreground"
              }`}
            >
              {label}
              {isDone && <CheckCircle2 className="h-3 w-3 mx-auto mt-0.5" />}
              {isActive && <Loader2 className="h-3 w-3 mx-auto mt-0.5 animate-spin" />}
            </div>
          );
        })}
      </div>

      {isRunning && jobProgress != null && (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span>{jobMessage || "Working…"}</span>
            <span>{Math.round(jobProgress * 100)}%</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
            <div className="h-full bg-primary transition-all" style={{ width: `${Math.round(jobProgress * 100)}%` }} />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px] text-muted-foreground pt-1">
        <div>Estimated frames <div className="text-foreground font-medium">{estimatedFrames}</div></div>
        <div>Missing images <div className={missingImages > 0 ? "text-amber-400 font-medium" : "text-foreground font-medium"}>{missingImages}</div></div>
        <div>Audio duration <div className="text-foreground font-medium">{formatDuration(storyboard.total_audio_duration)}</div></div>
        <div>Voice / Aspect <div className="text-foreground font-medium">{storyboard.settings.voice} · {storyboard.settings.aspect_ratio}</div></div>
      </div>
    </Card>
  );
}
