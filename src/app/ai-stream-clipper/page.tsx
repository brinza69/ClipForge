"use client";

// AI Stream Clipper — landing page: new-project form + the project list.
//
// Polling is conditional on purpose. A terminal project can never change on
// its own, so an idle tab full of finished projects issues zero requests; the
// 4s interval only exists while the backend has something in flight.

import { useCallback, useEffect, useState } from "react";
import { Scissors } from "lucide-react";
import { toast } from "sonner";
import { SourceForm } from "@/components/clipper/source-form";
import { ClipperProjectCard } from "@/components/clipper/project-card";
import { Skeleton } from "@/components/ui/skeleton";
import { readApiError, errorDescription } from "@/lib/api-error";
import {
  CLIPPER_API,
  type ClipperProjectSummary,
  type ProjectStatus,
} from "@/types/clipper";

const TERMINAL: ReadonlySet<ProjectStatus> = new Set<ProjectStatus>([
  "ready",
  "failed",
  "cancelled",
]);

export default function AiStreamClipperPage() {
  const [projects, setProjects] = useState<ClipperProjectSummary[]>([]);
  const [loaded, setLoaded] = useState(false);

  const loadProjects = useCallback(async () => {
    try {
      const r = await fetch(`${CLIPPER_API}/projects`);
      if (r.ok) setProjects((await r.json()) as ClipperProjectSummary[]);
    } catch {
      // Silent: this runs on a timer, and a toast per tick would be noise.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  const busy = projects.some((p) => !TERMINAL.has(p.status));
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => void loadProjects(), 4000);
    return () => clearInterval(id);
  }, [busy, loadProjects]);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this clip project? This removes every clip and file it produced.")) {
      return;
    }
    try {
      const r = await fetch(`${CLIPPER_API}/projects/${id}`, { method: "DELETE" });
      if (!r.ok) {
        const e = await readApiError(r, "Could not delete the project");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      setProjects((ps) => ps.filter((p) => p.id !== id));
      toast.success("Project deleted");
    } catch (err) {
      toast.error("Could not delete the project", {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-emerald-400">
          <Scissors className="h-5 w-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">AI Stream Clipper</h1>
          <p className="text-sm text-muted-foreground">
            Point it at a stream VOD and it finds the moments worth posting, then cuts them into
            ranked vertical clips you review before export.
          </p>
        </div>
      </div>

      <SourceForm onCreated={() => void loadProjects()} />

      <div className="space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Projects {projects.length > 0 && `(${projects.length})`}
        </div>

        {!loaded && (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-52 w-full rounded-xl" />
            ))}
          </div>
        )}

        {loaded && projects.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No clip projects yet — paste a VOD URL above to make the first one.
          </p>
        )}

        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <ClipperProjectCard key={p.id} project={p} onDelete={handleDelete} />
          ))}
        </div>
      </div>
    </div>
  );
}
