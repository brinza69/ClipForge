"use client";

/**
 * Past Remix runs panel — used at the bottom of /remix. Owns its own fetch
 * + state. Parent can request a refresh by incrementing `refreshKey`
 * (typically after a finished job); the inline Refresh button works too.
 *
 * Renders nothing when the backend returns no runs (the empty card was
 * just visual noise on first load).
 */

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Download } from "lucide-react";

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

export function RemixPastRuns({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<PastRun[]>([]);
  const [localKey, setLocalKey] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/remix/recent?limit=10`);
      if (!r.ok) return;
      const j = await r.json();
      setRuns(j.runs || []);
    } catch {
      /* keep last value */
    }
  }, []);

  useEffect(() => { load(); }, [load, refreshKey, localKey]);

  if (runs.length === 0) return null;

  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
          Past remix runs ({runs.length})
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
            </div>
          );
        })}
      </div>
    </Card>
  );
}
