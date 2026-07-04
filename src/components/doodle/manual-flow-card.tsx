"use client";

import { Card } from "@/components/ui/card";
import { Info, Copy, Download, MousePointerClick, ImagePlus } from "lucide-react";
import { MANUAL_FLOW_WARNING } from "@/components/doodle/constants";

/** Persistent banner shown at the top of the /doodle tab home. */
export function ManualFlowBanner() {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">
      <Info className="h-4 w-4 mt-0.5 shrink-0 text-primary" />
      <p className="text-primary/90">{MANUAL_FLOW_WARNING}</p>
    </div>
  );
}

const STEPS = [
  { icon: Copy, text: "Copy a scene's image prompt (or export all as CSV/JSON)." },
  { icon: MousePointerClick, text: "Paste it into Google Flow and generate the image." },
  { icon: Download, text: "Download the generated image from Flow." },
  { icon: ImagePlus, text: "Drag & drop (or click to browse) into the matching scene slot below." },
  { icon: Info, text: "Once every scene has an image, hit Render Video." },
];

/** Numbered instructions card shown on the project detail page. */
export function ManualFlowInstructionsCard() {
  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
        Manual Flow Mode — how it works
      </div>
      <ol className="space-y-2">
        {STEPS.map((s, i) => (
          <li key={i} className="flex items-start gap-2.5 text-sm">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">
              {i + 1}
            </span>
            <span className="flex-1">{s.text}</span>
            <s.icon className="h-3.5 w-3.5 mt-0.5 text-muted-foreground shrink-0" />
          </li>
        ))}
      </ol>
    </Card>
  );
}
