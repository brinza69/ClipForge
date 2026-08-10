"use client";

// AI Stream Clipper — source setup.
//
// Deliberately short. The first version put all twenty-odd settings on screen,
// which made the common case (paste a link, get clips) look like a
// configuration exercise. There are now three decisions above the fold —
// source, how long the clips should be, how many — and everything else sits
// behind "More options" with defaults that already work.
//
// Clip length is a named preset rather than two number boxes because
// "min/max seconds" is a question about the tool, not about the video. The
// bands come from the stream-clipping guidance in docs/research: real stream
// clips land at 15-45s.
//
// /preview answers 200 with an {error, suggestion} body for a URL it cannot
// use. That is field-level feedback, so it renders inline next to the input;
// only transport failures become toasts.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Check, Link2, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { readApiError, errorDescription } from "@/lib/api-error";
import {
  CLIPPER_API,
  DEFAULT_SETTINGS,
  LAYOUT_LABELS,
  formatTimecode,
  type ClipperSettings,
  type LayoutMode,
  type SourceMetadata,
  type TargetPlatform,
} from "@/types/clipper";

const LENGTH_PRESETS = [
  { id: "short", label: "Short", hint: "15–30s · punchy", min: 15, max: 30 },
  { id: "standard", label: "Standard", hint: "20–45s · recommended", min: 20, max: 45 },
  { id: "long", label: "Long", hint: "45–90s · for explanations", min: 45, max: 90 },
] as const;

const PLATFORMS: { id: TargetPlatform; label: string }[] = [
  { id: "tiktok", label: "TikTok" },
  { id: "youtube_shorts", label: "YouTube Shorts" },
  { id: "instagram_reels", label: "Instagram Reels" },
  { id: "facebook_reels", label: "Facebook Reels" },
];

const LANGUAGES = [
  { id: "auto", label: "Auto-detect" },
  { id: "ro", label: "Română" },
  { id: "en", label: "English" },
];

const LAYOUT_CHOICES: LayoutMode[] = [
  "auto",
  "face_top_game_bottom",
  "fullscreen_game",
  "fullscreen_crop",
];

function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border/50 text-muted-foreground hover:bg-accent hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

export function SourceForm({ onCreated }: { onCreated?: () => void }) {
  const router = useRouter();

  const [mode, setMode] = useState<"url" | "upload">("url");
  const [url, setUrl] = useState("");
  const [meta, setMeta] = useState<SourceMetadata | null>(null);
  const [uploadPath, setUploadPath] = useState<string | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [probing, setProbing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [lengthPreset, setLengthPreset] = useState<string>("standard");
  const [clipCount, setClipCount] = useState(8);
  const [rights, setRights] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  const [platform, setPlatform] = useState<TargetPlatform>("tiktok");
  const [language, setLanguage] = useState("auto");
  const [layout, setLayout] = useState<LayoutMode>("auto");

  const metaError = meta?.error ? meta : null;
  const metaOk = meta && !meta.error ? meta : null;
  const hasSource = mode === "url" ? Boolean(metaOk) : Boolean(uploadPath);

  async function probe() {
    if (!url.trim()) return;
    setProbing(true);
    setMeta(null);
    try {
      const r = await fetch(`${CLIPPER_API}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      if (!r.ok) {
        const e = await readApiError(r, "Could not read that link");
        setMeta({ error: e.message, suggestion: e.details } as SourceMetadata);
        return;
      }
      setMeta((await r.json()) as SourceMetadata);
    } catch (err) {
      setMeta({
        error: err instanceof Error ? err.message : String(err),
      } as SourceMetadata);
    } finally {
      setProbing(false);
    }
  }

  async function upload(file: File) {
    setProbing(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const r = await fetch(`${CLIPPER_API}/upload`, { method: "POST", body });
      if (!r.ok) {
        const e = await readApiError(r, "Upload failed");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      const j = (await r.json()) as { upload_path: string; filename: string };
      setUploadPath(j.upload_path);
      setUploadName(j.filename);
    } catch (err) {
      toast.error("Upload failed", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setProbing(false);
    }
  }

  async function submit() {
    const preset = LENGTH_PRESETS.find((p) => p.id === lengthPreset) ?? LENGTH_PRESETS[1];
    const settings: ClipperSettings = {
      ...DEFAULT_SETTINGS,
      clip_count: clipCount,
      min_clip_s: preset.min,
      max_clip_s: preset.max,
      platform,
      language,
      layout_mode: layout,
    };

    setSubmitting(true);
    try {
      const r = await fetch(`${CLIPPER_API}/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_kind: mode,
          url: mode === "url" ? url.trim() : undefined,
          upload_path: mode === "upload" ? uploadPath : undefined,
          title: metaOk?.title || uploadName || "Untitled clip project",
          rights_confirmed: rights,
          settings,
        }),
      });
      if (!r.ok) {
        const e = await readApiError(r, "Could not create the project");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      const project = (await r.json()) as { id: string };
      onCreated?.();
      router.push(`/ai-stream-clipper/${project.id}`);
    } catch (err) {
      toast.error("Could not create the project", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="space-y-4 p-5">
      <div className="flex gap-2">
        <Pill active={mode === "url"} onClick={() => setMode("url")}>
          <span className="flex items-center gap-1.5">
            <Link2 className="h-3.5 w-3.5" /> Paste a link
          </span>
        </Pill>
        <Pill active={mode === "upload"} onClick={() => setMode("upload")}>
          <span className="flex items-center gap-1.5">
            <Upload className="h-3.5 w-3.5" /> Upload a file
          </span>
        </Pill>
      </div>

      {mode === "url" ? (
        <div className="flex gap-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void probe();
            }}
            placeholder="YouTube, Twitch VOD, or a direct video URL"
            className="flex-1"
          />
          <Button
            onClick={() => void probe()}
            disabled={probing || !url.trim()}
            variant="secondary"
          >
            {probing ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check"}
          </Button>
        </div>
      ) : (
        <div>
          <input
            type="file"
            accept="video/*"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
            }}
            className="block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border-0 file:bg-primary/10 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-primary"
          />
          {uploadName && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-emerald-400">
              <Check className="h-3.5 w-3.5" /> {uploadName}
            </p>
          )}
        </div>
      )}

      {metaError && (
        <div className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="text-xs">
            <p className="font-medium text-destructive">{metaError.error}</p>
            {metaError.suggestion && (
              <p className="mt-1 text-muted-foreground">{metaError.suggestion}</p>
            )}
          </div>
        </div>
      )}

      {metaOk && (
        <div className="flex gap-3 rounded-lg border border-border/40 bg-muted/20 p-3">
          {metaOk.thumbnail_url && (
            // eslint-disable-next-line @next/next/no-img-element -- remote thumb, no loader wanted
            <img
              src={metaOk.thumbnail_url}
              alt=""
              className="h-16 w-28 shrink-0 rounded object-cover"
            />
          )}
          <div className="min-w-0 text-xs">
            <p className="truncate font-medium">{metaOk.title}</p>
            <p className="text-muted-foreground">{metaOk.channel_name}</p>
            <p className="mt-1 text-muted-foreground">
              {metaOk.duration ? formatTimecode(metaOk.duration) : "—"}
              {metaOk.width ? ` · ${metaOk.width}×${metaOk.height}` : ""}
              {metaOk.fps ? ` · ${Math.round(metaOk.fps)}fps` : ""}
              {metaOk.estimated_size_formatted ? ` · ${metaOk.estimated_size_formatted}` : ""}
            </p>
          </div>
        </div>
      )}

      <Separator />

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label className="text-xs">Clip length</Label>
          <div className="flex flex-wrap gap-2">
            {LENGTH_PRESETS.map((p) => (
              <Pill
                key={p.id}
                active={lengthPreset === p.id}
                onClick={() => setLengthPreset(p.id)}
              >
                {p.label}
              </Pill>
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground">
            {LENGTH_PRESETS.find((p) => p.id === lengthPreset)?.hint}
          </p>
        </div>

        <div className="space-y-2">
          <Label className="text-xs">How many clips</Label>
          <Input
            type="number"
            min={1}
            max={20}
            value={clipCount}
            onChange={(e) =>
              setClipCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))
            }
            className="w-24"
          />
          <p className="text-[11px] text-muted-foreground">
            The best {clipCount} moments, ranked. Nothing exports until you approve it.
          </p>
        </div>
      </div>

      <button
        type="button"
        onClick={() => setAdvanced((v) => !v)}
        className="text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      >
        {advanced ? "Hide options" : "More options"}
      </button>

      {advanced && (
        <div className="space-y-4 rounded-lg border border-border/40 p-4">
          <div className="space-y-2">
            <Label className="text-xs">Target platform</Label>
            <div className="flex flex-wrap gap-2">
              {PLATFORMS.map((p) => (
                <Pill key={p.id} active={platform === p.id} onClick={() => setPlatform(p.id)}>
                  {p.label}
                </Pill>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-xs">Spoken language</Label>
            <div className="flex flex-wrap gap-2">
              {LANGUAGES.map((l) => (
                <Pill key={l.id} active={language === l.id} onClick={() => setLanguage(l.id)}>
                  {l.label}
                </Pill>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-xs">Layout</Label>
            <div className="flex flex-wrap gap-2">
              {LAYOUT_CHOICES.map((l) => (
                <Pill key={l} active={layout === l} onClick={() => setLayout(l)}>
                  {LAYOUT_LABELS[l]}
                </Pill>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              Auto-detect picks a facecam split for gaming and a tracked crop otherwise. You can
              change it per clip afterwards.
            </p>
          </div>
        </div>
      )}

      <Separator />

      <label className="flex cursor-pointer items-start gap-2.5 text-xs">
        <input
          type="checkbox"
          checked={rights}
          onChange={(e) => setRights(e.target.checked)}
          className="mt-0.5 h-4 w-4 accent-emerald-500"
        />
        <span className="text-muted-foreground">
          I own this content or have permission to process it.
        </span>
      </label>

      <Button
        onClick={() => void submit()}
        disabled={!hasSource || !rights || submitting}
        className="w-full"
      >
        {submitting ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Creating…
          </>
        ) : (
          "Create project"
        )}
      </Button>
    </Card>
  );
}
