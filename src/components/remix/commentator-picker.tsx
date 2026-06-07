"use client";

/**
 * Commentator picker — the optional final layer of the Remix pipeline.
 *
 * Manages:
 *   - the preset grid (None + saved presets + upload-new button)
 *   - per-run chroma-key overrides (color, similarity, blend) layered on top
 *     of each preset's saved defaults
 *   - the AI background-removal job (start + status + remove)
 *   - "Save to preset" — bake the current overrides into the preset's defaults
 *
 * State for chroma-color/similarity/blend lives in the parent so the
 * Run button sees it inside the start payload; the parent passes the
 * current values + setters in as props.
 */

import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Loader2, Mic, Sparkles } from "lucide-react";
import { toast } from "sonner";

export interface Commentator {
  id: string;
  name: string;
  chroma_key: string | null;
  chroma_similarity?: number;
  chroma_blend?: number;
  duration?: number;
  video_available: boolean;
  thumb_available: boolean;
  ai_processed?: boolean;
  has_native_alpha?: boolean;
}

export interface AiJob {
  jobId: string;
  progress: number;
  msg: string;
  done: boolean;
  error?: string;
}

interface Props {
  commentators: Commentator[];
  commentatorId: string;
  setCommentatorId: (id: string) => void;

  // Per-run chroma overrides. null = "use preset's saved value".
  chromaColor: string | null;
  setChromaColor: (v: string | null) => void;
  chromaSimilarity: number | null;
  setChromaSimilarity: (v: number | null) => void;
  chromaBlend: number | null;
  setChromaBlend: (v: number | null) => void;

  // AI processing job state keyed by preset id.
  aiJobs: Record<string, AiJob>;
  startAiRemoval: (presetId: string) => void;
  removeAiProcessed: (presetId: string) => void;

  // Upload + delete handlers (kept in parent so they can refresh
  // commentators alongside other unrelated state).
  uploadingCommentator: boolean;
  uploadCommentator: (file: File) => void;
  deleteCommentator: (id: string) => void;
  reloadCommentators: () => Promise<void> | void;

  /** Whole picker is inert when this is true (e.g. job currently running). */
  disabled: boolean;
}

export function CommentatorPicker({
  commentators, commentatorId, setCommentatorId,
  chromaColor, setChromaColor,
  chromaSimilarity, setChromaSimilarity,
  chromaBlend, setChromaBlend,
  aiJobs, startAiRemoval, removeAiProcessed,
  uploadingCommentator, uploadCommentator, deleteCommentator, reloadCommentators,
  disabled,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className="pt-3 border-t border-border/40">
      <div className="flex items-center justify-between mb-2">
        <Label className="text-xs flex items-center gap-1.5">
          <Mic className="h-3 w-3" /> Commentator overlay
        </Label>
        <input
          ref={fileRef}
          type="file"
          accept=".mp4,.mov,.webm,.mkv"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadCommentator(f);
            if (fileRef.current) fileRef.current.value = "";
          }}
        />
        <Button
          size="sm"
          variant="ghost"
          disabled={disabled || uploadingCommentator}
          onClick={() => fileRef.current?.click()}
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
          disabled={disabled}
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
              disabled={disabled}
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
        const hasOverride =
          chromaColor !== null || chromaSimilarity !== null || chromaBlend !== null;

        const saveToPreset = async () => {
          try {
            const body: Record<string, unknown> = {
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
                      Uses U²-Net (same kind of model as CapCut&apos;s background remove).
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
                      disabled={disabled}
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
                      disabled={disabled}
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
                    disabled={disabled || isKeyingOff}
                    className="h-9 w-12 rounded-md border border-input bg-background cursor-pointer disabled:opacity-50"
                  />
                  <Input
                    value={effectiveColor}
                    onChange={(e) => setChromaColor(e.target.value)}
                    placeholder="#FFFFFF or empty to disable"
                    disabled={disabled}
                    className="flex-1 text-xs font-mono"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant={isKeyingOff ? "default" : "outline"}
                    onClick={() => setChromaColor(isKeyingOff ? (com.chroma_key || "#FFFFFF") : "")}
                    disabled={disabled}
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
                  disabled={disabled || isKeyingOff}
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
                  disabled={disabled || isKeyingOff}
                />
                <p className="text-[10px] text-muted-foreground mt-1">
                  Soft edges blur the boundary between kept and removed pixels.
                  0 = hard cut, higher = softer transition.
                </p>
              </div>

              <div className="flex items-center justify-between gap-2 pt-1">
                <p className="text-[10px] text-muted-foreground flex-1">
                  Changes apply to this run. Use &quot;Save to preset&quot; to make them the default.
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={saveToPreset}
                  disabled={disabled || !hasOverride}
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
  );
}
