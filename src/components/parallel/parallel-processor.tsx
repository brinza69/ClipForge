"use client";

/**
 * ParallelProcessor — the body of the Parallel Processing page.
 *
 * Extracted from /parallel/page.tsx so the new "Parallel from Sheets" page can
 * reuse the same pipeline UI while adding Sheets-specific bits (config card,
 * Pull-next button, auto-commit) on top.
 *
 * Props let the parent:
 *   - own the `url` state (so it can fill it after pulling from a Sheet)
 *   - inject extra content above the URL card (`topContent`)
 *   - merge extra fields into the /start payload (`startPayloadExtras`)
 *   - react to job completion (`onJobDone`)
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Loader2, Download, AlertCircle, CheckCircle2, Eraser, FileText, Plus, Layers,
} from "lucide-react";
import { toast } from "sonner";
import { VariantCard, VariantState, VariantPreset, makeDefaultVariant } from "@/components/parallel/variant-card";
import { ZonePicker, Rect, ActiveRect, PickerTemplate } from "@/components/parallel/zone-picker";
import { DriveCard } from "@/components/parallel/drive-card";

interface PreviewMeta {
  title: string | null;
  thumbnail_url: string | null;
  width: number;
  height: number;
  duration: number | null;
}
interface EngineInfo { id: string; label: string; ready: boolean; hint: string | null }
interface Commentator { id: string; name: string }
interface FontsLists { system: string[]; user: { family: string; filename: string }[] }
interface VariantResult {
  index: number;
  name: string | null;
  commentator_preset_id: string | null;
  output_filename: string;
  file_available: boolean;
  file_size: number;
  drive?: { status: string; folder_id?: string; reason?: string; uploaded?: string[] } | null;
  parts?: { part: number; of: number; filename: string; start: number; duration: number; available: boolean }[];
}

export interface JobDoneData {
  jobId: string;
  results: VariantResult[];
  descriptions: { original_translated: string; ai_generated: string } | null;
  raw: Record<string, unknown>;
}

interface Props {
  url: string;
  setUrl: (u: string) => void;
  /** Rendered before the Source URL card. Used by the Sheets page for its config + Pull button. */
  topContent?: ReactNode;
  /** Returns extra fields merged into the /api/parallel/start payload at submit time. */
  startPayloadExtras?: () => Record<string, unknown>;
  /** Called once results land (the full /result response is also passed as `raw`). */
  onJobDone?: (data: JobDoneData) => void;
  /** Disable the Run button externally (e.g. Sheets page when no row pulled). */
  runDisabled?: boolean;
  /** Tooltip shown when runDisabled is true. */
  runDisabledReason?: string;
}

function driveLabel(d: VariantResult["drive"]): { text: string; cls: string } | null {
  if (!d) return null;
  switch (d.status) {
    case "uploaded": return { text: "✓ Uploaded to Drive", cls: "text-emerald-400" };
    case "no_files": return { text: "Drive: no file", cls: "text-muted-foreground" };
    case "invalid_link": return { text: "Drive: invalid link", cls: "text-amber-500" };
    case "blocked_missing_credentials": return { text: "Drive: credentials missing", cls: "text-amber-500" };
    case "failed": return { text: "Drive: upload failed", cls: "text-destructive" };
    default: return { text: `Drive: ${d.status}`, cls: "text-muted-foreground" };
  }
}

export function ParallelProcessor({
  url, setUrl,
  topContent, startPayloadExtras, onJobDone, runDisabled, runDisabledReason,
}: Props) {
  const [preview, setPreview] = useState<PreviewMeta | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const [eraseRect, setEraseRect] = useState<Rect>({ x: 0, y: 0, w: 800, h: 200 });
  const [captionRect, setCaptionRect] = useState<Rect>({ x: 0, y: 0, w: 800, h: 200 });
  const [active, setActive] = useState<ActiveRect>("erase");

  const [eraseMethod, setEraseMethod] = useState<"lama" | "ns" | "blur">("lama");
  const [eraseAutoDetect, setEraseAutoDetect] = useState(false);

  const [txEngines, setTxEngines] = useState<EngineInfo[]>([]);
  const [transcriptEngine, setTranscriptEngine] = useState("ollama");
  const [transcriptLang, setTranscriptLang] = useState("");

  const [ttsEngines, setTtsEngines] = useState<EngineInfo[]>([]);
  const [templates, setTemplates] = useState<PickerTemplate[]>([]);
  const [commentators, setCommentators] = useState<Commentator[]>([]);
  const [fonts, setFonts] = useState<FontsLists>({ system: [], user: [] });
  const [presets, setPresets] = useState<VariantPreset[]>([]);

  const [variants, setVariants] = useState<VariantState[]>([
    makeDefaultVariant(), makeDefaultVariant(),
  ]);

  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [results, setResults] = useState<VariantResult[] | null>(null);
  const [descriptions, setDescriptions] = useState<{ original_translated: string; ai_generated: string } | null>(null);

  useEffect(() => {
    (async () => {
      try { const j = await (await fetch(`/worker-api/transcript/engines`)).json(); setTxEngines(j.engines || []); } catch {}
      try { const j = await (await fetch(`/worker-api/tts/engines`)).json(); setTtsEngines(j.engines || []); } catch {}
      try { const j = await (await fetch(`/worker-api/captions/templates`)).json(); setTemplates(j.templates || []); } catch {}
      try { const j = await (await fetch(`/worker-api/captions/fonts`)).json(); setFonts({ system: j.system || [], user: j.user || [] }); } catch {}
      try { const j = await (await fetch(`/worker-api/commentators`)).json(); setCommentators(j.commentators || []); } catch {}
      loadPresets();
    })();
  }, []);

  const loadPresets = async () => {
    try { const j = await (await fetch(`/worker-api/variant-presets`)).json(); setPresets(j.presets || []); } catch {}
  };

  const isRunning = jobStatus === "queued" || jobStatus === "running";

  const getPreview = async () => {
    if (!url.trim()) { toast.error("Paste a URL"); return; }
    setPreviewLoading(true); setErrorMsg("");
    try {
      const r = await fetch(`/worker-api/remix/preview`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url.trim() }),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Preview failed (${r.status})`); }
      const m: PreviewMeta = await r.json();
      setPreview(m);
      const w = m.width || 1080, h = m.height || 1920;
      setEraseRect({ x: Math.round(w * 0.1), y: Math.round(h * 0.08), w: Math.round(w * 0.8), h: Math.round(h * 0.08) });
      setCaptionRect({ x: Math.round(w * 0.1), y: Math.round(h * 0.82), w: Math.round(w * 0.8), h: Math.round(h * 0.08) });
    } catch (e: any) {
      setErrorMsg(e.message); toast.error("Preview failed", { description: e.message });
    } finally {
      setPreviewLoading(false);
    }
  };

  const addVariant = () => setVariants((vs) => vs.length < 4 ? [...vs, makeDefaultVariant()] : vs);
  const removeVariant = (i: number) => setVariants((vs) => vs.length > 2 ? vs.filter((_, idx) => idx !== i) : vs);
  const updateVariant = (i: number, v: VariantState) => setVariants((vs) => vs.map((old, idx) => idx === i ? v : old));

  const start = async () => {
    if (!preview) { toast.error("Get preview first"); return; }
    for (const [i, v] of variants.entries()) {
      if (!v.tts_voice_id) { toast.error(`Variant #${i + 1}: pick a voice`); return; }
    }
    setErrorMsg(""); setResults(null); setDescriptions(null);
    setProgress(0); setProgressMsg("Submitting…"); setJobStatus("queued");

    const payload: Record<string, unknown> = {
      url: url.trim(),
      title: preview.title || undefined,
      erase_zone: { ...eraseRect, src_w: preview.width, src_h: preview.height },
      caption_zone: { ...captionRect, src_w: preview.width, src_h: preview.height },
      erase_mode: eraseMethod === "blur" ? "blur" : "inpaint",
      erase_algorithm: eraseMethod === "ns" ? "ns" : "telea",
      erase_auto_detect: eraseAutoDetect,
      transcript_engine: transcriptEngine,
      transcript_target_lang: transcriptLang || null,
      variants: variants.map((v) => ({
        name: v.name || null,
        tts_engine: v.tts_engine,
        tts_voice_id: v.tts_voice_id,
        tts_language: v.tts_language,
        tts_speed: v.tts_speed,
        caption_template_id: v.caption_template_id,
        caption_font_family: v.caption_font_family || null,
        caption_scale: v.caption_scale,
        caption_text_color: v.caption_text_color || null,
        caption_uppercase: v.caption_uppercase,
        caption_italic: v.caption_italic,
        caption_words_per_chunk: v.caption_words_per_chunk,
        caption_strip_punct: v.caption_strip_punct,
        commentator_preset_id: v.commentator_preset_id || null,
        drive_folder: v.drive_folder || null,
        split_into_parts: v.split_into_parts,
      })),
      ...(startPayloadExtras ? startPayloadExtras() : {}),
    };

    try {
      const r = await fetch(`/worker-api/parallel/start`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `Start failed (${r.status})`); }
      const j = await r.json();
      setJobId(j.job_id);
    } catch (e: any) {
      setErrorMsg(e.message); setJobStatus(""); toast.error("Start failed", { description: e.message });
    }
  };

  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await fetch(`/worker-api/jobs/${jobId}`);
        if (!r.ok) return;
        const j = await r.json();
        if (stop) return;
        setProgress(Math.round((j.progress || 0) * 100));
        setProgressMsg(j.progress_message || "");
        setJobStatus(j.status);
        if (j.status === "done") {
          stop = true;
          try {
            const meta = await fetch(`/worker-api/parallel/${jobId}/result`);
            if (meta.ok) {
              const m = await meta.json();
              const vs: VariantResult[] = m.variants || [];
              setResults(vs);
              const d = m.descriptions || null;
              if (d) setDescriptions(d);
              onJobDone?.({ jobId, results: vs, descriptions: d, raw: m });
            }
          } catch {}
        } else if (j.status === "failed") { stop = true; setErrorMsg(j.error || "Pipeline failed"); }
        else if (j.status === "cancelled") { stop = true; setErrorMsg("Cancelled"); }
      } catch {}
    };
    tick();
    const id = setInterval(tick, 1500);
    return () => { stop = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const stages = useMemo(() => {
    const ranges = [
      { label: "Download", lo: 0, hi: 5 }, { label: "Transcribe", lo: 5, hi: 10 },
      { label: "Erase", lo: 10, hi: 40 }, { label: "Clean", lo: 40, hi: 48 },
      { label: "Variants", lo: 48, hi: 97 }, { label: "Descriptions", lo: 97, hi: 100 },
    ];
    return ranges.map((s) => ({ ...s, done: progress >= s.hi, active: progress >= s.lo && progress < s.hi }));
  }, [progress]);

  const v0 = variants[0];
  const captionSample = {
    templateId: v0.caption_template_id, fontFamily: v0.caption_font_family,
    textColor: v0.caption_text_color, scale: v0.caption_scale,
    uppercase: v0.caption_uppercase, italic: v0.caption_italic,
    wordsPerChunk: v0.caption_words_per_chunk, stripPunct: v0.caption_strip_punct,
  };

  return (
    <div className="space-y-5">
      {topContent}

      {/* 1. Source */}
      <Card className="p-4 space-y-3 border-border/40">
        <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">1. Source URL</div>
        <div className="flex gap-2">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.tiktok.com/..." disabled={!!jobId} />
          <Button onClick={getPreview} disabled={previewLoading || !!jobId}>
            {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Preview"}
          </Button>
        </div>
      </Card>

      <DriveCard />

      {preview && (
        <ZonePicker
          preview={preview}
          eraseRect={eraseRect} setEraseRect={setEraseRect}
          captionRect={captionRect} setCaptionRect={setCaptionRect}
          active={active} setActive={setActive}
          templates={templates} captionSample={captionSample}
        />
      )}

      {preview && (
        <Card className="p-4 space-y-3 border-border/40">
          <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">3. Shared settings</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <Label className="text-[11px] text-muted-foreground flex items-center gap-1"><Eraser className="h-3 w-3" /> Erase</Label>
              <select value={eraseMethod} onChange={(e) => setEraseMethod(e.target.value as any)} className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm">
                <option value="lama">LaMa (inpaint)</option>
                <option value="ns">Navier-Stokes</option>
                <option value="blur">Blur</option>
              </select>
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-xs h-8">
                <input type="checkbox" checked={eraseAutoDetect} onChange={(e) => setEraseAutoDetect(e.target.checked)} /> Auto-detect captions
              </label>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground flex items-center gap-1"><FileText className="h-3 w-3" /> Transcript engine</Label>
              <select value={transcriptEngine} onChange={(e) => setTranscriptEngine(e.target.value)} className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm">
                {(txEngines.length ? txEngines : [{ id: "ollama", label: "Ollama", ready: true, hint: null }]).map((e) => (
                  <option key={e.id} value={e.id} disabled={!e.ready}>{e.label}{!e.ready ? " (not ready)" : ""}</option>
                ))}
              </select>
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">Target language</Label>
              <select value={transcriptLang} onChange={(e) => setTranscriptLang(e.target.value)} className="w-full h-8 rounded-md border border-border/60 bg-background px-2 text-sm">
                <option value="">Keep original</option>
                {["en", "ro", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "tr"].map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">The cleaned transcript is generated once with these settings and reused for every variant&apos;s voice.</p>
        </Card>
      )}

      {preview && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">4. Variants ({variants.length})</div>
            <Button size="sm" variant="outline" onClick={addVariant} disabled={variants.length >= 4}>
              <Plus className="h-3.5 w-3.5 mr-1" /> Add variant
            </Button>
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            {variants.map((v, i) => (
              <VariantCard
                key={i} index={i} value={v}
                onChange={(nv) => updateVariant(i, nv)} onRemove={() => removeVariant(i)}
                canRemove={variants.length > 2}
                ttsEngines={ttsEngines} templates={templates} commentators={commentators} fonts={fonts}
                presets={presets} onPresetsChanged={loadPresets}
              />
            ))}
          </div>
        </div>
      )}

      {preview && (
        <Card className="p-4 space-y-3 border-border/40">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">5. Run</div>
            {!isRunning && (
              <Button onClick={start} disabled={runDisabled} title={runDisabled ? runDisabledReason : undefined}>
                <Layers className="h-4 w-4 mr-1" /> Process {variants.length} videos
              </Button>
            )}
          </div>

          {(isRunning || progress > 0) && (
            <>
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium">{progressMsg || jobStatus}</span>
                <Badge variant="outline">{progress}%</Badge>
              </div>
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div className="h-full bg-primary transition-all" style={{ width: `${progress}%` }} />
              </div>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
                {stages.map((s) => (
                  <div key={s.label} className={`rounded-md border p-2 text-center text-[11px] ${s.done ? "border-primary/40 bg-primary/5 text-primary" : s.active ? "border-amber-400/40 bg-amber-400/5 text-amber-500" : "border-border/40 text-muted-foreground"}`}>
                    {s.label}
                    {s.done && <CheckCircle2 className="h-3 w-3 mx-auto mt-0.5" />}
                    {s.active && <Loader2 className="h-3 w-3 mx-auto mt-0.5 animate-spin" />}
                  </div>
                ))}
              </div>
            </>
          )}

          {errorMsg && (
            <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" /> <div>{errorMsg}</div>
            </div>
          )}

          {results && results.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">Results</div>
              {results.map((r) => (
                <div key={r.index} className="rounded-md border border-border/40 bg-card p-3 space-y-2">
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className="text-[11px] shrink-0">#{r.index + 1}</Badge>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{r.name || r.commentator_preset_id || `Variant ${r.index + 1}`}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {r.commentator_preset_id ? `Commentator: ${r.commentator_preset_id}` : "No commentator"}
                        {r.file_size > 0 && ` · ${(r.file_size / 1024 / 1024).toFixed(1)} MB`}
                      </div>
                      {(() => {
                        const d = driveLabel(r.drive);
                        return d ? <div className={`text-[11px] mt-0.5 ${d.cls}`} title={r.drive?.reason || ""}>{d.text}</div> : null;
                      })()}
                    </div>
                    {r.file_available ? (
                      <a href={`/worker-api/parallel/${jobId}/download/${r.index}`} download={r.output_filename}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-primary/40 bg-primary/5 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 transition-colors">
                        <Download className="h-3.5 w-3.5" /> {r.parts && r.parts.length ? "Full" : "Download"}
                      </a>
                    ) : (
                      <Badge variant="outline" className="text-[10px] text-muted-foreground shrink-0">file gone</Badge>
                    )}
                  </div>
                  {r.parts && r.parts.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pl-9">
                      {r.parts.map((p) => (
                        p.available ? (
                          <a key={p.part} href={`/worker-api/parallel/${jobId}/download/${r.index}/part/${p.part}`} download={p.filename}
                            className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-background px-2 py-1 text-[11px] hover:border-primary/40 hover:text-primary transition-colors"
                            title={`${p.duration}s, starts at ${p.start}s`}>
                            <Download className="h-3 w-3" /> Part {p.part}/{p.of}
                          </a>
                        ) : (
                          <span key={p.part} className="text-[10px] text-muted-foreground">Part {p.part} gone</span>
                        )
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {descriptions && (descriptions.original_translated || descriptions.ai_generated) && (
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">Descriptions (shared)</div>
              {descriptions.original_translated && (
                <div className="rounded-md border border-border/40 bg-card p-3 text-sm whitespace-pre-wrap">
                  <div className="text-[11px] uppercase text-muted-foreground mb-1">Original (translated)</div>
                  {descriptions.original_translated}
                </div>
              )}
              {descriptions.ai_generated && (
                <div className="rounded-md border border-border/40 bg-card p-3 text-sm whitespace-pre-wrap">
                  <div className="text-[11px] uppercase text-muted-foreground mb-1">AI-generated</div>
                  {descriptions.ai_generated}
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
