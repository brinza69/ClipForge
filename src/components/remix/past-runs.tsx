"use client";

/**
 * Past Remix runs panel — used at the bottom of /remix. Owns its own fetch
 * + state. Parent can request a refresh by incrementing `refreshKey`
 * (typically after a finished job); the inline Refresh button works too.
 *
 * Paginated (10/page) with Prev/Next, and each run can be deleted (removes
 * its media files + the job row). Renders nothing when there are no runs.
 */

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Download, Trash2, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { toast } from "sonner";

interface PastRun {
  job_id: string;
  project_id: string;
  title: string;
  output_filename: string;
  file_size: number;
  file_available: boolean;
  finished_at: string | null;
  tts_engine?: string;
  transcript_target_lang?: string;
}

const PAGE = 10;

export function RemixPastRuns({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<PastRun[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [localKey, setLocalKey] = useState(0);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/remix/recent?limit=${PAGE}&offset=${offset}`);
      if (!r.ok) return;
      const j = await r.json();
      setRuns(j.runs || []);
      setTotal(typeof j.total === "number" ? j.total : (j.runs || []).length);
    } catch {
      /* keep last value */
    }
  }, [offset]);

  useEffect(() => { load(); }, [load, refreshKey, localKey]);

  // When a parent refresh or delete leaves the current page empty (e.g. last
  // item on the last page deleted), step back a page.
  useEffect(() => {
    if (runs.length === 0 && offset > 0) setOffset((o) => Math.max(0, o - PAGE));
  }, [runs.length, offset]);

  const del = async (run: PastRun) => {
    const mb = (run.file_size / 1024 / 1024).toFixed(0);
    if (!window.confirm(`Delete "${run.title}" and its files (~${mb} MB)? This can't be undone.`)) return;
    setDeleting(run.job_id);
    try {
      const r = await fetch(`/worker-api/remix/${run.job_id}`, { method: "DELETE" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Delete failed (${r.status})`);
      }
      toast.success("Run deleted");
      setLocalKey((k) => k + 1);
    } catch (e: any) {
      toast.error("Delete failed", { description: e.message });
    } finally {
      setDeleting(null);
    }
  };

  if (total === 0 && runs.length === 0) return null;

  const from = total === 0 ? 0 : offset + 1;
  const to = offset + runs.length;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE < total;

  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          Past remix runs {total > 0 && <span className="normal-case font-normal">({from}–{to} of {total})</span>}
        </div>
        <button
          type="button"
          onClick={() => setLocalKey((k) => k + 1)}
          className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
        >
          Refresh
        </button>
      </div>
      <div className="divide-y divide-border/40">
        {runs.map((r) => {
          const sizeMb = (r.file_size / 1024 / 1024).toFixed(1);
          const when = r.finished_at ? new Date(r.finished_at).toLocaleString() : "";
          return (
            <div key={r.job_id} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate" title={r.title}>
                  {r.title}
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-0.5 text-[11px] text-muted-foreground">
                  <span>{sizeMb} MB</span>
                  {when && <span>· {when}</span>}
                  {r.tts_engine && <span>· {r.tts_engine}</span>}
                  {r.transcript_target_lang && <span>· {r.transcript_target_lang}</span>}
                  <span className="font-mono">· {r.job_id}</span>
                </div>
              </div>
              {r.file_available ? (
                <a
                  href={`/worker-api/remix/${r.job_id}/download`}
                  download={r.output_filename}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-primary/40 bg-primary/5 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 transition-colors"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download
                </a>
              ) : (
                <Badge variant="outline" className="text-[10px] text-muted-foreground border-border/40 shrink-0">
                  file gone
                </Badge>
              )}
              <button
                type="button"
                onClick={() => del(r)}
                disabled={deleting === r.job_id}
                className="text-muted-foreground hover:text-destructive transition-colors p-1 shrink-0"
                title="Delete run + files"
              >
                {deleting === r.job_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              </button>
            </div>
          );
        })}
      </div>

      {(hasPrev || hasNext) && (
        <div className="flex items-center justify-between pt-1">
          <button
            type="button"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
            disabled={!hasPrev}
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Prev
          </button>
          <button
            type="button"
            onClick={() => setOffset((o) => o + PAGE)}
            disabled={!hasNext}
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground"
          >
            Next <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </Card>
  );
}
