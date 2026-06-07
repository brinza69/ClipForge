"use client";

/**
 * Settings → Whisper / Transcription card.
 *
 * Shows what Whisper model + device is configured AND actually loaded.
 * Whisper runs in a subprocess so the backend log never shows the model's
 * resolved device — the user couldn't previously confirm whether CUDA
 * was being used or it silently fell back to CPU.
 *
 * Lets the user change model (tiny→large-v3) and device (auto/cuda/cpu),
 * persisted to data/whisper_config.json. After Apply the cached model is
 * dropped so the next transcription loads with the new settings.
 *
 * The "Verify GPU" button forces an actual model load and reports the
 * resolved device + load time. This is the only reliable way to know if
 * CUDA actually works (gpu_available=true in /api/system doesn't prove
 * faster-whisper itself succeeds — cuDNN can be missing for CTranslate2
 * specifically and still work for PyTorch).
 */

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Cpu, Zap, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";

interface DeviceInfo {
  configured_model: string | null;
  configured_device: string | null;
  actual_model: string | null;
  actual_device: string | null;
  actual_compute_type: string | null;
  fell_back_to_cpu: boolean;
  load_time_ms: number | null;
  error: string | null;
  loaded?: boolean;
  cuda_available: boolean;
  cuda_device_name: string | null;
  models: string[];
  devices: string[];
}

export function WhisperCard() {
  const [info, setInfo] = useState<DeviceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [saving, setSaving] = useState(false);

  // Form state (mirror of saved config when info loads).
  const [pickModel, setPickModel] = useState<string>("medium");
  const [pickDevice, setPickDevice] = useState<string>("auto");

  const fetchStatus = useCallback(async (verify = false) => {
    if (verify) setVerifying(true);
    else setLoading(true);
    try {
      const r = await fetch(`/worker-api/transcript/device${verify ? "?verify=true" : ""}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: DeviceInfo = await r.json();
      setInfo(j);
      if (!verify) {
        // First load: hydrate the form with the configured values
        if (j.configured_model) setPickModel(j.configured_model);
        if (j.configured_device) setPickDevice(j.configured_device);
      }
      if (verify) {
        if (j.error) {
          toast.error("Whisper load failed", { description: j.error });
        } else if (j.fell_back_to_cpu) {
          toast.warning("CUDA fell back to CPU", {
            description: "Configured for CUDA but the load failed and we used CPU instead. See cuDNN / CTranslate2 install.",
          });
        } else {
          toast.success(`Whisper loaded on ${j.actual_device}`, {
            description: `${j.actual_model} @ ${j.actual_compute_type} in ${j.load_time_ms}ms`,
          });
        }
      }
    } catch (e: any) {
      toast.error("Could not fetch Whisper status", { description: e.message });
    } finally {
      setLoading(false);
      setVerifying(false);
    }
  }, []);

  useEffect(() => { fetchStatus(false); }, [fetchStatus]);

  const apply = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/worker-api/transcript/device`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          whisper_model: pickModel,
          whisper_device: pickDevice,
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `Save failed (${r.status})`);
      toast.success("Settings saved — next transcription will reload Whisper");
      await fetchStatus(false);
    } catch (e: any) {
      toast.error("Save failed", { description: e.message });
    } finally {
      setSaving(false);
    }
  };

  const verify = () => fetchStatus(true);

  const dirty = info && (
    pickModel !== (info.configured_model || "medium") ||
    pickDevice !== (info.configured_device || "auto")
  );

  return (
    <Card className="border-border/30 bg-card/50 p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Cpu className="h-4 w-4 text-primary" /> Whisper Transcription
        </h3>
        <p className="text-[11px] text-muted-foreground">
          Speech-to-text engine. Larger models = better accuracy but slower; GPU = much faster.
        </p>
      </div>

      {/* Current status */}
      <div className="rounded-md border border-border/40 bg-card p-3 space-y-2">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Status:</span>
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          ) : info?.loaded === false ? (
            <Badge variant="outline" className="text-[10px] border-muted-foreground/40 text-muted-foreground">
              not yet loaded (loads on first transcription)
            </Badge>
          ) : info?.fell_back_to_cpu ? (
            <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">
              <AlertCircle className="h-3 w-3 mr-1" /> CUDA fell back to CPU
            </Badge>
          ) : info?.error ? (
            <Badge variant="outline" className="text-[10px] border-destructive/40 text-destructive">
              <AlertCircle className="h-3 w-3 mr-1" /> failed
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-400">
              <CheckCircle2 className="h-3 w-3 mr-1" /> ready
            </Badge>
          )}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
          <Field label="Configured model" value={info?.configured_model} mono />
          <Field label="Configured device" value={info?.configured_device} mono />
          <Field
            label="GPU visible to PyTorch"
            value={info ? (info.cuda_available ? info.cuda_device_name || "yes" : "no") : "—"}
            highlight={info?.cuda_available}
          />
          <Field label="Actual model" value={info?.actual_model} mono />
          <Field label="Actual device" value={info?.actual_device} mono highlight={info?.actual_device === "cuda"} />
          <Field
            label="Load time"
            value={info?.load_time_ms ? `${info.load_time_ms}ms` : null}
            mono
          />
        </div>
        {info?.error && (
          <div className="text-[11px] text-destructive font-mono">{info.error}</div>
        )}
      </div>

      {/* Picker form */}
      <div className="space-y-2 border-t border-border/30 pt-4">
        <div className="text-xs font-medium">Change settings</div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label className="text-[11px] text-muted-foreground">Model</Label>
            <select
              value={pickModel}
              onChange={(e) => setPickModel(e.target.value)}
              className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm font-mono"
            >
              {(info?.models || ["tiny", "base", "small", "medium", "large-v3"]).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground">Device</Label>
            <select
              value={pickDevice}
              onChange={(e) => setPickDevice(e.target.value)}
              className="w-full h-9 rounded-md border border-input bg-background px-2 text-sm font-mono"
            >
              {(info?.devices || ["auto", "cuda", "cpu"]).map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>
        </div>

        <p className="text-[10px] text-muted-foreground leading-relaxed">
          <strong>Model picks:</strong> {" "}
          <code className="font-mono">medium</code> = good balance (current default).
          {" "}<code className="font-mono">large-v3</code> = best accuracy (~30% fewer
          errors on RO/accented speech), needs ~3GB VRAM.
          {" "}<code className="font-mono">small</code>/<code className="font-mono">base</code> = faster on CPU.
        </p>
        <p className="text-[10px] text-muted-foreground leading-relaxed">
          <strong>Device picks:</strong> <code className="font-mono">auto</code> tries
          CUDA then CPU. <code className="font-mono">cuda</code> forces GPU (errors if unavailable).
          Force <code className="font-mono">cpu</code> only if CUDA fails repeatedly.
        </p>

        <div className="flex gap-2 pt-1">
          <Button onClick={apply} disabled={!dirty || saving} size="sm">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Apply"}
          </Button>
          <Button onClick={verify} disabled={verifying} variant="outline" size="sm" title="Force a model load to verify the configured device works (takes ~5-15s for medium)">
            {verifying ? (
              <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Loading…</>
            ) : (
              <><Zap className="h-4 w-4 mr-1" /> Verify GPU</>
            )}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function Field({ label, value, mono, highlight }: { label: string; value: string | null | undefined; mono?: boolean; highlight?: boolean }) {
  return (
    <div className="space-y-0.5">
      <div className="text-[10px] uppercase text-muted-foreground">{label}</div>
      <div className={`text-xs truncate ${mono ? "font-mono" : ""} ${highlight ? "text-emerald-400" : ""}`}>
        {value ?? "—"}
      </div>
    </div>
  );
}
