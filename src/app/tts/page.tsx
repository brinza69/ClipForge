"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Badge } from "@/components/ui/badge";
import {
  Mic, Sparkles, Loader2, Download, Trash2, Upload,
  Volume2, AlertCircle, CheckCircle2, Search, Play, Pause,
  Key, Cloud, Cpu, ExternalLink, Globe,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

type Engine = "xtts" | "elevenlabs" | "local_clone";

interface Voice {
  id: string;
  name: string;
  path?: string;
  size_kb?: number;
  // ElevenLabs-only fields
  category?: string | null;
  preview_url?: string | null;
  description?: string | null;
  gender?: string | null;
  age?: string | null;
  accent?: string | null;
  use_case?: string | null;
}

interface EngineInfo {
  id: Engine;
  label: string;
  ready: boolean;
  hint: string | null;
  languages: string[];
  supports_romanian: boolean;
  supports_cloning?: boolean;
  cost: string;
  details?: {
    piper_installed?: boolean;
    piper_model_downloaded?: boolean;
    openvoice_installed?: boolean;
    openvoice_ckpt_downloaded?: boolean;
  };
}

interface ElevenStatus {
  configured: boolean;
  info: { tier?: string; character_count?: number; character_limit?: number } | null;
  error: string | null;
}

const LANG_LABELS: Record<string, string> = {
  en: "English", es: "Spanish", fr: "French", de: "German", it: "Italian",
  pt: "Portuguese", pl: "Polish", tr: "Turkish", ru: "Russian", nl: "Dutch",
  cs: "Czech", ar: "Arabic", "zh-cn": "Chinese (CN)", ja: "Japanese",
  hu: "Hungarian", ko: "Korean", hi: "Hindi",
};

export default function TTSPage() {
  const [engine, setEngine] = useState<Engine>("xtts");
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [elevenStatus, setElevenStatus] = useState<ElevenStatus | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voicesLoading, setVoicesLoading] = useState(true);
  const [voiceSearch, setVoiceSearch] = useState("");
  const [selectedVoice, setSelectedVoice] = useState<string>("");
  const [text, setText] = useState("");
  const [language, setLanguage] = useState("en");

  // XTTS knobs
  const [speed, setSpeed] = useState(1.0);
  const [temperature, setTemperature] = useState(0.7);
  // ElevenLabs knobs
  const [stability, setStability] = useState(0.5);
  const [similarityBoost, setSimilarityBoost] = useState(0.75);
  const [style, setStyle] = useState(0.0);

  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [resultUrl, setResultUrl] = useState("");
  const [resultName, setResultName] = useState("");
  const [isPlaying, setIsPlaying] = useState(false);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  // API-key dialog state
  const [keyDialogOpen, setKeyDialogOpen] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [keySaving, setKeySaving] = useState(false);

  const currentEngine = useMemo(() => engines.find((e) => e.id === engine), [engines, engine]);

  const refreshEngines = useCallback(async () => {
    try {
      const r = await fetch(`${WORKER_URL}/api/tts/engines`);
      const j = await r.json();
      setEngines(j.engines || []);
    } catch {
      setEngines([]);
    }
  }, []);

  const refreshElevenStatus = useCallback(async () => {
    try {
      const r = await fetch(`${WORKER_URL}/api/tts/elevenlabs/status`);
      const j: ElevenStatus = await r.json();
      setElevenStatus(j);
    } catch {
      setElevenStatus({ configured: false, info: null, error: "Server unreachable" });
    }
  }, []);

  const refreshVoices = useCallback(async (forEngine: Engine) => {
    setVoicesLoading(true);
    try {
      const r = await fetch(`${WORKER_URL}/api/tts/voices?engine=${forEngine}`);
      if (!r.ok) {
        // ElevenLabs not configured, etc. — just empty list
        setVoices([]);
        return;
      }
      const j = await r.json();
      const vs: Voice[] = j.voices || [];
      setVoices(vs);
      if (vs.length > 0) {
        setSelectedVoice((prev) => (prev && vs.find((v) => v.id === prev) ? prev : vs[0].id));
      } else {
        setSelectedVoice("");
      }
    } catch {
      // keep stale list
    } finally {
      setVoicesLoading(false);
    }
  }, []);

  useEffect(() => { refreshEngines(); refreshElevenStatus(); }, [refreshEngines, refreshElevenStatus]);
  useEffect(() => { refreshVoices(engine); }, [engine, refreshVoices]);

  // Auto-select Romanian when switching to a Romanian-capable engine
  useEffect(() => {
    if (engine === "elevenlabs") {
      const langs = engines.find((e) => e.id === "elevenlabs")?.languages || [];
      if (langs.includes("ro") && language === "en") {
        setLanguage("ro");
      }
    } else if (engine === "local_clone") {
      setLanguage("ro");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, engines]);

  const onSaveApiKey = async () => {
    const key = apiKeyInput.trim();
    setKeySaving(true);
    try {
      const r = await fetch(`${WORKER_URL}/api/tts/elevenlabs/key`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Server error ${r.status}`);
      }
      toast.success(key ? "API key saved" : "API key cleared");
      setApiKeyInput("");
      setKeyDialogOpen(false);
      await refreshEngines();
      await refreshElevenStatus();
      if (engine === "elevenlabs") await refreshVoices("elevenlabs");
    } catch (e: any) {
      toast.error("Could not save key", { description: e.message });
    } finally {
      setKeySaving(false);
    }
  };

  const filteredVoices = useMemo(() => {
    const q = voiceSearch.trim().toLowerCase();
    if (!q) return voices;
    return voices.filter((v) => v.name.toLowerCase().includes(q));
  }, [voices, voiceSearch]);

  const onGenerate = async () => {
    if (!text.trim()) { toast.error("Type some text first"); return; }
    if (!selectedVoice) { toast.error("Pick a voice first"); return; }
    if (currentEngine && !currentEngine.ready) {
      toast.error(`${currentEngine.label} not ready`, { description: currentEngine.hint || "" });
      return;
    }

    setGenerating(true); setErrorMsg(""); setResultUrl("");
    setProgress("Submitting…");
    try {
      const body: any = {
        text: text.trim(),
        voice_id: selectedVoice,
        engine,
        language,
      };
      if (engine === "xtts") {
        body.speed = speed;
        body.temperature = temperature;
      } else {
        body.stability = stability;
        body.similarity_boost = similarityBoost;
        body.style = style;
        // ElevenLabs accepts speed too (0.7-1.2). Backend clamps to the
        // engine-supported range, so we can safely send the same `speed`
        // state the XTTS slider uses.
        body.speed = speed;
      }

      const startRes = await fetch(`${WORKER_URL}/api/tts/synthesize`, {
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
      if (!job_id) throw new Error("Server did not return a job id");

      setProgress("Generating speech…");
      let errs = 0;
      const t0 = Date.now();
      while (true) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const stRes = await fetch(`${WORKER_URL}/api/tts/jobs/${job_id}`);
          if (!stRes.ok) {
            errs++;
            if (errs > 10) throw new Error(`Status check failed (${stRes.status})`);
            continue;
          }
          errs = 0;
          const st = await stRes.json();
          if (st.status === "failed") throw new Error(st.error || "Synth failed");
          if (st.message) setProgress(st.message);
          if (st.status === "done") break;
          if (Date.now() - t0 > 10 * 60 * 1000) throw new Error("Timed out after 10 minutes");
        } catch (e) {
          errs++;
          if (errs > 10) throw e;
        }
      }

      setProgress("Fetching audio…");
      const dlRes = await fetch(`${WORKER_URL}/api/tts/jobs/${job_id}/download`);
      if (!dlRes.ok) {
        let msg = `Download failed: ${dlRes.status}`;
        try { const j = await dlRes.json(); msg = j.detail || msg; } catch {}
        throw new Error(msg);
      }
      const blob = await dlRes.blob();
      const url = URL.createObjectURL(blob);
      if (resultUrl) URL.revokeObjectURL(resultUrl);
      setResultUrl(url);
      const ext = engine === "elevenlabs" ? "mp3" : "wav";
      setResultName(`tts_${Date.now()}.${ext}`);
      setProgress("");
      toast.success("Speech generated");
    } catch (e: any) {
      setErrorMsg(e.message || "Generation failed");
      toast.error("Generation failed", { description: e.message });
    } finally {
      setGenerating(false);
    }
  };

  const onUploadVoice = async (file: File) => {
    if (!file) return;
    const stem = file.name.replace(/\.[^.]+$/, "");
    const name = window.prompt("Name this voice:", stem) || stem;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("name", name);
      const r = await fetch(`${WORKER_URL}/api/tts/voices`, { method: "POST", body: fd });
      if (!r.ok) {
        let msg = `Upload failed (${r.status})`;
        try { const j = await r.json(); msg = j.detail || msg; } catch {}
        throw new Error(msg);
      }
      const j = await r.json();
      toast.success(`Uploaded "${j.name}"`);
      await refreshVoices(engine);
      setSelectedVoice(j.id);
    } catch (e: any) {
      toast.error("Upload failed", { description: e.message });
    } finally {
      setUploading(false);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  };

  const onDeleteVoice = async (id: string) => {
    if (!window.confirm(`Delete voice "${id}"?`)) return;
    try {
      const r = await fetch(`${WORKER_URL}/api/tts/voices/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error(`Delete failed (${r.status})`);
      toast.success("Voice deleted");
      await refreshVoices(engine);
    } catch (e: any) {
      toast.error("Delete failed", { description: e.message });
    }
  };

  const downloadResult = () => {
    if (!resultUrl) return;
    const a = document.createElement("a");
    a.href = resultUrl;
    a.download = resultName || "tts.wav";
    a.click();
  };

  const togglePlay = () => {
    const a = audioRef.current; if (!a) return;
    if (a.paused) a.play().catch(() => {});
    else a.pause();
  };

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-500/10">
            <Mic className="h-6 w-6 text-indigo-400" />
          </div>
          <div className="flex-1">
            <h1 className="text-2xl font-bold">Voice Studio</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              AI text-to-speech with voice cloning · {engine === "xtts" ? "local XTTS-v2" : "ElevenLabs API"}
            </p>
          </div>
          {currentEngine && (
            currentEngine.ready
              ? <Badge variant="outline" className="border-emerald-500/40 text-emerald-300 bg-emerald-500/10 gap-1.5">
                  <CheckCircle2 className="h-3 w-3" /> Engine ready
                </Badge>
              : <Badge variant="outline" className="border-amber-500/40 text-amber-300 bg-amber-500/10 gap-1.5">
                  <AlertCircle className="h-3 w-3" /> Setup needed
                </Badge>
          )}
        </div>
      </div>

      {/* Engine selector */}
      <Card className="p-1 border-border/40 bg-card/60 inline-flex gap-1">
        <button
          onClick={() => setEngine("xtts")}
          className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
            engine === "xtts" ? "bg-indigo-500/20 text-indigo-300" : "text-muted-foreground hover:bg-muted/30"
          }`}
        >
          <Cpu className="h-3.5 w-3.5" />
          <span>XTTS-v2 (local)</span>
          <span className="text-[9px] rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5">Free</span>
        </button>
        <button
          onClick={() => setEngine("elevenlabs")}
          className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
            engine === "elevenlabs" ? "bg-indigo-500/20 text-indigo-300" : "text-muted-foreground hover:bg-muted/30"
          }`}
        >
          <Cloud className="h-3.5 w-3.5" />
          <span>ElevenLabs API</span>
          <span className="text-[9px] rounded bg-blue-500/15 text-blue-400 px-1.5 py-0.5">Romanian ✓</span>
        </button>
        <button
          onClick={() => setEngine("local_clone")}
          className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
            engine === "local_clone" ? "bg-indigo-500/20 text-indigo-300" : "text-muted-foreground hover:bg-muted/30"
          }`}
        >
          <Globe className="h-3.5 w-3.5" />
          <span>Local clone (RO)</span>
          <span className="text-[9px] rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5">Free + RO + clone</span>
        </button>
      </Card>

      {/* Engine-specific setup banners */}
      {engine === "xtts" && currentEngine && !currentEngine.ready && (
        <Card className="p-4 border-amber-500/40 bg-amber-500/10 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-2 text-sm">
            <div className="font-medium text-amber-200">XTTS-v2 not installed</div>
            <p className="text-amber-100/80 text-xs">
              {currentEngine.hint || "Coqui TTS is missing."}
            </p>
            <pre className="text-[11px] bg-black/30 rounded px-2 py-1.5 font-mono text-amber-200">
              {`cd server && .\\venv\\Scripts\\python -m pip install TTS`}
            </pre>
            <p className="text-amber-100/60 text-[11px]">
              First run downloads the XTTS-v2 model (~2GB). Doesn't support Romanian — pick the ElevenLabs tab for that.
            </p>
          </div>
        </Card>
      )}

      {engine === "local_clone" && currentEngine && (
        <Card className={`p-4 flex items-start gap-3 ${currentEngine.ready ? "border-emerald-500/40 bg-emerald-500/5" : "border-amber-500/40 bg-amber-500/10"}`}>
          {currentEngine.ready
            ? <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0 mt-0.5" />
            : <AlertCircle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />}
          <div className="space-y-2 text-sm flex-1">
            {currentEngine.ready ? (
              <>
                <div className="font-medium text-emerald-200">Local Romanian + cloning ready</div>
                <p className="text-xs text-muted-foreground">
                  Pipeline: <span className="font-mono">Piper RO</span> → <span className="font-mono">OpenVoice tone converter</span>. Models cached in <code>data/models/local_clone/</code>.
                </p>
              </>
            ) : (
              <>
                <div className="font-medium text-amber-200">Local Romanian clone engine — setup needed</div>
                <p className="text-amber-100/80 text-xs">
                  This pipeline pairs <strong>Piper</strong> (native Romanian phonemes) with <strong>OpenVoice v2</strong> (voice timbre cloning). Both are free and local. One-time install:
                </p>
                <pre className="text-[11px] bg-black/40 rounded px-2 py-1.5 font-mono text-amber-200 overflow-x-auto whitespace-pre">
{`cd server
.\\venv\\Scripts\\python -m pip install piper-tts wavmark
.\\venv\\Scripts\\python -m pip install "git+https://github.com/myshell-ai/OpenVoice.git"`}
                </pre>
                <ul className="text-[11px] text-amber-100/70 space-y-0.5 mt-1">
                  <li>
                    <span className={currentEngine.details?.piper_installed ? "text-emerald-300" : "text-amber-300"}>
                      {currentEngine.details?.piper_installed ? "✓" : "○"} piper-tts package
                    </span>
                  </li>
                  <li>
                    <span className={currentEngine.details?.openvoice_installed ? "text-emerald-300" : "text-amber-300"}>
                      {currentEngine.details?.openvoice_installed ? "✓" : "○"} OpenVoice package
                    </span>
                  </li>
                </ul>
                <p className="text-[10px] text-amber-100/50">
                  After install, the first generation downloads ~560MB of model weights automatically. After that, everything runs offline.
                </p>
                <Button
                  size="sm" variant="outline"
                  className="gap-1.5 text-xs h-7 mt-1"
                  onClick={refreshEngines}
                >
                  <Loader2 className="h-3 w-3" /> Re-check install
                </Button>
              </>
            )}
          </div>
        </Card>
      )}

      {engine === "elevenlabs" && (
        <Card className="p-4 border-border/40 bg-card/60 flex items-start gap-3">
          <Key className={`h-5 w-5 shrink-0 mt-0.5 ${elevenStatus?.configured ? "text-emerald-400" : "text-amber-400"}`} />
          <div className="space-y-2 text-sm flex-1">
            {elevenStatus?.configured ? (
              <>
                <div className="font-medium text-emerald-200 flex items-center gap-2">
                  ElevenLabs API key configured
                  {elevenStatus.info?.tier && (
                    <Badge variant="outline" className="text-[9px] uppercase border-emerald-500/40 text-emerald-300">
                      {elevenStatus.info.tier}
                    </Badge>
                  )}
                </div>
                {elevenStatus.info?.character_limit != null && (
                  <p className="text-xs text-muted-foreground">
                    Usage: <span className="font-mono text-foreground">
                      {(elevenStatus.info.character_count ?? 0).toLocaleString()} / {elevenStatus.info.character_limit.toLocaleString()}
                    </span> chars this period
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <Button size="sm" variant="outline" onClick={() => setKeyDialogOpen(true)} className="gap-1.5 text-xs h-7">
                    <Key className="h-3 w-3" /> Update key
                  </Button>
                  <Button
                    size="sm" variant="outline"
                    onClick={() => { setApiKeyInput(""); onSaveApiKey(); }}
                    className="gap-1.5 text-xs h-7 text-red-300 hover:text-red-200"
                  >
                    Disconnect
                  </Button>
                </div>
              </>
            ) : (
              <>
                <div className="font-medium text-amber-200">ElevenLabs API key needed</div>
                <p className="text-amber-100/80 text-xs">
                  Paste your API key to unlock 30+ languages including Romanian. Free tier gives you 10,000 characters/month.
                </p>
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button size="sm" onClick={() => setKeyDialogOpen(true)} className="gap-1.5 text-xs h-7 bg-indigo-500/20 text-indigo-300 hover:bg-indigo-500/30 border border-indigo-500/30">
                    <Key className="h-3 w-3" /> Add API key
                  </Button>
                  <a
                    href="https://elevenlabs.io/app/settings/api-keys"
                    target="_blank"
                    rel="noopener"
                    className="inline-flex items-center gap-1 text-xs text-indigo-300 hover:text-indigo-200 underline underline-offset-2"
                  >
                    Get a key <ExternalLink className="h-3 w-3" />
                  </a>
                </div>
              </>
            )}
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {/* Left column: text + sliders + generate */}
        <div className="space-y-4">
          <Card className="p-4 border-border/40 bg-card/60 space-y-3">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
              Script
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={2000}
              rows={6}
              placeholder="Type or paste the text you want the AI voice to read…"
              className="w-full rounded-md border border-border/40 bg-background/60 px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-indigo-500/30 resize-y min-h-[120px]"
            />
            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
              <span>{text.length} / 2000 chars</span>
              <span>Tip: split long scripts into shorter takes for better pacing</span>
            </div>
          </Card>

          <Card className="p-4 border-border/40 bg-card/60 space-y-4">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
              Voice parameters
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Language</span>
                <span className="font-mono text-foreground">{LANG_LABELS[language] || language}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {(currentEngine?.languages || ["en"]).map((l) => (
                  <button
                    key={l}
                    onClick={() => setLanguage(l)}
                    className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                      l === language
                        ? "bg-indigo-500/20 text-indigo-300 border border-indigo-500/40"
                        : "bg-muted/20 text-muted-foreground border border-transparent hover:bg-muted/40"
                    }`}
                  >
                    {l}
                  </button>
                ))}
              </div>
              {engine === "elevenlabs" && language === "ro" && (
                <p className="text-[10px] text-emerald-300/80">Romanian is supported natively by eleven_multilingual_v2.</p>
              )}
            </div>

            {engine === "xtts" ? (
              <>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Speed</span>
                    <span className="font-mono text-foreground">{speed.toFixed(2)}x</span>
                  </div>
                  <Slider
                    value={[speed]}
                    onValueChange={(v) => setSpeed(Array.isArray(v) ? v[0] : v)}
                    min={0.5} max={2.0} step={0.05}
                  />
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Expressiveness (temperature)</span>
                    <span className="font-mono text-foreground">{temperature.toFixed(2)}</span>
                  </div>
                  <Slider
                    value={[temperature]}
                    onValueChange={(v) => setTemperature(Array.isArray(v) ? v[0] : v)}
                    min={0.1} max={1.0} step={0.05}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Lower = more consistent / monotone. Higher = more variation per take.
                  </p>
                </div>
              </>
            ) : engine === "local_clone" ? (
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                Piper handles Romanian pronunciation and pace; OpenVoice handles the voice timbre. There are no expression knobs — the prosody comes from the input text (commas, periods, question marks all matter).
                <br /><br />
                Pick a <strong>clean 6-30s reference clip</strong> from the voice library. The cleaner the clip, the closer the clone.
              </p>
            ) : (
              <>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Speed</span>
                    <span className="font-mono text-foreground">{speed.toFixed(2)}x</span>
                  </div>
                  <Slider
                    value={[Math.max(0.7, Math.min(1.2, speed))]}
                    onValueChange={(v) => setSpeed(Array.isArray(v) ? v[0] : v)}
                    min={0.7} max={1.2} step={0.05}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    ElevenLabs supports 0.7×–1.2× on multilingual_v2 / turbo / flash models.
                  </p>
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Stability</span>
                    <span className="font-mono text-foreground">{(stability * 100).toFixed(0)}%</span>
                  </div>
                  <Slider
                    value={[stability]}
                    onValueChange={(v) => setStability(Array.isArray(v) ? v[0] : v)}
                    min={0} max={1} step={0.05}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Higher = more consistent across takes. Lower = more emotional variance.
                  </p>
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Similarity boost</span>
                    <span className="font-mono text-foreground">{(similarityBoost * 100).toFixed(0)}%</span>
                  </div>
                  <Slider
                    value={[similarityBoost]}
                    onValueChange={(v) => setSimilarityBoost(Array.isArray(v) ? v[0] : v)}
                    min={0} max={1} step={0.05}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    How close to the original voice. ElevenLabs recommends 75%.
                  </p>
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Style exaggeration</span>
                    <span className="font-mono text-foreground">{(style * 100).toFixed(0)}%</span>
                  </div>
                  <Slider
                    value={[style]}
                    onValueChange={(v) => setStyle(Array.isArray(v) ? v[0] : v)}
                    min={0} max={1} step={0.05}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    0% = neutral. Higher = pushes the voice's signature style harder.
                  </p>
                </div>
              </>
            )}
          </Card>

          {errorMsg && (
            <Card className="p-3 border-red-500/40 bg-red-500/10 flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
              <div className="text-xs text-red-400 break-words">{errorMsg}</div>
            </Card>
          )}

          {!resultUrl ? (
            <Button
              size="lg"
              className="w-full gap-2 bg-indigo-500/20 text-indigo-300 hover:bg-indigo-500/30 border border-indigo-500/30"
              onClick={onGenerate}
              disabled={generating || !text.trim() || !selectedVoice}
            >
              {generating
                ? <><Loader2 className="h-4 w-4 animate-spin" /> {progress || "Generating…"}</>
                : <><Sparkles className="h-4 w-4" /> Generate speech</>}
            </Button>
          ) : (
            <Card className="p-3 border-border/40 bg-card/60 space-y-3">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={togglePlay}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-300 hover:bg-indigo-500/30 transition-colors"
                >
                  {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate">{resultName}</div>
                  <audio
                    ref={audioRef}
                    src={resultUrl}
                    controls
                    onPlay={() => setIsPlaying(true)}
                    onPause={() => setIsPlaying(false)}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full mt-1"
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <Button className="flex-1 gap-2" onClick={downloadResult}>
                  <Download className="h-4 w-4" /> Download WAV
                </Button>
                <Button
                  variant="outline"
                  className="gap-2"
                  onClick={() => { setResultUrl(""); setResultName(""); }}
                >
                  New take
                </Button>
              </div>
            </Card>
          )}
        </div>

        {/* Right column: voice library */}
        <div className="space-y-3">
          <Card className="p-4 border-border/40 bg-card/60 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
                Voice library
              </div>
              <span className="text-[10px] text-muted-foreground">{voices.length}</span>
            </div>

            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={voiceSearch}
                onChange={(e) => setVoiceSearch(e.target.value)}
                placeholder="Search voices…"
                className="pl-7 h-8 text-xs"
              />
            </div>

            <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
              {voicesLoading && (
                <div className="text-xs text-muted-foreground py-4 text-center">Loading…</div>
              )}
              {!voicesLoading && voices.length === 0 && (
                <div className="text-xs text-muted-foreground py-4 text-center space-y-1">
                  <p>No voices yet.</p>
                  <p className="text-[10px]">Upload a 6-30s clean reference clip below.</p>
                </div>
              )}
              {filteredVoices.map((v) => (
                <button
                  key={v.id}
                  onClick={() => setSelectedVoice(v.id)}
                  className={`group flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors ${
                    v.id === selectedVoice
                      ? "border-indigo-500/50 bg-indigo-500/10"
                      : "border-border/30 bg-muted/10 hover:border-border/60"
                  }`}
                >
                  <Volume2 className={`h-4 w-4 shrink-0 ${
                    v.id === selectedVoice ? "text-indigo-300" : "text-muted-foreground"
                  }`} />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium truncate">{v.name}</div>
                    {engine === "elevenlabs" ? (
                      <div className="text-[9px] text-muted-foreground truncate">
                        {[v.gender, v.age, v.accent, v.use_case].filter(Boolean).join(" · ") || v.category || v.id.slice(0, 12)}
                      </div>
                    ) : (
                      <div className="text-[9px] text-muted-foreground truncate">{v.id} · {v.size_kb}KB</div>
                    )}
                  </div>
                  {engine === "xtts" && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onDeleteVoice(v.id); }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-red-400 p-1"
                      title="Delete voice"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  )}
                </button>
              ))}
            </div>

            {(engine === "xtts" || engine === "local_clone") && (
              <>
                <input
                  ref={uploadInputRef}
                  type="file"
                  accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg"
                  className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) onUploadVoice(f); }}
                />
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full gap-2"
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={uploading}
                >
                  {uploading
                    ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Uploading…</>
                    : <><Upload className="h-3.5 w-3.5" /> Add voice clip</>}
                </Button>
              </>
            )}
            {engine === "elevenlabs" && (
              <p className="text-[10px] text-muted-foreground text-center">
                Voices come from your ElevenLabs library. Add new ones at{" "}
                <a href="https://elevenlabs.io/app/voice-library" target="_blank" rel="noopener" className="text-indigo-300 hover:underline">
                  elevenlabs.io
                </a>.
              </p>
            )}
          </Card>

          <Card className="p-3 border-border/40 bg-card/60 space-y-1.5 text-[10px] text-muted-foreground">
            {engine === "xtts" ? (
              <>
                <p className="font-semibold text-foreground text-[11px]">How to add ElevenLabs-quality voices locally</p>
                <p>1. Sign up for the free ElevenLabs tier (10k chars/mo).</p>
                <p>2. Generate a 10-30s sample of any voice you like.</p>
                <p>3. Download the MP3 and upload it here as a reference clip.</p>
                <p className="text-amber-300/70 pt-1">XTTS-v2 doesn't support Romanian — switch to <strong>Local clone (RO)</strong> or ElevenLabs for that.</p>
              </>
            ) : engine === "elevenlabs" ? (
              <>
                <p className="font-semibold text-foreground text-[11px]">ElevenLabs tips</p>
                <p>Free tier: 10,000 chars/month with no credit card.</p>
                <p>The <code>eleven_multilingual_v2</code> model handles Romanian, Polish, Hungarian, Greek, and ~29 other languages with proper accents.</p>
                <p>For dramatic content try lower stability (~30%). For voiceover-style narration use higher stability (~75%).</p>
              </>
            ) : (
              <>
                <p className="font-semibold text-foreground text-[11px]">Local clone tips</p>
                <p>Use a 10-20s clean reference clip — single speaker, no music, no echo.</p>
                <p>Piper handles diacritics correctly (ă, â, î, ș, ț) so write Romanian text normally.</p>
                <p>Add punctuation generously — Piper uses it for natural prosody.</p>
                <p className="text-amber-300/70 pt-1">First generation will download ~560MB of model weights. After that, fully offline.</p>
              </>
            )}
          </Card>
        </div>
      </div>

      {/* API key dialog */}
      <Dialog open={keyDialogOpen} onOpenChange={setKeyDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Key className="h-4 w-4 text-indigo-400" /> ElevenLabs API key
            </DialogTitle>
            <DialogDescription>
              Paste your key from{" "}
              <a href="https://elevenlabs.io/app/settings/api-keys" target="_blank" rel="noopener" className="text-indigo-300 hover:underline">
                elevenlabs.io
              </a>
              . It's stored only on this device, in <code>data/tts_config.json</code>.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="sk_…"
              autoFocus
            />
            <p className="text-[10px] text-muted-foreground">
              We'll verify the key works before saving. Leave empty + save to clear the stored key.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setKeyDialogOpen(false)} disabled={keySaving}>
              Cancel
            </Button>
            <Button onClick={onSaveApiKey} disabled={keySaving} className="gap-2">
              {keySaving ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Verifying…</> : "Save key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
