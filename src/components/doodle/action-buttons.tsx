"use client";

// Buttons row: Generate Flow Prompts, Copy All Prompts, Export CSV/JSON,
// Generate Voiceover, Render Video (409 → placeholder-frames dialog).

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import { Loader2, RefreshCw, Copy, FileDown, FileJson, Mic, Clapperboard, Archive } from "lucide-react";
import { toast } from "sonner";
import { readApiError, errorDescription } from "@/lib/api-error";
import type { DoodleStoryboard } from "@/types/doodle";

interface Props {
  projectId: string;
  storyboard: DoodleStoryboard;
  onJobStarted: (jobId: string) => void;
  busy: boolean;
}

export function ActionButtons({ projectId, storyboard, onJobStarted, busy }: Props) {
  const [placeholderDialogOpen, setPlaceholderDialogOpen] = useState(false);
  const [missingCount, setMissingCount] = useState(0);
  const [loading, setLoading] = useState<string | null>(null);

  const generateScript = async () => {
    setLoading("script");
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/script`, { method: "POST" });
      if (!r.ok) {
        const err = await readApiError(r, "Script generation failed");
        toast.error("Failed to start script generation", { description: errorDescription(err) });
        return;
      }
      const j = await r.json();
      onJobStarted(j.job_id);
      toast.success("Writing script + Flow prompts…");
    } catch (e: any) {
      console.error("[doodle] script start failed", e);
      toast.error("Failed to start script generation", { description: e.message });
    } finally {
      setLoading(null);
    }
  };

  const copyAllPrompts = async () => {
    const text = storyboard.scenes
      .map((s) => `${s.index + 1}. ${s.image_prompt}`)
      .join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      toast.success("All prompts copied");
    } catch {
      toast.error("Clipboard write failed");
    }
  };

  const generateVoiceover = async (onlyMissing: boolean) => {
    setLoading("voiceover");
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/voiceover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ only_missing: onlyMissing }),
      });
      if (!r.ok) {
        const err = await readApiError(r, "Voiceover failed");
        throw new Error(errorDescription(err));
      }
      const j = await r.json();
      onJobStarted(j.job_id);
      toast.success(onlyMissing ? "Voicing missing scenes…" : "Voiceover generation started");
    } catch (e: any) {
      toast.error("Failed to start voiceover", { description: e.message });
    } finally {
      setLoading(null);
    }
  };

  const backupImages = async () => {
    setLoading("backup");
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/backup-images`, { method: "POST" });
      if (!r.ok) {
        const err = await readApiError(r, "Backup failed");
        throw new Error(errorDescription(err));
      }
      const j = await r.json();
      toast.success(`Backed up ${j.count} image${j.count === 1 ? "" : "s"} as ZIP`, {
        description: j.zip_path,
        action: {
          label: "Download",
          onClick: () => window.open(`/worker-doodle/${projectId}/${j.zip_path}`, "_blank"),
        },
      });
    } catch (e: any) {
      toast.error("Failed to back up images", { description: e.message });
    } finally {
      setLoading(null);
    }
  };

  const render = async (allowPlaceholders: boolean) => {
    setLoading("render");
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow_placeholders: allowPlaceholders }),
      });
      if (r.status === 409) {
        const j = await r.json().catch(() => ({}));
        const detail = j.detail;
        if (detail?.error === "VOICE_REQUIRED") {
          toast.error("Voiceover needed first", {
            description: detail.message || "Images are safe. Generate voiceover before rendering.",
          });
          return;
        }
        if (detail?.error === "MISSING_IMAGES") {
          const missing = Array.isArray(detail?.missing_scenes)
            ? detail.missing_scenes.length
            : (storyboard.missing_images?.length ?? 0);
          setMissingCount(missing);
          setPlaceholderDialogOpen(true);
          return;
        }
        toast.error("Cannot render right now", { description: detail?.message || "Project is busy." });
        return;
      }
      if (!r.ok) {
        const err = await readApiError(r, "Render failed");
        throw new Error(errorDescription(err));
      }
      const j = await r.json();
      onJobStarted(j.job_id);
      toast.success("Render started");
      setPlaceholderDialogOpen(false);
    } catch (e: any) {
      toast.error("Failed to start render", { description: e.message });
    } finally {
      setLoading(null);
    }
  };

  const anyLoading = loading !== null || busy;

  const total = storyboard.scenes.length;
  const voicedCount = storyboard.scenes.filter((s) => !!s.audio_duration).length;
  const allVoiced = total > 0 && voicedCount === total;
  const partiallyVoiced = voicedCount > 0 && !allVoiced;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant={storyboard.scenes.length === 0 ? "default" : "outline"}
        size="sm"
        onClick={generateScript}
        disabled={anyLoading}
      >
        {loading === "script" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        {storyboard.scenes.length === 0 ? "Generate Script + Flow Prompts" : "Regenerate Script"}
      </Button>
      <Button variant="outline" size="sm" onClick={copyAllPrompts} disabled={storyboard.scenes.length === 0}>
        <Copy className="h-3.5 w-3.5" /> Copy All Prompts
      </Button>
      <a href={`/worker-api/doodle/projects/${projectId}/prompts.csv`} target="_blank" rel="noreferrer">
        <Button variant="outline" size="sm"><FileDown className="h-3.5 w-3.5" /> Export CSV</Button>
      </a>
      <a href={`/worker-api/doodle/projects/${projectId}/prompts.json`} target="_blank" rel="noreferrer">
        <Button variant="outline" size="sm"><FileJson className="h-3.5 w-3.5" /> Export JSON</Button>
      </a>
      <Button variant="outline" size="sm" onClick={backupImages} disabled={anyLoading || total === 0}>
        {loading === "backup" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Archive className="h-3.5 w-3.5" />}
        Backup Images as ZIP
      </Button>
      <Button variant="outline" size="sm" onClick={() => generateVoiceover(false)} disabled={anyLoading || total === 0}>
        {loading === "voiceover" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
        Generate Voiceover
      </Button>
      {partiallyVoiced && (
        <Button variant="outline" size="sm" onClick={() => generateVoiceover(true)} disabled={anyLoading}>
          {loading === "voiceover" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
          Generate Missing Voiceover ({total - voicedCount})
        </Button>
      )}
      <Button
        onClick={() => render(false)}
        disabled={anyLoading || total === 0 || !allVoiced}
        title={!allVoiced ? "All scenes need a voiceover (audio_duration) before rendering." : undefined}
      >
        {loading === "render" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Clapperboard className="h-3.5 w-3.5" />}
        {allVoiced && storyboard.status === "voice_ready" ? "Continue Render" : "Render Video"}
      </Button>

      <Dialog open={placeholderDialogOpen} onOpenChange={setPlaceholderDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Missing images</DialogTitle>
            <DialogDescription>
              {missingCount} scene{missingCount === 1 ? "" : "s"} still {missingCount === 1 ? "has" : "have"} no image.
              You can render anyway using placeholder doodle frames (white background, scene subtitle text) for those
              scenes, or go back and finish uploading images.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPlaceholderDialogOpen(false)}>Cancel</Button>
            <Button onClick={() => render(true)} disabled={anyLoading}>
              {loading === "render" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Use placeholder frames
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
