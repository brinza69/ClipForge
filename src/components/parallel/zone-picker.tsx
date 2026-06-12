"use client";

/**
 * Shared erase + caption zone picker for Parallel Processing.
 * Pixel-for-pixel the same picker the Remix Pipeline uses: a centered 400px
 * thumbnail with a canvas overlay (drag to move, drag corner to resize), the
 * Erase/Caption toggle in the header, a live CSS caption preview, and the
 * coordinate readout boxes. The live caption preview reflects the FIRST
 * variant's template/style as a representative sample (parallel has many).
 */

import { useCallback, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Eraser, Type } from "lucide-react";

export type Rect = { x: number; y: number; w: number; h: number };
export type ActiveRect = "erase" | "caption";

export interface PickerPreview {
  thumbnail_url: string | null;
  width: number;
  height: number;
}

export interface PickerTemplate {
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

/** First variant's caption look, used for the representative live preview. */
export interface CaptionSample {
  templateId: string;
  fontFamily: string;
  textColor: string;
  scale: number;
  uppercase: boolean | null;
  italic: boolean | null;
  wordsPerChunk: number;
  stripPunct: boolean;
}

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }
function getRenderedRect(el: HTMLElement, srcW: number, srcH: number) {
  const cw = el.clientWidth, ch = el.clientHeight, va = srcW / srcH, ca = cw / ch;
  if (va > ca) { const rh = cw / va; return { x: 0, y: (ch - rh) / 2, w: cw, h: rh }; }
  const rw = ch * va; return { x: (cw - rw) / 2, y: 0, w: rw, h: ch };
}

interface Props {
  preview: PickerPreview;
  eraseRect: Rect;
  setEraseRect: (r: Rect) => void;
  captionRect: Rect;
  setCaptionRect: (r: Rect) => void;
  active: ActiveRect;
  setActive: (a: ActiveRect) => void;
  templates: PickerTemplate[];
  captionSample: CaptionSample;
}

export function ZonePicker({
  preview, eraseRect, setEraseRect, captionRect, setCaptionRect,
  active, setActive, templates, captionSample,
}: Props) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderedRef = useRef({ x: 0, y: 0, w: 0, h: 0 });
  const dragRef = useRef<{ mode: "move" | "resize"; target: ActiveRect; startX: number; startY: number; origX: number; origY: number; origW: number; origH: number } | null>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current, img = imgRef.current;
    if (!canvas || !img || !preview) return;
    canvas.width = img.clientWidth; canvas.height = img.clientHeight;
    const rendered = getRenderedRect(img, preview.width || 1, preview.height || 1);
    renderedRef.current = rendered;
    const ctx = canvas.getContext("2d"); if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const sx = rendered.w / (preview.width || 1), sy = rendered.h / (preview.height || 1);

    const e = eraseRect;
    const ex = rendered.x + e.x * sx, ey = rendered.y + e.y * sy, ew = e.w * sx, eh = e.h * sy;
    ctx.strokeStyle = active === "erase" ? "rgba(251,113,133,1)" : "rgba(251,113,133,0.5)";
    ctx.lineWidth = active === "erase" ? 3 : 2; ctx.setLineDash([8, 4]);
    ctx.strokeRect(ex, ey, ew, eh);
    ctx.fillStyle = "rgba(251,113,133,0.15)"; ctx.fillRect(ex, ey, ew, eh);
    ctx.setLineDash([]); ctx.fillStyle = active === "erase" ? "rgba(251,113,133,1)" : "rgba(251,113,133,0.6)";
    ctx.fillRect(ex + ew - 10, ey + eh - 10, 12, 12);
    ctx.fillStyle = "rgba(0,0,0,0.7)"; ctx.fillRect(ex, ey - 18, 60, 16);
    ctx.fillStyle = "rgb(251,113,133)"; ctx.font = "11px sans-serif"; ctx.fillText("ERASE", ex + 4, ey - 6);

    const c = captionRect;
    const cx = rendered.x + c.x * sx, cy = rendered.y + c.y * sy, cw = c.w * sx, ch = c.h * sy;
    ctx.strokeStyle = active === "caption" ? "rgba(251,191,36,1)" : "rgba(251,191,36,0.5)";
    ctx.lineWidth = active === "caption" ? 3 : 2; ctx.setLineDash([8, 4]);
    ctx.strokeRect(cx, cy, cw, ch);
    ctx.fillStyle = "rgba(251,191,36,0.15)"; ctx.fillRect(cx, cy, cw, ch);
    ctx.setLineDash([]); ctx.fillStyle = active === "caption" ? "rgba(251,191,36,1)" : "rgba(251,191,36,0.6)";
    ctx.fillRect(cx + cw - 10, cy + ch - 10, 12, 12);
    ctx.fillStyle = "rgba(0,0,0,0.7)"; ctx.fillRect(cx, cy - 18, 80, 16);
    ctx.fillStyle = "rgb(251,191,36)"; ctx.font = "11px sans-serif"; ctx.fillText("CAPTION", cx + 4, cy - 6);
  }, [preview, eraseRect, captionRect, active]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const img = imgRef.current; if (!img) return;
    const ro = new ResizeObserver(draw); ro.observe(img);
    return () => ro.disconnect();
  }, [draw, preview]);

  const getPos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { mx: e.clientX - r.left, my: e.clientY - r.top };
  };
  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rendered = renderedRef.current;
    const sx = rendered.w / preview.width, sy = rendered.h / preview.height;
    const rect = active === "erase" ? eraseRect : captionRect;
    const { mx, my } = getPos(e);
    const cx = rendered.x + rect.x * sx, cy = rendered.y + rect.y * sy, cw = rect.w * sx, ch = rect.h * sy;
    const base = { target: active, startX: mx, startY: my, origX: rect.x, origY: rect.y, origW: rect.w, origH: rect.h };
    if (mx >= cx + cw - 14 && my >= cy + ch - 14) dragRef.current = { mode: "resize", ...base };
    else if (mx >= cx && mx <= cx + cw && my >= cy && my <= cy + ch) dragRef.current = { mode: "move", ...base };
  };
  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current; if (!drag) return;
    const rendered = renderedRef.current;
    const px = preview.width / rendered.w, py = preview.height / rendered.h;
    const { mx, my } = getPos(e);
    const dx = Math.round((mx - drag.startX) * px), dy = Math.round((my - drag.startY) * py);
    const setter = drag.target === "erase" ? setEraseRect : setCaptionRect;
    if (drag.mode === "move") {
      setter({ x: clamp(drag.origX + dx, 0, preview.width - drag.origW), y: clamp(drag.origY + dy, 0, preview.height - drag.origH), w: drag.origW, h: drag.origH });
    } else {
      setter({ x: drag.origX, y: drag.origY, w: clamp(drag.origW + dx, 10, preview.width - drag.origX), h: clamp(drag.origH + dy, 10, preview.height - drag.origY) });
    }
  };
  const onMouseUp = () => { dragRef.current = null; };

  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          Pick the erase + caption zones (shared)
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant={active === "erase" ? "default" : "outline"}
            onClick={() => setActive("erase")}
            className={active === "erase" ? "" : "border-rose-500/40 text-rose-300"}
          >
            <Eraser className="h-3.5 w-3.5 mr-1.5" /> Erase zone
          </Button>
          <Button
            size="sm"
            variant={active === "caption" ? "default" : "outline"}
            onClick={() => setActive("caption")}
            className={active === "caption" ? "" : "border-amber-500/40 text-amber-300"}
          >
            <Type className="h-3.5 w-3.5 mr-1.5" /> Caption zone
          </Button>
        </div>
      </div>

      <div className="relative mx-auto" style={{ maxWidth: "400px" }}>
        {preview.thumbnail_url && (
          // The thumbnail's aspect can differ from the video's (YouTube
          // Shorts thumbs are 16:9 with the 9:16 video pillarboxed between
          // blurred bars; TikTok thumbs are full 9:16). Force the box to the
          // VIDEO's aspect and cover-crop the image so the preview always
          // fills the picker like the video will.
          <div
            className="relative w-full overflow-hidden rounded-md bg-black"
            style={{ aspectRatio: `${preview.width || 9} / ${preview.height || 16}` }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              ref={imgRef}
              src={preview.thumbnail_url}
              alt="thumb"
              className="absolute inset-0 h-full w-full object-cover"
              onLoad={draw}
            />
          </div>
        )}
        <canvas
          ref={canvasRef}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
          className="absolute inset-0 cursor-crosshair"
        />

        {/* Live caption preview (representative — first variant's style). */}
        {(() => {
          const tpl = templates.find((t) => t.id === captionSample.templateId);
          if (!tpl) return null;
          const srcW = preview.width || 1, srcH = preview.height || 1;
          const leftPct = ((captionRect.x + captionRect.w / 2) / srcW) * 100;
          const topPct = ((captionRect.y + captionRect.h / 2) / srcH) * 100;
          const widthPct = (captionRect.w / srcW) * 100;
          const autoFit = Math.max(0.5, Math.min(3.0, (captionRect.h / srcH) * 4.0));
          const effectiveScale = autoFit * captionSample.scale;
          const displayPxPerSrc = 400 / srcW;
          const fontPx = Math.max(14, (tpl.font_size || 64) * effectiveScale * displayPxPerSrc);
          const family = captionSample.fontFamily || tpl.font_family || "Arial Black";
          const color = captionSample.textColor || tpl.text_color || "#ffffff";
          const isItalic = captionSample.italic ?? tpl.italic ?? false;
          const isUpper = captionSample.uppercase ?? tpl.uppercase ?? false;
          const boxBg = tpl.borderstyle === 3 ? "rgba(0,0,0,0.85)" : "transparent";
          const outlineW = tpl.outline_width || 0;
          const outlineColor = tpl.outline_color || "#000";
          const textShadow = tpl.borderstyle !== 3 && outlineW > 0
            ? `0 0 ${outlineW}px ${outlineColor}, 0 0 ${outlineW}px ${outlineColor}, 1px 1px 0 ${outlineColor}, -1px -1px 0 ${outlineColor}, 1px -1px 0 ${outlineColor}, -1px 1px 0 ${outlineColor}`
            : "none";
          const weight = (tpl.font_weight || "Bold").toLowerCase();
          const isHeavy = weight === "bold" || weight === "black" || weight === "heavy";
          return (
            <div
              className="absolute pointer-events-none flex items-center justify-center text-center"
              style={{ left: `${leftPct}%`, top: `${topPct}%`, width: `${widthPct}%`, transform: "translate(-50%, -50%)", zIndex: 10 }}
            >
              <span
                style={{
                  fontFamily: family, fontSize: `${fontPx}px`, color,
                  fontStyle: isItalic ? "italic" : "normal",
                  fontWeight: isHeavy ? 900 : 400,
                  textTransform: isUpper ? "uppercase" : "none",
                  backgroundColor: boxBg, textShadow,
                  padding: tpl.borderstyle === 3 ? "0.1em 0.4em" : 0,
                  lineHeight: 1.1, maxWidth: "100%", whiteSpace: "nowrap",
                  overflow: "hidden", textOverflow: "ellipsis",
                }}
              >
                {captionSample.wordsPerChunk === 1 ? "Sample" : "Sample caption"}{captionSample.stripPunct ? "" : "."}
              </span>
            </div>
          );
        })()}
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
  );
}
