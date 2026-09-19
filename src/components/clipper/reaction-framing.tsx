"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { errorDescription, readApiError } from "@/lib/api-error";
import { CLIPPER_API, type ClipperClip, type Rect } from "@/types/clipper";
import { readSourceFrame, selectionRect, type SourceFrame } from "./reaction-selection";

type Region = "content" | "face";
const names: Record<Region, string> = { content: "Materialul urmărit", face: "Reacția" };
const button = "rounded border border-border/60 px-3 py-1.5 text-xs hover:bg-muted/40 disabled:opacity-40";

export function ReactionFraming({ clip, disabled, onSaved, onBusyChange }: {
  clip: ClipperClip;
  disabled: boolean;
  onSaved: (clip: ClipperClip) => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [shown, setShown] = useState<{ url: string; frame: SourceFrame } | null>(null);
  const [at, setAt] = useState(Math.min(0.5, (clip.end_time - clip.start_time) / 2));
  const [region, setRegion] = useState<Region>("content");
  const [rects, setRects] = useState<Record<Region, Rect | null>>({ content: null, face: null });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(clip.layout_plan?.game_content_fit === true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const objectUrl = useRef<string | null>(null);
  const drag = useRef<{ x: number; y: number; region: Region; before: Rect | null } | null>(null);

  useEffect(() => () => {
    request.current?.abort();
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  async function loadFrame() {
    if (disabled || saving || !Number.isFinite(at)) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true); setError(null); setNotice(null);
    try {
      const response = await fetch(`${CLIPPER_API}/clips/${clip.id}/reaction-source?t=${at}`, {
        signal: controller.signal, cache: "no-store",
      });
      if (!response.ok) throw new Error(errorDescription(
        await readApiError(response, "Nu am putut citi cadrul sursei"),
      ));
      const frame = readSourceFrame(response.headers);
      if (frame.start !== clip.start_time || frame.end !== clip.end_time) {
        throw new Error("Intervalul clipului s-a schimbat. Redeschide editorul.");
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = url;
      if (!shown || shown.frame.version !== frame.version) {
        const plan = clip.layout_plan, binding = plan?.reaction_binding;
        const current = plan?.game_content_fit === true && binding?.source_version === frame.version &&
          binding.source_start <= frame.start && binding.source_end >= frame.end;
        setRects({ content: current ? plan.game_rect : null, face: current ? plan.face_rect : null });
      }
      setShown({ url, frame });
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
        // A failed refresh must not leave a stale picture available for saving.
        setShown(null); setRects({ content: null, face: null });
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  function point(e: PointerEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    return {
      x: (e.clientX - box.left) / box.width * shown!.frame.width,
      y: (e.clientY - box.top) / box.height * shown!.frame.height,
    };
  }

  function draw(e: PointerEvent<HTMLDivElement>) {
    if (!drag.current || !shown) return;
    const activeRegion = drag.current.region;
    const selected = selectionRect(drag.current, point(e), shown.frame.width,
      shown.frame.height, activeRegion === "face");
    setRects((previous) => ({ ...previous, [activeRegion]: selected }));
  }

  async function save(clear = false) {
    if (disabled || saving || loading || (!clear && (!shown || !rects.content || !rects.face))) return;
    setSaving(true); onBusyChange(true); setError(null); setNotice(null);
    try {
      const frame = shown?.frame;
      const response = await fetch(`${CLIPPER_API}/clips/${clip.id}/reaction-layout`, {
        method: clear ? "DELETE" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: clear ? undefined : JSON.stringify({
          content_rect: rects.content, face_rect: rects.face,
          source_version: frame!.version, source_start: frame!.start, source_end: frame!.end,
          src_w: frame!.width, src_h: frame!.height,
        }),
      });
      if (!response.ok) throw new Error(errorDescription(
        await readApiError(response, "Încadrarea nu a putut fi salvată"),
      ));
      const result: { clip: ClipperClip } = await response.json();
      setSaved(!clear);
      if (clear) setRects({ content: null, face: null });
      setNotice(clear ? "Încadrarea automată a fost restabilită." :
        "Încadrare salvată. Previzualizarea și exportul folosesc acum aceste regiuni.");
      onSaved(result.clip);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false); onBusyChange(false);
    }
  }

  return (
    <details className="rounded border border-border/50 p-3">
      <summary className="cursor-pointer text-sm">Încadrare pentru reacții {saved ? "· activă" : ""}</summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs text-muted-foreground">
          Desenează materialul urmărit și reacția pe cadrul sursei. Materialul apare sus,
          iar reacția ocupă partea de jos. Încadrarea rămâne fixă în acest clip;
          verifică mai multe momente înainte de salvare.
        </p>
        {disabled && !saving && <p className="text-xs text-amber-500">
          Salvează mai întâi celelalte modificări și așteaptă terminarea operației curente.
        </p>}
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs" htmlFor={`reaction-time-${clip.id}`}>Secunda din clip</label>
          <input id={`reaction-time-${clip.id}`} type="number" min={0}
            max={Math.max(0, clip.end_time - clip.start_time - 0.05)} step={0.1}
            value={Number.isFinite(at) ? at : ""} onChange={(e) => setAt(e.target.valueAsNumber)}
            disabled={disabled || saving || loading}
            className="w-24 rounded border border-border/50 bg-transparent px-2 py-1 text-xs" />
          <button type="button" onClick={loadFrame}
            disabled={disabled || saving || loading || !Number.isFinite(at) || at < 0 || at >= clip.end_time - clip.start_time}
            className={button}>{loading ? "Se citește…" : "Arată cadrul"}</button>
        </div>
        {shown && <>
          <div className="flex flex-wrap gap-2">
            {(["content", "face"] as const).map((name) => <button key={name} type="button"
              aria-pressed={region === name} onClick={() => setRegion(name)} disabled={disabled || saving || loading}
              className={`${button} ${region === name ? "bg-muted ring-1 ring-foreground/40" : ""}`}>
              {name === "content" ? "1. " : "2. "}{names[name]} {rects[name] ? "✓" : ""}
            </button>)}
          </div>
          <p className="text-xs text-muted-foreground">Trage pentru a selecta: {names[region].toLowerCase()}.
            {region === "face" && " Proporțiile sunt fixe pentru a păstra forma imaginii."}</p>
          <div className="relative touch-none select-none overflow-hidden"
            style={{ cursor: disabled || loading || saving ? "default" : "crosshair" }}
            onPointerDown={(e) => {
              if (disabled || saving || loading || e.button !== 0) return;
              e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
              drag.current = { ...point(e), region, before: rects[region] };
              setNotice(null);
            }} onPointerMove={draw} onPointerUp={(e) => {
              draw(e); drag.current = null;
              if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
            }} onPointerCancel={() => {
              const previous = drag.current;
              if (previous) setRects((all) => ({ ...all, [previous.region]: previous.before }));
              drag.current = null;
            }}>
            {/* Original pixels are selected on a scaled image; Next image optimization would change this URL. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={shown.url} alt="Cadrul original pe care se aleg cele două regiuni"
              draggable={false} className="block h-auto w-full" onError={() => {
                setShown(null); setError("Imaginea nu poate fi afișată. Reîncarcă cadrul.");
              }} />
            {(["content", "face"] as const).map((name) => {
              const rect = rects[name];
              return rect && <div key={name} className={`pointer-events-none absolute border-2 ${
                name === "content" ? "border-sky-400 bg-sky-400/10" : "border-amber-400 bg-amber-400/10"}`}
                style={{ left: `${rect.x / shown.frame.width * 100}%`, top: `${rect.y / shown.frame.height * 100}%`,
                  width: `${rect.w / shown.frame.width * 100}%`, height: `${rect.h / shown.frame.height * 100}%` }}>
                <span className="absolute left-0 top-0 bg-black/80 px-1 text-[10px] text-white">{names[name]}</span>
              </div>;
            })}
          </div>
          <p className="text-[11px] text-muted-foreground">
            Cadru afișat: +{(shown.frame.time - shown.frame.start).toFixed(3)} s din clip.
            Schimbă secunda și apasă „Arată cadrul” pentru a verifica aceeași încadrare în alt moment.
            {" "}Previzualizarea de sus se actualizează după „Aplică încadrarea”.
          </p>
        </>}
        {error && <p role="alert" className="text-xs text-rose-500">{error}</p>}
        {notice && <p role="status" className="text-xs text-emerald-500">{notice}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          {saved && <button type="button" onClick={() => save(true)}
            disabled={disabled || saving || loading} className={button}>Revino la automat</button>}
          <button type="button" onClick={() => save()}
            disabled={disabled || saving || loading || !shown || !rects.content || !rects.face}
            className={`${button} bg-emerald-600 text-white`}>
            {saving ? "Se salvează…" : "Aplică încadrarea"}
          </button>
        </div>
      </div>
    </details>
  );
}
