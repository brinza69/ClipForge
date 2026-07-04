"use client";

// New Video form — mode tabs (topic/script), niche, duration, frame interval,
// aspect ratio, voice, subtitle style, motion style, live estimated-frames
// math, and a collapsed Advanced section. Submits POST /projects then hands
// the created project id back to the caller for navigation.

import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Loader2, Sparkles, ChevronDown, ChevronRight as ChevronRightIcon } from "lucide-react";
import { toast } from "sonner";
import { readApiError, errorDescription } from "@/lib/api-error";
import type { DoodleCreateProjectPayload, DoodleVoicesResponse } from "@/types/doodle";
import {
  NICHES, DURATION_PRESETS, FRAME_INTERVALS, ASPECT_RATIOS,
  SUBTITLE_STYLES, MOTION_STYLES, RENDER_QUALITIES, estimatedFrameCount,
} from "@/components/doodle/constants";

interface Props {
  onCreated: (projectId: string) => void;
}

export function NewProjectForm({ onCreated }: Props) {
  const [mode, setMode] = useState<"topic" | "script">("topic");
  const [topic, setTopic] = useState("");
  const [scriptText, setScriptText] = useState("");
  const [niche, setNiche] = useState("history");
  const [customNiche, setCustomNiche] = useState("");

  const [durationPreset, setDurationPreset] = useState(180);
  const [customMinutes, setCustomMinutes] = useState(3);

  const [frameInterval, setFrameInterval] = useState("3");
  const [aspectRatio, setAspectRatio] = useState<"16:9" | "9:16" | "1:1">("16:9");

  const [voices, setVoices] = useState<DoodleVoicesResponse | null>(null);
  const [voice, setVoice] = useState("am_michael");

  // Default: no subtitles. SRT captions are still exported; burning is
  // derived from the mode (anything other than "none").
  const [subtitleStyle, setSubtitleStyle] = useState("none");
  const [motionStyle, setMotionStyle] = useState("subtle");

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [openaiModel, setOpenaiModel] = useState("");
  const [voiceSpeed, setVoiceSpeed] = useState(0.95);
  const [motionIntensity, setMotionIntensity] = useState(0.5);
  const [renderQuality, setRenderQuality] = useState("high");
  const [useGpu, setUseGpu] = useState(true);

  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/worker-api/doodle/voices");
        if (r.ok) setVoices(await r.json());
      } catch {}
    })();
  }, []);

  const durationSeconds = durationPreset === -1 ? Math.max(1, customMinutes) * 60 : durationPreset;
  const estimatedFrames = useMemo(
    () => estimatedFrameCount(durationSeconds, frameInterval),
    [durationSeconds, frameInterval],
  );

  const submit = async () => {
    if (mode === "topic" && !topic.trim()) { toast.error("Enter a topic"); return; }
    if (mode === "script" && !scriptText.trim()) { toast.error("Paste your script"); return; }
    if (niche === "custom" && !customNiche.trim()) { toast.error("Enter a custom niche"); return; }

    setSubmitting(true);
    const payload: DoodleCreateProjectPayload = {
      mode,
      topic: mode === "topic" ? topic.trim() : undefined,
      script_text: mode === "script" ? scriptText.trim() : undefined,
      niche,
      custom_niche: niche === "custom" ? customNiche.trim() : undefined,
      target_duration_seconds: durationSeconds,
      frame_interval_seconds: (frameInterval === "auto" ? "auto" : Number(frameInterval)) as 2 | 3 | 4 | "auto",
      aspect_ratio: aspectRatio,
      voice,
      voice_speed: voiceSpeed,
      subtitle_style: subtitleStyle as DoodleCreateProjectPayload["subtitle_style"],
      burn_subtitles: subtitleStyle !== "none",
      motion_style: motionStyle as DoodleCreateProjectPayload["motion_style"],
      motion_intensity: motionIntensity,
      openai_model: openaiModel.trim() || null,
      render_quality: renderQuality as DoodleCreateProjectPayload["render_quality"],
      use_gpu: useGpu,
    };

    // Step 1: create the project only (folders + storyboard.json — no OpenAI,
    // no Kokoro, no FFmpeg). Step 2: kick off script generation separately so
    // a missing OpenAI key never loses the project.
    let projectId: string;
    try {
      const r = await fetch("/worker-api/doodle/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const err = await readApiError(r, "Create failed");
        toast.error("Failed to create project", { description: errorDescription(err) });
        return;
      }
      const j = await r.json();
      projectId = j.projectId ?? j.project?.id;
      toast.success("Project created");
    } catch (e: any) {
      console.error("[doodle] create request failed", e);
      toast.error("Failed to create project", { description: e.message });
      return;
    } finally {
      setSubmitting(false);
    }

    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/script`, { method: "POST" });
      if (!r.ok) {
        const err = await readApiError(r, "Script generation failed");
        toast.warning("Project created, but script generation did not start", {
          description: errorDescription(err),
        });
      } else {
        toast.success("Writing script…");
      }
    } catch (e: any) {
      console.error("[doodle] script start failed", e);
      toast.warning("Project created, but script generation did not start", {
        description: e.message,
      });
    }
    onCreated(projectId);
  };

  return (
    <Card className="p-4 space-y-4 border-border/40">
      <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">New Video</div>

      <Tabs value={mode} onValueChange={(v) => setMode(v as "topic" | "script")}>
        <TabsList>
          <TabsTrigger value="topic">From Topic</TabsTrigger>
          <TabsTrigger value="script">From Script</TabsTrigger>
        </TabsList>
        <TabsContent value="topic" className="pt-3">
          <Label className="text-[11px] text-muted-foreground mb-1">Topic</Label>
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g. Why ancient humans stopped sleeping through the night"
          />
        </TabsContent>
        <TabsContent value="script" className="pt-3">
          <Label className="text-[11px] text-muted-foreground mb-1">Your script</Label>
          <Textarea
            rows={6}
            value={scriptText}
            onChange={(e) => setScriptText(e.target.value)}
            placeholder="Paste your full narration script here…"
          />
        </TabsContent>
      </Tabs>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Niche</Label>
          <select
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
          >
            {NICHES.map((n) => <option key={n.value} value={n.value}>{n.label}</option>)}
          </select>
        </div>
        {niche === "custom" && (
          <div>
            <Label className="text-[11px] text-muted-foreground mb-1">Custom niche</Label>
            <Input value={customNiche} onChange={(e) => setCustomNiche(e.target.value)} placeholder="e.g. True Crime" />
          </div>
        )}
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Video length</Label>
          <select
            value={durationPreset}
            onChange={(e) => setDurationPreset(Number(e.target.value))}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
          >
            {DURATION_PRESETS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
          </select>
        </div>
        {durationPreset === -1 && (
          <div>
            <Label className="text-[11px] text-muted-foreground mb-1">Minutes</Label>
            <Input
              type="number" min={1} step={1}
              value={customMinutes}
              onChange={(e) => setCustomMinutes(Number(e.target.value) || 1)}
            />
          </div>
        )}
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Frame interval</Label>
          <select
            value={frameInterval}
            onChange={(e) => setFrameInterval(e.target.value)}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
          >
            {FRAME_INTERVALS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Aspect ratio</Label>
          <select
            value={aspectRatio}
            onChange={(e) => setAspectRatio(e.target.value as "16:9" | "9:16" | "1:1")}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
          >
            {ASPECT_RATIOS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </div>
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Voice</Label>
          <select
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
            disabled={voices ? !voices.available : false}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm disabled:opacity-50"
          >
            {(voices?.voices || []).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            {!voices?.voices?.length && <option value="am_michael">Michael (US male, warm)</option>}
          </select>
          {voices && !voices.available && (
            <p className="text-[11px] text-destructive mt-1">{voices.reason || "Kokoro TTS unavailable"}</p>
          )}
        </div>
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Subtitle mode</Label>
          <select
            value={subtitleStyle}
            onChange={(e) => setSubtitleStyle(e.target.value)}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
          >
            {SUBTITLE_STYLES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
          <p className="text-[10px] text-muted-foreground mt-1">
            {subtitleStyle === "none"
              ? "captions.srt is still exported — nothing is burned into the video."
              : "Burned into the MP4. You can re-render any style later."}
          </p>
        </div>
        <div>
          <Label className="text-[11px] text-muted-foreground mb-1">Motion style</Label>
          <select
            value={motionStyle}
            onChange={(e) => setMotionStyle(e.target.value)}
            className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
          >
            {MOTION_STYLES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </div>
      </div>

      <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs text-primary/90 flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 shrink-0" />
        Estimated <strong className="mx-1">{estimatedFrames}</strong> images to generate in Flow.
      </div>

      <button
        type="button"
        onClick={() => setAdvancedOpen((o) => !o)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {advancedOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRightIcon className="h-3.5 w-3.5" />}
        Advanced
      </button>

      {advancedOpen && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 rounded-md border border-border/40 p-3">
          <div>
            <Label className="text-[11px] text-muted-foreground mb-1">OpenAI model</Label>
            <Input value={openaiModel} onChange={(e) => setOpenaiModel(e.target.value)} placeholder="gpt-4o-mini" />
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground mb-1">Voice speed ({voiceSpeed.toFixed(2)})</Label>
            <Slider min={0.8} max={1.1} step={0.01} value={voiceSpeed} onValueChange={(v) => setVoiceSpeed(v[0])} />
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground mb-1">Motion intensity ({motionIntensity.toFixed(2)})</Label>
            <Slider min={0} max={1} step={0.05} value={motionIntensity} onValueChange={(v) => setMotionIntensity(v[0])} />
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground mb-1">Render quality</Label>
            <select
              value={renderQuality}
              onChange={(e) => setRenderQuality(e.target.value)}
              className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
            >
              {RENDER_QUALITIES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-xs h-8">
              <input type="checkbox" checked={useGpu} onChange={(e) => setUseGpu(e.target.checked)} />
              Use GPU (nvenc)
            </label>
          </div>
        </div>
      )}

      <Button onClick={submit} disabled={submitting} className="w-full">
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create Video"}
      </Button>
    </Card>
  );
}
