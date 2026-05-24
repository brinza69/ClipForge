"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  FileText, Loader2, Download, Copy, Upload, AlertCircle, CheckCircle2,
  Sparkles, Key, Cpu, Cloud, ExternalLink, Languages, Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

type Engine = "ollama" | "openai" | "anthropic";

interface EngineInfo {
  id: Engine;
  label: string;
  ready: boolean;
  hint: string | null;
  default_model: string;
  available_models?: string[];
  cost: string;
}

interface Language {
  code: string;
  name: string;
}

export default function TranscriptStudioPage() {
  const [engine, setEngine] = useState<Engine>("ollama");
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [languages, setLanguages] = useState<Language[]>([]);
  const [targetLang, setTargetLang] = useState<string>("");  // "" = keep original
  const [model, setModel] = useState<string>("");

  const [sourceText, setSourceText] = useState("");
  const [sourceFilename, setSourceFilename] = useState("");
  const [resultText, setResultText] = useState("");

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [stats, setStats] = useState<{ src: number; out: number } | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // API key dialog
  const [keyDialogOpen, setKeyDialogOpen] = useState(false);
  const [keyEngineTarget, setKeyEngineTarget] = useState<"openai" | "anthropic">("openai");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [keySaving, setKeySaving] = useState(false);

  const currentEngine = useMemo(
    () => engines.find((e) => e.id === engine),
    [engines, engine]
  );

  const refreshEngines = useCallback(async () => {
    try {
      const r = await fetch(`${WORKER_URL}/api/transcript/engines`);
      const j = await r.json();
      setEngines(j.engines || []);
      setLanguages(j.languages || []);
    } catch {
      setEngines([]);
    }
  }, []);

  useEffect(() => { refreshEngines(); }, [refreshEngines]);

  // Reset model dropdown when switching engines (so a stale ollama tag doesn't get sent to openai)
  useEffect(() => { setModel(""); }, [engine]);

  const openKeyDialog = (which: "openai" | "anthropic") => {
    setKeyEngineTarget(which);
    setApiKeyInput("");
    setKeyDialogOpen(true);
  };

  const onSaveApiKey = async () => {
    const key = apiKeyInput.trim();
    setKeySaving(true);
    try {
      const r = await fetch(`${WORKER_URL}/api/transcript/${keyEngineTarget}/key`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Server error ${r.status}`);
      }
      toast.success(key ? `${keyEngineTarget === "openai" ? "OpenAI" : "Anthropic"} key saved` : "Key cleared");
      setApiKeyInput("");
      setKeyDialogOpen(false);
      await refreshEngines();
    } catch (e: any) {
      toast.error("Could not save key", { description: e.message });
    } finally {
      setKeySaving(false);
    }
  };

  const onFile = async (file: File | null) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast.error("File too large (max 5MB)");
      return;
    }
    try {
      const txt = await file.text();
      setSourceText(txt);
      setSourceFilename(file.name);
      toast.success(`Loaded ${file.name} (${(file.size / 1024).toFixed(1)} KB)`);
    } catch (e: any) {
      toast.error("Could not read file", { description: e.message });
    }
  };

  const onClean = async (forceLang?: string) => {
    if (!sourceText.trim()) {
      toast.error("Paste a transcript or upload a file first");
      return;
    }
    if (currentEngine && !currentEngine.ready) {
      toast.error(`${currentEngine.label} not ready`, { description: currentEngine.hint || "" });
      return;
    }

    // Translate buttons pass forceLang directly; also update targetLang so download filename is correct
    const activeLang = forceLang !== undefined ? forceLang : targetLang;
    if (forceLang !== undefined) setTargetLang(forceLang);

    setBusy(true);
    setErrorMsg("");
    setResultText("");
    setStats(null);
    setProgress("Submitting…");

    try {
      const body: any = {
        text: sourceText,
        engine,
        source_filename: sourceFilename,
      };
      if (activeLang) body.target_language = activeLang;
      if (model) body.model = model;

      const startRes = await fetch(`${WORKER_URL}/api/transcript/clean`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!startRes.ok) {
        let msg = `Server error ${startRes.status}`;
        try { const j = await startRes.json(); msg = j.detail || msg; } catch {}
        throw new Error(msg);
      }
      const { job_id } = await startRes.json();

      setProgress("Cleaning transcript…");
      let errs = 0;
      const t0 = Date.now();
      while (true) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const stRes = await fetch(`${WORKER_URL}/api/transcript/jobs/${job_id}`);
          if (!stRes.ok) {
            errs++;
            if (errs > 10) throw new Error(`Status check failed (${stRes.status})`);
            continue;
          }
          errs = 0;
          const st = await stRes.json();
          if (st.status === "failed") throw new Error(st.error || "Job failed");
          if (st.message) setProgress(st.message);
          if (st.status === "done") break;
          if (Date.now() - t0 > 15 * 60 * 1000) throw new Error("Timed out after 15 minutes");
        } catch (e) {
          errs++;
          if (errs > 10) throw e;
        }
      }

      setProgress("Fetching result…");
      const rRes = await fetch(`${WORKER_URL}/api/transcript/jobs/${job_id}/result`);
      if (!rRes.ok) {
        let msg = `Result fetch failed: ${rRes.status}`;
        try { const j = await rRes.json(); msg = j.detail || msg; } catch {}
        throw new Error(msg);
      }
      const data = await rRes.json();
      setResultText(data.text || "");
      setStats({ src: data.source_length || 0, out: data.result_length || 0 });
      setProgress("");
      toast.success("Transcript cleaned");
    } catch (e: any) {
      setErrorMsg(e.message || "Failed");
      toast.error("Failed", { description: e.message });
    } finally {
      setBusy(false);
    }
  };

  const onCopy = async () => {
    if (!resultText) return;
    try {
      await navigator.clipboard.writeText(resultText);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Copy failed — try selecting + Ctrl+C");
    }
  };

  const onDownload = () => {
    if (!resultText) return;
    const blob = new Blob([resultText], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const langSuffix = targetLang ? `_${targetLang}` : "";
    const stem = sourceFilename
      ? sourceFilename.replace(/\.[^.]+$/, "")
      : `transcript_${Date.now()}`;
    a.download = `${stem}_clean${langSuffix}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const onClear = () => {
    setSourceText("");
    setSourceFilename("");
    setResultText("");
    setStats(null);
    setErrorMsg("");
  };

  return (
    <div className="container mx-auto max-w-7xl px-4 py-8 sm:py-10">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-500 to-cyan-500 shadow-lg shadow-emerald-500/20">
            <FileText className="h-5 w-5 text-white" />
          </div>
          <h1 className="text-3xl font-bold">Transcript Studio</h1>
        </div>
        <p className="text-muted-foreground">
          Turn raw, fragmented transcripts into clean readable prose. Optionally translate to any language.
        </p>
      </div>

      {/* Engine pills */}
      <Card className="p-4 mb-6">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-medium text-muted-foreground">Engine</div>
          <button
            onClick={refreshEngines}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Refresh
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {engines.map((e) => {
            const active = engine === e.id;
            const Icon = e.id === "ollama" ? Cpu : Cloud;
            return (
              <button
                key={e.id}
                onClick={() => setEngine(e.id)}
                className={`text-left rounded-lg border-2 p-3 transition-all ${
                  active
                    ? "border-primary bg-primary/5"
                    : "border-border/40 hover:border-border"
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2">
                    <Icon className="h-4 w-4" />
                    <span className="font-semibold text-sm">{e.label}</span>
                  </div>
                  {e.ready ? (
                    <Badge variant="outline" className="text-emerald-500 border-emerald-500/30 text-[10px]">
                      <CheckCircle2 className="mr-1 h-3 w-3" /> ready
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-amber-500 border-amber-500/30 text-[10px]">
                      <AlertCircle className="mr-1 h-3 w-3" /> setup
                    </Badge>
                  )}
                </div>
                <div className="text-[11px] text-muted-foreground line-clamp-2 min-h-[2em]">
                  {e.hint || `${e.cost}`}
                </div>
                {(e.id === "openai" || e.id === "anthropic") && (
                  <div className="mt-2 flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-[10px]"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        openKeyDialog(e.id as "openai" | "anthropic");
                      }}
                    >
                      <Key className="mr-1 h-3 w-3" />
                      {e.ready ? "Update key" : "Add API key"}
                    </Button>
                    <a
                      href={
                        e.id === "openai"
                          ? "https://platform.openai.com/api-keys"
                          : "https://console.anthropic.com/settings/keys"
                      }
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(ev) => ev.stopPropagation()}
                      className="text-[10px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
                    >
                      get one <ExternalLink className="h-2.5 w-2.5" />
                    </a>
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Model picker (Ollama only — pick from available local models) */}
        {engine === "ollama" && currentEngine?.available_models && currentEngine.available_models.length > 0 && (
          <div className="mt-4 flex items-center gap-3">
            <div className="text-xs text-muted-foreground">Model:</div>
            <select
              value={model || currentEngine.default_model}
              onChange={(ev) => setModel(ev.target.value)}
              className="text-xs bg-background border border-border rounded px-2 py-1"
            >
              {currentEngine.available_models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        )}

        {/* Quick-translate language selector — EN / RO */}
        <div className="mt-4 flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Languages className="h-3.5 w-3.5" /> Output language:
          </div>
          {[
            { code: "", label: "Keep original" },
            { code: "en", label: "🇬🇧 English" },
            { code: "ro", label: "🇷🇴 Romanian" },
          ].map(({ code, label }) => (
            <button
              key={code}
              onClick={() => setTargetLang(code)}
              className={`text-xs px-3 py-1.5 rounded-md border font-medium transition-all ${
                targetLang === code
                  ? "bg-primary/10 border-primary text-primary"
                  : "border-border/40 hover:border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </Card>

      {/* Input / Output panes */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Input */}
        <Card className="p-4 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="font-semibold">Source transcript</h2>
              {sourceFilename && (
                <Badge variant="outline" className="text-[10px]">{sourceFilename}</Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.srt,.vtt,.json"
                className="hidden"
                onChange={(ev) => onFile(ev.target.files?.[0] || null)}
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => fileInputRef.current?.click()}
                disabled={busy}
              >
                <Upload className="mr-1.5 h-3.5 w-3.5" /> Upload
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={onClear}
                disabled={busy || (!sourceText && !resultText)}
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Clear
              </Button>
            </div>
          </div>
          <Textarea
            value={sourceText}
            onChange={(ev) => {
              setSourceText(ev.target.value);
              if (sourceFilename) setSourceFilename("");
            }}
            placeholder="Paste a transcript here, or upload a .txt / .srt / .vtt / .json file.
Timestamps, line numbers and cue tags are stripped automatically."
            className="flex-1 min-h-[360px] font-mono text-xs resize-none"
            disabled={busy}
          />
          <div className="mt-2 text-[11px] text-muted-foreground flex items-center justify-between">
            <span>{sourceText.length.toLocaleString()} chars · {sourceText.split(/\s+/).filter(Boolean).length.toLocaleString()} words</span>
          </div>

          {/* Primary action + translate shortcuts */}
          <div className="mt-3 flex gap-2">
            <Button
              onClick={() => onClean()}
              disabled={busy || !sourceText.trim() || (currentEngine ? !currentEngine.ready : false)}
              className="flex-1"
              size="lg"
            >
              {busy ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {progress || "Working…"}
                </>
              ) : (
                <>
                  <Sparkles className="mr-2 h-4 w-4" />
                  {targetLang === "en"
                    ? "Clean → English"
                    : targetLang === "ro"
                    ? "Clean → Romanian"
                    : "Clean up"}
                </>
              )}
            </Button>

            {/* Quick translate buttons */}
            <Button
              onClick={() => onClean("en")}
              disabled={busy || !sourceText.trim() || (currentEngine ? !currentEngine.ready : false)}
              variant="outline"
              size="lg"
              title="Clean & translate to English"
            >
              🇬🇧
            </Button>
            <Button
              onClick={() => onClean("ro")}
              disabled={busy || !sourceText.trim() || (currentEngine ? !currentEngine.ready : false)}
              variant="outline"
              size="lg"
              title="Clean & translate to Romanian"
            >
              🇷🇴
            </Button>
          </div>

          {errorMsg && (
            <div className="mt-3 text-xs text-red-500 flex items-start gap-2">
              <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}
        </Card>

        {/* Output */}
        <Card className="p-4 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h2 className="font-semibold">Cleaned output</h2>
              {stats && (
                <Badge variant="outline" className="text-[10px]">
                  {stats.out.toLocaleString()} chars · {stats.src > 0 ? `${Math.round((stats.out / stats.src) * 100)}% of source` : ""}
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={onCopy}
                disabled={!resultText}
              >
                <Copy className="mr-1.5 h-3.5 w-3.5" /> Copy
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={onDownload}
                disabled={!resultText}
              >
                <Download className="mr-1.5 h-3.5 w-3.5" /> .txt
              </Button>
            </div>
          </div>
          <Textarea
            value={resultText}
            onChange={(ev) => setResultText(ev.target.value)}
            placeholder="The cleaned transcript will appear here. You can edit it before copying or downloading."
            className="flex-1 min-h-[360px] text-sm leading-relaxed resize-none"
          />
        </Card>
      </div>

      {/* API key dialog */}
      <Dialog open={keyDialogOpen} onOpenChange={setKeyDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {keyEngineTarget === "openai" ? "OpenAI API Key" : "Anthropic API Key"}
            </DialogTitle>
            <DialogDescription>
              The key is stored on your local server only (in <code className="text-[10px]">data/transcript_config.json</code>) and never sent to the browser after saving. Leave empty + save to remove an existing key.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Input
              type="password"
              value={apiKeyInput}
              onChange={(ev) => setApiKeyInput(ev.target.value)}
              placeholder={keyEngineTarget === "openai" ? "sk-…" : "sk-ant-…"}
              autoFocus
            />
            <p className="text-[11px] text-muted-foreground mt-2">
              Get a key at{" "}
              <a
                href={
                  keyEngineTarget === "openai"
                    ? "https://platform.openai.com/api-keys"
                    : "https://console.anthropic.com/settings/keys"
                }
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline inline-flex items-center gap-1"
              >
                {keyEngineTarget === "openai" ? "platform.openai.com" : "console.anthropic.com"}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setKeyDialogOpen(false)} disabled={keySaving}>
              Cancel
            </Button>
            <Button onClick={onSaveApiKey} disabled={keySaving}>
              {keySaving ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Verifying…</> : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
