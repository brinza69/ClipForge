"use client";

// Local ComfyUI image generation card — free, runs on the user's own
// dual-GPU rig (GPU0 = GTX 1660 Super, GPU1 = RTX 3060). Lets the user
// switch the project's image_provider between "manual_flow" (default) and
// "comfyui_local", shows live GPU status, and kicks off/retries generation
// jobs. Mirrors the style of action-buttons.tsx and voice-status-strip.tsx.

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  CheckCircle2, XCircle, Loader2, RefreshCw, ImageIcon, AlertTriangle, Cpu,
} from "lucide-react";
import { toast } from "sonner";
import { readApiError, errorDescription } from "@/lib/api-error";
import { IMAGE_PROVIDER_LABELS } from "@/components/doodle/constants";
import type { DoodleComfyStatus, DoodleImageProviderMode, DoodleStoryboard } from "@/types/doodle";

interface Props {
  projectId: string;
  storyboard: DoodleStoryboard;
  onJobStarted: (jobId: string) => void;
  busy: boolean;
  onRefresh: () => void | Promise<void>;
}

const PROVIDER_MODES: DoodleImageProviderMode[] = ["manual_flow", "comfyui_local"];

export function LocalImageGeneration({ projectId, storyboard, onJobStarted, busy, onRefresh }: Props) {
  const provider = storyboard.settings.image_provider || "manual_flow";
  const [switching, setSwitching] = useState(false);
  const [comfyStatus, setComfyStatus] = useState<DoodleComfyStatus | null>(null);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [starting, setStarting] = useState(false);

  const fetchStatus = useCallback(async () => {
    setCheckingStatus(true);
    try {
      const r = await fetch("/worker-api/doodle/comfy/status");
      if (!r.ok) {
        const err = await readApiError(r, "Could not reach local GPUs");
        toast.error("Failed to check local GPUs", { description: errorDescription(err) });
        return;
      }
      const j: DoodleComfyStatus = await r.json();
      setComfyStatus(j);
    } catch (e: any) {
      console.error("[doodle] comfy status check failed", e);
      toast.error("Failed to check local GPUs", { description: e.message });
    } finally {
      setCheckingStatus(false);
    }
  }, []);

  useEffect(() => {
    if (provider === "comfyui_local") {
      fetchStatus();
    }
  }, [provider, fetchStatus]);

  const setProvider = async (mode: DoodleImageProviderMode) => {
    if (mode === provider) return;
    setSwitching(true);
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_provider: mode }),
      });
      if (!r.ok) {
        const err = await readApiError(r, "Failed to update image provider");
        toast.error("Failed to switch mode", { description: errorDescription(err) });
        return;
      }
      await onRefresh();
      toast.success(`Switched to ${IMAGE_PROVIDER_LABELS[mode]}`);
    } catch (e: any) {
      console.error("[doodle] set image provider failed", e);
      toast.error("Failed to switch mode", { description: e.message });
    } finally {
      setSwitching(false);
    }
  };

  const startGeneration = async (sceneIndexes?: number[]) => {
    setStarting(true);
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/generate-images`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          only_missing: !sceneIndexes,
          scene_indexes: sceneIndexes ?? null,
        }),
      });
      if (!r.ok) {
        const err = await readApiError(r, "Image generation failed to start");
        toast.error("Failed to start image generation", { description: errorDescription(err) });
        return;
      }
      const j = await r.json();
      onJobStarted(j.job_id);
      toast.success("Local image generation started");
    } catch (e: any) {
      console.error("[doodle] generate-images failed", e);
      toast.error("Failed to start image generation", { description: e.message });
    } finally {
      setStarting(false);
    }
  };

  const total = storyboard.scenes.length;
  const generatedCount = storyboard.scenes.filter((s) => !!s.image_path).length;
  const failedList = storyboard.image_generation?.failed ?? [];
  const failedCount = failedList.length;
  const disabled = busy || switching || starting || (provider === "comfyui_local" && !comfyStatus?.any_alive);

  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
        Image generation
      </div>

      <div className="flex flex-wrap gap-2">
        {PROVIDER_MODES.map((mode) => (
          <Button
            key={mode}
            type="button"
            size="sm"
            variant={provider === mode ? "default" : "outline"}
            onClick={() => setProvider(mode)}
            disabled={switching}
          >
            {switching && provider !== mode ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {IMAGE_PROVIDER_LABELS[mode]}
          </Button>
        ))}
      </div>

      {provider === "comfyui_local" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap items-center gap-2">
              {(comfyStatus?.gpus ?? []).map((gpu) => (
                <span
                  key={gpu.index}
                  title={gpu.error ?? undefined}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${
                    gpu.alive
                      ? "border-emerald-500/40 text-emerald-400"
                      : "border-destructive/40 text-destructive"
                  }`}
                >
                  {gpu.alive ? (
                    <CheckCircle2 className="h-3 w-3" />
                  ) : (
                    <XCircle className="h-3 w-3" />
                  )}
                  GPU {gpu.index} — :{gpu.url.split(":").pop()}
                  {gpu.alive && gpu.queue_pending > 0 && (
                    <span className="opacity-70">({gpu.queue_pending} queued)</span>
                  )}
                </span>
              ))}
              <span className="inline-flex items-center gap-1.5 rounded-full border border-border/40 px-2 py-0.5 text-xs text-muted-foreground">
                <Cpu className="h-3 w-3" />
                {comfyStatus?.model_file_found ? "SDXL Turbo" : "Model not found"}
              </span>
            </div>
            <Button type="button" size="sm" variant="outline" onClick={fetchStatus} disabled={checkingStatus}>
              {checkingStatus ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Check Local GPUs
            </Button>
          </div>

          {comfyStatus && !comfyStatus.any_alive && (
            <p className="flex items-start gap-1.5 text-xs text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              {comfyStatus.hint || "Start ComfyUI first using scripts/start_comfy_all.bat"}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <ImageIcon className="h-3.5 w-3.5" />
              Images generated: <b className="text-foreground">{generatedCount} / {total}</b>
            </span>
            <span className="flex items-center gap-1.5">
              Failed: <b className={failedCount > 0 ? "text-destructive" : "text-foreground"}>{failedCount}</b>
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              onClick={() => startGeneration()}
              disabled={disabled || total === 0}
            >
              {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Start Local Image Generation
            </Button>
            {failedCount > 0 && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => startGeneration(failedList.map((f) => f.index))}
                disabled={disabled}
              >
                {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                Retry Failed ({failedCount})
              </Button>
            )}
          </div>

          <p className="text-[11px] text-amber-400/90">
            Local generation is free but uses your computer. Keep ComfyUI running.
          </p>
        </div>
      )}
    </Card>
  );
}
