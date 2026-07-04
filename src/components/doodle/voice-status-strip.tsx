"use client";

// Compact readiness strip: image/voice counts + a reassurance line when
// images are uploaded but the voiceover hasn't been generated yet.

import { ImageIcon, Mic, ShieldCheck, TimerOff } from "lucide-react";
import type { DoodleStoryboard } from "@/types/doodle";

export function VoiceStatusStrip({ storyboard }: { storyboard: DoodleStoryboard }) {
  const total = storyboard.scenes.length;
  if (total === 0) return null;

  const imagesUploaded = storyboard.scenes.filter((s) => !!s.image_path).length;
  const voiced = storyboard.scenes.filter((s) => !!s.audio_duration).length;
  const missingAudio = total - voiced;
  const needsVoice = imagesUploaded > 0 && missingAudio > 0;

  return (
    <div className="rounded-lg border border-border/40 bg-card/50 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <ImageIcon className="h-3.5 w-3.5" />
          Images uploaded: <b className="text-foreground">{imagesUploaded} / {total}</b>
        </span>
        <span className="flex items-center gap-1.5">
          <Mic className="h-3.5 w-3.5" />
          Voice generated: <b className="text-foreground">{voiced} / {total}</b>
        </span>
        <span className="flex items-center gap-1.5">
          <TimerOff className="h-3.5 w-3.5" />
          Missing audio_duration:{" "}
          <b className={missingAudio > 0 ? "text-amber-400" : "text-foreground"}>{missingAudio}</b>
        </span>
      </div>
      {needsVoice && (
        <p className="mt-1.5 flex items-center gap-1.5 text-emerald-400">
          <ShieldCheck className="h-3.5 w-3.5" />
          Images are safe. Generate voiceover before rendering.
        </p>
      )}
    </div>
  );
}
