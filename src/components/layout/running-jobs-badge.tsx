"use client";

/**
 * Sidebar running-jobs badge. Polls /api/jobs?status=queued,running every
 * 3s so the user can see active pipeline work from any page, and click
 * through to the relevant page. Renders nothing when idle.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

interface ActiveJob {
  id: string;
  type: string;
  status: string;
  progress: number;
  progress_message: string;
}

function hrefForType(type: string): string {
  switch (type) {
    case "parallel_pipeline":
      return "/parallel-sheets";
    case "remix_pipeline":
      return "/remix";
    default:
      return "/utilities";
  }
}

export function RunningJobsBadge() {
  const [jobs, setJobs] = useState<ActiveJob[]>([]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch("/worker-api/jobs?status=queued,running");
        if (!r.ok) return;
        const data = await r.json();
        const list: ActiveJob[] = Array.isArray(data) ? data : (data.jobs || []);
        if (!cancelled) setJobs(list.filter((j) => j.status === "running" || j.status === "queued"));
      } catch {
        /* keep last value */
      }
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (jobs.length === 0) return null;

  // Prefer a running job for the headline; fall back to the first.
  const headline = jobs.find((j) => j.status === "running") || jobs[0];
  const pct = Math.round((headline.progress || 0) * 100);

  return (
    <Link
      href={hrefForType(headline.type)}
      className="mx-3 mb-2 flex items-center gap-2 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-xs text-primary transition-colors hover:bg-primary/15"
      title={headline.progress_message || "Job running"}
    >
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
      <span className="flex-1 min-w-0 truncate">
        {jobs.length === 1
          ? `${headline.progress_message || "Running"} (${pct}%)`
          : `${jobs.length} jobs · ${pct}%`}
      </span>
    </Link>
  );
}
