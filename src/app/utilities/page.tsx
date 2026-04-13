"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Download, Eraser, Loader2, ArrowRight, CheckCircle2,
  Play, Film, ImageOff, Type,
} from "lucide-react";
import { toast } from "sonner";

export default function UtilitiesPage() {
  const router = useRouter();
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadTitle, setDownloadTitle] = useState("");

  const downloadMutation = useMutation({
    mutationFn: () => api.utilities.download(downloadUrl.trim(), downloadTitle.trim() || undefined),
    onSuccess: (data) => {
      toast.success("Download started!", {
        description: `"${data.title}" is being processed.`,
        action: {
          label: "View Project",
          onClick: () => router.push(`/projects/${data.project_id}`),
        },
      });
      setDownloadUrl("");
      setDownloadTitle("");
    },
    onError: (err: Error) => {
      toast.error("Download failed", { description: err.message });
    },
  });

  const handleDownload = () => {
    if (!downloadUrl.trim()) {
      toast.error("Paste a URL first");
      return;
    }
    downloadMutation.mutate();
  };

  return (
    <div className="space-y-8 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold">Utilities</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Quick tools for downloading and processing video content.
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">

        {/* ── Shorts / TikTok Downloader ── */}
        <Card className="p-6 space-y-5 border-border/40 bg-card/60">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 shrink-0">
              <Download className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h2 className="font-semibold">Shorts / TikTok Downloader</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Paste any YouTube Shorts, TikTok, or Instagram Reel URL to download and process it automatically.
              </p>
            </div>
          </div>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Video URL</Label>
              <Input
                value={downloadUrl}
                onChange={(e) => setDownloadUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleDownload()}
                placeholder="https://youtube.com/shorts/..."
                className="bg-background"
                disabled={downloadMutation.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Title (optional)</Label>
              <Input
                value={downloadTitle}
                onChange={(e) => setDownloadTitle(e.target.value)}
                placeholder="Custom project name..."
                className="bg-background"
                disabled={downloadMutation.isPending}
              />
            </div>
          </div>

          <Button
            className="w-full gap-2"
            onClick={handleDownload}
            disabled={downloadMutation.isPending || !downloadUrl.trim()}
          >
            {downloadMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Processing…
              </>
            ) : downloadMutation.isSuccess ? (
              <>
                <CheckCircle2 className="h-4 w-4 text-emerald-400" /> Started
              </>
            ) : (
              <>
                <Download className="h-4 w-4" /> Download & Process
                <ArrowRight className="h-4 w-4 ml-auto" />
              </>
            )}
          </Button>

          <div className="rounded-lg border border-border/30 bg-muted/20 p-3 space-y-1.5">
            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Supported platforms</div>
            <div className="grid grid-cols-2 gap-1 text-xs text-muted-foreground">
              <div className="flex items-center gap-1.5"><Play className="h-3.5 w-3.5 text-red-500" /> YouTube Shorts</div>
              <div className="flex items-center gap-1.5"><Film className="h-3.5 w-3.5 text-pink-500" /> TikTok</div>
              <div className="flex items-center gap-1.5"><Film className="h-3.5 w-3.5 text-purple-500" /> Instagram Reels</div>
              <div className="flex items-center gap-1.5"><Film className="h-3.5 w-3.5 text-blue-400" /> Twitter/X</div>
            </div>
          </div>
        </Card>

        {/* ── Caption / Text / Logo Eraser ── */}
        <Card className="p-6 space-y-5 border-border/40 bg-card/60 relative overflow-hidden">
          {/* Coming soon badge */}
          <div className="absolute top-4 right-4 px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-400 text-[10px] font-semibold">
            Coming Soon
          </div>

          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10 shrink-0">
              <Eraser className="h-5 w-5 text-amber-400" />
            </div>
            <div>
              <h2 className="font-semibold">Caption / Text / Logo Eraser</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Remove hardcoded captions, logos, or watermarks from any video using AI inpainting.
              </p>
            </div>
          </div>

          {/* File drop area — disabled */}
          <div className="rounded-xl border-2 border-dashed border-border/30 bg-muted/10 p-8 flex flex-col items-center justify-center gap-3 opacity-50 cursor-not-allowed select-none">
            <Film className="h-8 w-8 text-muted-foreground" />
            <div className="text-sm text-muted-foreground text-center">
              Drop a video file here<br />
              <span className="text-xs">MP4, MOV, WebM supported</span>
            </div>
          </div>

          {/* Planned features */}
          <div className="space-y-2">
            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Planned features</div>
            <div className="space-y-1.5 text-xs text-muted-foreground">
              <div className="flex items-center gap-2">
                <Type className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                Auto-detect and remove burnt-in subtitles
              </div>
              <div className="flex items-center gap-2">
                <ImageOff className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                Watermark and logo masking with AI inpainting
              </div>
              <div className="flex items-center gap-2">
                <Eraser className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                Region-select eraser with temporal smoothing
              </div>
            </div>
          </div>
        </Card>

      </div>
    </div>
  );
}
