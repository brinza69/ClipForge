"use client";

import { useRef, useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Eraser, Upload, X, ArrowLeft,
  CheckCircle2, Play, Pause, Volume2, VolumeX,
} from "lucide-react";
import { toast } from "sonner";
import { ControlsPanel } from "./controls-panel";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

type Dims = { w: number; h: number };
type RenderedRect = { x: number; y: number; w: number; h: number };
type DragState = { mode: "move" | "resize"; startX: number; startY: number; origX: number; origY: number; origW: number; origH: number } | null;
type EraseMode = "inpaint" | "blur";

function getRenderedRect(video: HTMLVideoElement, dims: Dims): RenderedRect {
  const cw = video.clientWidth, ch = video.clientHeight;
  const va = dims.w / dims.h, ca = cw / ch;
  if (va > ca) {
    const rh = cw / va;
    return { x: 0, y: (ch - rh) / 2, w: cw, h: rh };
  }
  const rw = ch * va;
  return { x: (cw - rw) / 2, y: 0, w: rw, h: ch };
}

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }

export default function CaptionEraserPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderedRef = useRef<RenderedRect | null>(null);
  const dragRef = useRef<DragState>(null);

  const [file, setFile] = useState<File | null>(null);
  const [localUrl, setLocalUrl] = useState("");
  const [dims, setDims] = useState<Dims | null>(null);
  const [rX, setRX] = useState(0);
  const [rY, setRY] = useState(0);
  const [rW, setRW] = useState(0);
  const [rH, setRH] = useState(0);
  const [mode, setMode] = useState<EraseMode>("inpaint");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [resultUrl, setResultUrl] = useState("");
  const [resultName, setResultName] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [compareMode, setCompareMode] = useState<"side" | "before" | "after">("side");

  // Playback state (custom controls — native controls are blocked by the canvas overlay)
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isMuted, setIsMuted] = useState(true);

  const xPct = dims ? Math.round(rX / dims.w * 100) : 0;
  const yPct = dims ? Math.round(rY / dims.h * 100) : 0;
  const wPct = dims ? Math.round(rW / dims.w * 100) : 100;
  const hPct = dims ? Math.round(rH / dims.h * 100) : 18;

  const setFromPct = (axis: "x" | "y" | "w" | "h", pct: number) => {
    if (!dims) return;
    const total = axis === "x" || axis === "w" ? dims.w : dims.h;
    const val = Math.round(pct / 100 * total);
    if (axis === "x") setRX(clamp(val, 0, dims.w - rW));
    else if (axis === "y") setRY(clamp(val, 0, dims.h - rH));
    else if (axis === "w") setRW(clamp(val, 1, dims.w - rX));
    else setRH(clamp(val, 1, dims.h - rY));
  };

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video || !dims) return;
    canvas.width = video.clientWidth;
    canvas.height = video.clientHeight;
    const rendered = getRenderedRect(video, dims);
    renderedRef.current = rendered;
    const ctx = canvas.getContext("2d")!;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const sx = rendered.w / dims.w, sy = rendered.h / dims.h;
    const cx = rendered.x + rX * sx;
    const cy = rendered.y + rY * sy;
    const cw = rW * sx, ch = rH * sy;

    // Dim everything outside the mask
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.clearRect(cx, cy, cw, ch);

    // Mask border
    ctx.strokeStyle = "rgba(251,191,36,0.95)";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 4]);
    ctx.strokeRect(cx + 1, cy + 1, cw - 2, ch - 2);
    ctx.setLineDash([]);

    // Corner resize handle
    const hr = 8;
    ctx.fillStyle = "#fbbf24";
    ctx.fillRect(cx + cw - hr, cy + ch - hr, hr * 2, hr * 2);
    ctx.fillStyle = "#000";
    ctx.fillRect(cx + cw - hr + 3, cy + ch - hr + 3, 2, hr * 2 - 6);
    ctx.fillRect(cx + cw - hr + 3, cy + ch - hr + 3, hr * 2 - 6, 2);

    // Move cross in centre
    if (cw > 40 && ch > 40) {
      ctx.strokeStyle = "rgba(251,191,36,0.9)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx + cw / 2 - 8, cy + ch / 2);
      ctx.lineTo(cx + cw / 2 + 8, cy + ch / 2);
      ctx.moveTo(cx + cw / 2, cy + ch / 2 - 8);
      ctx.lineTo(cx + cw / 2, cy + ch / 2 + 8);
      ctx.stroke();
    }
  }, [rX, rY, rW, rH, dims]);

  useEffect(() => { drawCanvas(); }, [drawCanvas]);
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const ro = new ResizeObserver(drawCanvas);
    ro.observe(v);
    return () => ro.disconnect();
  }, [drawCanvas]);

  const getCanvasPos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { mx: e.clientX - r.left, my: e.clientY - r.top };
  };

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rendered = renderedRef.current;
    if (!rendered || !dims) return;
    const { mx, my } = getCanvasPos(e);
    const sx = rendered.w / dims.w, sy = rendered.h / dims.h;
    const cx = rendered.x + rX * sx, cy = rendered.y + rY * sy;
    const cw = rW * sx, ch = rH * sy;
    const base = { startX: mx, startY: my, origX: rX, origY: rY, origW: rW, origH: rH };
    if (mx >= cx + cw - 14 && my >= cy + ch - 14) {
      dragRef.current = { mode: "resize", ...base };
    } else if (mx >= cx && mx <= cx + cw && my >= cy && my <= cy + ch) {
      dragRef.current = { mode: "move", ...base };
    }
  };

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    const rendered = renderedRef.current;
    if (!drag || !rendered || !dims) return;
    const { mx, my } = getCanvasPos(e);
    const px = dims.w / rendered.w, py = dims.h / rendered.h;
    const dx = Math.round((mx - drag.startX) * px);
    const dy = Math.round((my - drag.startY) * py);
    if (drag.mode === "move") {
      setRX(clamp(drag.origX + dx, 0, dims.w - drag.origW));
      setRY(clamp(drag.origY + dy, 0, dims.h - drag.origH));
    } else {
      setRW(clamp(drag.origW + dx, 10, dims.w - drag.origX));
      setRH(clamp(drag.origH + dy, 10, dims.h - drag.origY));
    }
  };

  const onMouseUp = () => { dragRef.current = null; };

  const onFilePick = (f: File) => {
    if (!f.type.startsWith("video/")) { toast.error("Please select a video file"); return; }
    if (localUrl) URL.revokeObjectURL(localUrl);
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    setFile(f);
    setLocalUrl(URL.createObjectURL(f));
    setDims(null); setResultUrl(""); setResultName(""); setErrorMsg("");
  };

  const onVideoLoaded = () => {
    const v = videoRef.current; if (!v) return;
    const vw = v.videoWidth, vh = v.videoHeight;
    setDims({ w: vw, h: vh });
    setRX(0); setRY(Math.round(vh * 0.82));
    setRW(vw); setRH(Math.round(vh * 0.18));
    setDuration(v.duration || 0);
    setCurrentTime(0);
  };

  // Wire up video element events for custom playback controls
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => setCurrentTime(v.currentTime);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onDur = () => setDuration(v.duration || 0);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("durationchange", onDur);
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("durationchange", onDur);
    };
  }, [localUrl]);

  const togglePlay = () => {
    const v = videoRef.current; if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  };

  const toggleMute = () => {
    const v = videoRef.current; if (!v) return;
    v.muted = !v.muted;
    setIsMuted(v.muted);
  };

  const onSeek = (t: number) => {
    const v = videoRef.current; if (!v) return;
    v.currentTime = t;
    setCurrentTime(t);
  };

  const fmtTime = (s: number) => {
    if (!isFinite(s) || s < 0) s = 0;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const handleErase = async () => {
    if (!file) return;
    if (rW <= 0 || rH <= 0) { toast.error("Region must have positive size"); return; }
    setLoading(true); setErrorMsg(""); setResultUrl("");
    setProgress(mode === "inpaint" ? "Uploading…" : "Uploading…");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("x", rX.toString()); fd.append("y", rY.toString());
    fd.append("w", rW.toString()); fd.append("h", rH.toString());
    fd.append("mode", mode);
    fd.append("algorithm", "telea");
    try {
      setProgress(mode === "inpaint"
        ? "Inpainting frames with OpenCV (this can take 30-90s)…"
        : "Processing with FFmpeg blur…");
      const res = await fetch(`${WORKER_URL}/api/utilities/erase`, { method: "POST", body: fd });
      if (!res.ok) {
        let msg = `Server error ${res.status}`;
        try { const j = await res.json(); msg = j.detail || msg; } catch {}
        throw new Error(msg);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const stem = file.name.replace(/\.[^.]+$/, "");
      setResultUrl(url); setResultName(`${stem}_erased.mp4`);
      setProgress("");
      toast.success("Erase complete!");
    } catch (e: any) {
      const msg = e.message || "Processing failed";
      setErrorMsg(msg);
      setProgress("");
      toast.error("Erase failed", { description: msg });
    } finally { setLoading(false); }
  };

  const downloadResult = () => {
    if (!resultUrl) return;
    const a = document.createElement("a");
    a.href = resultUrl;
    a.download = resultName || "erased.mp4";
    a.click();
  };

  const reset = () => {
    if (localUrl) URL.revokeObjectURL(localUrl);
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    setFile(null); setLocalUrl(""); setDims(null);
    setResultUrl(""); setResultName(""); setErrorMsg(""); setProgress("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center gap-3">
        <Link href="/utilities">
          <Button variant="ghost" size="sm" className="gap-1.5">
            <ArrowLeft className="h-4 w-4" /> Back to Utilities
          </Button>
        </Link>
      </div>

      <div>
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber-500/10">
            <Eraser className="h-6 w-6 text-amber-400" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Caption / Logo Eraser</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              Seamlessly remove burnt-in captions, logos, or watermarks using OpenCV inpainting.
            </p>
          </div>
        </div>
      </div>

      {!file ? (
        <Card
          className="border-2 border-dashed border-border/40 bg-muted/10 p-10 flex flex-col items-center justify-center gap-4 cursor-pointer hover:border-amber-500/40 hover:bg-amber-500/5 transition-colors min-h-[240px]"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) onFilePick(f); }}
        >
          <Upload className="h-10 w-10 text-muted-foreground" />
          <div className="text-center">
            <p className="text-base font-medium">Drop a video here or click to upload</p>
            <p className="text-xs text-muted-foreground mt-1">MP4, MOV, WebM, MKV · max 500 MB</p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onFilePick(f); }}
          />
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
          {/* Left: video preview + canvas overlay */}
          <Card className="p-4 border-border/40 bg-card/60 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs text-muted-foreground truncate">
                <span className="text-foreground font-medium">{file.name}</span>
                {dims && <span className="ml-2">· {dims.w}×{dims.h}</span>}
              </div>
              <Button size="sm" variant="ghost" onClick={reset} className="shrink-0">
                <X className="h-3.5 w-3.5 mr-1" /> New file
              </Button>
            </div>

            <div className="relative rounded-lg overflow-hidden bg-black">
              <video
                ref={videoRef}
                src={localUrl}
                className="w-full max-h-[60vh] object-contain block"
                muted={isMuted}
                playsInline
                onLoadedMetadata={onVideoLoaded}
                onClick={togglePlay}
              />
              {dims && (
                <canvas
                  ref={canvasRef}
                  className="absolute inset-0 w-full h-full"
                  style={{ cursor: loading ? "wait" : "crosshair", pointerEvents: loading ? "none" : "auto" }}
                  onMouseDown={onMouseDown}
                  onMouseMove={onMouseMove}
                  onMouseUp={onMouseUp}
                  onMouseLeave={onMouseUp}
                />
              )}
            </div>

            {/* Custom playback controls — live outside the canvas overlay so they stay clickable */}
            <div className="flex items-center gap-3 px-1">
              <button
                type="button"
                onClick={togglePlay}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 transition-colors"
                aria-label={isPlaying ? "Pause" : "Play"}
              >
                {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              </button>
              <span className="text-[10px] font-mono text-muted-foreground tabular-nums shrink-0">
                {fmtTime(currentTime)}
              </span>
              <input
                type="range"
                min={0}
                max={duration || 0}
                step={0.01}
                value={Math.min(currentTime, duration || 0)}
                onChange={(e) => onSeek(parseFloat(e.target.value))}
                className="flex-1 h-1.5 accent-amber-400 cursor-pointer"
                aria-label="Seek"
              />
              <span className="text-[10px] font-mono text-muted-foreground tabular-nums shrink-0">
                {fmtTime(duration)}
              </span>
              <button
                type="button"
                onClick={toggleMute}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted/30 text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors"
                aria-label={isMuted ? "Unmute" : "Mute"}
              >
                {isMuted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
              </button>
            </div>

            <p className="text-[11px] text-muted-foreground text-center">
              Scrub the timeline to find the frame with captions · drag mask to reposition · drag corner to resize
            </p>

            {resultUrl && (
              <>
                <div className="pt-3 border-t border-border/30 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-xs font-medium">
                      <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                      <span>Result preview</span>
                    </div>
                    <div className="flex items-center gap-1 rounded-md bg-muted/30 p-0.5">
                      {(["before", "side", "after"] as const).map((m) => (
                        <button
                          key={m}
                          onClick={() => setCompareMode(m)}
                          className={`px-2 py-1 text-[10px] rounded transition-colors ${
                            compareMode === m ? "bg-amber-500/20 text-amber-300" : "text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          {m === "side" ? "Side-by-side" : m === "before" ? "Before" : "After"}
                        </button>
                      ))}
                    </div>
                  </div>
                  {compareMode === "side" ? (
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1">
                        <div className="text-[9px] uppercase tracking-wide text-muted-foreground">Before</div>
                        <video src={localUrl} className="w-full rounded bg-black" controls muted />
                      </div>
                      <div className="space-y-1">
                        <div className="text-[9px] uppercase tracking-wide text-amber-400">After</div>
                        <video src={resultUrl} className="w-full rounded bg-black" controls muted />
                      </div>
                    </div>
                  ) : compareMode === "before" ? (
                    <video src={localUrl} className="w-full rounded bg-black max-h-[40vh]" controls muted />
                  ) : (
                    <video src={resultUrl} className="w-full rounded bg-black max-h-[40vh]" controls muted />
                  )}
                </div>
              </>
            )}
          </Card>

          <ControlsPanel
            mode={mode}
            setMode={setMode}
            loading={loading}
            progress={progress}
            errorMsg={errorMsg}
            dims={dims}
            xPct={xPct}
            yPct={yPct}
            wPct={wPct}
            hPct={hPct}
            rX={rX}
            rY={rY}
            rW={rW}
            rH={rH}
            setFromPct={setFromPct}
            resultUrl={resultUrl}
            onErase={handleErase}
            onDownload={downloadResult}
            onClearResult={() => { setResultUrl(""); setResultName(""); setErrorMsg(""); }}
          />
        </div>
      )}
    </div>
  );
}
