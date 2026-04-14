"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Download, Eraser, Loader2, ArrowRight, CheckCircle2,
  Play, Film, Clock, Sparkles, ExternalLink, Wand2,
} from "lucide-react";
import { toast } from "sonner";
import type { Project } from "@/types";

function formatDurationMMSS(seconds?: number | null) {
  if (!seconds || seconds < 0) return "--:--";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function relativeTime(iso?: string | null) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function UtilitiesPage() {
  const router = useRouter();
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadTitle, setDownloadTitle] = useState("");

  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: api.projects.list,
    refetchInterval: 5000,
  });

  const lastDownloaded: Project | null = (() => {
    if (!projects || projects.length === 0) return null;
    const withVideo = projects.filter(
      (p: any) => p.video_path || p.status === "downloaded" || p.status === "transcribed" || p.status === "ready"
    );
    return (withVideo[0] || projects[0]) as Project;
  })();

  const downloadMutation = useMutation({
    mutationFn: () => api.utilities.download(downloadUrl.trim(), downloadTitle.trim() || undefined),
    onSuccess: (data) => {
      toast.success("Download started!", {
        description: `"${data.title}" is being processed.`,
        action: { label: "Open project", onClick: () => router.push(`/projects/${data.project_id}`) },
      });
      setDownloadUrl("");
      setDownloadTitle("");
    },
    onError: (err: Error) => toast.error("Download failed", { description: err.message }),
  });

  const handleDownload = () => {
    if (!downloadUrl.trim()) { toast.error("Paste a URL first"); return; }
    downloadMutation.mutate();
  };

  return (
    <div className="space-y-8 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold">Utilities</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Quick tools for downloading and processing video content.
        </p>
      </div>

      {/* Last downloaded video hero */}
      {lastDownloaded && (
        <Link
          href={`/projects/${lastDownloaded.id}`}
          className="block group"
        >
          <Card className="p-4 border-border/40 bg-gradient-to-br from-card/80 via-card/60 to-primary/5 hover:border-primary/30 transition-colors">
            <div className="flex items-center gap-4">
              <div className="relative w-28 aspect-video rounded-lg overflow-hidden bg-black shrink-0 border border-border/20">
                {lastDownloaded.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={lastDownloaded.thumbnail_url}
                    alt={lastDownloaded.title}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <Film className="h-6 w-6 text-muted-foreground/40" />
                  </div>
                )}
                <div className="absolute inset-0 bg-black/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                  <Play className="h-6 w-6 text-white fill-white" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground uppercase tracking-wider">
                  <Clock className="h-3 w-3" />
                  <span>Last downloaded</span>
                  <span>·</span>
                  <span>{relativeTime(lastDownloaded.created_at)}</span>
                </div>
                <h3 className="text-sm font-semibold mt-1 truncate group-hover:text-primary transition-colors">
                  {lastDownloaded.title}
                </h3>
                <div className="flex items-center gap-3 mt-1.5 text-[11px] text-muted-foreground">
                  {lastDownloaded.channel_name && <span className="truncate max-w-[160px]">{lastDownloaded.channel_name}</span>}
                  {lastDownloaded.duration != null && <span>· {formatDurationMMSS(lastDownloaded.duration)}</span>}
                  {lastDownloaded.status && (
                    <span className="rounded bg-muted/30 px-1.5 py-0.5 text-[9px] uppercase tracking-wider">
                      {lastDownloaded.status}
                    </span>
                  )}
                </div>
              </div>
              <ArrowRight className="h-5 w-5 text-muted-foreground group-hover:text-primary group-hover:translate-x-0.5 transition-all shrink-0" />
            </div>
          </Card>
        </Link>
      )}

      {/* Two tool cards */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* Downloader */}
        <Card className="p-6 space-y-5 border-border/40 bg-card/60">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 shrink-0">
              <Download className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h2 className="font-semibold">Shorts / TikTok Downloader</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Paste any YouTube Shorts, TikTok, or Instagram Reel URL and process it automatically.
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
              <><Loader2 className="h-4 w-4 animate-spin" /> Processing…</>
            ) : downloadMutation.isSuccess ? (
              <><CheckCircle2 className="h-4 w-4 text-emerald-400" /> Started</>
            ) : (
              <><Download className="h-4 w-4" /> Download & Process <ArrowRight className="h-4 w-4 ml-auto" /></>
            )}
          </Button>
          <div className="rounded-lg border border-border/30 bg-muted/20 p-3 space-y-1.5">
            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
              Supported platforms
            </div>
            <div className="grid grid-cols-2 gap-1 text-xs text-muted-foreground">
              <div className="flex items-center gap-1.5"><Play className="h-3.5 w-3.5 text-red-500" /> YouTube Shorts</div>
              <div className="flex items-center gap-1.5"><Film className="h-3.5 w-3.5 text-pink-500" /> TikTok</div>
              <div className="flex items-center gap-1.5"><Film className="h-3.5 w-3.5 text-purple-500" /> Instagram Reels</div>
              <div className="flex items-center gap-1.5"><Film className="h-3.5 w-3.5 text-blue-400" /> Twitter/X</div>
            </div>
          </div>
        </Card>

        {/* Caption Eraser launcher */}
        <Link href="/utilities/caption-eraser" className="block group">
          <Card className="p-6 space-y-5 border-border/40 bg-card/60 hover:border-amber-500/40 hover:bg-amber-500/[0.03] transition-colors h-full flex flex-col">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10 shrink-0 group-hover:bg-amber-500/20 transition-colors">
                <Eraser className="h-5 w-5 text-amber-400" />
              </div>
              <div>
                <h2 className="font-semibold">Caption / Logo Eraser</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Seamlessly remove burnt-in captions, logos, or watermarks using OpenCV inpainting.
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 space-y-2">
              <div className="flex items-center gap-2 text-xs text-amber-300">
                <Wand2 className="h-3.5 w-3.5" />
                <span className="font-semibold">Now with real inpainting</span>
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                Frame-by-frame OpenCV TELEA algorithm reconstructs pixels from surrounding content
                for natural-looking removal — far beyond simple blur.
              </p>
            </div>

            <div className="flex-1" />

            <Button
              variant="outline"
              className="w-full gap-2 border-amber-500/30 text-amber-300 hover:bg-amber-500/10 hover:text-amber-200 group-hover:border-amber-500/50"
            >
              Open Caption Eraser <ExternalLink className="h-3.5 w-3.5 ml-auto" />
            </Button>
          </Card>
        </Link>
      </div>
    </div>
  );
}
