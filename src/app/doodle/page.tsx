"use client";

// Auto Story Doodle Video — tab home. New-project form + polling project list.

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { PenTool } from "lucide-react";
import { toast } from "sonner";
import { NewProjectForm } from "@/components/doodle/new-project-form";
import { ProjectCard } from "@/components/doodle/project-card";
import { ManualFlowBanner } from "@/components/doodle/manual-flow-card";
import type { DoodleProjectSummary } from "@/types/doodle";

export default function DoodlePage() {
  const router = useRouter();
  const [projects, setProjects] = useState<DoodleProjectSummary[]>([]);
  const [loaded, setLoaded] = useState(false);

  const loadProjects = useCallback(async () => {
    try {
      const r = await fetch("/worker-api/doodle/projects");
      if (r.ok) setProjects(await r.json());
    } catch {}
    finally { setLoaded(true); }
  }, []);

  useEffect(() => {
    loadProjects();
    const id = setInterval(loadProjects, 5000);
    return () => clearInterval(id);
  }, [loadProjects]);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this project? This removes all its files.")) return;
    try {
      const r = await fetch(`/worker-api/doodle/projects/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`Delete failed (${r.status})`);
      setProjects((ps) => ps.filter((p) => p.id !== id));
      toast.success("Project deleted");
    } catch (e: any) {
      toast.error("Failed to delete", { description: e.message });
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-emerald-400">
          <PenTool className="h-5 w-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">Auto Story Doodle Video</h1>
          <p className="text-sm text-muted-foreground">Topic → script → Kokoro voiceover → Flow images → rendered video.</p>
        </div>
      </div>

      <ManualFlowBanner />

      <NewProjectForm onCreated={(id) => router.push(`/doodle/${id}`)} />

      <div className="space-y-3">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          Projects {projects.length > 0 && `(${projects.length})`}
        </div>
        {loaded && projects.length === 0 && (
          <p className="text-sm text-muted-foreground">No projects yet — create one above.</p>
        )}
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
          {projects.map((p) => (
            <ProjectCard key={p.id} project={p} onDelete={handleDelete} />
          ))}
        </div>
      </div>
    </div>
  );
}
