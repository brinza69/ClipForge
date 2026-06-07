"use client";

/**
 * Settings → API Keys card. Manages the three API keys ClipForge stores
 * server-side (no key ever leaves the box once saved):
 *   - ElevenLabs (TTS, RO voices)
 *   - OpenAI (transcript cleaning / translation)
 *   - Anthropic (transcript cleaning / translation)
 *
 * Each section:
 *   - shows a status badge (Configured / Not configured)
 *   - has a paste input + Save button → verifies with the provider
 *   - Clear button → wipes the saved key
 */

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loader2, KeyRound, Eye, EyeOff, Check, X } from "lucide-react";
import { toast } from "sonner";

interface KeyState {
  configured: boolean;
  loading: boolean;
  value: string;
  show: boolean;
  saving: boolean;
}

const initial = (): KeyState => ({
  configured: false,
  loading: true,
  value: "",
  show: false,
  saving: false,
});

export function ApiKeysCard() {
  const [eleven, setEleven] = useState<KeyState>(initial());
  const [openai, setOpenai] = useState<KeyState>(initial());
  const [anthropic, setAnthropic] = useState<KeyState>(initial());
  const [elevenInfo, setElevenInfo] = useState<{ tier?: string; character_count?: number; character_limit?: number } | null>(null);

  const refreshAll = useCallback(async () => {
    // ElevenLabs status
    try {
      const r = await fetch(`/worker-api/tts/elevenlabs/status`);
      const j = await r.json();
      setEleven((s) => ({ ...s, configured: !!j.configured, loading: false }));
      setElevenInfo(j.info || null);
    } catch {
      setEleven((s) => ({ ...s, loading: false }));
    }
    // Transcript engines → openai + anthropic ready bits
    try {
      const r = await fetch(`/worker-api/transcript/engines`);
      const j = await r.json();
      const find = (id: string) => (j.engines || []).find((e: any) => e.id === id);
      setOpenai((s) => ({ ...s, configured: !!find("openai")?.ready, loading: false }));
      setAnthropic((s) => ({ ...s, configured: !!find("anthropic")?.ready, loading: false }));
    } catch {
      setOpenai((s) => ({ ...s, loading: false }));
      setAnthropic((s) => ({ ...s, loading: false }));
    }
  }, []);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  const save = async (
    label: string, endpoint: string, state: KeyState,
    setState: (next: (s: KeyState) => KeyState) => void,
  ) => {
    const k = state.value.trim();
    if (!k) {
      toast.error(`Paste an ${label} key first`);
      return;
    }
    setState((s) => ({ ...s, saving: true }));
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: k }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `${label} key rejected (${r.status})`);
      toast.success(`${label} key verified and saved`);
      setState((s) => ({ ...s, saving: false, value: "", show: false }));
      await refreshAll();
    } catch (e: any) {
      setState((s) => ({ ...s, saving: false }));
      toast.error(`${label} key failed`, { description: e.message });
    }
  };

  const clear = async (
    label: string, endpoint: string,
    setState: (next: (s: KeyState) => KeyState) => void,
  ) => {
    if (!window.confirm(`Clear the saved ${label} key?`)) return;
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "" }),
      });
      if (!r.ok) throw new Error(`Clear failed (${r.status})`);
      toast.success(`${label} key cleared`);
      setState((s) => ({ ...s, value: "", show: false }));
      await refreshAll();
    } catch (e: any) {
      toast.error(`${label} clear failed`, { description: e.message });
    }
  };

  return (
    <Card className="border-border/30 bg-card/50 p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" /> API Keys
        </h3>
        <p className="text-[11px] text-muted-foreground">
          Stored locally in <code className="font-mono">data/*_config.json</code> (gitignored). Never sent to anyone except the provider.
        </p>
      </div>

      <KeyRow
        label="ElevenLabs"
        helpText="For Romanian TTS (XTTS doesn't support RO) and high-quality voices. Get a key at elevenlabs.io → Profile → API Keys."
        state={eleven}
        setState={setEleven}
        onSave={() => save("ElevenLabs", `/worker-api/tts/elevenlabs/key`, eleven, setEleven)}
        onClear={() => clear("ElevenLabs", `/worker-api/tts/elevenlabs/key`, setEleven)}
        placeholder="sk_..."
        extra={elevenInfo && eleven.configured ? (
          <div className="text-[11px] text-muted-foreground mt-1">
            Tier: <span className="font-medium">{elevenInfo.tier || "—"}</span>
            {typeof elevenInfo.character_count === "number" && typeof elevenInfo.character_limit === "number" && (
              <> · Usage: <span className="font-mono">{elevenInfo.character_count.toLocaleString()} / {elevenInfo.character_limit.toLocaleString()} chars</span></>
            )}
          </div>
        ) : null}
      />

      <KeyRow
        label="OpenAI"
        helpText="For transcript cleaning / translation (gpt-4o-mini). Get a key at platform.openai.com → API keys."
        state={openai}
        setState={setOpenai}
        onSave={() => save("OpenAI", `/worker-api/transcript/openai/key`, openai, setOpenai)}
        onClear={() => clear("OpenAI", `/worker-api/transcript/openai/key`, setOpenai)}
        placeholder="sk-proj-..."
      />

      <KeyRow
        label="Anthropic"
        helpText="For transcript cleaning / translation (claude-haiku-4-5). Get a key at console.anthropic.com → API keys."
        state={anthropic}
        setState={setAnthropic}
        onSave={() => save("Anthropic", `/worker-api/transcript/anthropic/key`, anthropic, setAnthropic)}
        onClear={() => clear("Anthropic", `/worker-api/transcript/anthropic/key`, setAnthropic)}
        placeholder="sk-ant-..."
      />
    </Card>
  );
}

function KeyRow({
  label, helpText, state, setState, onSave, onClear, placeholder, extra,
}: {
  label: string;
  helpText: string;
  state: KeyState;
  setState: (next: (s: KeyState) => KeyState) => void;
  onSave: () => void;
  onClear: () => void;
  placeholder: string;
  extra?: React.ReactNode;
}) {
  return (
    <div className="space-y-2 border-t border-border/30 pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{label}</span>
        {state.loading ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
        ) : state.configured ? (
          <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-400">
            <Check className="h-3 w-3 mr-1" /> Configured
          </Badge>
        ) : (
          <Badge variant="outline" className="text-[10px] border-muted-foreground/40 text-muted-foreground">
            <X className="h-3 w-3 mr-1" /> Not configured
          </Badge>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">{helpText}</p>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input
            type={state.show ? "text" : "password"}
            value={state.value}
            onChange={(e) => setState((s) => ({ ...s, value: e.target.value }))}
            placeholder={placeholder}
            className="font-mono text-xs pr-9"
            autoComplete="off"
          />
          <button
            type="button"
            onClick={() => setState((s) => ({ ...s, show: !s.show }))}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            tabIndex={-1}
          >
            {state.show ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        </div>
        <Button onClick={onSave} disabled={state.saving || !state.value.trim()}>
          {state.saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save & Verify"}
        </Button>
        {state.configured && (
          <Button variant="outline" onClick={onClear} disabled={state.saving} title={`Clear saved ${label} key`}>
            Clear
          </Button>
        )}
      </div>
      {extra}
    </div>
  );
}
