"use client";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Download, Eraser, Loader2, Wand2, Zap, AlertCircle,
} from "lucide-react";

type EraseMode = "inpaint" | "blur";

interface ControlsPanelProps {
  mode: EraseMode;
  setMode: (m: EraseMode) => void;
  loading: boolean;
  progress: string;
  errorMsg: string;
  dims: { w: number; h: number } | null;
  xPct: number;
  yPct: number;
  wPct: number;
  hPct: number;
  rX: number;
  rY: number;
  rW: number;
  rH: number;
  setFromPct: (axis: "x" | "y" | "w" | "h", pct: number) => void;
  resultUrl: string;
  onErase: () => void;
  onDownload: () => void;
  onClearResult: () => void;
}

export function ControlsPanel({
  mode, setMode, loading, progress, errorMsg, dims,
  xPct, yPct, wPct, hPct, rX, rY, rW, rH, setFromPct,
  resultUrl, onErase, onDownload, onClearResult,
}: ControlsPanelProps) {
  return (
    <div className="space-y-4">
      <Card className="p-4 space-y-3 border-border/40 bg-card/60">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
          Removal method
        </div>
        <div className="space-y-2">
          <button
            onClick={() => setMode("inpaint")}
            disabled={loading}
            className={`w-full rounded-lg border p-3 text-left transition-colors ${
              mode === "inpaint"
                ? "border-amber-500/50 bg-amber-500/10"
                : "border-border/30 bg-muted/10 hover:border-border/60"
            }`}
          >
            <div className="flex items-center gap-2">
              <Wand2 className={`h-4 w-4 ${mode === "inpaint" ? "text-amber-400" : "text-muted-foreground"}`} />
              <span className="text-sm font-medium">Inpaint</span>
              <span className="ml-auto text-[9px] rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5">Best</span>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">
              OpenCV TELEA — seamless, natural-looking removal. Slower but much better quality.
            </p>
          </button>
          <button
            onClick={() => setMode("blur")}
            disabled={loading}
            className={`w-full rounded-lg border p-3 text-left transition-colors ${
              mode === "blur"
                ? "border-amber-500/50 bg-amber-500/10"
                : "border-border/30 bg-muted/10 hover:border-border/60"
            }`}
          >
            <div className="flex items-center gap-2">
              <Zap className={`h-4 w-4 ${mode === "blur" ? "text-amber-400" : "text-muted-foreground"}`} />
              <span className="text-sm font-medium">Fast blur</span>
              <span className="ml-auto text-[9px] rounded bg-blue-500/15 text-blue-400 px-1.5 py-0.5">Fast</span>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">
              FFmpeg avgblur — quick but obvious blur. Good for privacy masking.
            </p>
          </button>
        </div>
      </Card>

      {dims && (
        <Card className="p-4 space-y-3 border-border/40 bg-card/60">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
            Region position
          </div>
          {[
            { label: "Left", val: xPct, axis: "x" as const, max: 99 },
            { label: "Top", val: yPct, axis: "y" as const, max: 99 },
            { label: "Width", val: wPct, axis: "w" as const, max: 100 },
            { label: "Height", val: hPct, axis: "h" as const, max: 100 },
          ].map(({ label, val, axis, max }) => (
            <div key={axis} className="space-y-1">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">{label}</span>
                <span className="font-mono text-muted-foreground">{val}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={max}
                value={val}
                disabled={loading}
                onChange={(e) => setFromPct(axis, parseInt(e.target.value))}
                className="w-full h-1.5 accent-amber-400 cursor-pointer disabled:opacity-40"
              />
            </div>
          ))}
          <div className="pt-2 mt-1 border-t border-border/30 text-[10px] text-muted-foreground">
            <span className="font-mono">{rX},{rY} / {rW}×{rH}px</span>
          </div>
        </Card>
      )}

      {errorMsg && (
        <Card className="p-3 border-red-500/40 bg-red-500/10 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
          <div className="text-xs text-red-400 break-words">{errorMsg}</div>
        </Card>
      )}

      {!resultUrl && (
        <Button
          size="lg"
          className="w-full gap-2 bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/30"
          onClick={onErase}
          disabled={loading}
        >
          {loading
            ? <><Loader2 className="h-4 w-4 animate-spin" /> {progress || "Processing…"}</>
            : <><Eraser className="h-4 w-4" /> Erase region ({mode === "inpaint" ? "Inpaint" : "Blur"})</>}
        </Button>
      )}

      {resultUrl && (
        <div className="space-y-2">
          <Button className="w-full gap-2" onClick={onDownload}>
            <Download className="h-4 w-4" /> Download result
          </Button>
          <Button
            variant="outline"
            className="w-full gap-2"
            onClick={onClearResult}
          >
            Try a different region
          </Button>
        </div>
      )}
    </div>
  );
}
