"use client";

// AI Stream Clipper — project screen.
//
// Every visible state is re-derived from GET /projects/{id} on each poll: a
// hard refresh mid-analysis lands on exactly the same screen, because the
// server (not this component) owns "which stage are we at" and "which clips
// exist". The only local state here is the fetched payload itself.

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, ArrowLeft, Loader2, Play, RotateCcw, Scissors } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AnalysisProgress } from "@/components/clipper/analysis-progress";
import { CandidateGrid } from "@/components/clipper/candidate-grid";
import { errorDescription, readApiError } from "@/lib/api-error";
import {
  CLIPPER_API,
  CONTENT_TYPE_LABELS,
  formatTimecode,
  type ClipperProject,
  type ContentType,
  type ProjectStatus,
} from "@/types/clipper";

const CONTENT_TYPES = Object.keys(CONTENT_TYPE_LABELS) as ContentType[];

const TERMINAL: ReadonlySet<ProjectStatus> = new Set<ProjectStatus>([
  "ready",
  "failed",
  "cancelled",
]);

const SELECT_CLASS =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground " +
  "outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

export default function ClipperProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const [project, setProject] = useState<ClipperProject | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [savingType, setSavingType] = useState(false);

  const loadProject = useCallback(async () => {
    try {
      const r = await fetch(`${CLIPPER_API}/projects/${projectId}`);
      if (r.status === 404) {
        setNotFound(true);
        return;
      }
      if (!r.ok) {
        const e = await readApiError(r, "Could not load the project");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      setProject((await r.json()) as ClipperProject);
    } catch {
      // Silent: this also runs on a timer and a toast per tick would be noise.
    }
  }, [projectId]);

  useEffect(() => {
    void loadProject();
  }, [loadProject]);

  // Poll only while the backend has something in flight. A finished project
  // cannot change on its own, so an idle tab issues zero requests.
  const busy = !!project && (!!project.active_job || !TERMINAL.has(project.status));
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => void loadProject(), 3000);
    return () => clearInterval(id);
  }, [busy, loadProject]);

  const post = async (path: string, failure: string): Promise<boolean> => {
    try {
      const r = await fetch(`${CLIPPER_API}${path}`, { method: "POST" });
      if (!r.ok) {
        const e = await readApiError(r, failure);
        toast.error(e.message, { description: errorDescription(e) });
        return false;
      }
      return true;
    } catch (err) {
      toast.error(failure, {
        description: err instanceof Error ? err.message : String(err),
      });
      return false;
    }
  };

  const startAnalysis = async () => {
    setStarting(true);
    if (await post(`/projects/${projectId}/analyze`, "Could not start the analysis")) {
      toast.success("Analysis queued");
      await loadProject();
    }
    setStarting(false);
  };

  const retry = async () => {
    setRetrying(true);
    if (await post(`/projects/${projectId}/retry`, "Could not retry the analysis")) {
      toast.success("Retrying from the last completed stage");
      await loadProject();
    }
    setRetrying(false);
  };

  // "" clears the override — the backend's patcher treats an empty string as
  // "fall back to detection" and ignores nulls entirely.
  const overrideContentType = async (value: string) => {
    setSavingType(true);
    try {
      const r = await fetch(`${CLIPPER_API}/projects/${projectId}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content_type_override: value }),
      });
      if (!r.ok) {
        const e = await readApiError(r, "Could not change the content type");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      await loadProject();
      toast.success(value ? "Content type overridden" : "Using the detected content type");
    } catch (err) {
      toast.error("Could not change the content type", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSavingType(false);
    }
  };

  if (notFound) {
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-6">
        <p className="text-sm text-muted-foreground">That clip project no longer exists.</p>
        <Link href="/ai-stream-clipper" className="text-sm text-primary hover:underline">
          Back to AI Stream Clipper
        </Link>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Skeleton className="h-12 w-2/3 rounded-lg" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    );
  }

  const clips = project.clips ?? [];
  const detected = project.content_type ?? "unknown";
  const confidence = project.content_type_confidence;
  const effective = project.content_type_override || detected;

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <div className="flex items-center gap-3">
        <Link
          href="/ai-stream-clipper"
          className="text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-emerald-400">
          <Scissors className="h-5 w-5 text-primary-foreground" />
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold tracking-tight">
            {project.title || "Untitled clip project"}
          </h1>
          <p className="truncate text-sm text-muted-foreground">
            {project.channel_name || project.source_type}
            {project.duration ? ` · ${formatTimecode(project.duration)}` : ""}
            {project.width && project.height ? ` · ${project.width}×${project.height}` : ""}
            {project.fps ? ` · ${Math.round(project.fps)} fps` : ""}
          </p>
        </div>
      </div>

      <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 border-border/40 p-4">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Content type
        </div>
        <Badge variant="outline">{CONTENT_TYPE_LABELS[effective as ContentType] ?? effective}</Badge>
        <span className="text-xs text-muted-foreground">
          Detected {CONTENT_TYPE_LABELS[detected as ContentType] ?? detected}
          {typeof confidence === "number" ? ` · ${Math.round(confidence * 100)}% confident` : ""}
          {project.content_type_override ? " · overridden by you" : ""}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <label htmlFor="content-type-override" className="text-xs text-muted-foreground">
            Override
          </label>
          <select
            id="content-type-override"
            className={SELECT_CLASS}
            disabled={savingType}
            value={project.content_type_override ?? ""}
            onChange={(e) => void overrideContentType(e.target.value)}
          >
            <option value="">Use detected</option>
            {CONTENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {CONTENT_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>
      </Card>

      {project.error && !project.active_job && (
        <Card className="space-y-3 border-destructive/40 p-4">
          <div className="flex items-start gap-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0 break-words">{project.error}</div>
          </div>
          <p className="text-xs text-muted-foreground">
            Retry resumes from the last stage whose files are still on disk — a completed
            download is not fetched twice.
          </p>
          <Button variant="outline" onClick={() => void retry()} disabled={retrying}>
            {retrying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            Retry
          </Button>
        </Card>
      )}

      {project.active_job && (
        <AnalysisProgress job={project.active_job} onFinished={() => void loadProject()} />
      )}

      {clips.length > 0 && (
        <CandidateGrid project={project} clips={clips} onRefresh={() => void loadProject()} />
      )}

      {clips.length === 0 && !project.active_job && (
        <Card className="space-y-3 border-border/40 p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Ready to analyse
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs text-muted-foreground md:grid-cols-4">
            <div>
              Clips wanted
              <div className="font-medium text-foreground">
                {project.clipper_settings?.clip_count ?? "—"}
              </div>
            </div>
            <div>
              Length range
              <div className="font-medium text-foreground">
                {project.clipper_settings
                  ? `${project.clipper_settings.min_clip_s}–${project.clipper_settings.max_clip_s}s`
                  : "—"}
              </div>
            </div>
            <div>
              Platform
              <div className="font-medium text-foreground">
                {project.clipper_settings?.platform ?? "—"}
              </div>
            </div>
            <div>
              Source
              <div className="truncate font-medium text-foreground">{project.source_type}</div>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Analysis transcribes the source, finds candidate moments and ranks them. It runs on
            the worker, so you can leave this page while it works.
          </p>
          <Button onClick={() => void startAnalysis()} disabled={starting}>
            {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Start analysis
          </Button>
        </Card>
      )}
    </div>
  );
}
