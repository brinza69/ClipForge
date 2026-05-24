"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  AudioLines, Loader2, Download, Upload, AlertCircle, CheckCircle2,
  Sparkles, Trash2, Settings2, FileVideo, FileAudio,
} from "lucide-react";
import { toast } from "sonner";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

const AUDIO_EXTS = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma"];
const VIDEO_EXTS = ["mp4", "mov", "webm", "mkv", "m4v", "avi"];

interface Stats {
  before_ms: number;
  after_ms: number;
  removed_ms: number;
  removed_pct: number;
  segments: number;
}

function fmtDuration(ms: number) {
  if (!ms || ms <= 0) return "0.0s";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(2)}s`;
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  return `${m}m ${rem.toFixed(1)}s`;
}

function detectMode(filename: string): "audio" | "video" | "unknown" {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (VIDEO_EXTS.includes(ext)) return "video";
  if (AUDIO_EXTS.includes(ext)) return "audio";
  return "unknown";
}

export default function SilenceRemoverPage() {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"audio" | "video" | "unknown">("unknown");

  // Params (defaults match the upstream HF Space exactly)
  const [keepSilenceSec, setKeepSilenceSec] = useState(0.05);
  const [minSilenceMs, setMinSilenceMs] = useState(100);
  const [thresholdDb, setThresholdDb] = useState(-45);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Job state
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadFilename, setDownloadFilename] = useState("");

  // Preview
  const [previewUrl, setPreviewUrl] = useState("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Build a local URL for the input file (for the source preview)
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
    setMode("unknown");
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
    const m = detectMode(f.name);
    if (m === "unknown") {
      toast.error(`Unsupported file type. Audio: ${AUDIO_EXTS.join(", ")} • Video: ${VIDEO_EXTS.join(", ")}`);
      return;
    }
    if (f.size > 500 * 1024 * 1024) {
      toast.error("File too large. Maximum 500 MB.");
      return;
    }
    setFile(f);
    setMode(m);
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

  const onClean = async () => {
    if (!file) {
      toast.error("Pick a file first");
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
      form.append("min_silence_ms", String(minSilenceMs));
      form.append("silence_thresh_db", String(thresholdDb));
      form.append("keep_silence_ms", String(Math.round(keepSilenceSec * 1000)));

      const submit = await fetch(`${WORKER_URL}/api/utilities/silence-remove`, {
        method: "POST",
        body: form,
      });
      if (!submit.ok) {
        const body = await submit.json().catch(() => ({}));
        throw new Error(body.detail || `Upload failed (${submit.status})`);
      }
      const { job_id, output_filename } = await submit.json();

      // Poll job
      const start = Date.now();
      while (true) {
        if (Date.now() - start > 15 * 60 * 1000) {
          throw new Error("Job timed out after 15 minutes");
        }
        await new Promise((r) => setTimeout(r, 1000));
        const sr = await fetch(`${WORKER_URL}/api/jobs/${job_id}`);
        if (!sr.ok) throw new Error(`Status fetch failed (${sr.status})`);
        const j = await sr.json();
        setProgress(Math.round((j.progress || 0) * 100));
        setProgressMsg(j.progress_message || "");
        if (j.status === "done") break;
        if (j.status === "failed") throw new Error(j.error || "Job failed");
        if (j.status === "cancelled") throw new Error("Job was cancelled");
      }

      // Fetch stats + binary
      const rRes = await fetch(`${WORKER_URL}/api/utilities/silence-remove/${job_id}/result`);
      const result = await rRes.json();
      setStats(result.stats as Stats);

      const dlRes = await fetch(`${WORKER_URL}/api/utilities/silence-remove/${job_id}/download`);
      if (!dlRes.ok) throw new Error("Failed to fetch output");
      const blob = await dlRes.blob();
      const url = URL.createObjectURL(blob);
      setDownloadUrl(url);
      setDownloadFilename(output_filename || `silence-removed-${Date.now()}`);

      toast.success(`Removed ${result.stats.removed_pct.toFixed(1)}% silence`);
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
          <AudioLines className="h-6 w-6 text-primary" />
          Silence Remover
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Strip silent gaps from audio or video. Powered by pydub (-45 dBFS threshold,
          dynamic fallback). Audio and video formats supported.
        </p>
      </div>

      {/* File picker */}
      <Card className="p-6">
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          className="rounded-lg border-2 border-dashed border-border/50 px-6 py-10 text-center transition-colors hover:border-primary/40 cursor-pointer"
          onClick={() => !busy && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={[...AUDIO_EXTS, ...VIDEO_EXTS].map((e) => `.${e}`).join(",")}
            className="hidden"
            onChange={(e) => handlePickFile(e.target.files?.[0] || null)}
          />
          {file ? (
            <div className="flex items-center justify-center gap-3">
              {mode === "video" ? (
                <FileVideo className="h-7 w-7 text-primary" />
              ) : (
                <FileAudio className="h-7 w-7 text-primary" />
              )}
              <div className="text-left">
                <div className="font-medium">{file.name}</div>
                <div className="text-xs text-muted-foreground">
                  {(file.size / (1024 * 1024)).toFixed(2)} MB · {mode}
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
              <div className="text-sm">Drop a file here or click to browse</div>
              <div className="text-xs">
                Audio: {AUDIO_EXTS.join(", ")} · Video: {VIDEO_EXTS.join(", ")} · max 500 MB
              </div>
            </div>
          )}
        </div>

        {/* Source preview */}
        {file && previewUrl && (
          <div className="mt-4">
            {mode === "video" ? (
              <video src={previewUrl} controls className="w-full max-h-80 rounded-lg bg-black" />
            ) : (
              <audio src={previewUrl} controls className="w-full" />
            )}
          </div>
        )}
      </Card>

      {/* Settings */}
      <Card className="p-6 space-y-5">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            <Settings2 className="h-4 w-4" />
            Settings
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "Hide advanced" : "Advanced options"}
          </Button>
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <Label className="text-sm">Keep silence padding</Label>
            <Badge variant="secondary" className="text-xs">
              {keepSilenceSec.toFixed(2)}s
            </Badge>
          </div>
          <Slider
            value={[keepSilenceSec]}
            min={0}
            max={1}
            step={0.01}
            onValueChange={([v]) => setKeepSilenceSec(v)}
            disabled={busy}
          />
          <p className="text-xs text-muted-foreground mt-1">
            How much silence (seconds) to keep around each non-silent chunk.
            Higher = less choppy, lower = tighter cuts.
          </p>
        </div>

        {showAdvanced && (
          <>
            <div>
              <div className="flex items-center justify-between mb-2">
                <Label className="text-sm">Silence threshold</Label>
                <Badge variant="secondary" className="text-xs">{thresholdDb} dBFS</Badge>
              </div>
              <Slider
                value={[thresholdDb]}
                min={-80}
                max={-20}
                step={1}
                onValueChange={([v]) => setThresholdDb(v)}
                disabled={busy}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Audio quieter than this counts as silence. Default -45 dBFS matches
                the upstream tool. If no silence is detected we auto-fallback to
                (avg loudness − 16 dB).
              </p>
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <Label className="text-sm">Minimum silence length</Label>
                <Badge variant="secondary" className="text-xs">{minSilenceMs} ms</Badge>
              </div>
              <Slider
                value={[minSilenceMs]}
                min={50}
                max={2000}
                step={10}
                onValueChange={([v]) => setMinSilenceMs(v)}
                disabled={busy}
              />
              <p className="text-xs text-muted-foreground mt-1">
                Silence must last at least this long to be removed. Increase to keep
                short natural pauses.
              </p>
            </div>
          </>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button
            size="lg"
            onClick={onClean}
            disabled={!file || busy}
            className="flex-1"
          >
            {busy ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                {progressMsg || `Working ${progress}%`}
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4 mr-2" />
                Remove silence
              </>
            )}
          </Button>
        </div>

        {busy && progress > 0 && (
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all duration-300 ease-out"
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

          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg border border-border/40 bg-muted/30 p-4 text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                Original
              </div>
              <div className="text-xl font-bold">{fmtDuration(stats.before_ms)}</div>
            </div>
            <div className="rounded-lg border border-primary/40 bg-primary/5 p-4 text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                New
              </div>
              <div className="text-xl font-bold text-primary">{fmtDuration(stats.after_ms)}</div>
            </div>
            <div className="rounded-lg border border-border/40 bg-muted/30 p-4 text-center">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                Removed
              </div>
              <div className="text-xl font-bold text-red-400">
                {stats.removed_pct.toFixed(1)}%
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {fmtDuration(stats.removed_ms)}
              </div>
            </div>
          </div>

          <div className="text-xs text-muted-foreground text-center">
            {stats.segments} segment{stats.segments === 1 ? "" : "s"} kept
          </div>

          {/* Output preview */}
          {downloadUrl && (
            <div>
              {mode === "video" ? (
                <video src={downloadUrl} controls className="w-full max-h-80 rounded-lg bg-black" />
              ) : (
                <audio src={downloadUrl} controls className="w-full" />
              )}
            </div>
          )}

          <div className="flex gap-2">
            <Button onClick={handleDownload} className="flex-1" disabled={!downloadUrl}>
              <Download className="h-4 w-4 mr-2" />
              Download {downloadFilename}
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
