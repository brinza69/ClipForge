"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  Sparkles, Loader2, Download, Upload, AlertCircle, CheckCircle2,
  Trash2, Settings2, FileVideo, Wand2, ArrowRight, Gauge, Zap,
} from "lucide-react";
import { toast } from "sonner";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

const VIDEO_EXTS = ["mp4", "mov", "webm", "mkv", "m4v", "avi"];

interface Stats {
  src_w: number;
  src_h: number;
  out_w: number;
  out_h: number;
  frames: number;
  model: string;
  out_size: number;
}

const TARGETS = [
  { value: 720, label: "720p" },
  { value: 1080, label: "1080p" },
  { value: 1440, label: "1440p" },
];

const MODELS = [
  {
    value: "video",
    label: "Fast (video)",
    icon: Zap,
    desc: "Real-ESRGAN animevideo ×2 — quick, temporally stable. Best for real footage.",
  },
  {
    value: "photo",
    label: "Max detail (photo)",
    icon: Gauge,
    desc: "Real-ESRGAN x4plus ×4 — slower, squeezes more texture. Can flicker on video.",
  },
];

function isVideo(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  return VIDEO_EXTS.includes(ext);
}

function fmtSize(bytes: number) {
  if (!bytes) return "—";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

export default function UpscalePage() {
  const [file, setFile] = useState<File | null>(null);

  // Params
  const [targetP, setTargetP] = useState(1080);
  const [model, setModel] = useState("video");
  const [denoise, setDenoise] = useState(true);

  // Job state
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadFilename, setDownloadFilename] = useState("");

  const [previewUrl, setPreviewUrl] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!file) {
      setPreviewUrl("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const reset = useCallback(() => {
    setFile(null);
    setBusy(false);
    setProgress(0);
    setProgressMsg("");
    setErrorMsg("");
    setStats(null);
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    setDownloadUrl("");
    setDownloadFilename("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [downloadUrl]);

  const handlePickFile = (f: File | null) => {
    if (!f) return;
    if (!isVideo(f.name)) {
      toast.error(`Unsupported file type. Video only: ${VIDEO_EXTS.join(", ")}`);
      return;
    }
    if (f.size > 1024 * 1024 * 1024) {
      toast.error("File too large. Maximum 1 GB.");
      return;
    }
    setFile(f);
    setErrorMsg("");
    setStats(null);
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    setDownloadUrl("");
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (busy) return;
    const f = e.dataTransfer.files?.[0];
    if (f) handlePickFile(f);
  };

  const onUpscale = async () => {
    if (!file) {
      toast.error("Pick a video first");
      return;
    }
    setBusy(true);
    setErrorMsg("");
    setStats(null);
    setProgress(0);
    setProgressMsg("Uploading…");

    try {
      const form = new FormData();
      form.append("file", file);
      form.append("model", model);
      form.append("target_p", String(targetP));
      form.append("denoise", String(denoise));

      const submit = await fetch(`${WORKER_URL}/api/utilities/upscale`, {
        method: "POST",
        body: form,
      });
      if (!submit.ok) {
        const body = await submit.json().catch(() => ({}));
        throw new Error(body.detail || `Upload failed (${submit.status})`);
      }
      const { job_id, output_filename } = await submit.json();

      // Poll — upscale is GPU-bound and can take many minutes.
      const start = Date.now();
      while (true) {
        if (Date.now() - start > 60 * 60 * 1000) {
          throw new Error("Job timed out after 60 minutes");
        }
        await new Promise((r) => setTimeout(r, 1500));
        const sr = await fetch(`${WORKER_URL}/api/jobs/${job_id}`);
        if (!sr.ok) throw new Error(`Status fetch failed (${sr.status})`);
        const j = await sr.json();
        setProgress(Math.round((j.progress || 0) * 100));
        setProgressMsg(j.progress_message || "");
        if (j.status === "done") break;
        if (j.status === "failed") throw new Error(j.error || "Job failed");
        if (j.status === "cancelled") throw new Error("Job was cancelled");
      }

      const rRes = await fetch(`${WORKER_URL}/api/utilities/upscale/${job_id}/result`);
      const result = await rRes.json();
      setStats(result.stats as Stats);

      const dlRes = await fetch(`${WORKER_URL}/api/utilities/upscale/${job_id}/download`);
      if (!dlRes.ok) throw new Error("Failed to fetch output");
      const blob = await dlRes.blob();
      const url = URL.createObjectURL(blob);
      setDownloadUrl(url);
      setDownloadFilename(output_filename || `upscaled-${Date.now()}.mp4`);

      const s = result.stats as Stats;
      toast.success(`Upscaled to ${s?.out_w}×${s?.out_h}`);
    } catch (e: any) {
      setErrorMsg(e?.message || String(e));
      toast.error("Failed", { description: e?.message || String(e) });
    } finally {
      setBusy(false);
      setProgressMsg("");
    }
  };

  const handleDownload = () => {
    if (!downloadUrl) return;
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = downloadFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Sparkles className="h-6 w-6 text-fuchsia-400" />
          AI Upscale
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Turn a low-res or soft clip into genuinely higher-res video by
          reconstructing detail with Real-ESRGAN on the GPU — not a plain
          stretch. Runs locally.
        </p>
      </div>

      {/* File picker */}
      <Card className="p-6">
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          className="rounded-lg border-2 border-dashed border-border/50 px-6 py-10 text-center transition-colors hover:border-fuchsia-500/40 cursor-pointer"
          onClick={() => !busy && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={VIDEO_EXTS.map((e) => `.${e}`).join(",")}
            className="hidden"
            onChange={(e) => handlePickFile(e.target.files?.[0] || null)}
          />
          {file ? (
            <div className="flex items-center justify-center gap-3">
              <FileVideo className="h-7 w-7 text-fuchsia-400" />
              <div className="text-left">
                <div className="font-medium">{file.name}</div>
                <div className="text-xs text-muted-foreground">
                  {(file.size / (1024 * 1024)).toFixed(2)} MB
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={(e) => {
                  e.stopPropagation();
                  reset();
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Upload className="h-7 w-7" />
              <div className="text-sm">Drop a video here or click to browse</div>
              <div className="text-xs">{VIDEO_EXTS.join(", ")} · max 1 GB</div>
            </div>
          )}
        </div>

        {file && previewUrl && (
          <div className="mt-4">
            <video src={previewUrl} controls className="w-full max-h-80 rounded-lg bg-black" />
          </div>
        )}
      </Card>

      {/* Settings */}
      <Card className="p-6 space-y-6">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Settings2 className="h-4 w-4" />
          Settings
        </h2>

        {/* Target resolution */}
        <div>
          <Label className="text-sm">Target resolution (short side)</Label>
          <div className="mt-2 flex gap-2">
            {TARGETS.map((t) => (
              <Button
                key={t.value}
                type="button"
                variant={targetP === t.value ? "default" : "outline"}
                onClick={() => setTargetP(t.value)}
                disabled={busy}
                className="flex-1"
              >
                {t.label}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            1080p → 1080×1920 for a vertical clip. Aspect ratio is preserved.
          </p>
        </div>

        {/* Model */}
        <div>
          <Label className="text-sm">Model</Label>
          <div className="mt-2 grid sm:grid-cols-2 gap-2">
            {MODELS.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setModel(m.value)}
                disabled={busy}
                className={`text-left rounded-lg border p-3 transition-colors disabled:opacity-60 ${
                  model === m.value
                    ? "border-fuchsia-500/60 bg-fuchsia-500/10"
                    : "border-border/50 hover:border-fuchsia-500/30"
                }`}
              >
                <div className="flex items-center gap-2 text-sm font-medium">
                  <m.icon className="h-4 w-4 text-fuchsia-400" />
                  {m.label}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                  {m.desc}
                </p>
              </button>
            ))}
          </div>
        </div>

        {/* Denoise */}
        <div className="flex items-center justify-between rounded-lg border border-border/40 bg-muted/20 p-3">
          <div>
            <div className="text-sm font-medium">Light pre-denoise</div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Cleans compression blocking before upscaling (recommended for
              TikTok / downloaded clips).
            </p>
          </div>
          <Button
            type="button"
            variant={denoise ? "default" : "outline"}
            size="sm"
            onClick={() => setDenoise((v) => !v)}
            disabled={busy}
          >
            {denoise ? "On" : "Off"}
          </Button>
        </div>

        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
          <div className="flex items-center gap-2 text-xs text-amber-300">
            <Wand2 className="h-3.5 w-3.5" />
            <span className="font-semibold">Reconstructs detail — doesn&apos;t invent a native capture</span>
          </div>
          <p className="text-[11px] text-muted-foreground leading-relaxed mt-1">
            AI adds plausible texture and sharpens; already-soft or AI-generated
            sources gain the least. GPU-bound — a 60s clip takes a few minutes.
          </p>
        </div>

        <Button size="lg" onClick={onUpscale} disabled={!file || busy} className="w-full">
          {busy ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              {progressMsg || `Working ${progress}%`}
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4 mr-2" />
              Upscale to {targetP}p
            </>
          )}
        </Button>

        {busy && progress > 0 && (
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-fuchsia-500 transition-all duration-300 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}

        {errorMsg && (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <div>{errorMsg}</div>
          </div>
        )}
      </Card>

      {/* Results */}
      {stats && (
        <Card className="p-6 space-y-4">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
            <h2 className="text-base font-semibold">Result</h2>
          </div>

          <div className="flex items-center justify-center gap-4">
            <div className="rounded-lg border border-border/40 bg-muted/30 p-4 text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                Source
              </div>
              <div className="text-xl font-bold">
                {stats.src_w}×{stats.src_h}
              </div>
            </div>
            <ArrowRight className="h-5 w-5 text-muted-foreground shrink-0" />
            <div className="rounded-lg border border-fuchsia-500/40 bg-fuchsia-500/5 p-4 text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                Upscaled
              </div>
              <div className="text-xl font-bold text-fuchsia-400">
                {stats.out_w}×{stats.out_h}
              </div>
            </div>
          </div>

          <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">{stats.frames} frames</Badge>
            <Badge variant="secondary">{stats.model}</Badge>
            <Badge variant="secondary">{fmtSize(stats.out_size)}</Badge>
          </div>

          {downloadUrl && (
            <video src={downloadUrl} controls className="w-full max-h-96 rounded-lg bg-black" />
          )}

          <Button onClick={handleDownload} className="w-full" disabled={!downloadUrl}>
            <Download className="h-4 w-4 mr-2" />
            Download {downloadFilename}
          </Button>
        </Card>
      )}
    </div>
  );
}
