"use client";

/**
 * Batch Process — paste a list of URLs, pick a single erase region on the
 * first video, then run download → transcribe → erase across all of them
 * with the same rectangle. Status table updates live; transcripts and
 * erased videos are downloadable per item.
 */

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  ArrowLeft, Wand2, Zap, Loader2, Image as ImageIcon, Play,
  FileText, Download, AlertCircle, CheckCircle2, Clock, Hourglass,
} from "lucide-react";
import { toast } from "sonner";

type RenderedRect = { x: number; y: number; w: number; h: number };
type DragState =
  | {
      mode: "move" | "resize";
      startX: number; startY: number;
      origX: number; origY: number; origW: number; origH: number;
    }
  | null;

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

/**
 * Compute the actual rendered rectangle of an `object-contain` media element
 * given its container size and the media's intrinsic aspect ratio.
 */
function getRenderedRect(el: HTMLElement, srcW: number, srcH: number): RenderedRect {
  const cw = el.clientWidth;
  const ch = el.clientHeight;
  const va = srcW / srcH;
  const ca = cw / ch;
  if (va > ca) {
    const rh = cw / va;
    return { x: 0, y: (ch - rh) / 2, w: cw, h: rh };
  }
  const rw = ch * va;
  return { x: (cw - rw) / 2, y: 0, w: rw, h: ch };
}

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

type Mode = "inpaint" | "blur";

interface PreviewMeta {
  title: string | null;
  thumbnail_url: string | null;
  width: number;
  height: number;
  duration: number | null;
  channel_name: string | null;
}

interface BatchItem {
  index: number;
  project_id: string;
  title: string;
  status: string;
  width: number | null;
  height: number | null;
  duration: number | null;
  thumbnail_url: string | null;
  job_id: string | null;
  job_type: string | null;
  job_status: string | null;
  progress: number;
  progress_message: string;
  job_error: string | null;
  has_erased_video: boolean;
  transcript_available: boolean;
}

interface BatchStatus {
  batch_id: string;
  total: number;
  done: number;
  failed: number;
  items: BatchItem[];
}

export default function BatchProcessPage() {
  const [urlsText, setUrlsText] = useState("");
  const [mode, setMode] = useState<Mode>("inpaint");

  const [preview, setPreview] = useState<PreviewMeta | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Region in absolute pixel coordinates of the first video. The canvas overlay
  // and sliders mutate these directly; the thumbnail-vs-video scaling is done
  // once when the thumbnail loads (most TikTok thumbnails ARE the video frame,
  // but the displayed image may be at a smaller resolution).
  const [rX, setRX] = useState(0);
  const [rY, setRY] = useState(0);
  const [rW, setRW] = useState(0);
  const [rH, setRH] = useState(0);

  // Canvas + image refs for the drag-to-pick overlay.
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderedRef = useRef<RenderedRect | null>(null);
  const dragRef = useRef<DragState>(null);

  const [submitting, setSubmitting] = useState(false);
  const [batchId, setBatchId] = useState<string>("");
  const [status, setStatus] = useState<BatchStatus | null>(null);

  const urls = useMemo(
    () => urlsText.split("\n").map((s) => s.trim()).filter(Boolean),
    [urlsText],
  );

  // ── Preview ─────────────────────────────────────────────────────────────
  const fetchPreview = useCallback(async () => {
    if (urls.length === 0) {
      toast.error("Paste at least one URL first.");
      return;
    }
    setPreviewLoading(true);
    setPreview(null);
    try {
      const res = await fetch(`${WORKER_URL}/api/utilities/batch/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: urls[0] }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      const meta: PreviewMeta = await res.json();
      setPreview(meta);
      // Seed the rectangle to a typical caption strip (bottom 18% full-width).
      if (meta.width && meta.height) {
        const newW = meta.width;
        const newH = Math.round(meta.height * 0.18);
        const newY = Math.round(meta.height * 0.82);
        setRX(0); setRY(newY); setRW(newW); setRH(newH);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Preview failed";
      toast.error("Couldn't fetch first video", { description: msg });
    } finally {
      setPreviewLoading(false);
    }
  }, [urls]);

  // ── Submit ──────────────────────────────────────────────────────────────
  const submitBatch = useCallback(async () => {
    if (!preview) return;
    if (urls.length === 0) return;
    if (rW <= 0 || rH <= 0) {
      toast.error("Region must have positive size.");
      return;
    }
    setSubmitting(true);
    try {
      const W = preview.width || 1080;
      const H = preview.height || 1920;
      const region = {
        x: Math.max(0, Math.min(rX, W - 1)),
        y: Math.max(0, Math.min(rY, H - 1)),
        w: Math.max(1, Math.min(rW, W - rX)),
        h: Math.max(1, Math.min(rH, H - rY)),
      };
      const res = await fetch(`${WORKER_URL}/api/utilities/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          urls,
          mode,
          algorithm: "telea",
          region,
          source_dimensions: { width: W, height: H },
        }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setBatchId(data.batch_id);
      toast.success(`Batch queued (${urls.length} videos)`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Submission failed";
      toast.error("Batch submission failed", { description: msg });
    } finally {
      setSubmitting(false);
    }
  }, [preview, urls, mode, rX, rY, rW, rH]);

  // ── Poll status ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!batchId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const res = await fetch(`${WORKER_URL}/api/utilities/batch/${batchId}`);
        if (res.ok) {
          const data = await res.json();
          if (!stopped) setStatus(data);
        }
      } catch {
        /* transient errors — keep polling */
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [batchId]);

  // ── Region preview overlay (canvas, drag-to-move + drag-corner-to-resize)
  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !preview) return;
    canvas.width = img.clientWidth;
    canvas.height = img.clientHeight;
    const rendered = getRenderedRect(img, preview.width || 1, preview.height || 1);
    renderedRef.current = rendered;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const sx = rendered.w / (preview.width || 1);
    const sy = rendered.h / (preview.height || 1);
    const cx = rendered.x + rX * sx;
    const cy = rendered.y + rY * sy;
    const cw = rW * sx;
    const ch = rH * sy;

    // Dim everything outside the mask.
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.clearRect(cx, cy, cw, ch);

    // Dashed amber border around the selection.
    ctx.strokeStyle = "rgba(251,191,36,0.95)";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 4]);
    ctx.strokeRect(cx + 1, cy + 1, Math.max(0, cw - 2), Math.max(0, ch - 2));
    ctx.setLineDash([]);

    // Bottom-right resize handle.
    const hr = 8;
    ctx.fillStyle = "#fbbf24";
    ctx.fillRect(cx + cw - hr, cy + ch - hr, hr * 2, hr * 2);
    ctx.fillStyle = "#000";
    ctx.fillRect(cx + cw - hr + 3, cy + ch - hr + 3, 2, hr * 2 - 6);
    ctx.fillRect(cx + cw - hr + 3, cy + ch - hr + 3, hr * 2 - 6, 2);

    // Move-cross in the centre when the rect is big enough.
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
  }, [preview, rX, rY, rW, rH]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  // Keep the canvas in sync if the container resizes (window resize / layout shift).
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const ro = new ResizeObserver(drawCanvas);
    ro.observe(img);
    return () => ro.disconnect();
  }, [drawCanvas, preview]);

  const getCanvasPos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { mx: e.clientX - r.left, my: e.clientY - r.top };
  };

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rendered = renderedRef.current;
    if (!rendered || !preview) return;
    const { mx, my } = getCanvasPos(e);
    const sx = rendered.w / preview.width, sy = rendered.h / preview.height;
    const cx = rendered.x + rX * sx;
    const cy = rendered.y + rY * sy;
    const cw = rW * sx, ch = rH * sy;
    const base = {
      startX: mx, startY: my,
      origX: rX, origY: rY, origW: rW, origH: rH,
    };
    if (mx >= cx + cw - 14 && my >= cy + ch - 14) {
      dragRef.current = { mode: "resize", ...base };
    } else if (mx >= cx && mx <= cx + cw && my >= cy && my <= cy + ch) {
      dragRef.current = { mode: "move", ...base };
    }
  };

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    const rendered = renderedRef.current;
    if (!drag || !rendered || !preview) return;
    const { mx, my } = getCanvasPos(e);
    const px = preview.width / rendered.w;
    const py = preview.height / rendered.h;
    const dx = Math.round((mx - drag.startX) * px);
    const dy = Math.round((my - drag.startY) * py);
    if (drag.mode === "move") {
      setRX(clamp(drag.origX + dx, 0, preview.width  - drag.origW));
      setRY(clamp(drag.origY + dy, 0, preview.height - drag.origH));
    } else {
      setRW(clamp(drag.origW + dx, 10, preview.width  - drag.origX));
      setRH(clamp(drag.origH + dy, 10, preview.height - drag.origY));
    }
  };

  const onMouseUp = () => { dragRef.current = null; };

  // ── Slider <-> pixel conversion ─────────────────────────────────────────
  const xPct = preview ? Math.round((rX / preview.width)  * 100) : 0;
  const yPct = preview ? Math.round((rY / preview.height) * 100) : 0;
  const wPct = preview ? Math.round((rW / preview.width)  * 100) : 0;
  const hPct = preview ? Math.round((rH / preview.height) * 100) : 0;
  const setFromPct = (axis: "x" | "y" | "w" | "h", pct: number) => {
    if (!preview) return;
    const total = axis === "x" || axis === "w" ? preview.width : preview.height;
    const val = Math.round((pct / 100) * total);
    if (axis === "x") setRX(clamp(val, 0, preview.width  - rW));
    else if (axis === "y") setRY(clamp(val, 0, preview.height - rH));
    else if (axis === "w") setRW(clamp(val, 1, preview.width  - rX));
    else                    setRH(clamp(val, 1, preview.height - rY));
  };

  // ── Render ──────────────────────────────────────────────────────────────
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
        <h1 className="text-2xl font-bold">Batch Process</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Paste a list of URLs. Pick the erase region on the first video. The same
          rectangle is applied to all of them (scaled if dimensions differ). You get
          a transcript and an erased mp4 per video.
        </p>
      </div>

      {/* Step 1: URL list */}
      <Card className="p-4 space-y-3 border-border/40 bg-card/60">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          1. URLs ({urls.length})
        </div>
        <Textarea
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
          placeholder="https://...tiktok.com/...\nhttps://...tiktok.com/...\nhttps://www.youtube.com/watch?v=..."
          className="h-40 font-mono text-xs"
          disabled={!!batchId}
        />
        {urls.length > 0 && (
          <div className="text-[11px] text-muted-foreground font-mono leading-5">
            {urls.slice(0, 10).map((u, i) => (
              <div key={i} className="truncate">
                <span className="text-amber-400">#{i + 1}</span> {u}
              </div>
            ))}
            {urls.length > 10 && <div className="text-muted-foreground">…and {urls.length - 10} more</div>}
          </div>
        )}
      </Card>

      {/* Step 2: Mode */}
      <Card className="p-4 space-y-3 border-border/40 bg-card/60">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          2. Removal method
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button
            disabled={!!batchId}
            onClick={() => setMode("inpaint")}
            className={`rounded-lg border p-3 text-left transition-colors ${
              mode === "inpaint"
                ? "border-amber-500/50 bg-amber-500/10"
                : "border-border/30 bg-muted/10 hover:border-border/60"
            }`}
          >
            <div className="flex items-center gap-2">
              <Wand2 className={`h-4 w-4 ${mode === "inpaint" ? "text-amber-400" : "text-muted-foreground"}`} />
              <span className="text-sm font-medium">Inpaint</span>
              <span className="ml-auto text-[10px] rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5">Best</span>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">Seamless. Slower. LaMa GPU when available.</p>
          </button>
          <button
            disabled={!!batchId}
            onClick={() => setMode("blur")}
            className={`rounded-lg border p-3 text-left transition-colors ${
              mode === "blur"
                ? "border-amber-500/50 bg-amber-500/10"
                : "border-border/30 bg-muted/10 hover:border-border/60"
            }`}
          >
            <div className="flex items-center gap-2">
              <Zap className={`h-4 w-4 ${mode === "blur" ? "text-amber-400" : "text-muted-foreground"}`} />
              <span className="text-sm font-medium">Fast blur</span>
              <span className="ml-auto text-[10px] rounded bg-blue-500/15 text-blue-400 px-1.5 py-0.5">Fast</span>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">FFmpeg avgblur. Quick. Less natural.</p>
          </button>
        </div>
      </Card>

      {/* Step 3: Region picker on first video */}
      <Card className="p-4 space-y-3 border-border/40 bg-card/60">
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
            3. Erase region (from first video)
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchPreview}
            disabled={previewLoading || urls.length === 0 || !!batchId}
          >
            {previewLoading ? (
              <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…</>
            ) : (
              <><ImageIcon className="h-3.5 w-3.5" /> Fetch first video</>
            )}
          </Button>
        </div>

        {preview ? (
          <div className="grid md:grid-cols-[1fr_280px] gap-4 items-start">
            <div className="flex justify-center bg-black/40 rounded-md overflow-hidden border border-border/30 p-2">
              {preview.thumbnail_url ? (
                <div className="relative inline-block">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    ref={imgRef}
                    src={preview.thumbnail_url}
                    alt={preview.title || "first video"}
                    className="block select-none max-h-[60vh] max-w-full w-auto h-auto"
                    crossOrigin="anonymous"
                    draggable={false}
                    onLoad={() => drawCanvas()}
                  />
                  <canvas
                    ref={canvasRef}
                    className={`absolute inset-0 ${batchId ? "pointer-events-none" : "cursor-move"}`}
                    onMouseDown={batchId ? undefined : onMouseDown}
                    onMouseMove={batchId ? undefined : onMouseMove}
                    onMouseUp={batchId ? undefined : onMouseUp}
                    onMouseLeave={batchId ? undefined : onMouseUp}
                  />
                </div>
              ) : (
                <div className="flex h-72 items-center justify-center text-xs text-muted-foreground">
                  No thumbnail
                </div>
              )}
            </div>
            <div className="space-y-3">
              <div className="text-[11px] text-muted-foreground space-y-0.5">
                <div className="font-medium text-foreground truncate">{preview.title || "(no title)"}</div>
                <div>{preview.width}×{preview.height}{preview.duration ? ` · ${Math.round(preview.duration)}s` : ""}</div>
                {preview.channel_name && <div>@{preview.channel_name}</div>}
                <div className="pt-1 text-[10px] italic text-muted-foreground/80">
                  Drag the rectangle to move; drag the bottom-right corner to resize.
                </div>
              </div>
              {[
                { label: "Left",   pct: xPct, axis: "x" as const, max: 99  },
                { label: "Top",    pct: yPct, axis: "y" as const, max: 99  },
                { label: "Width",  pct: wPct, axis: "w" as const, max: 100 },
                { label: "Height", pct: hPct, axis: "h" as const, max: 100 },
              ].map(({ label, pct, axis, max }) => (
                <div key={axis} className="space-y-1">
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-mono text-muted-foreground">{pct}%</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={max}
                    value={pct}
                    disabled={!!batchId}
                    onChange={(e) => setFromPct(axis, parseInt(e.target.value))}
                    className="w-full h-1.5 accent-amber-400 cursor-pointer disabled:opacity-40"
                  />
                </div>
              ))}
              <div className="pt-1 text-[10px] text-muted-foreground font-mono border-t border-border/30">
                {rX},{rY} / {rW}×{rH}px
              </div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-muted-foreground">
            Paste URLs above and click <em>Fetch first video</em> to pick a region.
          </div>
        )}
      </Card>

      {/* Step 4: Submit */}
      {!batchId && (
        <Button
          size="lg"
          className="gap-2 bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/30"
          onClick={submitBatch}
          disabled={!preview || urls.length === 0 || submitting}
        >
          {submitting
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Queueing…</>
            : <><Play className="h-4 w-4" /> Process {urls.length} video{urls.length === 1 ? "" : "s"}</>}
        </Button>
      )}

      {/* Status table */}
      {batchId && status && (
        <Card className="p-4 space-y-3 border-border/40 bg-card/60">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                Batch {batchId}
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                {status.done}/{status.total} done
                {status.failed > 0 ? ` · ${status.failed} failed` : ""}
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setBatchId("");
                setStatus(null);
                setPreview(null);
                setUrlsText("");
              }}
            >
              Start a new batch
            </Button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase text-muted-foreground border-b border-border/30">
                <tr>
                  <th className="text-left py-2 pr-3 font-medium">#</th>
                  <th className="text-left py-2 pr-3 font-medium">Title</th>
                  <th className="text-left py-2 pr-3 font-medium">Status</th>
                  <th className="text-left py-2 pr-3 font-medium">Transcript</th>
                  <th className="text-left py-2 pr-3 font-medium">Erased video</th>
                </tr>
              </thead>
              <tbody>
                {status.items.map((it) => (
                  <BatchItemRow key={it.project_id || it.index} item={it} batchId={batchId} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function BatchItemRow({ item, batchId }: { item: BatchItem; batchId: string }) {
  const statusIcon = (() => {
    if (item.status === "failed") return <AlertCircle className="h-3.5 w-3.5 text-red-400" />;
    if (item.has_erased_video)    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />;
    if (item.job_status === "running") return <Loader2 className="h-3.5 w-3.5 text-amber-400 animate-spin" />;
    if (item.job_status === "queued")  return <Hourglass className="h-3.5 w-3.5 text-muted-foreground" />;
    return <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
  })();

  const pct = Math.round((item.progress || 0) * 100);
  const dl = `${WORKER_URL}/api/utilities/batch/${batchId}/items/${item.project_id}/erased`;

  const downloadTranscript = async () => {
    try {
      const res = await fetch(`${WORKER_URL}/api/utilities/batch/${batchId}/items/${item.project_id}/transcript`);
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const blob = new Blob([data.full_text || ""], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(item.title || `video_${item.index}`).slice(0, 40).replace(/[^a-z0-9_-]+/gi, "_")}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Transcript download failed";
      toast.error("Transcript download failed", { description: msg });
    }
  };

  return (
    <tr className="border-b border-border/20 last:border-0">
      <td className="py-2 pr-3 align-top font-mono text-muted-foreground">#{item.index}</td>
      <td className="py-2 pr-3 align-top">
        <div className="font-medium text-foreground truncate max-w-[260px]" title={item.title}>
          {item.title || "—"}
        </div>
        {item.width && item.height ? (
          <div className="text-[10px] text-muted-foreground">
            {item.width}×{item.height}
            {item.duration ? ` · ${Math.round(item.duration)}s` : ""}
          </div>
        ) : null}
      </td>
      <td className="py-2 pr-3 align-top">
        <div className="flex items-center gap-2">
          {statusIcon}
          <div className="space-y-0.5 min-w-0">
            <div className="capitalize">{item.status.replaceAll("_", " ")}</div>
            {item.job_status && item.job_status !== "done" && (
              <div className="text-[10px] text-muted-foreground truncate max-w-[260px]" title={item.progress_message}>
                {pct}% · {item.progress_message || item.job_type || ""}
              </div>
            )}
            {item.job_error && (
              <div className="text-[10px] text-red-400 truncate max-w-[260px]" title={item.job_error}>
                {item.job_error}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="py-2 pr-3 align-top">
        {item.transcript_available ? (
          <Button size="sm" variant="outline" className="gap-1.5 h-7" onClick={downloadTranscript}>
            <FileText className="h-3 w-3" /> .txt
          </Button>
        ) : (
          <span className="text-muted-foreground text-[10px]">—</span>
        )}
      </td>
      <td className="py-2 pr-3 align-top">
        {item.has_erased_video ? (
          <a href={dl} target="_blank" rel="noopener noreferrer">
            <Button size="sm" variant="outline" className="gap-1.5 h-7">
              <Download className="h-3 w-3" /> .mp4
            </Button>
          </a>
        ) : (
          <span className="text-muted-foreground text-[10px]">—</span>
        )}
      </td>
    </tr>
  );
}
