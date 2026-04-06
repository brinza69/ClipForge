"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { APP_NAME } from "@/lib/constants";
import { UrlInput } from "@/components/ingestion/url-input";
import { ProjectCard } from "@/components/dashboard/project-card";
import { Separator } from "@/components/ui/separator";
import { Info, Sparkles, Film, Scissors, Monitor, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";

/* ══════════════════════════════════════════════════════════════════════════════
   DASHBOARD PAGE
   ══════════════════════════════════════════════════════════════════════════════ */
export default function DashboardPage() {
  const router = useRouter();

  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: api.projects.list,
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-10">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{APP_NAME}</h1>
        <p className="mt-1 text-muted-foreground">
          Paste a video link to preview it first, then choose how to process.
        </p>
      </div>

      {/* ── URL Paste Card ─────────────────────────────────────────────── */}
      <UrlInput
        onProjectCreated={(id) => {
          if (id) {
            router.push(`/projects/${id}`);
          }
        }}
      />

      {/* ── Storage-safe notice ────────────────────────────────────────── */}
      <div className="flex items-start gap-3 rounded-xl border border-border/20 bg-card/30 px-4 py-3 text-xs text-muted-foreground">
        <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-primary/60" />
        <p>
          <strong className="text-foreground/70">Storage-safe by default.</strong>{" "}
          Pasting a link only fetches metadata and a thumbnail — no full downloads
          happen until you choose an action. Estimated file sizes are shown before
          any download.
        </p>
      </div>

      <Separator className="bg-border/20" />

      {/* ── Recent Projects ────────────────────────────────────────────── */}
      <div>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Recent Projects</h2>
        </div>

        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : !projects || projects.length === 0 ? (
          /* Empty state */
          <div className="flex min-h-[300px] flex-col items-center justify-center rounded-2xl border border-dashed border-border/40 bg-card/20 px-8 text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
              <Sparkles className="h-8 w-8 text-primary" />
            </div>
            <h3 className="text-xl font-semibold">No projects yet</h3>
            <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
              Paste a video link above to get started. ClipForge will fetch metadata
              first, show you a preview, and only download after you choose an action.
            </p>
            <div className="mt-6 flex items-center gap-6 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Film className="h-3.5 w-3.5" /> YouTube, Twitch, Vimeo
              </span>
              <span className="flex items-center gap-1.5">
                <Scissors className="h-3.5 w-3.5" /> Auto-clip detection
              </span>
              <span className="flex items-center gap-1.5">
                <Monitor className="h-3.5 w-3.5" /> 1080x1920 export
              </span>
            </div>
          </div>
        ) : (
          /* Projects Grid */
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((project: any) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
