"use client";

// Project detail page — kept thin, composes doodle components. Polls the
// storyboard while a job is active and polls /worker-api/jobs/{job_id} at 1s
// while that job runs (per PRP contract).

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, PenTool } from "lucide-react";
import { toast } from "sonner";
import { ManualFlowInstructionsCard } from "@/components/doodle/manual-flow-card";
import { ProgressSteps } from "@/components/doodle/progress-steps";
import { LocalImageGeneration } from "@/components/doodle/local-image-gen";
import { ActionButtons } from "@/components/doodle/action-buttons";
import { StoryboardTable } from "@/components/doodle/storyboard-table";
import { BulkUploadZone } from "@/components/doodle/bulk-upload-zone";
import { MissingImagesBanner } from "@/components/doodle/missing-images-banner";
import { VoiceStatusStrip } from "@/components/doodle/voice-status-strip";
import { ExportPanel } from "@/components/doodle/export-panel";
import type { DoodleScene, DoodleStoryboard } from "@/types/doodle";
import { estimatedFrameCount } from "@/components/doodle/constants";

export default function DoodleProjectPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;

  const [storyboard, setStoryboard] = useState<DoodleStoryboard | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobProgress, setJobProgress] = useState<number | null>(null);
  const [jobMessage, setJobMessage] = useState("");
  const [retrying, setRetrying] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStoryboard = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}`);
      if (r.status === 404) { setNotFound(true); return; }
      if (!r.ok) throw new Error(`Load failed (${r.status})`);
      const sb: DoodleStoryboard = await r.json();
      setStoryboard(sb);
    } catch (e: any) {
      toast.error("Failed to load project", { description: e.message });
    }
  }, [projectId]);

  useEffect(() => {
    loadStoryboard();
  }, [loadStoryboard]);

  // Background poll of the storyboard while a pipeline stage is running, so
  // scenes/status update even if this tab didn't kick off the job itself.
  useEffect(() => {
    const isActive = storyboard && ["scripting", "voicing", "rendering"].includes(storyboard.status);
    if (!isActive) return;
    const id = setInterval(loadStoryboard, 3000);
    return () => clearInterval(id);
  }, [storyboard, loadStoryboard]);

  // Job polling at 1s while a job we started is running.
  useEffect(() => {
    if (!jobId) return;
    let stop = false;

    const tick = async () => {
      if (stop) return;
      try {
        const r = await fetch(`/worker-api/jobs/${jobId}`);
        if (r.ok) {
          const j = await r.json();
          setJobProgress(j.progress ?? 0);
          setJobMessage(j.progress_message || "");
          if (j.status === "done" || j.status === "failed" || j.status === "cancelled") {
            stop = true;
            if (pollRef.current) clearInterval(pollRef.current);
            setJobId(null);
            setJobProgress(null);
            await loadStoryboard();
            if (j.status === "failed") toast.error("Job failed", { description: j.error || "" });
          }
        }
      } catch {}
    };

    tick();
    pollRef.current = setInterval(tick, 1000);
    return () => {
      stop = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId, loadStoryboard]);

  const handleImageUploaded = (index: number, scene: DoodleScene) => {
    setStoryboard((sb) => {
      if (!sb) return sb;
      const scenes = sb.scenes.map((s) => (s.index === index ? scene : s));
      const missing = scenes.filter((s) => !s.image_path).map((s) => s.index);
      return { ...sb, scenes, missing_images: missing };
    });
  };

  const handleImageRemoved = (index: number) => {
    setStoryboard((sb) => {
      if (!sb) return sb;
      const scenes = sb.scenes.map((s) => (s.index === index ? { ...s, image_path: null } : s));
      const missing = scenes.filter((s) => !s.image_path).map((s) => s.index);
      return { ...sb, scenes, missing_images: missing };
    });
  };

  const handleReorder = async (order: number[]) => {
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/scenes/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order }),
      });
      if (!r.ok) throw new Error(`Reorder failed (${r.status})`);
      await loadStoryboard();
    } catch (e: any) {
      toast.error("Failed to reorder", { description: e.message });
    }
  };

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await loadStoryboard();
      toast.info("Reloaded project state");
    } finally {
      setRetrying(false);
    }
  };

  if (notFound) {
    return (
      <div className="mx-auto max-w-3xl p-6 space-y-3">
        <p className="text-sm text-muted-foreground">Project not found.</p>
        <Link href="/doodle" className="text-sm text-primary hover:underline">Back to Auto Story Doodle</Link>
      </div>
    );
  }

  if (!storyboard) {
    return <div className="mx-auto max-w-5xl p-6 text-sm text-muted-foreground">Loading…</div>;
  }

  const estimatedFrames = estimatedFrameCount(
    storyboard.settings.target_duration_seconds,
    String(storyboard.settings.frame_interval_seconds),
  );
  const missingIndexes = storyboard.missing_images ?? storyboard.scenes.filter((s) => !s.image_path).map((s) => s.index);
  const busy = ["scripting", "voicing", "rendering"].includes(storyboard.status);

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex items-center gap-3">
        <button onClick={() => router.push("/doodle")} className="text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-emerald-400 shrink-0">
          <PenTool className="h-5 w-5 text-primary-foreground" />
        </div>
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight truncate">{storyboard.title || storyboard.topic || "Untitled"}</h1>
          <p className="text-sm text-muted-foreground truncate">{storyboard.description || "No description yet."}</p>
        </div>
      </div>

      <ProgressSteps
        storyboard={storyboard}
        estimatedFrames={estimatedFrames}
        jobProgress={jobId ? jobProgress : null}
        jobMessage={jobMessage}
      />

      <ManualFlowInstructionsCard />

      <LocalImageGeneration
        projectId={projectId}
        storyboard={storyboard}
        onJobStarted={(id) => { setJobId(id); setJobProgress(0); }}
        busy={busy}
        onRefresh={loadStoryboard}
      />

      <ActionButtons
        projectId={projectId}
        storyboard={storyboard}
        onJobStarted={(id) => { setJobId(id); setJobProgress(0); }}
        busy={busy}
      />

      <VoiceStatusStrip storyboard={storyboard} />

      <MissingImagesBanner missingIndexes={missingIndexes} />

      <ExportPanel projectId={projectId} storyboard={storyboard} onRetry={handleRetry} retrying={retrying} />

      <StoryboardTable
        projectId={projectId}
        scenes={storyboard.scenes}
        onImageUploaded={handleImageUploaded}
        onImageRemoved={handleImageRemoved}
        onReorder={handleReorder}
      />

      <BulkUploadZone projectId={projectId} onDone={loadStoryboard} />
    </div>
  );
}
