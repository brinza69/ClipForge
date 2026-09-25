"use client";

// The PROJECT's answer to "does the source already carry burned subtitles".
//
// A clip's own answer (clip editor) still wins; this one decides for every clip
// that has not said. Saved through PATCH /projects/{id}/settings, which for this
// key alone re-scores nothing (B2, PRPs/clipper-master-plan-2026-09-24.md §5) —
// before that, every settings change launched a rescore, which is why the switch
// had no UI at all. The select is bound to the server's value: a refused change
// leaves the saved one showing, never an optimistic one.

import { useState } from "react";

import { Card } from "@/components/ui/card";
import { errorDescription, readApiError } from "@/lib/api-error";
import { CLIPPER_API } from "@/types/clipper";

interface BlockingClip {
  clip_id: string;
  title?: string | null;
  max_content_height?: number | null;
}

const SELECT_CLASS =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm text-foreground " +
  "outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

export function ProjectCaptionSource({ projectId, value, onSaved }: {
  projectId: string;
  value: boolean | null;
  onSaved: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blocking, setBlocking] = useState<BlockingClip[]>([]);

  const save = async (next: boolean | null) => {
    setSaving(true);
    setNotice(null);
    setError(null);
    setBlocking([]);
    try {
      const r = await fetch(`${CLIPPER_API}/projects/${projectId}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: { source_has_burned_captions: next } }),
      });
      if (!r.ok) {
        // The body is read twice: the shared reader for the message, the raw
        // detail for the clips that blocked the change.
        const raw = await r.clone().json().catch(() => null);
        const detail = raw?.detail ?? raw;
        setBlocking(Array.isArray(detail?.blocking_clips) ? detail.blocking_clips : []);
        setError(errorDescription(
          await readApiError(r, "Setarea proiectului nu a putut fi salvată")));
        return;
      }
      const body = await r.json();
      const cs = body.caption_source ?? {};
      const invalidated = (cs.invalidated_clip_ids ?? []).length;
      const cleared = (cs.export_cleared_clip_ids ?? []).length;
      setNotice(!cs.changed
        ? "Nimic de schimbat."
        : `Salvat. ${invalidated === 1 ? "1 clip urmează" : `${invalidated} clipuri urmează`} acum altă decizie`
          + (cleared
            ? `; ${cleared === 1 ? "1 export trebuie refăcut" : `${cleared} exporturi trebuie refăcute`} (fișierele vechi rămân pe disc).`
            : "."));
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="space-y-2 border-border/40 p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Subtitrarea din sursă
        </div>
        <span className="text-xs text-muted-foreground">
          Pentru clipurile fără răspuns propriu; răspunsul din editorul unui clip câștigă.
        </span>
        <select
          aria-label="Subtitrarea din sursă, la nivel de proiect"
          className={`${SELECT_CLASS} ml-auto`}
          disabled={saving}
          value={value === null ? "" : value ? "suppress" : "burn"}
          onChange={(e) => void save(e.target.value === "" ? null : e.target.value === "suppress")}
        >
          <option value="">Nedeclarat — se arde (implicit)</option>
          <option value="burn">Arde subtitrarea ClipForge</option>
          <option value="suppress">Nu arde — sursa are deja subtitrare</option>
        </select>
      </div>
      {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
      {error && (
        <div className="space-y-1 rounded border border-rose-500/30 bg-rose-500/10 p-2 text-xs text-rose-500">
          <p>{error}</p>
          {blocking.length > 0 && (
            <ul className="list-disc pl-4">
              {blocking.map((c) => (
                <li key={c.clip_id}>
                  {c.title || c.clip_id}
                  {c.max_content_height
                    ? ` — materialul trebuie să aibă cel mult ${c.max_content_height} px înălțime`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}
