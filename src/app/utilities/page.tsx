"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Download, Eraser, Loader2, ArrowRight, CheckCircle2,
  Play, Film, Upload, X,
} from "lucide-react";
import { toast } from "sonner";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

export default function UtilitiesPage() {
  const router = useRouter();

  // ── Shorts Downloader ──────────────────────────────────────────────────────
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
    if (!downloadUrl.trim()) { toast.error("Paste a URL first"); return; }
    downloadMutation.mutate();
  };

  // ── Caption Eraser ─────────────────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const [eraseFile, setEraseFile] = useState<File | null>(null);
  const [eraseLocalUrl, setEraseLocalUrl] = useState("");
  const [eraseDims, setEraseDims] = useState<{ w: number; h: number } | null>(null);
  const [eraseX, setEraseX] = useState(0);
  const [eraseY, setEraseY] = useState(0);
  const [eraseW, setEraseW] = useState(0);
  const [eraseH, setEraseH] = useState(0);
  const [eraseLoading, setEraseLoading] = useState(false);
  const [eraseResultUrl, setEraseResultUrl] = useState("");
  const [eraseResultName, setEraseResultName] = useState("");
  const [eraseError, setEraseError] = useState("");
  const [eraseProgress, setEraseProgress] = useState("");

  const handleFileSelect = (file: File) => {
    if (!file.type.startsWith("video/")) {
      toast.error("Please select a video file (MP4, MOV, WebM)");
      return;
    }
    // Revoke previous URL to avoid memory leaks
    if (eraseLocalUrl) URL.revokeObjectURL(eraseLocalUrl);
    if (eraseResultUrl) URL.revokeObjectURL(eraseResultUrl);

    setEraseFile(file);
    setEraseLocalUrl(URL.createObjectURL(file));
    setEraseDims(null);
    setEraseResultUrl("");
    setEraseResultName("");
    setEraseError("");
  };

  const handleVideoLoaded = () => {
    const v = videoRef.current;
    if (!v) return;
    const vw = v.videoWidth;
    const vh = v.videoHeight;
    setEraseDims({ w: vw, h: vh });
    // Pre-fill: bottom 18% strip (typical caption area)
    setEraseX(0);
    setEraseY(Math.round(vh * 0.82));
    setEraseW(vw);
    setEraseH(Math.round(vh * 0.18));
  };

  const handleErase = async () => {
    if (!eraseFile) return;
    if (eraseW <= 0 || eraseH <= 0) {
      toast.error("Region width and height must be > 0");
      return;
    }

    setEraseLoading(true);
    setEraseError("");
    setEraseResultUrl("");
    setEraseProgress("Uploading video…");

    const formData = new FormData();
    formData.append("file", eraseFile);
    formData.append("x", eraseX.toString());
    formData.append("y", eraseY.toString());
    formData.append("w", eraseW.toString());
    formData.append("h", eraseH.toString());

    try {
      // Direct to backend (CORS allowed); avoids Next.js proxy buffering large files
      const res = await fetch(`${WORKER_URL}/api/utilities/erase`, {
        method: "POST",
        body: formData,
      });

      setEraseProgress("Processing…");

      if (!res.ok) {
        let msg = `Server error ${res.status}`;
        try { const j = await res.json(); msg = j.detail || msg; } catch {}
        throw new Error(msg);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const stem = eraseFile.name.replace(/\.[^.]+$/, "");
      const name = `${stem}_erased.mp4`;
      setEraseResultUrl(url);
      setEraseResultName(name);
      setEraseProgress("");
      toast.success("Erase complete!");
    } catch (e: any) {
      const msg = e.message || "Processing failed";
      setEraseError(msg);
      setEraseProgress("");
      toast.error("Erase failed", { description: msg });
    } finally {
      setEraseLoading(false);
    }
  };

  const handleDownloadResult = () => {
    if (!eraseResultUrl) return;
    const a = document.createElement("a");
    a.href = eraseResultUrl;
    a.download = eraseResultName || "erased.mp4";
    a.click();
  };

  const clearEraseFile = () => {
    if (eraseLocalUrl) URL.revokeObjectURL(eraseLocalUrl);
    if (eraseResultUrl) URL.revokeObjectURL(eraseResultUrl);
    setEraseFile(null);
    setEraseLocalUrl("");
    setEraseDims(null);
    setEraseResultUrl("");
    setEraseResultName("");
    setEraseError("");
    setEraseProgress("");
    if (fileInputRef.current) fileInputRef.current.value = "";
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
              <><Loader2 className="h-4 w-4 animate-spin" /> Processing…</>
            ) : downloadMutation.isSuccess ? (
              <><CheckCircle2 className="h-4 w-4 text-emerald-400" /> Started</>
            ) : (
              <><Download className="h-4 w-4" /> Download & Process <ArrowRight className="h-4 w-4 ml-auto" /></>
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
        <Card className="p-6 space-y-4 border-border/40 bg-card/60">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-500/10 shrink-0">
              <Eraser className="h-5 w-5 text-amber-400" />
            </div>
            <div>
              <h2 className="font-semibold">Caption / Logo Eraser</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                Remove a rectangular region (captions, logo, watermark) from any video using FFmpeg.
              </p>
            </div>
          </div>

          {/* File picker */}
          {!eraseFile ? (
            <div
              className="rounded-xl border-2 border-dashed border-border/40 bg-muted/10 p-6 flex flex-col items-center justify-center gap-3 cursor-pointer hover:border-amber-500/40 hover:bg-amber-500/5 transition-colors"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files[0];
                if (f) handleFileSelect(f);
              }}
            >
              <Upload className="h-7 w-7 text-muted-foreground" />
              <div className="text-sm text-muted-foreground text-center">
                Click or drag a video here<br />
                <span className="text-xs">MP4, MOV, WebM, MKV supported</span>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFileSelect(f);
                }}
              />
            </div>
          ) : (
            <div className="space-y-3">
              {/* Video preview */}
              <div className="relative rounded-lg overflow-hidden bg-black">
                <video
                  ref={videoRef}
                  src={eraseLocalUrl}
                  className="w-full max-h-48 object-contain"
                  controls
                  muted
                  onLoadedMetadata={handleVideoLoaded}
                />
                <button
                  className="absolute top-1.5 right-1.5 rounded-full bg-black/60 p-1 hover:bg-black/80"
                  onClick={clearEraseFile}
                >
                  <X className="h-3.5 w-3.5 text-white" />
                </button>
              </div>

              {eraseDims && (
                <p className="text-[10px] text-muted-foreground text-center">
                  Video: {eraseDims.w} × {eraseDims.h} px — enter region in pixels below
                </p>
              )}

              {/* Region inputs */}
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: "X (left)", val: eraseX, set: setEraseX },
                  { label: "Y (top)", val: eraseY, set: setEraseY },
                  { label: "Width", val: eraseW, set: setEraseW },
                  { label: "Height", val: eraseH, set: setEraseH },
                ].map(({ label, val, set }) => (
                  <div key={label} className="space-y-1">
                    <Label className="text-[10px]">{label}</Label>
                    <Input
                      type="number"
                      min={0}
                      value={val}
                      onChange={(e) => set(Math.max(0, parseInt(e.target.value) || 0))}
                      className="h-7 text-xs bg-background"
                      disabled={eraseLoading}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Error */}
          {eraseError && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-2.5 text-xs text-red-400">
              {eraseError}
            </div>
          )}

          {/* Process button */}
          {eraseFile && !eraseResultUrl && (
            <Button
              className="w-full gap-2 bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/30"
              onClick={handleErase}
              disabled={eraseLoading || !eraseFile}
            >
              {eraseLoading ? (
                <><Loader2 className="h-4 w-4 animate-spin" /> {eraseProgress || "Processing…"}</>
              ) : (
                <><Eraser className="h-4 w-4" /> Erase Region</>
              )}
            </Button>
          )}

          {/* Result */}
          {eraseResultUrl && (
            <div className="space-y-3">
              <div className="rounded-lg overflow-hidden bg-black">
                <video
                  src={eraseResultUrl}
                  className="w-full max-h-48 object-contain"
                  controls
                  muted
                />
              </div>
              <div className="flex gap-2">
                <Button
                  className="flex-1 gap-2"
                  onClick={handleDownloadResult}
                >
                  <Download className="h-4 w-4" /> Download Result
                </Button>
                <Button
                  variant="outline"
                  className="gap-2"
                  onClick={() => {
                    setEraseResultUrl("");
                    setEraseResultName("");
                    setEraseError("");
                  }}
                >
                  Try Again
                </Button>
              </div>
            </div>
          )}
        </Card>

      </div>
    </div>
  );
}
