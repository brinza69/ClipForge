"use client";

import Link from "next/link";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Eraser, AudioLines, Wand2, ExternalLink } from "lucide-react";

export default function UtilitiesPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold">Utilities</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Standalone tools for cleaning up your videos.
        </p>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Caption / Logo Eraser */}
        <Link href="/utilities/caption-eraser" className="block group">
          <Card className="p-6 space-y-5 border-border/40 bg-card/60 hover:border-amber-500/40 hover:bg-amber-500/[0.03] transition-colors h-full flex flex-col">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10 shrink-0 group-hover:bg-amber-500/20 transition-colors">
                <Eraser className="h-5 w-5 text-amber-400" />
              </div>
              <div>
                <h2 className="font-semibold">Caption / Logo Eraser</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Seamlessly remove burnt-in captions, logos, or watermarks. LaMa GPU + auto-detect.
                </p>
              </div>
            </div>
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
              <div className="flex items-center gap-2 text-xs text-amber-300">
                <Wand2 className="h-3.5 w-3.5" />
                <span className="font-semibold">LaMa GPU neural inpainting</span>
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed mt-1">
                Auto-detect mode finds captions automatically — even when they move mid-clip.
              </p>
            </div>
            <div className="flex-1" />
            <Button variant="outline" className="w-full gap-2 border-amber-500/30 text-amber-300 hover:bg-amber-500/10 hover:text-amber-200 group-hover:border-amber-500/50">
              Open Caption Eraser <ExternalLink className="h-3.5 w-3.5 ml-auto" />
            </Button>
          </Card>
        </Link>

        {/* Silence Remover */}
        <Link href="/silence" className="block group">
          <Card className="p-6 space-y-5 border-border/40 bg-card/60 hover:border-sky-500/40 hover:bg-sky-500/[0.03] transition-colors h-full flex flex-col">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-500/10 shrink-0 group-hover:bg-sky-500/20 transition-colors">
                <AudioLines className="h-5 w-5 text-sky-400" />
              </div>
              <div>
                <h2 className="font-semibold">Silence Remover</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Cut dead air from a video or audio file to tighten the pacing.
                </p>
              </div>
            </div>
            <div className="flex-1" />
            <Button variant="outline" className="w-full gap-2 border-sky-500/30 text-sky-300 hover:bg-sky-500/10 hover:text-sky-200 group-hover:border-sky-500/50">
              Open Silence Remover <ExternalLink className="h-3.5 w-3.5 ml-auto" />
            </Button>
          </Card>
        </Link>
      </div>
    </div>
  );
}
