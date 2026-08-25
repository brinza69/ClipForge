"use client";

// The clip editor — trim, headline, captions, and a still that shows the result.
//
// The backend for this has been complete and reachable since the clipper
// shipped: PATCH /clips/{id} takes the boundaries, the headline and the caption
// plan, /regenerate re-derives any one part, and /preview-frame renders a still
// with the captions burned in through the SAME code path the export uses. None
// of it had a UI, so editing a clip meant curl. Phase 9.6 of the task board.
//
// What this deliberately does NOT do is drag-to-crop. Editing the layout rects
// by hand is a canvas tool and a build of its own; the layout is planned from
// detected regions and the honest fix for a bad one is better detection, which
// is where the work has gone. `layout_plan` stays patchable over the API for
// anyone who needs it.

import { useCallback, useEffect, useMemo, useState } from "react";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { errorDescription, readApiError } from "@/lib/api-error";
import { CLIPPER_API, type ClipperClip } from "@/types/clipper";

interface Preset {
  id: string;
  name: string;
}

function timecode(seconds: number): string {
  const s = Math.max(0, seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${h > 0 ? `${h}:${`${m}`.padStart(2, "0")}` : m}:${sec}`;
}

function Field({ label, hint, children }: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[11px] text-muted-foreground">{label}</span>
        {hint && <span className="text-[10px] text-muted-foreground/70">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// Seconds in and out of a text box.
//
// Uncontrolled, keyed on the value. A controlled input would have to hold the
// half-typed text in state — "1" on the way to "12.5" must not commit as 1 and
// move the clip — and syncing that state back from the prop is a setState in an
// effect. Remounting on an external change does the same job with no state at
// all, and the value only ever changes from outside on the +/- buttons.
function Seconds({ value, onCommit, step = 0.1 }: {
  value: number;
  onCommit: (v: number) => void;
  step?: number;
}) {
  const commit = (input: HTMLInputElement) => {
    const n = Number.parseFloat(input.value);
    if (Number.isFinite(n)) onCommit(n);
    else input.value = value.toFixed(2);      // put back what it was
  };

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onCommit(value - step)}
        className="rounded border border-border/50 px-2 py-1 text-xs hover:bg-muted/40"
      >
        −
      </button>
      <input
        key={value}
        defaultValue={value.toFixed(2)}
        onBlur={(e) => commit(e.currentTarget)}
        onKeyDown={(e) => e.key === "Enter" && commit(e.currentTarget)}
        inputMode="decimal"
        className="w-24 rounded border border-border/50 bg-transparent px-2 py-1 text-center text-xs tabular-nums"
      />
      <button
        type="button"
        onClick={() => onCommit(value + step)}
        className="rounded border border-border/50 px-2 py-1 text-xs hover:bg-muted/40"
      >
        +
      </button>
    </div>
  );
}

export function ClipEditor({
  clip,
  open,
  onOpenChange,
  onSaved,
}: {
  clip: ClipperClip | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [headline, setHeadline] = useState("");
  const [presetId, setPresetId] = useState("");
  const [captionY, setCaptionY] = useState(0.75);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped on every save so the <img> refetches — the frame is rendered
  // server-side from the SAVED clip, so it is only true after a round trip.
  const [frameKey, setFrameKey] = useState(0);
  const [at, setAt] = useState(0.5);

  useEffect(() => {
    if (!clip) return;
    setStart(clip.start_time);
    setEnd(clip.end_time);
    setHeadline(clip.headline_text ?? "");
    setPresetId(clip.caption_plan?.preset_id ?? "");
    setCaptionY(clip.caption_plan?.y_pct ?? 0.75);
    setAt(Math.min(0.5, Math.max(0, clip.duration / 2)));
    setError(null);
  }, [clip]);

  useEffect(() => {
    if (!open) return;
    fetch(`${CLIPPER_API}/presets`)
      .then((r) => (r.ok ? r.json() : { presets: [] }))
      .then((d) => setPresets(d.presets ?? []))
      .catch(() => setPresets([]));
  }, [open]);

  const duration = Math.max(0, end - start);
  const trimmed = clip ? start !== clip.start_time || end !== clip.end_time : false;
  const dirty = useMemo(() => {
    if (!clip) return false;
    return (
      trimmed ||
      headline !== (clip.headline_text ?? "") ||
      presetId !== (clip.caption_plan?.preset_id ?? "") ||
      Math.abs(captionY - (clip.caption_plan?.y_pct ?? 0.75)) > 1e-4
    );
  }, [clip, trimmed, headline, presetId, captionY]);

  const save = useCallback(async () => {
    if (!clip) return;
    setBusy("save");
    setError(null);
    const body: Record<string, unknown> = {};
    if (trimmed) {
      body.start_time = start;
      body.end_time = end;
    }
    if (headline !== (clip.headline_text ?? "")) body.headline_text = headline;
    if (presetId && presetId !== (clip.caption_plan?.preset_id ?? "")) {
      body.caption_preset_id = presetId;
    }
    if (clip.caption_plan && Math.abs(captionY - (clip.caption_plan.y_pct ?? 0.75)) > 1e-4) {
      // `y_pct_manual` is what stops the export moving it back. The render
      // re-places the caption around the game UI it detects in the cut, which
      // is right when nobody has expressed a preference and wrong the moment
      // somebody has.
      body.caption_plan = { ...clip.caption_plan, y_pct: captionY, y_pct_manual: true };
    }

    try {
      const r = await fetch(`${CLIPPER_API}/clips/${clip.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        // `readApiError` returns a shape, not a thrown Error, so it is read
        // here rather than in the catch — which only sees network failures.
        setError(errorDescription(await readApiError(r, "Could not save that edit")));
        return;
      }
      setFrameKey((k) => k + 1);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [clip, trimmed, start, end, headline, presetId, captionY, onSaved]);

  const regenerate = useCallback(
    async (what: string) => {
      if (!clip) return;
      setBusy(what);
      setError(null);
      try {
        const r = await fetch(`${CLIPPER_API}/clips/${clip.id}/regenerate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ what }),
        });
        if (!r.ok) {
          setError(errorDescription(
            await readApiError(r, `Could not regenerate the ${what}`)));
          return;
        }
        setFrameKey((k) => k + 1);
        onSaved();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [clip, onSaved],
  );

  if (!clip) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-base">Edit clip</DialogTitle>
        </DialogHeader>

        <div className="grid gap-5 sm:grid-cols-[300px_1fr]">
          {/* The still, rendered server-side through the same overlay builder
              the export uses — so the caption STYLE and HEIGHT are the real
              thing. The framing is not: the endpoint returns the 16:9 source
              and the export crops it to 9:16, which is said below rather than
              left for someone to discover after shipping a clip. */}
          <div className="space-y-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              key={frameKey}
              src={`${CLIPPER_API}/clips/${clip.id}/preview-frame?t=${at.toFixed(2)}&v=${frameKey}`}
              alt={`Frame at ${at.toFixed(1)}s with the captions burned in`}
              className="w-full rounded-lg border border-border/40 bg-black"
            />
            <input
              type="range"
              min={0}
              max={Math.max(0.1, duration)}
              step={0.1}
              value={Math.min(at, duration)}
              onChange={(e) => setAt(Number.parseFloat(e.target.value))}
              className="w-full"
              aria-label="Frame to preview"
            />
            <p className="text-center text-[11px] tabular-nums text-muted-foreground">
              +{at.toFixed(1)}s of {duration.toFixed(1)}s
            </p>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              The whole source frame. Caption style and height are exactly what
              the export burns; the framing is not — the export crops this to
              9:16. Use <span className="text-foreground/70">render preview</span> to
              see the cut itself.
            </p>
            {trimmed && (
              <p className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-500">
                The still is of the SAVED clip. Save to see the new boundaries.
              </p>
            )}
          </div>

          <div className="space-y-4">
            <Field
              label="Boundaries"
              hint={`${timecode(start)} – ${timecode(end)} · ${duration.toFixed(1)}s`}
            >
              <div className="flex flex-wrap items-center gap-3">
                <div className="space-y-1">
                  <span className="text-[10px] text-muted-foreground">start</span>
                  <Seconds value={start} onCommit={(v) => setStart(Math.max(0, v))} />
                </div>
                <div className="space-y-1">
                  <span className="text-[10px] text-muted-foreground">end</span>
                  <Seconds value={end} onCommit={setEnd} />
                </div>
              </div>
              {end <= start && (
                <p className="text-[11px] text-rose-500">
                  The end has to come after the start.
                </p>
              )}
              {trimmed && (
                <p className="text-[11px] text-muted-foreground">
                  Saving a new range drops the rendered preview — a stale render
                  of a clip you have moved is worse than none.
                </p>
              )}
            </Field>

            <Field label="Headline">
              <div className="flex gap-2">
                <input
                  value={headline}
                  onChange={(e) => setHeadline(e.target.value)}
                  placeholder="No headline"
                  className="min-w-0 flex-1 rounded border border-border/50 bg-transparent px-2 py-1.5 text-xs"
                />
                <button
                  type="button"
                  onClick={() => regenerate("headline")}
                  disabled={busy !== null}
                  className="shrink-0 rounded border border-border/50 px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted/40 disabled:opacity-50"
                >
                  {busy === "headline" ? "…" : "regenerate"}
                </button>
              </div>
            </Field>

            <Field label="Caption preset">
              <div className="flex gap-2">
                <select
                  value={presetId}
                  onChange={(e) => setPresetId(e.target.value)}
                  className="min-w-0 flex-1 rounded border border-border/50 bg-transparent px-2 py-1.5 text-xs"
                >
                  <option value="">— unchanged —</option>
                  {presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => regenerate("captions")}
                  disabled={busy !== null}
                  className="shrink-0 rounded border border-border/50 px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted/40 disabled:opacity-50"
                >
                  {busy === "captions" ? "…" : "rebuild"}
                </button>
              </div>
            </Field>

            <Field
              label="Caption height"
              hint={`${(captionY * 100).toFixed(0)}% of frame height`}
            >
              <input
                type="range"
                min={0.2}
                max={0.9}
                step={0.01}
                value={captionY}
                onChange={(e) => setCaptionY(Number.parseFloat(e.target.value))}
                className="w-full"
                disabled={!clip.caption_plan}
              />
              <p className="text-[11px] text-muted-foreground">
                The seven captioned reference Shorts sit at 50–78%, four of them
                at 50–53%. Moving this by hand also stops the export re-placing
                it around detected game UI.
              </p>
            </Field>

            {error && (
              <p className="rounded border border-rose-500/30 bg-rose-500/10 p-2 text-xs text-rose-500">
                {error}
              </p>
            )}

            <div className="flex items-center justify-end gap-2 border-t border-border/40 pt-3">
              <button
                type="button"
                onClick={() => regenerate("preview")}
                disabled={busy !== null || dirty}
                title={dirty ? "Save first — a preview of unsaved edits is a preview of the old clip" : ""}
                className="rounded border border-border/50 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted/40 disabled:opacity-40"
              >
                {busy === "preview" ? "queued…" : "render preview"}
              </button>
              <button
                type="button"
                onClick={save}
                disabled={busy !== null || !dirty || end <= start}
                className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {busy === "save" ? "saving…" : "save"}
              </button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
