"use client";

// SC3 (codex-verdict-next-33 §3, next-34 R1): what the export does with the SOURCE's own burned text.
//
// "Fără tratament" or "Blur" — erase is not offered. Blur is available only where a validated mask has been
// imported for this clip AND the clip renders on the multi-shot path; the server says whether it is and,
// if not, why. ClipForge's own caption layer is a SEPARATE choice with three states: the existing policy
// (null, the default — which may itself burn), burn, or suppress.
//
// Every request is built from the fields the API accepts and nothing else — never the object the server
// sent back, which carries the server's own `decided_by`. Everything shown comes from GET/PUT
// /clips/{id}/source-treatment, re-read when this clip's bounds or caption answers change; a late answer
// for another clip is dropped.

import { useCallback, useEffect, useRef, useState } from "react";

import { errorDescription, readApiError } from "@/lib/api-error";
import { CLIPPER_API, type ClipperClip } from "@/types/clipper";
import { placementNotice } from "./caption-placement";

type Layer = "burn" | "suppress" | null;
type Requested = { treatment: "none" | "blur"; mask_sha256?: string } | null;

interface View {
  requested: { source_caption_treatment: Requested; caption_layer: Layer };
  effective: { treatment: string | null; layer: string; refused: { reason: string; detail: string } | null };
  availability: { blur: { available: boolean; mask_sha256: string | null; reason: string | null } };
}

const REASONS: Record<string, string> = {
  no_validated_mask: "nu există o mască validată pentru acest clip",
  static_path_unsupported: "clipul se randează pe calea statică (reaction sau fără montaj dinamic)",
  mask_window_does_not_cover_clip: "masca nu mai acoperă limitele clipului (au fost editate)",
  mask_source_mismatch: "sursa nu mai e cea pentru care s-a făcut masca",
  glyph_hash_mismatch: "fișierele măștii s-au schimbat",
  params_missing: "lipsesc parametrii tratamentului",
};

/** Only the fields the API accepts: `decided_by` is the server's, never echoed back. */
function payloadTreatment(t: Requested): Requested {
  if (t?.treatment === "blur" && t.mask_sha256) return { treatment: "blur", mask_sha256: t.mask_sha256 };
  if (t?.treatment === "none") return { treatment: "none" };
  return null;
}

export function SourceTreatmentField({ clip, disabled, onSaved }: {
  clip: ClipperClip;
  disabled: boolean;
  onSaved: (clip: ClipperClip) => void;
}) {
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unchecked, setUnchecked] = useState<{ id: string; text: string } | null>(null);   // RX1
  const current = useRef(clip.id);
  current.current = clip.id;

  // Re-read whenever what decides availability changes on THIS clip: its window, its caption answers,
  // its framing, its stored choice.
  const watch = JSON.stringify([clip.id, clip.start_time, clip.end_time, clip.source_has_burned_captions,
    (clip as { layout_plan?: unknown }).layout_plan,
    (clip as { source_caption_treatment?: unknown }).source_caption_treatment,
    (clip as { caption_layer?: unknown }).caption_layer]);

  useEffect(() => {
    const id = clip.id;
    let live = true;
    fetch(`${CLIPPER_API}/clips/${id}/source-treatment`)
      .then(async (r) => {
        if (!live || current.current !== id) return;           // a late answer for another clip
        if (r.ok) setView(await r.json());
        else setError(errorDescription(await readApiError(r, "Starea tratamentului nu a putut fi citită")));
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch]);

  const save = useCallback(async (treatment: Requested, layer: Layer) => {
    const id = clip.id;
    setBusy(true);
    setError(null);
    setUnchecked(null);
    try {
      const r = await fetch(`${CLIPPER_API}/clips/${id}/source-treatment`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_caption_treatment: payloadTreatment(treatment), caption_layer: layer }),
      });
      if (current.current !== id) return;
      if (!r.ok) {
        setError(errorDescription(await readApiError(r, "Tratamentul nu a putut fi salvat")));
        return;
      }
      const body = await r.json();
      setView(body);
      const text = placementNotice(body.caption_placement);
      if (text) setUnchecked({ id, text });
      onSaved(body.clip);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [clip.id, onSaved]);

  const blur = view?.availability.blur;
  const stored = view?.requested.source_caption_treatment ?? null;
  const layer = view?.requested.caption_layer ?? null;
  const off = disabled || busy || view === null || clip.status === "exporting";

  return (
    <div className="space-y-1.5">
      <span className="text-[11px] text-muted-foreground">Textul ars în sursă</span>
      <select
        value={stored?.treatment === "blur" ? "blur" : "none"}
        onChange={(e) => save(e.target.value === "blur"
          ? { treatment: "blur", mask_sha256: blur?.mask_sha256 ?? "" } : null, layer)}
        disabled={off}
        className="w-full rounded border border-border/50 bg-transparent px-2 py-1.5 text-xs"
      >
        <option value="none">Fără tratament</option>
        <option value="blur" disabled={!blur?.available || !blur.mask_sha256}>Blur</option>
      </select>
      {blur && !blur.available && (
        <p className="text-[11px] text-muted-foreground">
          Blur indisponibil: {REASONS[blur.reason ?? ""] ?? blur.reason}.
        </p>
      )}
      <span className="block pt-1 text-[11px] text-muted-foreground">Subtitrarea nouă ClipForge</span>
      <select
        value={layer ?? ""}
        onChange={(e) => save(stored, (e.target.value || null) as Layer)}
        disabled={off}
        className="w-full rounded border border-border/50 bg-transparent px-2 py-1.5 text-xs"
      >
        <option value="">Politica existentă (implicit)</option>
        <option value="burn">Arde subtitrarea nouă</option>
        <option value="suppress">Fără subtitrare nouă</option>
      </select>
      {view?.effective.refused && (
        <p className="text-[11px] text-rose-500">
          Exportul ar fi refuzat: {view.effective.refused.reason}.
        </p>
      )}
      {view && !view.effective.refused && (
        <p className="text-[11px] font-medium text-foreground/80">
          La următoarea randare: {view.effective.treatment === "blur" ? "textul sursei se estompează"
            : "sursa rămâne neatinsă"}
          {" · "}{view.effective.layer === "burn" ? "subtitrarea ClipForge se arde" : "fără subtitrare ClipForge"}
        </p>
      )}
      {unchecked?.id === clip.id && <p className="text-[11px] text-amber-500">{unchecked.text}</p>}
      {error && <p className="text-[11px] text-rose-500">{error}</p>}
    </div>
  );
}
