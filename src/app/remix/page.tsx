"use client";

/**
 * Remix Pipeline — full end-to-end:
 *   URL → download → transcribe → (erase ∥ clean→TTS→desilence) → speed-match → captions
 *
 * The user picks BOTH the erase zone and the caption zone on the thumbnail
 * before kicking off, plus the engines for each stage.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import {
  Wand2, Loader2, Sparkles, Download, AlertCircle, CheckCircle2,
  Eraser, Type, Mic, FileText, Languages, Gauge,
} from "lucide-react";
import { toast } from "sonner";

// Same-origin proxy through Next.js rewrites — avoids extension content
// scripts that intercept cross-port fetches.
const WORKER_URL = "";

type Rect = { x: number; y: number; w: number; h: number };
type ActiveRect = "erase" | "caption";

interface PreviewMeta {
  title: string | null;
  thumbnail_url: string | null;
  width: number;
  height: number;
  duration: number | null;
  channel_name: string | null;
}

interface Template {
  id: string;
  name: string;
  font_family: string;
  font_size?: number;
  font_weight?: string;
  italic?: boolean;
  text_color?: string;
  outline_color?: string;
  outline_width?: number;
  uppercase?: boolean;
  borderstyle?: number;
}

interface Voice {
  id: string;
  name: string;
  gender?: string;
}

interface EngineInfo {
  id: string;
  label: string;
  ready: boolean;
  hint: string | null;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}
function getRenderedRect(el: HTMLElement, srcW: number, srcH: number) {
  const cw = el.clientWidth, ch = el.clientHeight;
  const va = srcW / srcH, ca = cw / ch;
  if (va > ca) {
    const rh = cw / va;
    return { x: 0, y: (ch - rh) / 2, w: cw, h: rh };
  }
  const rw = ch * va;
  return { x: (cw - rw) / 2, y: 0, w: rw, h: ch };
}

export default function RemixPage() {
  // ── URL + preview ──────────────────────────────────────────────────────
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState<PreviewMeta | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // ── Rects (in source pixel coords) ─────────────────────────────────────
  const [eraseRect, setEraseRect] = useState<Rect>({ x: 0, y: 0, w: 800, h: 200 });
  const [captionRect, setCaptionRect] = useState<Rect>({ x: 0, y: 0, w: 800, h: 200 });
  const [active, setActive] = useState<ActiveRect>("erase");

  // ── Erase method (mode + algorithm) ─────────────────────────────────────
  // "lama"  = inpaint mode, telea algo, LaMa GPU when available (default)
  // "telea" = inpaint mode, telea OpenCV (no GPU fallback path explicit)
  // "ns"    = inpaint mode, Navier-Stokes algorithm
  // "blur"  = ffmpeg avgblur (fastest, less invasive)
  const [eraseMethod, setEraseMethod] = useState<"lama" | "ns" | "blur">("lama");
  const [eraseAutoDetect, setEraseAutoDetect] = useState(false);

  // ── Engines ─────────────────────────────────────────────────────────────
  const [txEngines, setTxEngines] = useState<EngineInfo[]>([]);
  const [transcriptEngine, setTranscriptEngine] = useState("ollama");
  const [transcriptLang, setTranscriptLang] = useState("");

  const [ttsEngines, setTtsEngines] = useState<EngineInfo[]>([]);
  const [ttsEngine, setTtsEngine] = useState<"xtts" | "elevenlabs" | "local_clone">("xtts");
  const [ttsVoices, setTtsVoices] = useState<Voice[]>([]);
  const [ttsVoice, setTtsVoice] = useState("");
  const [ttsLanguage, setTtsLanguage] = useState("en");
  const [ttsSpeed, setTtsSpeed] = useState(1.0);

  const [templates, setTemplates] = useState<Template[]>([]);
  const [captionTemplateId, setCaptionTemplateId] = useState("bold_impact");

  // Caption style overrides (applied to all auto-generated overlays). Null/empty
  // means "use template default".
  const [captionScale, setCaptionScale] = useState(1.0);
  const [captionFontFamily, setCaptionFontFamily] = useState<string>("");
  const [captionTextColor, setCaptionTextColor] = useState<string>("");
  const [captionUppercase, setCaptionUppercase] = useState<boolean | null>(null);
  const [captionItalic, setCaptionItalic] = useState<boolean | null>(null);
  const [captionWordsPerChunk, setCaptionWordsPerChunk] = useState(1);
  const [captionStripPunct, setCaptionStripPunct] = useState(true);
  const [showAdvancedCaption, setShowAdvancedCaption] = useState(false);

  // Commentator overlay (optional, runs after caption burn). Full-frame
  // overlay — the user authors the character video at the target resolution
  // with the background chroma-keyable (typically white or green).
  interface Commentator {
    id: string;
    name: string;
    chroma_key: string | null;
    chroma_similarity?: number;
    chroma_blend?: number;
    duration?: number;
    video_available: boolean;
    thumb_available: boolean;
    ai_processed?: boolean;     // true if processed.webm exists → AI alpha mode active
    has_native_alpha?: boolean; // upload already carries alpha (e.g. CapCut webm export)
  }
  const [commentators, setCommentators] = useState<Commentator[]>([]);
  const [commentatorId, setCommentatorId] = useState<string>("");  // "" = None
  // Chroma-key override state. null = "use preset's saved value".
  // String "" = "disable keying for this run". "#RRGGBB" = use that color.
  const [chromaColor, setChromaColor] = useState<string | null>(null);
  const [chromaSimilarity, setChromaSimilarity] = useState<number | null>(null);
  const [chromaBlend, setChromaBlend] = useState<number | null>(null);
  const [uploadingCommentator, setUploadingCommentator] = useState(false);
  const commentatorFileRef = useRef<HTMLInputElement | null>(null);

  // Client-side chroma-keyed preview thumbnail. We pull the raw thumb,
  // walk pixels, and zero alpha where the chroma color matches within the
  // similarity tolerance — same idea as ffmpeg's chromakey but cheap and
  // local so the preview updates as the user drags the sliders.
  const [keyedThumbUrl, setKeyedThumbUrl] = useState<string>("");

  // Fonts list (loaded from caption_overlays endpoint — same as Caption Studio)
  interface FontsLists { system: string[]; user: { family: string; filename: string }[] }
  const [fonts, setFonts] = useState<FontsLists>({ system: [], user: [] });

  // ── Run state ──────────────────────────────────────────────────────────
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [jobStatus, setJobStatus] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadFilename, setDownloadFilename] = useState("");
  const [descriptions, setDescriptions] = useState<{
    original_translated: string;
    ai_generated: string;
  } | null>(null);
  const [copiedDesc, setCopiedDesc] = useState<"orig" | "ai" | null>(null);

  // ── Past runs ──────────────────────────────────────────────────────────
  interface PastRun {
    job_id: string;
    project_id: string;
    title: string;
    output_filename: string;
    file_size: number;
    file_available: boolean;
    finished_at: string | null;
    tts_engine?: string;
    transcript_target_lang?: string;
  }
  const [pastRuns, setPastRuns] = useState<PastRun[]>([]);
  const loadPastRuns = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/remix/recent?limit=10`);
      if (!r.ok) return;
      const j = await r.json();
      setPastRuns(j.runs || []);
    } catch { /* */ }
  }, []);

  // ── Canvas refs ────────────────────────────────────────────────────────
  const imgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderedRef = useRef({ x: 0, y: 0, w: 0, h: 0 });
  const dragRef = useRef<{
    mode: "move" | "resize";
    target: ActiveRect;
    startX: number; startY: number;
    origX: number; origY: number; origW: number; origH: number;
  } | null>(null);

  // ── Load engines / templates ────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`/worker-api/transcript/engines`);
        const j = await r.json();
        setTxEngines(j.engines || []);
      } catch { /* */ }
      try {
        const r = await fetch(`/worker-api/tts/engines`);
        const j = await r.json();
        setTtsEngines(j.engines || []);
      } catch { /* */ }
      try {
        const r = await fetch(`/worker-api/captions/templates`);
        const j = await r.json();
        setTemplates(j.templates || []);
      } catch { /* */ }
      try {
        const r = await fetch(`/worker-api/captions/fonts`);
        const j = await r.json();
        setFonts({ system: j.system || [], user: j.user || [] });
      } catch { /* */ }
      try {
        const r = await fetch(`/worker-api/commentators`);
        const j = await r.json();
        setCommentators(j.commentators || []);
      } catch { /* */ }
      loadPastRuns();
    })();
  }, [loadPastRuns]);

  // Re-key the preview thumbnail whenever the commentator or any chroma
  // setting changes. Runs entirely in the browser via <canvas>.
  useEffect(() => {
    if (!commentatorId) {
      if (keyedThumbUrl) URL.revokeObjectURL(keyedThumbUrl);
      setKeyedThumbUrl("");
      return;
    }
    const com = commentators.find((c) => c.id === commentatorId);
    if (!com || !com.thumb_available) {
      setKeyedThumbUrl("");
      return;
    }

    // Resolve effective chroma values (per-run override beats preset).
    const effColor = chromaColor !== null ? chromaColor : (com.chroma_key || "");
    const effSimilarity = chromaSimilarity !== null ? chromaSimilarity : (com.chroma_similarity ?? 0.10);
    const effBlend = chromaBlend !== null ? chromaBlend : (com.chroma_blend ?? 0.05);

    let cancelled = false;
    let prevUrl = keyedThumbUrl;

    (async () => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.src = `/worker-api/commentators/${com.id}/thumb`;
      try {
        await img.decode();
      } catch {
        return;
      }
      if (cancelled) return;

      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(img, 0, 0);

      // Only key out pixels when a color is configured. Empty effColor means
      // user explicitly disabled keying → show the thumb as-is, fully opaque.
      if (effColor && /^#[0-9A-Fa-f]{6}$/.test(effColor)) {
        const cr = parseInt(effColor.slice(1, 3), 16);
        const cg = parseInt(effColor.slice(3, 5), 16);
        const cb = parseInt(effColor.slice(5, 7), 16);
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const px = data.data;
        // Distance threshold: same shape as ffmpeg's chromakey.
        //   d < similarity            → fully transparent
        //   similarity ≤ d < +blend   → linear alpha falloff
        //   d ≥ similarity + blend    → opaque
        const simSq = (effSimilarity * 255) ** 2 * 3;     // squared, scaled
        const fullSq = ((effSimilarity + effBlend) * 255) ** 2 * 3;
        const denomSq = Math.max(1, fullSq - simSq);
        for (let i = 0; i < px.length; i += 4) {
          const dr = px[i] - cr;
          const dg = px[i + 1] - cg;
          const db = px[i + 2] - cb;
          const distSq = dr * dr + dg * dg + db * db;
          if (distSq < simSq) {
            px[i + 3] = 0;                                                 // keyed out
          } else if (distSq < fullSq) {
            px[i + 3] = Math.round(((distSq - simSq) / denomSq) * 255);    // soft edge
          }
          // else: leave alpha at 255 (opaque)
        }
        ctx.putImageData(data, 0, 0);
      }

      canvas.toBlob((blob) => {
        if (cancelled || !blob) return;
        const url = URL.createObjectURL(blob);
        setKeyedThumbUrl(url);
        if (prevUrl) URL.revokeObjectURL(prevUrl);
      }, "image/png");
    })();

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commentatorId, chromaColor, chromaSimilarity, chromaBlend, commentators]);

  const reloadCommentators = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/commentators`);
      const j = await r.json();
      setCommentators(j.commentators || []);
    } catch { /* */ }
  }, []);

  const uploadCommentator = async (file: File) => {
    const name = window.prompt("Name for this commentator (e.g. 'Grumpy Kid'):", file.name.replace(/\.[^.]+$/, ""));
    if (!name) return;
    const chroma = window.prompt(
      "Background color to remove from this video (so the main video shows through). " +
      "Common values: '#FFFFFF' for white, '#00FF00' for green screen. " +
      "Leave empty if the video already has a transparent background.",
      "#FFFFFF"
    );
    setUploadingCommentator(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("name", name);
      form.append("default_position", "fullscreen");
      form.append("default_scale", "1.0");
      if (chroma && chroma.trim()) form.append("chroma_key", chroma.trim());
      const r = await fetch(`/worker-api/commentators`, { method: "POST", body: form });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Upload failed (${r.status})`);
      }
      const j = await r.json();
      toast.success(`Added "${j.name}"`);
      await reloadCommentators();
      setCommentatorId(j.id);
      setCommentatorChroma("");
    } catch (e: any) {
      toast.error("Commentator upload failed", { description: e.message });
    } finally {
      setUploadingCommentator(false);
    }
  };

  // AI bg-removal job state — keyed by preset id.
  const [aiJobs, setAiJobs] = useState<Record<string, { jobId: string; progress: number; msg: string; done: boolean; error?: string }>>({});

  const startAiRemoval = async (presetId: string) => {
    try {
      const r = await fetch(`/worker-api/commentators/${presetId}/process-ai`, { method: "POST" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Failed (${r.status})`);
      }
      const j = await r.json();
      setAiJobs((s) => ({ ...s, [presetId]: { jobId: j.job_id, progress: 0, msg: "Queued…", done: false } }));
      toast.success("AI background removal started — ~3-5 min on GPU");

      // Poll until done
      const tick = setInterval(async () => {
        try {
          const sr = await fetch(`/worker-api/jobs/${j.job_id}`);
          if (!sr.ok) return;
          const data = await sr.json();
          setAiJobs((s) => ({
            ...s,
            [presetId]: {
              jobId: j.job_id,
              progress: Math.round((data.progress || 0) * 100),
              msg: data.progress_message || data.status,
              done: data.status === "done" || data.status === "failed" || data.status === "cancelled",
              error: data.status === "failed" ? (data.error || "Failed") : undefined,
            },
          }));
          if (data.status === "done") {
            clearInterval(tick);
            toast.success("AI background removed");
            await reloadCommentators();
          } else if (data.status === "failed") {
            clearInterval(tick);
            toast.error("AI processing failed", { description: data.error });
          }
        } catch { /* */ }
      }, 1500);
    } catch (e: any) {
      toast.error("Could not start", { description: e.message });
    }
  };

  const removeAiProcessed = async (presetId: string) => {
    if (!confirm("Remove AI-processed version? Preset will fall back to chroma key.")) return;
    try {
      const r = await fetch(`/worker-api/commentators/${presetId}/processed`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await reloadCommentators();
      toast.success("Removed");
    } catch (e: any) {
      toast.error("Failed", { description: e.message });
    }
  };

  const deleteCommentator = async (id: string) => {
    if (!confirm(`Delete commentator "${id}"?`)) return;
    try {
      const r = await fetch(`/worker-api/commentators/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      if (commentatorId === id) setCommentatorId("");
      await reloadCommentators();
      toast.success("Deleted");
    } catch (e: any) {
      toast.error("Delete failed", { description: e.message });
    }
  };

  // Load voices when TTS engine changes
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`/worker-api/tts/voices?engine=${ttsEngine}`);
        if (!r.ok) {
          setTtsVoices([]);
          return;
        }
        const j = await r.json();
        const voices: Voice[] = (j.voices || j || []).map((v: any) => ({
          id: v.id || v.voice_id || v.filename,
          name: v.name || v.id || "Unnamed",
          gender: v.gender,
        }));
        setTtsVoices(voices);
        if (voices.length > 0 && !voices.find((v) => v.id === ttsVoice)) {
          setTtsVoice(voices[0].id);
        }
      } catch {
        setTtsVoices([]);
      }
    })();
  }, [ttsEngine]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Preview URL → fetch thumbnail + dims ────────────────────────────────
  const runPreview = async () => {
    if (!url.trim()) {
      toast.error("Paste a URL first");
      return;
    }
    setPreviewLoading(true);
    setPreview(null);
    setErrorMsg("");
    try {
      const r = await fetch(`/worker-api/remix/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `Preview failed (${r.status})`);
      setPreview(j);

      // Default rects: bottom band for erase (covers existing captions),
      // bottom band for new captions too but smaller and a bit lower.
      const w = j.width || 1080;
      const h = j.height || 1920;
      setEraseRect({
        x: Math.round(w * 0.05),
        y: Math.round(h * 0.78),
        w: Math.round(w * 0.9),
        h: Math.round(h * 0.12),
      });
      setCaptionRect({
        x: Math.round(w * 0.1),
        y: Math.round(h * 0.82),
        w: Math.round(w * 0.8),
        h: Math.round(h * 0.08),
      });
    } catch (e: any) {
      setErrorMsg(e.message);
      toast.error("Preview failed", { description: e.message });
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── Canvas drawing of both rects ────────────────────────────────────────
  const draw = useCallback(() => {
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

    // Erase rect (red-ish)
    const e = eraseRect;
    const ex = rendered.x + e.x * sx, ey = rendered.y + e.y * sy;
    const ew = e.w * sx, eh = e.h * sy;
    ctx.strokeStyle = active === "erase" ? "rgba(251,113,133,1)" : "rgba(251,113,133,0.5)";
    ctx.lineWidth = active === "erase" ? 3 : 2;
    ctx.setLineDash([8, 4]);
    ctx.strokeRect(ex, ey, ew, eh);
    ctx.fillStyle = "rgba(251,113,133,0.15)";
    ctx.fillRect(ex, ey, ew, eh);
    ctx.setLineDash([]);
    ctx.fillStyle = active === "erase" ? "rgba(251,113,133,1)" : "rgba(251,113,133,0.6)";
    ctx.fillRect(ex + ew - 10, ey + eh - 10, 12, 12);
    // Label
    ctx.fillStyle = "rgba(0,0,0,0.7)";
    ctx.fillRect(ex, ey - 18, 60, 16);
    ctx.fillStyle = "rgb(251,113,133)";
    ctx.font = "11px sans-serif";
    ctx.fillText("ERASE", ex + 4, ey - 6);

    // Caption rect (amber)
    const c = captionRect;
    const cx = rendered.x + c.x * sx, cy = rendered.y + c.y * sy;
    const cw = c.w * sx, ch = c.h * sy;
    ctx.strokeStyle = active === "caption" ? "rgba(251,191,36,1)" : "rgba(251,191,36,0.5)";
    ctx.lineWidth = active === "caption" ? 3 : 2;
    ctx.setLineDash([8, 4]);
    ctx.strokeRect(cx, cy, cw, ch);
    ctx.fillStyle = "rgba(251,191,36,0.15)";
    ctx.fillRect(cx, cy, cw, ch);
    ctx.setLineDash([]);
    ctx.fillStyle = active === "caption" ? "rgba(251,191,36,1)" : "rgba(251,191,36,0.6)";
    ctx.fillRect(cx + cw - 10, cy + ch - 10, 12, 12);
    ctx.fillStyle = "rgba(0,0,0,0.7)";
    ctx.fillRect(cx, cy - 18, 80, 16);
    ctx.fillStyle = "rgb(251,191,36)";
    ctx.font = "11px sans-serif";
    ctx.fillText("CAPTION", cx + 4, cy - 6);
  }, [preview, eraseRect, captionRect, active]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const ro = new ResizeObserver(draw);
    ro.observe(img);
    return () => ro.disconnect();
  }, [draw, preview]);

  const getPos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { mx: e.clientX - r.left, my: e.clientY - r.top };
  };

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!preview) return;
    const rendered = renderedRef.current;
    const sx = rendered.w / preview.width, sy = rendered.h / preview.height;
    const rect = active === "erase" ? eraseRect : captionRect;
    const { mx, my } = getPos(e);
    const cx = rendered.x + rect.x * sx, cy = rendered.y + rect.y * sy;
    const cw = rect.w * sx, ch = rect.h * sy;
    const base = {
      target: active,
      startX: mx, startY: my,
      origX: rect.x, origY: rect.y, origW: rect.w, origH: rect.h,
    };
    if (mx >= cx + cw - 14 && my >= cy + ch - 14) {
      dragRef.current = { mode: "resize", ...base };
    } else if (mx >= cx && mx <= cx + cw && my >= cy && my <= cy + ch) {
      dragRef.current = { mode: "move", ...base };
    }
  };

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (!drag || !preview) return;
    const rendered = renderedRef.current;
    const px = preview.width / rendered.w, py = preview.height / rendered.h;
    const { mx, my } = getPos(e);
    const dx = Math.round((mx - drag.startX) * px);
    const dy = Math.round((my - drag.startY) * py);

    const setter = drag.target === "erase" ? setEraseRect : setCaptionRect;
    if (drag.mode === "move") {
      setter({
        x: clamp(drag.origX + dx, 0, preview.width - drag.origW),
        y: clamp(drag.origY + dy, 0, preview.height - drag.origH),
        w: drag.origW, h: drag.origH,
      });
    } else {
      setter({
        x: drag.origX, y: drag.origY,
        w: clamp(drag.origW + dx, 10, preview.width - drag.origX),
        h: clamp(drag.origH + dy, 10, preview.height - drag.origY),
      });
    }
  };

  const onMouseUp = () => { dragRef.current = null; };

  // ── Submit ─────────────────────────────────────────────────────────────
  const start = async () => {
    if (!preview) { toast.error("Get preview first"); return; }
    if (!ttsVoice) { toast.error("Pick a voice"); return; }

    const payload = {
      url: url.trim(),
      title: preview.title || undefined,
      erase_zone: {
        x: eraseRect.x, y: eraseRect.y, w: eraseRect.w, h: eraseRect.h,
        src_w: preview.width, src_h: preview.height,
      },
      caption_zone: {
        x: captionRect.x, y: captionRect.y, w: captionRect.w, h: captionRect.h,
        src_w: preview.width, src_h: preview.height,
      },
      // Map the single UI choice to backend mode+algorithm. LaMa/TELEA share
      // the inpaint path (LaMa is preferred at runtime if installed).
      erase_mode: eraseMethod === "blur" ? "blur" : "inpaint",
      erase_algorithm: eraseMethod === "ns" ? "ns" : "telea",
      erase_auto_detect: eraseAutoDetect,
      transcript_engine: transcriptEngine,
      transcript_target_lang: transcriptLang || null,
      tts_engine: ttsEngine,
      tts_voice_id: ttsVoice,
      tts_language: ttsLanguage,
      tts_speed: ttsSpeed,
      caption_template_id: captionTemplateId,
      caption_font_family: captionFontFamily || null,
      caption_scale: captionScale,
      caption_text_color: captionTextColor || null,
      caption_uppercase: captionUppercase,
      caption_italic: captionItalic,
      caption_words_per_chunk: captionWordsPerChunk,
      caption_strip_punct: captionStripPunct,
      commentator_preset_id: commentatorId || null,
      // Position/scale are ignored by the new backend (full-frame overlay);
      // we still send them as null to keep the schema strict.
      commentator_position: null,
      commentator_scale: null,
      // Chroma override: null = use preset's saved value
      commentator_chroma_color: chromaColor,
      commentator_chroma_similarity: chromaSimilarity,
      commentator_chroma_blend: chromaBlend,
    };

    setErrorMsg("");
    setProgress(0);
    setProgressMsg("Submitting…");
    setJobStatus("queued");
    setDownloadUrl("");
    setDescriptions(null);
    setCopiedDesc(null);

    try {
      const r = await fetch(`/worker-api/remix/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `Start failed (${r.status})`);
      setJobId(j.job_id);
      toast.success("Pipeline started");
    } catch (e: any) {
      setErrorMsg(e.message);
      setJobStatus("");
      toast.error("Failed to start", { description: e.message });
    }
  };

  // ── Poll job ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await fetch(`/worker-api/jobs/${jobId}`);
        if (!r.ok) return;
        const j = await r.json();
        if (stop) return;
        setProgress(Math.round((j.progress || 0) * 100));
        setProgressMsg(j.progress_message || "");
        setJobStatus(j.status);
        if (j.status === "done") {
          stop = true;
          // Don't blob-fetch the file (extensions can block big binary
          // responses). Just grab the metadata so we know the filename;
          // the actual download happens via a native <a download> link.
          try {
            const meta = await fetch(`/worker-api/remix/${jobId}/result`);
            if (meta.ok) {
              const m = await meta.json();
              setDownloadFilename(m.output_filename || `remix-${jobId}.mp4`);
              if (m.descriptions) setDescriptions(m.descriptions);
            } else {
              setDownloadFilename(`remix-${jobId}.mp4`);
            }
          } catch {
            setDownloadFilename(`remix-${jobId}.mp4`);
          }
          // Anchor href points directly at the server endpoint so the browser
          // handles the download itself (no JS fetch in the middle).
          setDownloadUrl(`/worker-api/remix/${jobId}/download`);
          // Refresh the past-runs list so the new one shows up.
          loadPastRuns();
        } else if (j.status === "failed") {
          stop = true;
          setErrorMsg(j.error || "Pipeline failed");
        } else if (j.status === "cancelled") {
          stop = true;
          setErrorMsg("Cancelled");
        }
      } catch { /* */ }
    };
    tick();
    const id = setInterval(tick, 1500);
    return () => { stop = true; clearInterval(id); };
  }, [jobId]);

  const handleDownload = () => {
    if (!downloadUrl) return;
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = downloadFilename || "remix.mp4";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  // ── Stage labels for the progress dashboard ────────────────────────────
  const stages = useMemo(() => {
    const ranges = [
      { label: "Download", lo: 0, hi: 10 },
      { label: "Transcribe", lo: 10, hi: 20 },
      { label: "Erase  ‖  Voice", lo: 20, hi: 65 },
      { label: "Speed-match", lo: 65, hi: 75 },
      { label: "Captions", lo: 75, hi: 95 },
      { label: "Descriptions", lo: 95, hi: 100 },
    ];
    return ranges.map((s) => ({
      ...s,
      done: progress >= s.hi,
      active: progress >= s.lo && progress < s.hi,
    }));
  }, [progress]);

  const isRunning = jobStatus === "queued" || jobStatus === "running";

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Wand2 className="h-6 w-6 text-primary" />
          Remix Pipeline
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Download → erase a zone → re-narrate with a new voice → time-match → caption.
          Pick all the engines + both regions before starting.
        </p>
      </div>

      {/* Step 1: URL */}
      <Card className="p-4 space-y-3 border-border/40">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          1. Source URL
        </div>
        <div className="flex gap-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.tiktok.com/..."
            disabled={!!jobId}
          />
          <Button onClick={runPreview} disabled={previewLoading || !!jobId}>
            {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Preview"}
          </Button>
        </div>
      </Card>

      {/* Step 2: Pick zones */}
      {preview && (
        <Card className="p-4 space-y-3 border-border/40">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
              2. Pick the erase + caption zones
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={active === "erase" ? "default" : "outline"}
                onClick={() => setActive("erase")}
                className={active === "erase" ? "" : "border-rose-500/40 text-rose-300"}
              >
                <Eraser className="h-3.5 w-3.5 mr-1.5" />
                Erase zone
              </Button>
              <Button
                size="sm"
                variant={active === "caption" ? "default" : "outline"}
                onClick={() => setActive("caption")}
                className={active === "caption" ? "" : "border-amber-500/40 text-amber-300"}
              >
                <Type className="h-3.5 w-3.5 mr-1.5" />
                Caption zone
              </Button>
            </div>
          </div>
          <div className="relative mx-auto" style={{ maxWidth: "400px" }}>
            {preview.thumbnail_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                ref={imgRef}
                src={preview.thumbnail_url}
                alt="thumb"
                className="block w-full rounded-md bg-black"
                onLoad={draw}
              />
            )}
            <canvas
              ref={canvasRef}
              onMouseDown={onMouseDown}
              onMouseMove={onMouseMove}
              onMouseUp={onMouseUp}
              onMouseLeave={onMouseUp}
              className="absolute inset-0 cursor-crosshair"
            />
            {/* Live caption preview — positioned at the caption rect, styled
                per the chosen template + overrides. CSS-only, instant. */}
            {(() => {
              const tpl = templates.find((t) => t.id === captionTemplateId);
              if (!tpl) return null;
              const srcW = preview.width || 1;
              const srcH = preview.height || 1;
              const leftPct = ((captionRect.x + captionRect.w / 2) / srcW) * 100;
              const topPct = ((captionRect.y + captionRect.h / 2) / srcH) * 100;
              const widthPct = (captionRect.w / srcW) * 100;
              // Display-pixel size of the caption rect inside the thumbnail.
              // 400 = the mx-auto maxWidth above; height follows aspect ratio.
              const displayH = (captionRect.h / srcH) * (400 * (srcH / srcW));
              // Use the auto-fit scale (matches backend) × user override.
              const autoFit = Math.max(0.5, Math.min(3.0, (captionRect.h / srcH) * 4.0));
              const effectiveScale = autoFit * captionScale;
              // Approximate libass font size to display pixels: tpl.font_size
              // is in source-pixel units. Scale it to display via thumb height.
              // displayPxPerSourcePx = thumbnail display width (400px) / source width.
              // We bump the floor to 14px so the preview is always readable
              // even when the caption rect is tiny relative to source.
              const displayPxPerSrc = 400 / srcW;
              const fontPx = Math.max(
                14,
                (tpl.font_size || 64) * effectiveScale * displayPxPerSrc
              );
              const family = captionFontFamily || tpl.font_family || "Arial Black";
              const color = captionTextColor || tpl.text_color || "#ffffff";
              const isItalic = captionItalic ?? tpl.italic ?? false;
              const isUpper = captionUppercase ?? tpl.uppercase ?? false;
              const boxBg = tpl.borderstyle === 3 ? "rgba(0,0,0,0.85)" : "transparent";
              const outlineW = tpl.outline_width || 0;
              const outlineColor = tpl.outline_color || "#000";
              const textShadow =
                tpl.borderstyle !== 3 && outlineW > 0
                  ? `0 0 ${outlineW}px ${outlineColor}, 0 0 ${outlineW}px ${outlineColor}, 1px 1px 0 ${outlineColor}, -1px -1px 0 ${outlineColor}, 1px -1px 0 ${outlineColor}, -1px 1px 0 ${outlineColor}`
                  : "none";
              const weight = (tpl.font_weight || "Bold").toLowerCase();
              const isHeavy = weight === "bold" || weight === "black" || weight === "heavy";
              return (
                <div
                  className="absolute pointer-events-none flex items-center justify-center text-center"
                  style={{
                    left: `${leftPct}%`,
                    top: `${topPct}%`,
                    width: `${widthPct}%`,
                    transform: "translate(-50%, -50%)",
                    zIndex: 10,
                  }}
                >
                  <span
                    style={{
                      fontFamily: family,
                      fontSize: `${fontPx}px`,
                      color,
                      fontStyle: isItalic ? "italic" : "normal",
                      fontWeight: isHeavy ? 900 : 400,
                      textTransform: isUpper ? "uppercase" : "none",
                      backgroundColor: boxBg,
                      textShadow,
                      padding: tpl.borderstyle === 3 ? "0.1em 0.4em" : 0,
                      lineHeight: 1.1,
                      maxWidth: "100%",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {captionWordsPerChunk === 1 ? "Sample" : "Sample caption"}{captionStripPunct ? "" : "."}
                  </span>
                </div>
              );
            })()}

            {/* Live commentator preview — full-frame overlay with the chroma
                key applied client-side. Matches what ffmpeg will produce at
                burn time: opaque character + transparent keyed-out background. */}
            {commentatorId && keyedThumbUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={keyedThumbUrl}
                alt="commentator preview"
                draggable={false}
                className="absolute pointer-events-none select-none"
                style={{
                  inset: 0,
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  zIndex: 20,
                }}
              />
            )}
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
            <div className="rounded bg-rose-500/10 border border-rose-500/30 p-2 text-rose-300">
              ERASE: {eraseRect.x},{eraseRect.y} · {eraseRect.w}×{eraseRect.h}
            </div>
            <div className="rounded bg-amber-500/10 border border-amber-500/30 p-2 text-amber-300">
              CAPTION: {captionRect.x},{captionRect.y} · {captionRect.w}×{captionRect.h}
            </div>
          </div>
        </Card>
      )}

      {/* Step 3: Engines */}
      {preview && (
        <Card className="p-4 space-y-4 border-border/40">
          <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
            3. Engines
          </div>

          {/* Transcript */}
          {/* Erase engine — picks the algorithm used to remove the source caption */}
          <div>
            <Label className="text-xs flex items-center gap-1.5">
              <Eraser className="h-3 w-3" /> Erase engine
            </Label>
            <div className="mt-1.5 grid grid-cols-3 gap-2">
              {([
                { id: "lama", label: "LaMa / TELEA", desc: "Neural inpaint (GPU). Best quality. Slow." },
                { id: "ns",   label: "Navier–Stokes", desc: "Classical OpenCV. Smoother on gradients." },
                { id: "blur", label: "Blur",         desc: "ffmpeg avgblur. Fastest. Less invasive." },
              ] as const).map((opt) => {
                const active = eraseMethod === opt.id;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setEraseMethod(opt.id)}
                    disabled={!!jobId}
                    className={`text-left rounded-md border-2 p-2 transition-colors ${
                      active
                        ? "border-primary bg-primary/5"
                        : "border-border/40 hover:border-border disabled:opacity-50"
                    }`}
                  >
                    <div className="text-xs font-semibold">{opt.label}</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">{opt.desc}</div>
                  </button>
                );
              })}
            </div>

            {/* Auto-detect toggle — applies only to inpaint modes */}
            {eraseMethod !== "blur" && (
              <label className="mt-2 flex items-start gap-2 rounded-md border border-border/40 bg-muted/20 p-2.5 cursor-pointer hover:border-border transition-colors">
                <input
                  type="checkbox"
                  checked={eraseAutoDetect}
                  onChange={(e) => setEraseAutoDetect(e.target.checked)}
                  disabled={!!jobId}
                  className="mt-0.5 h-4 w-4 rounded border-input"
                />
                <div className="flex-1">
                  <div className="text-xs font-medium">Auto-detect caption text (OCR)</div>
                  <div className="text-[10px] text-muted-foreground mt-0.5">
                    Scans video with OCR and inpaints only frames that actually contain text, using
                    tight per-segment bboxes. Cleaner results when captions move or appear only briefly.
                    Adds ~30–60s for the OCR pass.
                  </div>
                </div>
              </label>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs flex items-center gap-1.5"><FileText className="h-3 w-3" /> Transcript cleaner</Label>
              <select
                value={transcriptEngine}
                onChange={(e) => setTranscriptEngine(e.target.value)}
                disabled={!!jobId}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                {txEngines.map((e) => (
                  <option key={e.id} value={e.id} disabled={!e.ready}>
                    {e.label}{e.ready ? "" : " — needs key"}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <Label className="text-xs flex items-center gap-1.5"><Languages className="h-3 w-3" /> Target language</Label>
              <select
                value={transcriptLang}
                onChange={(e) => setTranscriptLang(e.target.value)}
                disabled={!!jobId}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                <option value="">Keep original</option>
                <option value="en">🇬🇧 English</option>
                <option value="ro">🇷🇴 Romanian</option>
              </select>
            </div>
          </div>

          {/* TTS */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label className="text-xs flex items-center gap-1.5"><Mic className="h-3 w-3" /> Voice engine</Label>
              <select
                value={ttsEngine}
                onChange={(e) => setTtsEngine(e.target.value as any)}
                disabled={!!jobId}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                {ttsEngines.map((e) => (
                  <option key={e.id} value={e.id} disabled={!e.ready}>
                    {e.label}{e.ready ? "" : " — needs setup"}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <Label className="text-xs">Voice</Label>
              <select
                value={ttsVoice}
                onChange={(e) => setTtsVoice(e.target.value)}
                disabled={!!jobId || ttsVoices.length === 0}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                {ttsVoices.length === 0 && <option value="">(no voices)</option>}
                {ttsVoices.map((v) => (
                  <option key={v.id} value={v.id}>{v.name}{v.gender ? ` · ${v.gender}` : ""}</option>
                ))}
              </select>
            </div>
            <div>
              <Label className="text-xs">TTS language</Label>
              <select
                value={ttsLanguage}
                onChange={(e) => setTtsLanguage(e.target.value)}
                disabled={!!jobId}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                <option value="en">English</option>
                <option value="ro">Romanian</option>
                <option value="es">Spanish</option>
                <option value="fr">French</option>
                <option value="de">German</option>
                <option value="it">Italian</option>
                <option value="pt">Portuguese</option>
              </select>
            </div>
          </div>

          {/* Voice speed — hidden for local_clone (Piper/OpenVoice has no speed knob) */}
          {ttsEngine !== "local_clone" && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <Label className="text-xs flex items-center gap-1.5">
                  <Gauge className="h-3 w-3" /> Voice speed
                </Label>
                <Badge variant="secondary" className="text-[10px] font-mono">
                  {ttsSpeed.toFixed(2)}×
                </Badge>
              </div>
              <Slider
                value={[Math.max(
                  ttsEngine === "elevenlabs" ? 0.7 : 0.5,
                  Math.min(ttsEngine === "elevenlabs" ? 1.2 : 2.0, ttsSpeed)
                )]}
                min={ttsEngine === "elevenlabs" ? 0.7 : 0.5}
                max={ttsEngine === "elevenlabs" ? 1.2 : 2.0}
                step={0.05}
                onValueChange={([v]) => setTtsSpeed(v)}
                disabled={!!jobId}
              />
              <p className="text-[10px] text-muted-foreground mt-1">
                {ttsEngine === "elevenlabs"
                  ? "ElevenLabs range: 0.7–1.2× (supported on multilingual_v2 / turbo / flash)."
                  : "XTTS range: 0.5–2.0×."}
              </p>
            </div>
          )}

          {/* Caption chunking — words per chunk + strip punctuation */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs">Words per caption</Label>
              <select
                value={captionWordsPerChunk}
                onChange={(e) => setCaptionWordsPerChunk(parseInt(e.target.value))}
                disabled={!!jobId}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                <option value={1}>1 word (TikTok)</option>
                <option value={2}>2 words</option>
                <option value={3}>3 words</option>
                <option value={4}>4 words</option>
                <option value={5}>5 words</option>
                <option value={6}>6 words</option>
              </select>
            </div>
            <div>
              <Label className="text-xs">Punctuation</Label>
              <select
                value={captionStripPunct ? "strip" : "keep"}
                onChange={(e) => setCaptionStripPunct(e.target.value === "strip")}
                disabled={!!jobId}
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              >
                <option value="strip">Remove .,!?;:"' …</option>
                <option value="keep">Keep punctuation</option>
              </select>
            </div>
          </div>

          {/* Caption template — visual picker (same look as Caption Studio) */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <Label className="text-xs flex items-center gap-1.5"><Type className="h-3 w-3" /> Caption template</Label>
              <button
                type="button"
                onClick={() => setShowAdvancedCaption((v) => !v)}
                className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              >
                {showAdvancedCaption ? "Hide style overrides" : "Style overrides"}
              </button>
            </div>
            <div className="mt-1.5 grid grid-cols-2 md:grid-cols-4 gap-2 max-h-72 overflow-auto">
              {templates.map((t) => {
                const active = captionTemplateId === t.id;
                const previewBg = t.borderstyle === 3 ? "#000" : "transparent";
                const previewShadow =
                  t.borderstyle !== 3 && (t.outline_width || 0) > 0
                    ? `0 0 ${t.outline_width}px ${t.outline_color || "#000"}, 0 0 ${t.outline_width}px ${t.outline_color || "#000"}`
                    : undefined;
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setCaptionTemplateId(t.id)}
                    disabled={!!jobId}
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
                        color: t.text_color || "#fff",
                        backgroundColor: previewBg,
                        textShadow: previewShadow,
                        textTransform: t.uppercase ? "uppercase" : "none",
                        fontStyle: t.italic ? "italic" : "normal",
                      }}
                    >
                      {t.name}
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {t.font_family}
                      {t.font_size ? ` · ${t.font_size}px` : ""}
                    </div>
                  </button>
                );
              })}
            </div>

            {showAdvancedCaption && (
              <div className="mt-3 rounded-md border border-border/40 bg-muted/20 p-3 space-y-3">
                <div className="text-[11px] text-muted-foreground">
                  Overrides applied on top of the chosen template. Leave blank to use template defaults.
                </div>

                {/* Font family */}
                <div>
                  <Label className="text-xs">Font</Label>
                  <select
                    value={captionFontFamily}
                    onChange={(e) => setCaptionFontFamily(e.target.value)}
                    disabled={!!jobId}
                    className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
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
                </div>

                {/* Scale */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <Label className="text-xs">Size multiplier</Label>
                    <Badge variant="secondary" className="text-[10px] font-mono">×{captionScale.toFixed(2)}</Badge>
                  </div>
                  <Slider
                    value={[captionScale]}
                    min={0.3} max={3} step={0.05}
                    onValueChange={([v]) => setCaptionScale(v)}
                    disabled={!!jobId}
                  />
                  <p className="text-[10px] text-muted-foreground mt-1">
                    Auto-fits to the caption zone height by default. This multiplies that auto-size.
                  </p>
                </div>

                {/* Text color */}
                <div>
                  <Label className="text-xs">Text color</Label>
                  <div className="mt-1 flex items-center gap-2">
                    <input
                      type="color"
                      value={captionTextColor || "#ffffff"}
                      onChange={(e) => setCaptionTextColor(e.target.value)}
                      disabled={!!jobId}
                      className="h-9 w-12 rounded-md border border-input bg-background cursor-pointer disabled:opacity-50"
                    />
                    <Input
                      value={captionTextColor}
                      onChange={(e) => setCaptionTextColor(e.target.value)}
                      placeholder="(use template default)"
                      disabled={!!jobId}
                      className="flex-1 text-xs font-mono"
                    />
                    {captionTextColor && (
                      <button
                        type="button"
                        onClick={() => setCaptionTextColor("")}
                        disabled={!!jobId}
                        className="text-[11px] text-muted-foreground hover:text-foreground"
                      >
                        Reset
                      </button>
                    )}
                  </div>
                </div>

                {/* Boolean toggles */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs">Uppercase</Label>
                    <select
                      value={captionUppercase === null ? "" : (captionUppercase ? "true" : "false")}
                      onChange={(e) => setCaptionUppercase(e.target.value === "" ? null : e.target.value === "true")}
                      disabled={!!jobId}
                      className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs"
                    >
                      <option value="">(template default)</option>
                      <option value="true">Force ON</option>
                      <option value="false">Force OFF</option>
                    </select>
                  </div>
                  <div>
                    <Label className="text-xs">Italic</Label>
                    <select
                      value={captionItalic === null ? "" : (captionItalic ? "true" : "false")}
                      onChange={(e) => setCaptionItalic(e.target.value === "" ? null : e.target.value === "true")}
                      disabled={!!jobId}
                      className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs"
                    >
                      <option value="">(template default)</option>
                      <option value="true">Force ON</option>
                      <option value="false">Force OFF</option>
                    </select>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Commentator picker — optional layer that gets composited AFTER captions */}
          <div className="pt-3 border-t border-border/40">
            <div className="flex items-center justify-between mb-2">
              <Label className="text-xs flex items-center gap-1.5">
                <Mic className="h-3 w-3" /> Commentator overlay
              </Label>
              <input
                ref={commentatorFileRef}
                type="file"
                accept=".mp4,.mov,.webm,.mkv"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadCommentator(f);
                  if (commentatorFileRef.current) commentatorFileRef.current.value = "";
                }}
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={!!jobId || uploadingCommentator}
                onClick={() => commentatorFileRef.current?.click()}
                className="h-7 text-[11px]"
              >
                {uploadingCommentator ? (
                  <><Loader2 className="h-3 w-3 mr-1 animate-spin" />Uploading…</>
                ) : (
                  <>+ Add new</>
                )}
              </Button>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {/* None card */}
              <button
                type="button"
                onClick={() => setCommentatorId("")}
                disabled={!!jobId}
                className={`text-left rounded-md border-2 p-2 transition-colors ${
                  commentatorId === ""
                    ? "border-primary bg-primary/5"
                    : "border-border/40 hover:border-border disabled:opacity-50"
                }`}
              >
                <div className="h-16 flex items-center justify-center rounded bg-muted/30 text-[10px] text-muted-foreground mb-1.5">
                  no overlay
                </div>
                <div className="text-xs font-semibold">None</div>
                <div className="text-[10px] text-muted-foreground">Skip this stage</div>
              </button>

              {commentators.map((c) => {
                const active = commentatorId === c.id;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => {
                      setCommentatorId(c.id);
                      // Clear any per-run override state — start fresh from the preset's saved chroma.
                      setChromaColor(null);
                      setChromaSimilarity(null);
                      setChromaBlend(null);
                    }}
                    disabled={!!jobId}
                    className={`text-left rounded-md border-2 p-2 transition-colors relative group ${
                      active
                        ? "border-primary bg-primary/5"
                        : "border-border/40 hover:border-border disabled:opacity-50"
                    }`}
                  >
                    <div className="h-16 rounded bg-black overflow-hidden mb-1.5 flex items-center justify-center">
                      {c.thumb_available ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={`/worker-api/commentators/${c.id}/thumb`}
                          alt={c.name}
                          className="h-full object-contain"
                        />
                      ) : (
                        <div className="text-[10px] text-muted-foreground">no thumb</div>
                      )}
                    </div>
                    <div className="text-xs font-semibold truncate">{c.name}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {c.duration ? `${c.duration.toFixed(1)}s loop` : c.id}
                    </div>
                    {/* Delete on hover */}
                    <span
                      onClick={(ev) => {
                        ev.stopPropagation();
                        deleteCommentator(c.id);
                      }}
                      className="absolute top-1 right-1 h-5 w-5 rounded-full bg-destructive/80 text-destructive-foreground text-[10px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
                      title="Delete"
                    >
                      ×
                    </span>
                  </button>
                );
              })}
            </div>

            {commentatorId && (() => {
              const com = commentators.find((c) => c.id === commentatorId);
              if (!com) return null;
              // Effective values shown in the controls: per-run override
              // takes precedence over the preset's saved values.
              const effectiveColor =
                chromaColor !== null ? chromaColor : (com.chroma_key || "");
              const effectiveSimilarity =
                chromaSimilarity !== null ? chromaSimilarity : (com.chroma_similarity ?? 0.10);
              const effectiveBlend =
                chromaBlend !== null ? chromaBlend : (com.chroma_blend ?? 0.05);
              const isKeyingOff = effectiveColor === "";

              const saveToPreset = async () => {
                try {
                  const body: any = {
                    chroma_similarity: effectiveSimilarity,
                    chroma_blend: effectiveBlend,
                  };
                  // Only send chroma_key if user explicitly changed it
                  if (chromaColor !== null) body.chroma_key = chromaColor;
                  const r = await fetch(`/worker-api/commentators/${com.id}/chroma`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                  });
                  if (!r.ok) {
                    const j = await r.json().catch(() => ({}));
                    throw new Error(j.detail || `Save failed (${r.status})`);
                  }
                  toast.success("Saved to preset");
                  await reloadCommentators();
                  // Reset overrides — they're now baked into the preset.
                  setChromaColor(null);
                  setChromaSimilarity(null);
                  setChromaBlend(null);
                } catch (e: any) {
                  toast.error("Save failed", { description: e.message });
                }
              };

              const hasOverride =
                chromaColor !== null || chromaSimilarity !== null || chromaBlend !== null;

              return (
                <div className="mt-3 rounded-md border border-border/40 bg-muted/20 p-3 space-y-3">
                  {/* Native alpha badge — supersedes everything below */}
                  {com.has_native_alpha && (
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-2.5">
                      <div className="text-xs font-semibold flex items-center gap-1.5 text-emerald-300">
                        <Sparkles className="h-3 w-3" />
                        Native alpha channel detected
                        <Badge variant="outline" className="text-[10px] text-emerald-400 border-emerald-400/40">
                          active
                        </Badge>
                      </div>
                      <p className="text-[10px] text-muted-foreground mt-1">
                        Your upload already carries alpha transparency
                        (CapCut/Premiere/DaVinci export with alpha). The pipeline uses
                        it directly — no chroma keying, no AI processing needed. Cleanest result.
                      </p>
                    </div>
                  )}

                  {/* AI background removal — supersedes chroma key when active. Hidden when native alpha is present. */}
                  {!com.has_native_alpha && (
                  <div className="rounded-md border border-primary/30 bg-primary/5 p-2.5 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="text-xs font-semibold flex items-center gap-1.5">
                          <Sparkles className="h-3 w-3 text-primary" />
                          AI background removal
                          {com.ai_processed && (
                            <Badge variant="outline" className="text-[10px] text-emerald-400 border-emerald-400/40">
                              active
                            </Badge>
                          )}
                        </div>
                        <p className="text-[10px] text-muted-foreground mt-0.5">
                          Uses U²-Net (same kind of model as CapCut's background remove).
                          Works on any background — no green screen required.
                          One-time processing per preset, then cached forever.
                        </p>
                      </div>
                      {com.ai_processed ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => removeAiProcessed(com.id)}
                          disabled={!!jobId}
                          className="h-7 text-[11px] shrink-0"
                        >
                          Remove
                        </Button>
                      ) : aiJobs[com.id] && !aiJobs[com.id].done ? (
                        <div className="text-[11px] text-muted-foreground shrink-0 text-right">
                          <div className="flex items-center gap-1.5">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            <span>{aiJobs[com.id].progress}%</span>
                          </div>
                          <div className="text-[10px] mt-0.5 max-w-[180px] truncate">
                            {aiJobs[com.id].msg}
                          </div>
                        </div>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => startAiRemoval(com.id)}
                          disabled={!!jobId}
                          className="h-7 text-[11px] shrink-0"
                        >
                          Process with AI
                        </Button>
                      )}
                    </div>
                    {com.ai_processed && (
                      <p className="text-[10px] text-emerald-400/90">
                        The chroma-key settings below are ignored while AI mode is active.
                      </p>
                    )}
                  </div>
                  )}

                  {!com.has_native_alpha && (<>
                  <div className="flex items-center justify-between">
                    <Label className={`text-xs font-semibold ${com.ai_processed ? "text-muted-foreground line-through" : ""}`}>
                      Background to remove (chroma key)
                    </Label>
                    {hasOverride && !com.ai_processed && (
                      <Badge variant="outline" className="text-[10px] text-amber-400 border-amber-400/40">
                        unsaved overrides
                      </Badge>
                    )}
                  </div>

                  {/* Color picker + hex input + "off" toggle */}
                  <div>
                    <Label className="text-xs">Color</Label>
                    <div className="mt-1 flex items-center gap-2">
                      <input
                        type="color"
                        value={effectiveColor || "#FFFFFF"}
                        onChange={(e) => setChromaColor(e.target.value)}
                        disabled={!!jobId || isKeyingOff}
                        className="h-9 w-12 rounded-md border border-input bg-background cursor-pointer disabled:opacity-50"
                      />
                      <Input
                        value={effectiveColor}
                        onChange={(e) => setChromaColor(e.target.value)}
                        placeholder="#FFFFFF or empty to disable"
                        disabled={!!jobId}
                        className="flex-1 text-xs font-mono"
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant={isKeyingOff ? "default" : "outline"}
                        onClick={() => setChromaColor(isKeyingOff ? (com.chroma_key || "#FFFFFF") : "")}
                        disabled={!!jobId}
                        className="text-[11px] h-9 shrink-0"
                      >
                        {isKeyingOff ? "Keying OFF" : "Turn OFF"}
                      </Button>
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-1">
                      {isKeyingOff
                        ? "Chroma keying disabled — the entire commentator video will cover the main one."
                        : "Pixels matching this color (and similar ones) become transparent so the main video shows through."}
                    </p>
                  </div>

                  {/* Similarity slider */}
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <Label className="text-xs">Similarity tolerance</Label>
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        {(effectiveSimilarity * 100).toFixed(0)}%
                      </Badge>
                    </div>
                    <Slider
                      value={[effectiveSimilarity]}
                      min={0.01} max={0.5} step={0.01}
                      onValueChange={([v]) => setChromaSimilarity(v)}
                      disabled={!!jobId || isKeyingOff}
                    />
                    <p className="text-[10px] text-muted-foreground mt-1">
                      How aggressively similar colors are also keyed out. Higher = more removed
                      (good if your background has JPEG compression or shading).
                    </p>
                  </div>

                  {/* Blend slider */}
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <Label className="text-xs">Edge softness</Label>
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        {(effectiveBlend * 100).toFixed(0)}%
                      </Badge>
                    </div>
                    <Slider
                      value={[effectiveBlend]}
                      min={0.0} max={0.3} step={0.01}
                      onValueChange={([v]) => setChromaBlend(v)}
                      disabled={!!jobId || isKeyingOff}
                    />
                    <p className="text-[10px] text-muted-foreground mt-1">
                      Soft edges blur the boundary between kept and removed pixels.
                      0 = hard cut, higher = softer transition.
                    </p>
                  </div>

                  <div className="flex items-center justify-between gap-2 pt-1">
                    <p className="text-[10px] text-muted-foreground flex-1">
                      Changes apply to this run. Use "Save to preset" to make them the default.
                    </p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={saveToPreset}
                      disabled={!!jobId || !hasOverride}
                      className="text-[11px] h-7 shrink-0"
                    >
                      Save to preset
                    </Button>
                  </div>
                  </>)}
                </div>
              );
            })()}
          </div>
        </Card>
      )}

      {/* Step 4: Start */}
      {preview && (
        <Card className="p-4 space-y-3 border-border/40">
          <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
            4. Run
          </div>
          {!jobId && (
            <Button size="lg" onClick={start} className="w-full">
              <Sparkles className="h-4 w-4 mr-2" />
              Run remix pipeline
            </Button>
          )}

          {jobId && (
            <>
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium">{progressMsg || jobStatus}</span>
                  <Badge variant="secondary">{progress}%</Badge>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary transition-all duration-300" style={{ width: `${progress}%` }} />
                </div>
              </div>
              <div className="grid grid-cols-5 gap-1.5">
                {stages.map((s) => (
                  <div
                    key={s.label}
                    className={`rounded-md border px-2 py-1.5 text-[10px] text-center ${
                      s.done
                        ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                        : s.active
                        ? "bg-primary/10 border-primary/40 text-primary"
                        : "bg-muted/30 border-border/40 text-muted-foreground"
                    }`}
                  >
                    <div className="font-semibold">{s.label}</div>
                    {s.done && <CheckCircle2 className="h-3 w-3 mx-auto mt-0.5" />}
                    {s.active && <Loader2 className="h-3 w-3 mx-auto mt-0.5 animate-spin" />}
                  </div>
                ))}
              </div>
            </>
          )}

          {errorMsg && (
            <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <div>{errorMsg}</div>
            </div>
          )}

          {descriptions && (descriptions.original_translated || descriptions.ai_generated) && (
            <div className="space-y-3">
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
                Video descriptions
              </div>
              {(["orig", "ai"] as const).map((kind) => {
                const text = kind === "orig"
                  ? descriptions.original_translated
                  : descriptions.ai_generated;
                if (!text) {
                  return (
                    <div
                      key={kind}
                      className="rounded-md border border-dashed border-border/40 bg-muted/30 p-3 text-xs text-muted-foreground"
                    >
                      {kind === "orig"
                        ? "Original description: source had no description to translate."
                        : "AI description: could not be generated (engine error)."}
                    </div>
                  );
                }
                return (
                  <div key={kind} className="rounded-md border border-border/40 bg-card p-3 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">
                        {kind === "orig" ? "Original (translated)" : "AI-generated (from transcript)"}
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(text);
                          setCopiedDesc(kind);
                          setTimeout(() => setCopiedDesc((c) => (c === kind ? null : c)), 1500);
                        }}
                        className="text-[11px] text-primary hover:underline"
                      >
                        {copiedDesc === kind ? "Copied!" : "Copy"}
                      </button>
                    </div>
                    <div className="text-sm leading-relaxed whitespace-pre-wrap">{text}</div>
                  </div>
                );
              })}
            </div>
          )}

          {downloadUrl && (
            // Native anchor download — no JS fetch, so browser extensions
            // can't intercept the response. The href is the proxy URL; the
            // `download` attribute forces save instead of inline open.
            <a
              href={downloadUrl}
              download={downloadFilename || "remix.mp4"}
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 transition-colors"
            >
              <Download className="h-4 w-4" />
              Download {downloadFilename || "remix.mp4"}
            </a>
          )}
        </Card>
      )}

      {/* Past runs — always visible if there's at least one finished remix on disk */}
      {pastRuns.length > 0 && (
        <Card className="p-4 space-y-3 border-border/40">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
              Past remix runs ({pastRuns.length})
            </div>
            <button
              type="button"
              onClick={loadPastRuns}
              className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              Refresh
            </button>
          </div>
          <div className="divide-y divide-border/40">
            {pastRuns.map((r) => {
              const sizeMb = (r.file_size / 1024 / 1024).toFixed(1);
              const when = r.finished_at
                ? new Date(r.finished_at).toLocaleString()
                : "";
              return (
                <div
                  key={r.job_id}
                  className="flex items-center gap-3 py-2 first:pt-0 last:pb-0"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate" title={r.title}>
                      {r.title}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 mt-0.5 text-[11px] text-muted-foreground">
                      <span>{sizeMb} MB</span>
                      {when && <span>· {when}</span>}
                      {r.tts_engine && <span>· {r.tts_engine}</span>}
                      {r.transcript_target_lang && (
                        <span>· {r.transcript_target_lang}</span>
                      )}
                      <span className="font-mono">· {r.job_id}</span>
                    </div>
                  </div>
                  {r.file_available ? (
                    <a
                      href={`/worker-api/remix/${r.job_id}/download`}
                      download={r.output_filename}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-primary/40 bg-primary/5 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 transition-colors"
                    >
                      <Download className="h-3.5 w-3.5" />
                      Download
                    </a>
                  ) : (
                    <Badge variant="outline" className="text-[10px] text-muted-foreground border-border/40 shrink-0">
                      file gone
                    </Badge>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}
