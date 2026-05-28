"use client";

import {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import {
  Type, Upload, Plus, Trash2, Loader2, Download, AlertCircle,
  CheckCircle2, FileVideo, Palette, Sparkles, Save,
} from "lucide-react";
import { toast } from "sonner";

// Same-origin proxy through Next.js rewrites (see next.config.ts:
// /worker-api/:path* → http://127.0.0.1:8420/api/:path*). Routing same-origin
// avoids browser extension content scripts that intercept cross-port fetches
// — Chrome/Edge plugins like Grammarly, screenshot tools etc. silently break
// multipart POSTs from :3000 → :8420.
const WORKER_URL = "";

// ── Types ──────────────────────────────────────────────────────────────────

interface Template {
  id: string;
  name: string;
  font_family: string;
  font_size: number;
  font_weight?: string;
  text_color: string;
  highlight_color?: string;
  outline_color: string;
  outline_width: number;
  shadow_offset: number;
  shadow_color: string;
  position: string;
  uppercase: boolean;
  builtin?: boolean;
  borderstyle?: number;
}

interface FontEntry {
  family: string;
  filename: string;
  size: number;
}

interface FontsList {
  system: string[];
  user: FontEntry[];
}

interface Overlay {
  id: string;
  text: string;
  template_id: string;
  // Optional inline style overrides — only `font_family` is editable from the
  // UI right now; the rest come from the template.
  style?: { font_family?: string };
  start_t: number;
  end_t: number;
  x_pct: number;
  y_pct: number;
  scale: number;
}

interface Session {
  session_id: string;
  width: number;
  height: number;
  filename: string;
}

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function CaptionStudioPage() {
  // Source
  const [session, setSession] = useState<Session | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Templates + fonts
  const [templates, setTemplates] = useState<Template[]>([]);
  const [fonts, setFonts] = useState<FontsList>({ system: [], user: [] });

  // Overlays
  const [overlays, setOverlays] = useState<Overlay[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Preview
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewTime, setPreviewTime] = useState(0.5);

  // Burn
  const [burning, setBurning] = useState(false);
  const [burnProgress, setBurnProgress] = useState(0);
  const [burnMsg, setBurnMsg] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadFilename, setDownloadFilename] = useState("");

  // ── Derived ──────────────────────────────────────────────────────────────

  const selected = useMemo(
    () => overlays.find((o) => o.id === selectedId) || null,
    [overlays, selectedId]
  );

  // Track drag state separately so we can move the HTML overlay without
  // a server round-trip per frame.
  const [dragging, setDragging] = useState<{
    id: string;
    mode: "move" | "scale";
    startX: number;
    startY: number;
    origXPct: number;
    origYPct: number;
    origScale: number;
  } | null>(null);

  // ── API helpers ──────────────────────────────────────────────────────────

  const loadTemplates = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/captions/templates`);
      const j = await r.json();
      setTemplates(j.templates || []);
    } catch (e) {
      console.error("templates", e);
    }
  }, []);

  const loadFonts = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/captions/fonts`);
      const j = await r.json();
      setFonts({ system: j.system || [], user: j.user || [] });
    } catch (e) {
      console.error("fonts", e);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
    loadFonts();
  }, [loadTemplates, loadFonts]);

  // ── Upload source ────────────────────────────────────────────────────────

  const uploadSource = async (file: File) => {
    if (file.size > 500 * 1024 * 1024) {
      toast.error("File too large (max 500 MB)");
      return;
    }
    console.log(`[caption-studio] uploading ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB) via Next.js proxy`);
    setUploading(true);
    // 5-minute hard timeout — even slow disks should finish a 500MB upload by then.
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 5 * 60 * 1000);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(`/worker-api/captions/upload-source`, {
        method: "POST",
        body: form,
        signal: ac.signal,
      });
      clearTimeout(timer);
      console.log(`[caption-studio] upload response: ${r.status}`);
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Upload failed (${r.status})`);
      }
      const j = (await r.json()) as Session;
      setSession(j);
      // Default to one centered overlay so the user sees something immediately.
      if (templates.length > 0 && overlays.length === 0) {
        const t = templates[0];
        setOverlays([{
          id: uid(),
          text: "Your caption here",
          template_id: t.id,
          start_t: 0,
          end_t: 5,
          x_pct: 0.5,
          y_pct: 0.85,
          scale: 1.0,
        }]);
      }
      toast.success(`Loaded ${j.width}×${j.height}`);
    } catch (e: any) {
      clearTimeout(timer);
      const msg = e?.name === "AbortError"
        ? "Upload timed out after 5 minutes"
        : (e?.message || String(e));
      console.error("[caption-studio] upload failed:", e);
      toast.error("Upload failed", { description: msg });
    } finally {
      setUploading(false);
    }
  };

  // ── Live preview (debounced) ─────────────────────────────────────────────

  const previewAbort = useRef<AbortController | null>(null);
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshPreview = useCallback(async () => {
    if (!session) return;
    if (previewAbort.current) previewAbort.current.abort();
    const ac = new AbortController();
    previewAbort.current = ac;
    setPreviewLoading(true);
    try {
      const r = await fetch(`/worker-api/captions/preview-frame`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          time_s: previewTime,
          overlays: overlays.map((o) => ({
            text: o.text,
            template_id: o.template_id,
            style: o.style,
            start_t: o.start_t,
            end_t: o.end_t,
            x_pct: o.x_pct,
            y_pct: o.y_pct,
            scale: o.scale,
          })),
        }),
        signal: ac.signal,
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Preview failed (${r.status})`);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      setPreviewUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return url;
      });
    } catch (e: any) {
      if (e.name === "AbortError") return;
      console.error("preview", e);
    } finally {
      setPreviewLoading(false);
    }
  }, [session, previewTime, overlays]);

  // Debounce: schedule a preview refresh 300ms after the last change.
  useEffect(() => {
    if (!session) return;
    if (previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => {
      refreshPreview();
    }, 300);
    return () => {
      if (previewTimer.current) clearTimeout(previewTimer.current);
    };
  }, [session, overlays, previewTime, refreshPreview]);

  // ── Overlay mutators ─────────────────────────────────────────────────────

  const addOverlay = () => {
    const t = templates[0];
    if (!t) {
      toast.error("Loading templates…");
      return;
    }
    const ovl: Overlay = {
      id: uid(),
      text: "New text",
      template_id: t.id,
      start_t: 0,
      end_t: 5,
      x_pct: 0.5,
      y_pct: 0.5,
      scale: 1.0,
    };
    setOverlays((s) => [...s, ovl]);
    setSelectedId(ovl.id);
  };

  const updateOverlay = (id: string, patch: Partial<Overlay>) => {
    setOverlays((s) => s.map((o) => (o.id === id ? { ...o, ...patch } : o)));
  };

  const deleteOverlay = (id: string) => {
    setOverlays((s) => s.filter((o) => o.id !== id));
    if (selectedId === id) setSelectedId(null);
  };

  // ── Auto-transcribe ───────────────────────────────────────────────────────

  const [autoBusy, setAutoBusy] = useState(false);
  const [autoLang, setAutoLang] = useState<string>(""); // "" = whisper auto-detect
  const [autoWordsPerChunk, setAutoWordsPerChunk] = useState(4);

  const autoTranscribe = async () => {
    if (!session) return;
    if (overlays.length > 0) {
      const ok = confirm(
        `Replace your current ${overlays.length} overlay(s) with auto-generated captions from the audio?`
      );
      if (!ok) return;
    }
    setAutoBusy(true);
    try {
      // Use the first template the user has, default to bold_impact when present.
      const tpl = templates.find((t) => t.id === "bold_impact") || templates[0];
      const r = await fetch(`/worker-api/captions/auto-transcribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          template_id: tpl?.id || "bold_impact",
          words_per_chunk: autoWordsPerChunk,
          x_pct: 0.5,
          y_pct: 0.85,
          scale: 1.0,
          language: autoLang || null,
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Auto-transcribe failed (${r.status})`);
      }
      const j = await r.json();
      const fromApi: Overlay[] = (j.overlays || []).map((o: any) => ({
        id: uid(),
        text: o.text,
        template_id: o.template_id,
        start_t: o.start_t,
        end_t: o.end_t,
        x_pct: o.x_pct,
        y_pct: o.y_pct,
        scale: o.scale,
      }));
      setOverlays(fromApi);
      setSelectedId(fromApi[0]?.id || null);
      toast.success(
        `Generated ${fromApi.length} caption${fromApi.length === 1 ? "" : "s"} (${j.language || "auto"})`
      );
    } catch (e: any) {
      toast.error("Auto-caption failed", { description: e.message });
    } finally {
      setAutoBusy(false);
    }
  };

  // ── Font upload ──────────────────────────────────────────────────────────

  const fontInputRef = useRef<HTMLInputElement | null>(null);
  const uploadFont = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`/worker-api/captions/fonts/upload`, {
      method: "POST",
      body: form,
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      toast.error("Font upload failed", { description: j.detail });
      return;
    }
    const j = await r.json();
    toast.success(`Font added: ${j.family}`);
    await loadFonts();
    // If a text is selected, auto-apply the newly uploaded font.
    if (selected) {
      updateOverlay(selected.id, {
        style: { ...(selected.style || {}), font_family: j.family },
      });
    }
  };

  // ── Drag on preview ──────────────────────────────────────────────────────

  const previewRef = useRef<HTMLDivElement | null>(null);

  const onDragStart = (e: React.PointerEvent, ovl: Overlay, mode: "move" | "scale") => {
    if (!previewRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    setSelectedId(ovl.id);
    (e.target as Element).setPointerCapture(e.pointerId);
    setDragging({
      id: ovl.id,
      mode,
      startX: e.clientX,
      startY: e.clientY,
      origXPct: ovl.x_pct,
      origYPct: ovl.y_pct,
      origScale: ovl.scale,
    });
  };

  const onDragMove = (e: React.PointerEvent) => {
    if (!dragging || !previewRef.current) return;
    const rect = previewRef.current.getBoundingClientRect();
    const dx = e.clientX - dragging.startX;
    const dy = e.clientY - dragging.startY;
    if (dragging.mode === "move") {
      const newX = Math.max(0, Math.min(1, dragging.origXPct + dx / rect.width));
      const newY = Math.max(0, Math.min(1, dragging.origYPct + dy / rect.height));
      updateOverlay(dragging.id, { x_pct: newX, y_pct: newY });
    } else {
      // Scale: dragging the handle outward grows the text.
      const delta = (dx + dy) / 200; // gentle factor
      const newScale = Math.max(0.3, Math.min(4, dragging.origScale + delta));
      updateOverlay(dragging.id, { scale: newScale });
    }
  };

  const onDragEnd = () => setDragging(null);

  // ── Burn ─────────────────────────────────────────────────────────────────

  const burn = async () => {
    if (!session) return;
    setBurning(true);
    setBurnProgress(0);
    setDownloadUrl("");
    try {
      const r = await fetch(`/worker-api/captions/burn`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          overlays: overlays.map((o) => ({
            text: o.text,
            template_id: o.template_id,
            style: o.style,
            start_t: o.start_t,
            end_t: o.end_t,
            x_pct: o.x_pct,
            y_pct: o.y_pct,
            scale: o.scale,
          })),
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Burn failed (${r.status})`);
      }
      const { job_id, output_filename } = await r.json();

      // Poll
      const start = Date.now();
      while (Date.now() - start < 30 * 60 * 1000) {
        await new Promise((r) => setTimeout(r, 1000));
        const sr = await fetch(`/worker-api/jobs/${job_id}`);
        if (!sr.ok) throw new Error(`Job poll failed (${sr.status})`);
        const j = await sr.json();
        setBurnProgress(Math.round((j.progress || 0) * 100));
        setBurnMsg(j.progress_message || "");
        if (j.status === "done") break;
        if (j.status === "failed") throw new Error(j.error || "Burn failed");
        if (j.status === "cancelled") throw new Error("Cancelled");
      }

      const dl = await fetch(`/worker-api/captions/burn/${job_id}/download`);
      if (!dl.ok) throw new Error("Download failed");
      const blob = await dl.blob();
      const url = URL.createObjectURL(blob);
      setDownloadUrl(url);
      setDownloadFilename(output_filename);
      toast.success("Captions burned!");
    } catch (e: any) {
      toast.error("Burn failed", { description: e.message });
    } finally {
      setBurning(false);
      setBurnMsg("");
    }
  };

  const triggerDownload = () => {
    if (!downloadUrl) return;
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = downloadFilename || "captioned.mp4";
    a.click();
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6 max-w-7xl">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Type className="h-6 w-6 text-primary" />
          Caption Studio
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Add CapCut-style text overlays. Templates from <code>data/caption_templates/</code>.
          Drop custom fonts (.ttf/.otf) — they're available in the next preview.
        </p>
      </div>

      {/* Step 1: upload */}
      {!session && (
        <Card className="p-8">
          <input
            ref={fileInputRef}
            type="file"
            accept=".mp4,.mov,.webm,.mkv,.m4v,.avi"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadSource(f);
            }}
          />
          <div
            onClick={() => !uploading && fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f && !uploading) uploadSource(f);
            }}
            className="rounded-lg border-2 border-dashed border-border/50 px-6 py-16 text-center cursor-pointer hover:border-primary/40 transition-colors"
          >
            {uploading ? (
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="h-8 w-8 animate-spin" />
                <div>Uploading…</div>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <FileVideo className="h-10 w-10" />
                <div className="text-base font-medium text-foreground">Drop a video here</div>
                <div className="text-xs">mp4 · mov · webm · mkv · avi · max 500 MB</div>
              </div>
            )}
          </div>
        </Card>
      )}

      {session && (
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-6">
          {/* Left: preview pane */}
          <div className="space-y-4">
            <Card className="p-3">
              <div
                ref={previewRef}
                onPointerMove={onDragMove}
                onPointerUp={onDragEnd}
                onPointerCancel={onDragEnd}
                className="relative bg-black rounded-md overflow-hidden mx-auto"
                style={{
                  // Pick the dimension that fits the available column space.
                  // For vertical videos the column-height (~70vh) is the
                  // binding constraint; for landscape the column-width is.
                  // We pin height to 70vh and let aspect-ratio compute the
                  // width — same logic CapCut/Reels editors use.
                  height: "70vh",
                  maxHeight: "70vh",
                  width: "auto",
                  maxWidth: "100%",
                  aspectRatio: `${session.width} / ${session.height}`,
                }}
              >
                {previewUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={previewUrl}
                    alt="preview"
                    className="absolute inset-0 w-full h-full object-contain select-none pointer-events-none"
                    draggable={false}
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center text-muted-foreground">
                    {previewLoading ? <Loader2 className="h-8 w-8 animate-spin" /> : "Loading preview…"}
                  </div>
                )}
                {/* Interactive overlay handles (do NOT render text — server PNG already has it) */}
                {overlays.map((o) => {
                  const active = o.id === selectedId;
                  return (
                    <div
                      key={o.id}
                      onPointerDown={(e) => onDragStart(e, o, "move")}
                      style={{
                        position: "absolute",
                        left: `${o.x_pct * 100}%`,
                        top: `${o.y_pct * 100}%`,
                        transform: "translate(-50%, -50%)",
                        width: `${50 * o.scale}px`,
                        height: `${50 * o.scale}px`,
                        cursor: dragging?.id === o.id ? "grabbing" : "grab",
                      }}
                      className={`flex items-center justify-center rounded-full border-2 ${
                        active
                          ? "border-primary bg-primary/20"
                          : "border-white/40 bg-white/10 hover:border-white/80"
                      }`}
                    >
                      <div
                        onPointerDown={(e) => onDragStart(e, o, "scale")}
                        className="absolute -bottom-2 -right-2 h-4 w-4 rounded-full border-2 border-primary bg-background cursor-nwse-resize"
                      />
                    </div>
                  );
                })}
                {previewLoading && previewUrl && (
                  <div className="absolute top-2 right-2 rounded-full bg-black/50 px-2 py-1 text-[10px] text-white flex items-center gap-1">
                    <Loader2 className="h-3 w-3 animate-spin" /> updating…
                  </div>
                )}
              </div>
              <div className="mt-3 flex items-center gap-3">
                <Label className="text-xs whitespace-nowrap">Preview at</Label>
                <Slider
                  value={[previewTime]}
                  min={0}
                  max={30}
                  step={0.1}
                  onValueChange={([v]) => setPreviewTime(v)}
                  className="flex-1"
                />
                <Badge variant="secondary" className="font-mono">{previewTime.toFixed(1)}s</Badge>
              </div>
            </Card>

            {/* Burn-in actions */}
            <Card className="p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold">Export</h3>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => { setSession(null); setOverlays([]); setSelectedId(null); }}
                >
                  Change source
                </Button>
              </div>
              <Button
                size="lg"
                onClick={burn}
                disabled={burning || overlays.length === 0}
                className="w-full"
              >
                {burning ? (
                  <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> {burnMsg || `Burning ${burnProgress}%`}</>
                ) : (
                  <><Sparkles className="h-4 w-4 mr-2" /> Burn captions into video</>
                )}
              </Button>
              {burning && burnProgress > 0 && (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary transition-all duration-300" style={{ width: `${burnProgress}%` }} />
                </div>
              )}
              {downloadUrl && (
                <Button onClick={triggerDownload} variant="default" className="w-full">
                  <Download className="h-4 w-4 mr-2" />
                  Download {downloadFilename}
                </Button>
              )}
            </Card>
          </div>

          {/* Right: controls */}
          <div className="space-y-4">
            {/* Overlays list */}
            <Card className="p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold">Text overlays</h3>
                <Button size="sm" onClick={addOverlay} disabled={autoBusy}>
                  <Plus className="h-4 w-4 mr-1" /> Add text
                </Button>
              </div>

              {/* Auto-transcribe row */}
              <div className="rounded-md border border-border/40 bg-muted/20 p-2.5 space-y-2">
                <div className="text-[11px] text-muted-foreground">
                  Or auto-generate from the video's audio (whisper word-level timing):
                </div>
                <div className="flex gap-1.5">
                  <select
                    value={autoLang}
                    onChange={(e) => setAutoLang(e.target.value)}
                    disabled={autoBusy}
                    className="rounded-md border border-input bg-background px-2 py-1 text-xs flex-1"
                  >
                    <option value="">Auto-detect language</option>
                    <option value="en">English</option>
                    <option value="ro">Romanian</option>
                    <option value="es">Spanish</option>
                    <option value="fr">French</option>
                    <option value="de">German</option>
                    <option value="it">Italian</option>
                    <option value="pt">Portuguese</option>
                  </select>
                  <select
                    value={autoWordsPerChunk}
                    onChange={(e) => setAutoWordsPerChunk(parseInt(e.target.value))}
                    disabled={autoBusy}
                    className="rounded-md border border-input bg-background px-2 py-1 text-xs w-24"
                    title="Words per caption chunk"
                  >
                    <option value="1">1 word</option>
                    <option value="2">2 words</option>
                    <option value="3">3 words</option>
                    <option value="4">4 words</option>
                    <option value="5">5 words</option>
                    <option value="6">6 words</option>
                  </select>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={autoTranscribe}
                  disabled={autoBusy}
                  className="w-full"
                >
                  {autoBusy ? (
                    <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> Transcribing audio…</>
                  ) : (
                    <><Sparkles className="h-3.5 w-3.5 mr-1.5" /> Auto-caption from audio</>
                  )}
                </Button>
              </div>

              <div className="space-y-1.5 max-h-40 overflow-auto">
                {overlays.length === 0 && (
                  <div className="text-xs text-muted-foreground text-center py-3">
                    No overlays. Click "Add text" or "Auto-caption".
                  </div>
                )}
                {overlays.map((o) => (
                  <div
                    key={o.id}
                    onClick={() => setSelectedId(o.id)}
                    className={`flex items-center justify-between gap-2 rounded-md px-2 py-1.5 cursor-pointer text-sm transition-colors ${
                      o.id === selectedId
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted/40"
                    }`}
                  >
                    <span className="truncate flex-1">{o.text || "(empty)"}</span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 shrink-0"
                      onClick={(e) => { e.stopPropagation(); deleteOverlay(o.id); }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </Card>

            {/* Selected overlay editor */}
            {selected && (
              <Card className="p-4 space-y-3">
                <h3 className="text-sm font-semibold">Edit overlay</h3>

                <div>
                  <Label className="text-xs">Text</Label>
                  <Textarea
                    value={selected.text}
                    onChange={(e) => updateOverlay(selected.id, { text: e.target.value })}
                    rows={2}
                    className="mt-1"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label className="text-xs">Start (s)</Label>
                    <Input
                      type="number"
                      step="0.1"
                      value={selected.start_t}
                      onChange={(e) => updateOverlay(selected.id, { start_t: parseFloat(e.target.value) || 0 })}
                    />
                  </div>
                  <div>
                    <Label className="text-xs">End (s)</Label>
                    <Input
                      type="number"
                      step="0.1"
                      value={selected.end_t}
                      onChange={(e) => updateOverlay(selected.id, { end_t: parseFloat(e.target.value) || 0 })}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <Label className="text-xs">Size</Label>
                    <Badge variant="secondary" className="text-[10px]">×{selected.scale.toFixed(2)}</Badge>
                  </div>
                  <Slider
                    value={[selected.scale]}
                    min={0.3}
                    max={4}
                    step={0.05}
                    onValueChange={([v]) => updateOverlay(selected.id, { scale: v })}
                  />
                </div>

                <div>
                  <Label className="text-xs">Font</Label>
                  <select
                    value={selected.style?.font_family || ""}
                    onChange={(e) => updateOverlay(selected.id, {
                      style: { ...(selected.style || {}), font_family: e.target.value || undefined },
                    })}
                    className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="">(use template default)</option>
                    {fonts.user.length > 0 && (
                      <optgroup label="User-uploaded">
                        {fonts.user.map((f) => (
                          <option key={f.filename} value={f.family}>{f.family}</option>
                        ))}
                      </optgroup>
                    )}
                    <optgroup label="System">
                      {fonts.system.map((f) => (
                        <option key={f} value={f}>{f}</option>
                      ))}
                    </optgroup>
                  </select>
                  <div className="mt-2 flex gap-2">
                    <input
                      ref={fontInputRef}
                      type="file"
                      accept=".ttf,.otf,.ttc"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) uploadFont(f);
                        if (fontInputRef.current) fontInputRef.current.value = "";
                      }}
                    />
                    <Button size="sm" variant="outline" onClick={() => fontInputRef.current?.click()} className="flex-1">
                      <Upload className="h-3.5 w-3.5 mr-1.5" /> Add a font
                    </Button>
                  </div>
                </div>
              </Card>
            )}

            {/* Templates picker */}
            <Card className="p-4 space-y-3">
              <h3 className="text-sm font-semibold">Templates</h3>
              <div className="grid grid-cols-2 gap-2 max-h-72 overflow-auto">
                {templates.map((t) => {
                  const active = selected?.template_id === t.id;
                  return (
                    <button
                      key={t.id}
                      onClick={() => selected && updateOverlay(selected.id, { template_id: t.id })}
                      disabled={!selected}
                      className={`text-left rounded-md border-2 p-2 transition-colors ${
                        active
                          ? "border-primary bg-primary/5"
                          : "border-border/40 hover:border-border disabled:opacity-50"
                      }`}
                    >
                      <div
                        className="rounded-sm px-2 py-1 mb-1 truncate text-xs font-bold"
                        style={{
                          fontFamily: t.font_family,
                          color: t.text_color,
                          backgroundColor: t.borderstyle === 3 ? "#000" : "transparent",
                          textShadow: t.borderstyle !== 3 ? `0 0 ${t.outline_width}px ${t.outline_color}, 0 0 ${t.outline_width}px ${t.outline_color}` : undefined,
                          textTransform: t.uppercase ? "uppercase" : "none",
                        }}
                      >
                        {t.name}
                      </div>
                      <div className="text-[10px] text-muted-foreground truncate">
                        {t.font_family} · {t.font_size}px
                      </div>
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] text-muted-foreground">
                Drop .json templates into <code>data/caption_templates/</code> to add more.
                See "Add a template" in the README for the CapCut import flow.
              </p>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
