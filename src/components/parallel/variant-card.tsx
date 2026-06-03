"use client";

/**
 * One variant's config card for the Parallel Processing page.
 * Per-variant: voice (engine + voice + language + speed), caption template +
 * full style overrides, and commentator preset. Download/transcribe/erase and
 * the cleaned transcript are shared by the page, not configured here.
 */

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Badge } from "@/components/ui/badge";
import { Mic, Type, Trash2, ChevronDown, ChevronUp, Save, Bookmark, FolderUp } from "lucide-react";
import { toast } from "sonner";

export interface VariantState {
  name: string;
  tts_engine: "xtts" | "elevenlabs" | "local_clone";
  tts_voice_id: string;
  tts_language: string;
  tts_speed: number;
  caption_template_id: string;
  caption_font_family: string;
  caption_scale: number;
  caption_text_color: string;
  caption_uppercase: boolean | null;
  caption_italic: boolean | null;
  caption_words_per_chunk: number;
  caption_strip_punct: boolean;
  commentator_preset_id: string; // "" = none
  drive_folder: string; // "" = none; Google Drive folder link
}

export function makeDefaultVariant(): VariantState {
  return {
    name: "",
    tts_engine: "xtts",
    tts_voice_id: "",
    tts_language: "en",
    tts_speed: 1.0,
    caption_template_id: "bold_impact",
    caption_font_family: "",
    caption_scale: 1.0,
    caption_text_color: "",
    caption_uppercase: null,
    caption_italic: null,
    caption_words_per_chunk: 1,
    caption_strip_punct: true,
    commentator_preset_id: "",
    drive_folder: "",
  };
}

interface Voice { id: string; name: string; gender?: string }
interface Template { id: string; name: string; font_family: string }
interface EngineInfo { id: string; label: string; ready: boolean; hint: string | null }
interface Commentator { id: string; name: string }
interface FontsLists { system: string[]; user: { family: string; filename: string }[] }
export interface VariantPreset extends VariantState { id: string }

interface Props {
  index: number;
  value: VariantState;
  onChange: (v: VariantState) => void;
  onRemove: () => void;
  canRemove: boolean;
  ttsEngines: EngineInfo[];
  templates: Template[];
  commentators: Commentator[];
  fonts: FontsLists;
  presets: VariantPreset[];
  onPresetsChanged: () => void;
}

const LANGS = ["en", "ro", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "tr"];

export function VariantCard({
  index, value, onChange, onRemove, canRemove,
  ttsEngines, templates, commentators, fonts, presets, onPresetsChanged,
}: Props) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loadingVoices, setLoadingVoices] = useState(false);
  const [showStyle, setShowStyle] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState("");

  const set = <K extends keyof VariantState>(k: K, v: VariantState[K]) =>
    onChange({ ...value, [k]: v });

  const applyPreset = (p: VariantPreset) => {
    onChange({
      name: p.name || value.name,
      tts_engine: p.tts_engine,
      tts_voice_id: p.tts_voice_id,
      tts_language: p.tts_language,
      tts_speed: p.tts_speed,
      caption_template_id: p.caption_template_id,
      caption_font_family: p.caption_font_family || "",
      caption_scale: p.caption_scale,
      caption_text_color: p.caption_text_color || "",
      caption_uppercase: p.caption_uppercase,
      caption_italic: p.caption_italic,
      caption_words_per_chunk: p.caption_words_per_chunk,
      caption_strip_punct: p.caption_strip_punct,
      commentator_preset_id: p.commentator_preset_id || "",
      drive_folder: p.drive_folder || "",
    });
  };

  const savePreset = async () => {
    const name = window.prompt("Save this variant as a preset. Name:", value.name || "");
    if (!name || !name.trim()) return;
    try {
      const r = await fetch(`/worker-api/variant-presets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...value, name: name.trim() }),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Save failed (${r.status})`); }
      toast.success(`Saved preset "${name.trim()}"`);
      onPresetsChanged();
    } catch (e: any) {
      toast.error("Save failed", { description: e.message });
    }
  };

  const deletePreset = async () => {
    if (!selectedPreset) return;
    const p = presets.find((x) => x.id === selectedPreset);
    if (!p || !window.confirm(`Delete preset "${p.name}"?`)) return;
    try {
      const r = await fetch(`/worker-api/variant-presets/${selectedPreset}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`Delete failed (${r.status})`);
      setSelectedPreset("");
      toast.success("Preset deleted");
      onPresetsChanged();
    } catch (e: any) {
      toast.error("Delete failed", { description: e.message });
    }
  };

  // Fetch voices whenever this variant's engine changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingVoices(true);
      try {
        const r = await fetch(`/worker-api/tts/voices?engine=${value.tts_engine}`);
        const j = await r.json();
        if (cancelled) return;
        const list: Voice[] = j.voices || [];
        setVoices(list);
        // Auto-pick the first voice if none selected or current not in list.
        if (list.length && !list.some((v) => v.id === value.tts_voice_id)) {
          set("tts_voice_id", list[0].id);
        }
      } catch {
        if (!cancelled) setVoices([]);
      } finally {
        if (!cancelled) setLoadingVoices(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.tts_engine]);

  const allFonts = [
    ...fonts.user.map((f) => f.family),
    ...fonts.system,
  ];

  return (
    <Card className="p-4 space-y-4 border-border/50">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="text-[11px]">#{index + 1}</Badge>
        <Input
          value={value.name}
          onChange={(e) => set("name", e.target.value)}
          placeholder={`Variant name (e.g. "Grinch")`}
          className="h-8 text-sm flex-1"
        />
        {canRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="text-muted-foreground hover:text-destructive transition-colors p-1"
            title="Remove variant"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Presets — load a saved voice+caption+commentator bundle, or save one */}
      <div className="flex items-center gap-2">
        <Bookmark className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        <select
          value={selectedPreset}
          onChange={(e) => {
            const id = e.target.value;
            setSelectedPreset(id);
            const p = presets.find((x) => x.id === id);
            if (p) applyPreset(p);
          }}
          className="flex-1 h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
        >
          <option value="">Load preset…</option>
          {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {selectedPreset && (
          <button
            type="button"
            onClick={deletePreset}
            className="text-muted-foreground hover:text-destructive transition-colors p-1"
            title="Delete selected preset"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
        <button
          type="button"
          onClick={savePreset}
          className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/5 px-2 py-1 text-xs font-medium text-primary hover:bg-primary/10 transition-colors"
          title="Save this variant as a preset"
        >
          <Save className="h-3.5 w-3.5" /> Save
        </button>
      </div>

      {/* Voice */}
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          <Mic className="h-3.5 w-3.5" /> Voice
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label className="text-[11px] text-muted-foreground">Engine</Label>
            <select
              value={value.tts_engine}
              onChange={(e) => set("tts_engine", e.target.value as VariantState["tts_engine"])}
              className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
            >
              {(ttsEngines.length ? ttsEngines : [{ id: "xtts", label: "XTTS", ready: true, hint: null }]).map((e) => (
                <option key={e.id} value={e.id} disabled={!e.ready}>
                  {e.label}{!e.ready ? " (not ready)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground">Voice</Label>
            <select
              value={value.tts_voice_id}
              onChange={(e) => set("tts_voice_id", e.target.value)}
              className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
              disabled={loadingVoices}
            >
              {loadingVoices && <option>Loading…</option>}
              {!loadingVoices && voices.length === 0 && <option value="">No voices</option>}
              {voices.map((v) => (
                <option key={v.id} value={v.id}>{v.name}</option>
              ))}
            </select>
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground">TTS language</Label>
            <select
              value={value.tts_language}
              onChange={(e) => set("tts_language", e.target.value)}
              className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
            >
              {LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground">
              Speed {value.tts_speed.toFixed(2)}×
            </Label>
            <Slider
              min={0.5} max={2.0} step={0.05}
              value={[value.tts_speed]}
              onValueChange={([v]) => set("tts_speed", v)}
              className="mt-2"
            />
          </div>
        </div>
      </div>

      {/* Captions */}
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          <Type className="h-3.5 w-3.5" /> Captions
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label className="text-[11px] text-muted-foreground">Template</Label>
            <select
              value={value.caption_template_id}
              onChange={(e) => set("caption_template_id", e.target.value)}
              className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
            >
              {(templates.length ? templates : [{ id: "bold_impact", name: "Bold Impact", font_family: "" }]).map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </div>
          <div>
            <Label className="text-[11px] text-muted-foreground">Words / chunk</Label>
            <select
              value={value.caption_words_per_chunk}
              onChange={(e) => set("caption_words_per_chunk", parseInt(e.target.value, 10))}
              className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
            >
              {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowStyle((s) => !s)}
          className="flex items-center gap-1 text-[11px] text-primary hover:underline"
        >
          {showStyle ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          Style overrides
        </button>

        {showStyle && (
          <div className="grid grid-cols-2 gap-2 rounded-md border border-border/40 bg-muted/20 p-2">
            <div>
              <Label className="text-[11px] text-muted-foreground">Font</Label>
              <select
                value={value.caption_font_family}
                onChange={(e) => set("caption_font_family", e.target.value)}
                className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-xs"
              >
                <option value="">Template default</option>
                {allFonts.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">Text color</Label>
              <div className="flex items-center gap-1">
                <input
                  type="color"
                  value={value.caption_text_color || "#ffffff"}
                  onChange={(e) => set("caption_text_color", e.target.value)}
                  className="h-8 w-10 rounded border border-border/60 bg-background"
                />
                {value.caption_text_color && (
                  <button
                    type="button"
                    onClick={() => set("caption_text_color", "")}
                    className="text-[10px] text-muted-foreground hover:text-foreground"
                  >
                    reset
                  </button>
                )}
              </div>
            </div>
            <div className="col-span-2">
              <Label className="text-[11px] text-muted-foreground">
                Scale {value.caption_scale.toFixed(2)}×
              </Label>
              <Slider
                min={0.5} max={2.5} step={0.05}
                value={[value.caption_scale]}
                onValueChange={([v]) => set("caption_scale", v)}
                className="mt-2"
              />
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={value.caption_uppercase === true}
                onChange={(e) => set("caption_uppercase", e.target.checked ? true : null)}
              />
              Uppercase
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={value.caption_italic === true}
                onChange={(e) => set("caption_italic", e.target.checked ? true : null)}
              />
              Italic
            </label>
            <label className="flex items-center gap-2 text-xs col-span-2">
              <input
                type="checkbox"
                checked={value.caption_strip_punct}
                onChange={(e) => set("caption_strip_punct", e.target.checked)}
              />
              Strip punctuation
            </label>
          </div>
        )}
      </div>

      {/* Commentator */}
      <div className="space-y-1.5">
        <Label className="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold">
          Commentator
        </Label>
        <select
          value={value.commentator_preset_id}
          onChange={(e) => set("commentator_preset_id", e.target.value)}
          className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm"
        >
          <option value="">None</option>
          {commentators.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      {/* Google Drive folder — optional auto-upload after the video is done */}
      <div className="space-y-1.5">
        <Label className="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold flex items-center gap-1">
          <FolderUp className="h-3 w-3" /> Google Drive folder (optional)
        </Label>
        <Input
          value={value.drive_folder}
          onChange={(e) => set("drive_folder", e.target.value)}
          placeholder="https://drive.google.com/drive/folders/…"
          className="h-8 text-sm"
        />
        <p className="text-[10px] text-muted-foreground">
          When set, the finished video is uploaded here automatically. Download stays available.
        </p>
      </div>
    </Card>
  );
}
