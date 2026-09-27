"use client";

// The clip editor — trim, headline, captions, and a still that shows the result.
//
// The backend for this has been complete and reachable since the clipper
// shipped: PATCH /clips/{id} takes the boundaries, the headline and the caption
// plan, /regenerate re-derives any one part, and /preview-frame renders a still
// with the captions burned in through the SAME code path the export uses. None
// of it had a UI, so editing a clip meant curl. Phase 9.6 of the task board.
//
// ReactionFraming adds explicit source-region selection for a reaction clip.
// Its saved choice is bound to the source and clip interval, and the ordinary
// preview/export honor it ahead of dynamic planning.

import { useCallback, useEffect, useMemo, useState } from "react";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { errorDescription, readApiError } from "@/lib/api-error";
import { rangeTooLong } from "@/components/clipper/range-limit";
import { CLIPPER_API, type ClipperClip } from "@/types/clipper";
import { ClipFramePreview } from "./clip-frame-preview";
import { ReactionFraming } from "./reaction-framing";
import { CaptionDisplayNote } from "./caption-display-note";

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
  maxClipS,
}: {
  clip: ClipperClip | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: (clip?: ClipperClip) => void;
  /** The server's effective maximum clip length (`max_clip_s_effective`); the server
   * refuses longer ranges (O4). Undefined: no local refusal, the server decides. */
  maxClipS?: number;
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

  useEffect(() => {
    if (!clip) return;
    setStart(clip.start_time);
    setEnd(clip.end_time);
    setHeadline(clip.headline_text ?? "");
    setPresetId(clip.caption_plan?.preset_id ?? clip.caption_preset_id ?? "");
    setCaptionY(clip.caption_plan?.y_pct ?? 0.75);
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
  // The server's rule (O4), from the same endpoints and the server's own limit.
  const tooLong = clip != null
    && rangeTooLong({ start: clip.start_time, end: clip.end_time }, start, end, maxClipS);
  const trimmed = clip ? start !== clip.start_time || end !== clip.end_time : false;
  const dirty = useMemo(() => {
    if (!clip) return false;
    return (
      trimmed ||
      headline !== (clip.headline_text ?? "") ||
      presetId !== (clip.caption_plan?.preset_id ?? clip.caption_preset_id ?? "") ||
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
    if (presetId && presetId !== (clip.caption_plan?.preset_id ?? clip.caption_preset_id ?? "")) {
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
      onSaved((await r.json()).clip);
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
        onSaved((await r.json()).clip);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [clip, onSaved],
  );

  // Saved on its own endpoint and at once, like the reaction framing: it is a
  // declaration about the source, not part of the caption plan — a rebuild
  // replaces the plan and must not bring a second layer back over the source's.
  const saveCaptionSource = useCallback(
    async (value: boolean | null) => {
      if (!clip) return;
      setBusy("caption-source");
      setError(null);
      try {
        const r = await fetch(`${CLIPPER_API}/clips/${clip.id}/caption-source`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_has_burned_captions: value }),
        });
        if (!r.ok) {
          setError(errorDescription(
            await readApiError(r, "Setarea subtitrării nu a putut fi salvată")));
          return;
        }
        setFrameKey((k) => k + 1);
        onSaved((await r.json()).clip);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [clip, onSaved],
  );

  if (!clip) return null;
  const sourceCaptions = clip.source_has_burned_captions ?? null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-base">Edit clip</DialogTitle>
        </DialogHeader>

        <div className="grid gap-5 sm:grid-cols-[300px_1fr]">
          <ClipFramePreview key={clip.id} clipId={clip.id} duration={clip.duration}
            revision={frameKey} dirty={dirty} />

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
              {tooLong && (
                <p className="text-[11px] text-rose-500">
                  Clips in this project can be at most {maxClipS}s, and this range is{" "}
                  {duration.toFixed(1)}s. Raise the maximum clip length in the project settings to
                  go longer.
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

            <Field label="Subtitrarea din sursă">
              <select
                value={sourceCaptions === null ? "" : sourceCaptions ? "suppress" : "burn"}
                onChange={(e) => saveCaptionSource(
                  e.target.value === "" ? null : e.target.value === "suppress")}
                disabled={busy !== null || dirty || clip.status === "exporting"}
                title={dirty ? "Salvează mai întâi celelalte modificări" : ""}
                className="w-full rounded border border-border/50 bg-transparent px-2 py-1.5 text-xs"
              >
                <option value="">Urmează proiectul</option>
                <option value="burn">Arde subtitrarea ClipForge</option>
                <option value="suppress">Nu arde — sursa are deja subtitrare</option>
              </select>
              <p className="text-[11px] text-muted-foreground">
                {sourceCaptions === true
                  ? "Exportul acestui clip nu primește subtitrarea ClipForge: textul ars în sursă rămâne singurul."
                  : sourceCaptions === false
                    ? "Subtitrarea ClipForge se arde în acest clip, oricum ar fi setat proiectul."
                    : "Decide setarea proiectului; dacă nimeni n-a spus nimic, subtitrarea ClipForge se arde. Alege „Nu arde” când video-ul are deja subtitrare, ca să nu apară două rânduri de text."}
              </p>
              {clip.effective_caption_policy === null && (
                // A response that did not compute it: say so rather than keep
                // showing the previous clip's decision as the current one.
                <p className="text-[11px] text-muted-foreground">Efectiv: necalculat în acest răspuns.</p>
              )}
              {clip.effective_caption_policy && (
                // Computed by the backend with the render's own function, so
                // "follow the project" shows what the project actually decides.
                <p className="text-[11px] font-medium text-foreground/80">
                  Efectiv: {clip.effective_caption_policy.action === "suppress" ? "nu se arde" : "se arde"}
                  {" — "}
                  {{ clip: "decis pentru acest clip", project: "decis de proiect",
                     default: "implicit, nimeni n-a declarat" }[clip.effective_caption_policy.scope]}
                </p>
              )}
              <CaptionDisplayNote view={clip.caption_display} />
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
                  disabled={busy !== null || dirty}
                  title={dirty ? "Save your changes before rebuilding captions" : ""}
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
                disabled={busy !== null || !dirty || end <= start || tooLong}
                className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {busy === "save" ? "saving…" : "save"}
              </button>
            </div>
          </div>
        </div>
        <ReactionFraming key={`${clip.id}:${clip.start_time}:${clip.end_time}`}
          clip={clip} disabled={dirty || busy !== null || clip.status === "exporting"}
          onBusyChange={(active) => setBusy(active ? "reaction" : null)}
          onSaved={(updated) => { setFrameKey((k) => k + 1); onSaved(updated); }} />
      </DialogContent>
    </Dialog>
  );
}
