"use client";

/**
 * Caption Cloner UI — upload a reference caption video, the backend extracts a
 * draft template (font, colours, position, animation…), and the user confirms
 * /tweaks it side-by-side with the reference crop before saving.
 *
 * Self-contained modal so it doesn't bloat the Caption Studio page.
 */

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Wand2, Loader2, X, Save, AlertCircle } from "lucide-react";
import { toast } from "sonner";

interface FontEntry { family: string; filename: string; size: number }
interface FontsList { system: string[]; user: FontEntry[] }

interface Draft {
  name: string;
  font_family: string;
  font_size: number;
  font_weight?: string;
  italic?: boolean;
  text_color: string;
  highlight_color?: string;
  outline_color: string;
  outline_width: number;
  shadow_offset?: number;
  shadow_color?: string;
  position: string;
  uppercase: boolean;
  animation?: string;
  max_words_per_line: number;
  borderstyle?: number;
}
interface Diagnostics {
  font_candidates: { family: string; score: number }[];
  font_matched: boolean;
  reference_crop_png_b64: string;
  sample_text: string;
  confidence_notes: string[];
  elapsed_s: number;
}

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 50) || "cloned_style";
}

export function CloneFromVideo({ fonts, onSaved }: { fonts: FontsList; onSaved: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [error, setError] = useState("");

  const allFonts = [...fonts.user.map((f) => f.family), ...fonts.system];

  const pick = () => fileRef.current?.click();

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    setBusy(true); setError(""); setDraft(null); setDiag(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(`/worker-api/captions/clone`, { method: "POST", body: form });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Clone failed (${r.status})`); }
      const j = await r.json();
      setDraft(j.template);
      setDiag(j.diagnostics);
    } catch (err: any) {
      setError(err.message); toast.error("Clone failed", { description: err.message });
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));

  const save = async () => {
    if (!draft) return;
    const name = draft.name.trim() || "Cloned style";
    const payload = { ...draft, name, id: slugify(name) };
    try {
      const r = await fetch(`/worker-api/captions/templates`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Save failed (${r.status})`); }
      toast.success(`Saved template "${name}"`);
      setDraft(null); setDiag(null);
      onSaved();
    } catch (err: any) {
      toast.error("Save failed", { description: err.message });
    }
  };

  return (
    <>
      <input ref={fileRef} type="file" accept="video/*" className="hidden" onChange={onFile} />
      <Button size="sm" variant="outline" onClick={pick} disabled={busy} className="w-full">
        {busy ? <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> Analyzing… (~40s)</> : <><Wand2 className="h-3.5 w-3.5 mr-1.5" /> Clone from video</>}
      </Button>

      {error && !busy && (
        <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-2 text-xs text-destructive">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" /> <div>{error}</div>
        </div>
      )}

      {draft && diag && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => { setDraft(null); setDiag(null); }}>
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-auto p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold flex items-center gap-2"><Wand2 className="h-4 w-4" /> Cloned caption style</h3>
              <button onClick={() => { setDraft(null); setDiag(null); }} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
            </div>

            {/* Reference vs live preview */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Reference (from video)</div>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`data:image/png;base64,${diag.reference_crop_png_b64}`} alt="reference" className="w-full rounded bg-black object-contain max-h-28" />
              </div>
              <div className="space-y-1">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Your template</div>
                <div className="rounded bg-black flex items-center justify-center max-h-28 h-28 overflow-hidden">
                  <span style={{
                    fontFamily: draft.font_family, color: draft.text_color,
                    fontWeight: 900, fontStyle: draft.italic ? "italic" : "normal",
                    textTransform: draft.uppercase ? "uppercase" : "none",
                    fontSize: 28, lineHeight: 1.1,
                    textShadow: draft.borderstyle === 3 ? undefined : `0 0 ${draft.outline_width}px ${draft.outline_color}, 1px 1px 0 ${draft.outline_color}, -1px -1px 0 ${draft.outline_color}`,
                    backgroundColor: draft.borderstyle === 3 ? "rgba(0,0,0,0.85)" : "transparent",
                    padding: draft.borderstyle === 3 ? "2px 8px" : 0,
                  }}>{diag.sample_text || "SAMPLE"}</span>
                </div>
              </div>
            </div>

            {/* Confidence notes */}
            {diag.confidence_notes.length > 0 && (
              <ul className="text-[11px] text-amber-500/90 space-y-0.5 list-disc list-inside">
                {diag.confidence_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}

            {/* Editable fields */}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Template name"><Input value={draft.name} onChange={(e) => set("name", e.target.value)} className="h-8 text-sm" /></Field>
              <Field label="Font (confirm — see notes)">
                <select value={draft.font_family} onChange={(e) => set("font_family", e.target.value)} className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm">
                  {!allFonts.includes(draft.font_family) && <option value={draft.font_family}>{draft.font_family} (guessed)</option>}
                  {allFonts.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </Field>
              <Field label={`Font size (${draft.font_size})`}><Input type="number" value={draft.font_size} onChange={(e) => set("font_size", parseInt(e.target.value) || 64)} className="h-8 text-sm" /></Field>
              <Field label="Position">
                <select value={draft.position} onChange={(e) => set("position", e.target.value)} className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm">
                  {["top", "center", "bottom"].map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </Field>
              <Field label="Text color"><ColorRow value={draft.text_color} onChange={(v) => set("text_color", v)} /></Field>
              <Field label="Outline color"><ColorRow value={draft.outline_color} onChange={(v) => set("outline_color", v)} /></Field>
              <Field label={`Outline width (${draft.outline_width})`}><Input type="number" value={draft.outline_width} onChange={(e) => set("outline_width", parseFloat(e.target.value) || 0)} className="h-8 text-sm" /></Field>
              <Field label="Highlight color"><ColorRow value={draft.highlight_color || draft.text_color} onChange={(v) => set("highlight_color", v)} /></Field>
              <Field label="Animation">
                <select value={draft.animation || "word"} onChange={(e) => set("animation", e.target.value)} className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm">
                  {["word", "phrase"].map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </Field>
              <Field label={`Words per line (${draft.max_words_per_line})`}><Input type="number" value={draft.max_words_per_line} onChange={(e) => set("max_words_per_line", parseInt(e.target.value) || 1)} className="h-8 text-sm" /></Field>
              <Field label="Uppercase">
                <label className="flex items-center gap-2 text-sm h-8"><input type="checkbox" checked={draft.uppercase} onChange={(e) => set("uppercase", e.target.checked)} /> All caps</label>
              </Field>
              <Field label="Italic">
                <label className="flex items-center gap-2 text-sm h-8"><input type="checkbox" checked={!!draft.italic} onChange={(e) => set("italic", e.target.checked)} /> Italic / oblique</label>
              </Field>
            </div>

            {/* Font candidates */}
            {diag.font_candidates.length > 0 && (
              <div className="space-y-1">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Closest fonts in your library</div>
                <div className="flex flex-wrap gap-1.5">
                  {diag.font_candidates.map((c) => (
                    <button key={c.family} onClick={() => set("font_family", c.family)}
                      className={`text-[11px] rounded border px-2 py-1 transition-colors ${draft.font_family === c.family ? "border-primary bg-primary/5 text-primary" : "border-border/40 hover:border-border"}`}>
                      {c.family} <span className="text-muted-foreground">{(c.score * 100).toFixed(0)}%</span>
                    </button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground">Not exact? Upload the real font (Fonts panel) then pick it here for a 1:1 match.</p>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" size="sm" onClick={() => { setDraft(null); setDiag(null); }}>Cancel</Button>
              <Button size="sm" onClick={save}><Save className="h-3.5 w-3.5 mr-1.5" /> Save as template</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label className="text-[11px] text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

function ColorRow({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-1.5">
      <input type="color" value={value.slice(0, 7)} onChange={(e) => onChange(e.target.value)} className="h-8 w-10 rounded border border-border/60 bg-background" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} className="h-8 text-xs flex-1" />
    </div>
  );
}
