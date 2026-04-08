"use client";

import { useState, useRef, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, THUMBNAIL_URL, VIDEO_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { ArrowLeft, Download, Scissors, MonitorPlay, Film, ArrowRight, Palette, ChevronDown, ChevronUp, Maximize } from "lucide-react";
import { toast } from "sonner";
import type { Clip, Project, TranscriptSegment } from "@/types";

export default function EditorPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const clipId = params.id as string;

  const [startTime, setStartTime] = useState<number>(0);
  const [endTime, setEndTime] = useState<number>(0);
  const [reframeMode, setReframeMode] = useState<string>("auto");
  const [captionPreset, setCaptionPreset] = useState<string>("bold_impact");
  const [hookText, setHookText] = useState<string>("");
  const [captionOverrideText, setCaptionOverrideText] = useState<string>("");
  const [originalCaptionSegments, setOriginalCaptionSegments] = useState<TranscriptSegment[] | null>(null);
  const [autoWords, setAutoWords] = useState<Array<{ word: string; start: number; end: number }>>([]);
  const [currentCaptionGroup, setCurrentCaptionGroup] = useState<string[]>([]);
  const [currentCaptionWord, setCurrentCaptionWord] = useState<string | null>(null);
  const [previewElapsed, setPreviewElapsed] = useState<number>(0);

  // Style override states
  const [captionFontSize, setCaptionFontSize] = useState<number>(72);
  const [captionTextColor, setCaptionTextColor] = useState<string>("#FFFFFF");
  const [captionHighlightColor, setCaptionHighlightColor] = useState<string>("#FFD700");
  const [captionOutlineColor, setCaptionOutlineColor] = useState<string>("#000000");
  const [hookFontSize, setHookFontSize] = useState<number>(46);
  const [hookTextColor, setHookTextColor] = useState<string>("#FFFFFF");
  const [hookBgColor, setHookBgColor] = useState<string>("#0A0A0A");
  const [styleOverridesOpen, setStyleOverridesOpen] = useState<boolean>(false);
  const [exportResolution, setExportResolution] = useState<string>("1080x1920");
  const [previewMode, setPreviewMode] = useState<"9:16" | "16:9" | "original">("9:16");

  // Preset defaults for syncing style overrides when preset changes
  const PRESET_DEFAULTS: Record<string, { fontSize: number; textColor: string; highlightColor: string; outlineColor: string }> = {
    bold_impact:     { fontSize: 72, textColor: "#FFFFFF", highlightColor: "#FFD700", outlineColor: "#000000" },
    clean_minimal:   { fontSize: 62, textColor: "#FFFFFF", highlightColor: "#00D4FF", outlineColor: "#000000" },
    neon_pop:        { fontSize: 74, textColor: "#FFFFFF", highlightColor: "#FF3366", outlineColor: "#1A0033" },
    classic_white:   { fontSize: 62, textColor: "#FFFFFF", highlightColor: "#FFFFFF", outlineColor: "#000000" },
    karaoke_yellow:  { fontSize: 68, textColor: "#FFFFFF", highlightColor: "#FFE600", outlineColor: "#000000" },
    boxed_white:     { fontSize: 64, textColor: "#FFFFFF", highlightColor: "#FFFFFF", outlineColor: "#000000" },
    viral_gradient:  { fontSize: 76, textColor: "#FFFFFF", highlightColor: "#FF6B35", outlineColor: "#000000" },
  };

  const videoRef = useRef<HTMLVideoElement>(null);
  const bgVideoRef = useRef<HTMLVideoElement>(null);
  const lastCaptionUpdateRef = useRef<number>(0);

  const { data: clip, isLoading: clipLoading } = useQuery({
    queryKey: ["clip", clipId],
    queryFn: () => api.clips.get(clipId),
  });

  const { data: project } = useQuery({
    queryKey: ["project", clip?.project_id],
    queryFn: () => api.projects.get(clip!.project_id),
    enabled: !!clip?.project_id,
  });

  // Initialize editor state when clip data first arrives.
  // Use a ref to guard against re-init on every re-render.
  const initializedRef = useRef(false);
  useEffect(() => {
    if (!clip || initializedRef.current) return;
    initializedRef.current = true;

    setStartTime(clip.start_time);
    setEndTime(clip.end_time);
    if (clip.reframe_mode) setReframeMode(clip.reframe_mode);
    if (clip.caption_preset_id) setCaptionPreset(clip.caption_preset_id);
    if (clip.hook_text) setHookText(clip.hook_text);
    setCaptionOverrideText("");

    // Initialize style overrides from clip data (or fall back to preset defaults)
    const presetId = clip.caption_preset_id || "bold_impact";
    const defaults = PRESET_DEFAULTS[presetId] || PRESET_DEFAULTS.bold_impact;
    setCaptionFontSize(clip.caption_font_size || defaults.fontSize);
    setCaptionTextColor(clip.caption_text_color || defaults.textColor);
    setCaptionHighlightColor(clip.caption_highlight_color || defaults.highlightColor);
    setCaptionOutlineColor(clip.caption_outline_color || defaults.outlineColor);
    setHookFontSize(clip.hook_font_size || 46);
    setHookTextColor(clip.hook_text_color || "#FFFFFF");
    setHookBgColor(clip.hook_bg_color || "#0A0A0A");
    if (clip.export_resolution) setExportResolution(clip.export_resolution);

    // Prepare caption preview data (word timestamps when available).
    try {
      const segs = (clip.transcript_segments || []) as TranscriptSegment[];
      setOriginalCaptionSegments(segs);
      const words: Array<{ word: string; start: number; end: number }> = [];
      for (const seg of segs) {
        const ws = seg?.words || [];
        for (const w of ws) {
          if (typeof w?.start === "number" && typeof w?.end === "number" && w?.word) {
            words.push({ word: String(w.word), start: Number(w.start), end: Number(w.end) });
          }
        }
      }
      setAutoWords(words);
    } catch {
      setOriginalCaptionSegments(null);
      setAutoWords([]);
    }
  }, [clip]);

  const updateMutation = useMutation({
    mutationFn: (data: Partial<Clip>) => api.clips.update(clipId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["clip", clipId] });
      toast.success("Clip settings saved");
    },
  });

  const handlePresetChange = (presetId: string) => {
    setCaptionPreset(presetId);
    const defaults = PRESET_DEFAULTS[presetId] || PRESET_DEFAULTS.bold_impact;
    setCaptionFontSize(defaults.fontSize);
    setCaptionTextColor(defaults.textColor);
    setCaptionHighlightColor(defaults.highlightColor);
    setCaptionOutlineColor(defaults.outlineColor);
  };

  const exportMutation = useMutation({
    mutationFn: () => api.clips.export(clipId),
    onSuccess: (data) => {
      toast.success("Export started", {
        description: "Your clip is rendering in the background.",
      });
      router.push(`/projects/${clip?.project_id}`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  if (clipLoading || !clip) return <div className="p-8">Loading...</div>;

  const handleSave = () => {
    const override = captionOverrideText.trim();

    updateMutation.mutate({
      start_time: startTime,
      end_time: endTime,
      reframe_mode: reframeMode,
      caption_preset_id: captionPreset,
      hook_text: hookText,
      caption_font_size: captionFontSize,
      caption_text_color: captionTextColor,
      caption_highlight_color: captionHighlightColor,
      caption_outline_color: captionOutlineColor,
      hook_font_size: hookFontSize,
      hook_text_color: hookTextColor,
      hook_bg_color: hookBgColor,
      export_resolution: exportResolution,
      // Caption override is exported by replacing the clip's caption segments.
      transcript_text: override ? override : (clip?.transcript_text ?? undefined),
      transcript_segments: override
        ? [{ start: startTime, end: endTime, text: override }]
        : originalCaptionSegments ?? undefined,
    });
  };

  const handleExportNow = async () => {
    const override = captionOverrideText.trim();
    try {
      await updateMutation.mutateAsync({
        start_time: startTime,
        end_time: endTime,
        reframe_mode: reframeMode,
        caption_preset_id: captionPreset,
        hook_text: hookText,
        caption_font_size: captionFontSize,
        caption_text_color: captionTextColor,
        caption_highlight_color: captionHighlightColor,
        caption_outline_color: captionOutlineColor,
        hook_font_size: hookFontSize,
        hook_text_color: hookTextColor,
        hook_bg_color: hookBgColor,
        export_resolution: exportResolution,
        transcript_text: override ? override : (clip?.transcript_text ?? undefined),
        transcript_segments: override
          ? [{ start: startTime, end: endTime, text: override }]
          : originalCaptionSegments ?? undefined,
      });
      await exportMutation.mutateAsync();
    } catch (err) {
      const e = err as { message?: string };
      toast.error(e?.message || "Export failed");
    }
  };

  const sourceVideoUrl = VIDEO_URL(project?.id || "", project?.video_path);

  return (
    <div className="flex flex-col lg:flex-row gap-6 lg:h-[calc(100vh-8rem)]">

      {/* LEFT PANEL - PREVIEW */}
      <div className="flex-1 min-h-[60vh] lg:min-h-0 rounded-xl bg-black border border-border/40 relative overflow-hidden flex flex-col">
        <div className="h-12 border-b border-border/30 px-4 flex items-center justify-between text-muted-foreground z-10 bg-black/50 backdrop-blur-md">
          <Button variant="ghost" size="sm" onClick={() => router.back()} className="gap-2">
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>
          <div className="flex items-center gap-1">
            {(["9:16", "16:9", "original"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setPreviewMode(mode)}
                className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                  previewMode === mode
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted/60"
                }`}
              >
                {mode === "original" ? "Original" : mode}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 relative flex items-center justify-center p-4">
          <div className={`relative overflow-hidden rounded-lg border border-border/40 bg-black ${
            previewMode === "9:16" ? "h-full max-h-full aspect-[9/16]" :
            previewMode === "16:9" ? "w-full aspect-[16/9]" :
            "w-full aspect-video"
          }`}>
            {/* Blurred background (preview-only). Export uses the backend reframe mode. */}
            {reframeMode === "blurred" && (
              // eslint-disable-next-line @next/next/no-img-element
              <video
                ref={bgVideoRef}
                src={sourceVideoUrl}
                muted
                playsInline
                preload="metadata"
                className="absolute inset-0 w-full h-full object-cover blur-2xl scale-110 pointer-events-none"
              />
            )}

            {/* Foreground subject preview */}
            <video
              ref={videoRef}
              src={sourceVideoUrl}
              controls
              playsInline
              preload="metadata"
              className="absolute inset-0 w-full h-full object-contain"
              onLoadedMetadata={() => {
                if (videoRef.current && startTime > 0) {
                  videoRef.current.currentTime = startTime;
                }
                if (bgVideoRef.current && startTime > 0) {
                  bgVideoRef.current.currentTime = startTime;
                }
              }}
              onPlay={() => {
                if (bgVideoRef.current && reframeMode === "blurred") bgVideoRef.current.play();
              }}
              onPause={() => {
                if (bgVideoRef.current) bgVideoRef.current.pause();
              }}
              onTimeUpdate={() => {
                const fg = videoRef.current;
                if (!fg) return;

                const tAbs = fg.currentTime;
                const clipDur = Math.max(endTime - startTime, 0.001);

                if (tAbs >= endTime) {
                  fg.currentTime = startTime;
                  if (bgVideoRef.current) bgVideoRef.current.currentTime = startTime;
                }

                // Keep background in sync for blurred preview.
                if (bgVideoRef.current && reframeMode === "blurred") {
                  bgVideoRef.current.currentTime = fg.currentTime;
                }

                const t = Math.min(Math.max(fg.currentTime, startTime), endTime);
                const elapsed = t - startTime;

                // Hook overlay for the first few seconds.
                const shouldShowHook = hookText.trim().length > 0 && elapsed <= 4.0;
                if (shouldShowHook) {
                  // Force rerender by updating caption group (handled below); hook uses currentTime state.
                }

                // Update caption highlight (~10fps max) for a usable preview.
                const now = Date.now();
                if (now - lastCaptionUpdateRef.current < 100) return;
                lastCaptionUpdateRef.current = now;
                setPreviewElapsed(elapsed);

                const maxWordsPerLine =
                  captionPreset === "neon_pop" || captionPreset === "viral_gradient" ? 2
                    : captionPreset === "clean_minimal" ? 4
                    : captionPreset === "classic_white" ? 5
                    : 3;

                const override = captionOverrideText.trim();
                if (override) {
                  const tokens = override.split(/\s+/).filter(Boolean);
                  if (!tokens.length) {
                    setCurrentCaptionGroup([]);
                    setCurrentCaptionWord(null);
                    return;
                  }
                  const ratio = (t - startTime) / clipDur;
                  const idx = Math.min(Math.max(Math.floor(ratio * tokens.length), 0), tokens.length - 1);
                  const word = tokens[idx];
                  const groupStart = Math.max(0, idx - (maxWordsPerLine - 1));
                  const group = tokens.slice(groupStart, idx + 1);
                  setCurrentCaptionGroup(group);
                  setCurrentCaptionWord(word);
                  return;
                }

                // Auto captions: word timestamps available from transcription (when present).
                if (!autoWords.length) {
                  setCurrentCaptionGroup([]);
                  setCurrentCaptionWord(null);
                  return;
                }

                let idxFound = -1;
                for (let i = 0; i < autoWords.length; i++) {
                  const w = autoWords[i];
                  if (w.start <= t && w.end >= t) {
                    idxFound = i;
                    break;
                  }
                }
                if (idxFound === -1) {
                  setCurrentCaptionGroup([]);
                  setCurrentCaptionWord(null);
                  return;
                }

                const word = autoWords[idxFound]?.word ?? "";
                const groupStart = Math.max(0, idxFound - (maxWordsPerLine - 1));
                const group = autoWords.slice(groupStart, idxFound + 1).map((w) => w.word);
                setCurrentCaptionGroup(group);
                setCurrentCaptionWord(word);
              }}
            />

            {/* Overlays */}
            <div className="absolute inset-0 pointer-events-none">
              {/* Hook box uses current video time */}
              {hookText.trim().length > 0 && previewElapsed <= 4.0 && (
                <div
                  className="absolute top-[32%] left-1/2 -translate-x-1/2 max-w-[82%] px-7 py-5 rounded-2xl border border-white/8 shadow-2xl backdrop-blur-sm"
                  style={{ backgroundColor: hookBgColor + "F2" }}
                >
                  <div
                    className="leading-snug font-bold text-center"
                    style={{ color: hookTextColor, fontSize: `${Math.round(hookFontSize * 0.37)}px` }}
                  >
                    {hookText}
                  </div>
                </div>
              )}

              {/* Captions */}
              {currentCaptionGroup.length > 0 && currentCaptionWord && (
                <div className="absolute bottom-[26%] left-0 right-0 px-6">
                  <div
                    className="font-extrabold tracking-wide text-center"
                    style={{
                      fontSize: `${Math.round(captionFontSize * 0.39)}px`,
                      color: captionTextColor,
                      textShadow: `0 2px 8px ${captionOutlineColor}E6, 0 0 2px ${captionOutlineColor}CC`,
                    }}
                  >
                    {currentCaptionGroup.map((w, i) => (
                      <span
                        key={`${w}-${i}`}
                        className="px-1"
                        style={
                          w === currentCaptionWord
                            ? { color: captionHighlightColor, transform: "scale(1.05)", display: "inline-block" }
                            : { opacity: 0.9 }
                        }
                      >
                        {w}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* TIMELINE CONTROL */}
        <div className="h-28 border-t border-border/30 bg-card/60 backdrop-blur-md p-4 flex flex-col gap-2">
          <div className="flex justify-between items-center px-1">
            <span className="font-mono text-xs text-primary">{startTime.toFixed(2)}s</span>
            <span className="font-semibold text-sm">Trim Clip ({ (endTime - startTime).toFixed(1) }s)</span>
            <span className="font-mono text-xs text-primary">{endTime.toFixed(2)}s</span>
          </div>
          <Slider 
            value={[startTime, endTime]} 
            min={0} 
            max={project?.duration || endTime + 60} 
            step={0.1}
            onValueChange={(val: number | readonly number[]) => {
              const arr = Array.isArray(val) ? val : [val, val];
              setStartTime(arr[0]);
              setEndTime(arr[1] || arr[0]);
              if (videoRef.current) videoRef.current.currentTime = arr[0];
              if (bgVideoRef.current && reframeMode === "blurred") bgVideoRef.current.currentTime = arr[0];
            }}
            className="mt-2"
          />
        </div>
      </div>

      {/* RIGHT PANEL - SETTINGS */}
      <div className="w-full lg:w-[380px] lg:max-h-[calc(100vh-8rem)] border border-border/40 rounded-xl bg-card/40 flex flex-col shadow-lg overflow-y-auto">
        <div className="p-5 border-b border-border/30 sticky top-0 bg-card z-10">
          <h2 className="text-lg font-bold">Clip Settings</h2>
          <p className="text-xs text-muted-foreground mt-1 truncate">{clip.title}</p>
        </div>

        <div className="p-5 space-y-8 flex-1">
          {/* Hook Text Editor */}
          <div className="space-y-3">
            <Label className="flex items-center gap-2"><Scissors className="h-4 w-4 text-primary" /> Auto Hook Text</Label>
            <div className="text-[10px] text-muted-foreground mb-1">
              This text appears as a large bold box in the first 5 seconds to hook viewers.
            </div>
            <Input 
              value={hookText}
              onChange={(e) => setHookText(e.target.value)}
              placeholder="e.g. This changes everything..."
              className="bg-card w-full"
            />
          </div>

          {/* Caption Override */}
          <div className="space-y-3">
            <Label className="flex items-center gap-2">
              <Film className="h-4 w-4 text-primary" /> Caption Override (Optional)
            </Label>
            <div className="text-[10px] text-muted-foreground mb-1">
              Leave empty to use auto captions. If filled, exports use this text for the clip.
            </div>
            <textarea
              value={captionOverrideText}
              onChange={(e) => setCaptionOverrideText(e.target.value)}
              placeholder="Paste a custom caption text for this clip..."
              className="w-full min-h-28 rounded-md border border-border/60 bg-card px-3 py-2 text-sm outline-none focus:border-primary/60"
            />
          </div>

          {/* Framer Mode */}
          <div className="space-y-4">
            <Label className="flex items-center gap-2"><MonitorPlay className="h-4 w-4 text-primary" /> Vertical Reframe Mode</Label>
            <RadioGroup value={reframeMode} onValueChange={setReframeMode} className="grid grid-cols-1 gap-3">
              <Label className={`border rounded-lg p-3 cursor-pointer flex items-center justify-between transition-colors ${reframeMode === "auto" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <div>
                  <div className="font-medium text-sm">Auto (AI Tracking)</div>
                  <div className="text-xs text-muted-foreground">Follows the primary speaker using facial recognition.</div>
                </div>
                <RadioGroupItem value="auto" />
              </Label>
              <Label className={`border rounded-lg p-3 cursor-pointer flex items-center justify-between transition-colors ${reframeMode === "center" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <div>
                  <div className="font-medium text-sm">Center Crop</div>
                  <div className="text-xs text-muted-foreground">Static dead-center cut without moving.</div>
                </div>
                <RadioGroupItem value="center" />
              </Label>
              <Label className={`border rounded-lg p-3 cursor-pointer flex items-center justify-between transition-colors ${reframeMode === "blurred" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <div>
                  <div className="font-medium text-sm">Blurred Background</div>
                  <div className="text-xs text-muted-foreground">Sharp subject with a blurred backdrop.</div>
                </div>
                <RadioGroupItem value="blurred" />
              </Label>
            </RadioGroup>
          </div>

          {/* Caption Style */}
          <div className="space-y-4">
            <Label className="flex items-center gap-2"><Film className="h-4 w-4 text-primary"/> Caption Style</Label>
            <RadioGroup value={captionPreset} onValueChange={handlePresetChange} className="grid grid-cols-3 gap-3">
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "bold_impact" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-black italic block text-lg uppercase tracking-wider text-yellow-500" style={{ textShadow: "1px 1px 0 #000" }}>BOLD</span>
                <RadioGroupItem value="bold_impact" className="sr-only" />
              </Label>
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "clean_minimal" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-semibold block text-sm text-white/90">Clean</span>
                <RadioGroupItem value="clean_minimal" className="sr-only" />
              </Label>
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "neon_pop" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-extrabold uppercase text-pink-500 drop-shadow-[0_0_8px_rgba(236,72,153,0.8)]">NEON</span>
                <RadioGroupItem value="neon_pop" className="sr-only" />
              </Label>
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "classic_white" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-bold block text-white">Classic</span>
                <RadioGroupItem value="classic_white" className="sr-only" />
              </Label>
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "karaoke_yellow" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-black block text-yellow-400" style={{ textShadow: "0 0 8px rgba(234,179,8,0.5)" }}>KARAOKE</span>
                <RadioGroupItem value="karaoke_yellow" className="sr-only" />
              </Label>
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "boxed_white" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-bold block text-sm text-white bg-black/80 px-2 py-1 rounded">Boxed</span>
                <RadioGroupItem value="boxed_white" className="sr-only" />
              </Label>
              <Label className={`border rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer text-center h-24 transition-colors ${captionPreset === "viral_gradient" ? "bg-primary/10 border-primary" : "border-border/60 hover:bg-muted/50"}`}>
                <span className="font-black block text-orange-500 uppercase" style={{ textShadow: "1px 1px 0 #000" }}>VIRAL</span>
                <RadioGroupItem value="viral_gradient" className="sr-only" />
              </Label>
            </RadioGroup>
          </div>

          {/* Style Overrides */}
          <div className="space-y-3">
            <button
              type="button"
              onClick={() => setStyleOverridesOpen(!styleOverridesOpen)}
              className="flex items-center gap-2 w-full text-sm font-semibold text-left"
            >
              <Palette className="h-4 w-4 text-primary" />
              Style Overrides
              {styleOverridesOpen ? <ChevronUp className="h-3 w-3 ml-auto" /> : <ChevronDown className="h-3 w-3 ml-auto" />}
            </button>
            <p className="text-[10px] text-muted-foreground">Fine-tune colors and sizes. Presets set the defaults; tweak them here.</p>

            {styleOverridesOpen && (
              <div className="space-y-4 pt-1">
                {/* Caption overrides */}
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Captions</p>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Font Size ({captionFontSize})</Label>
                    <Slider
                      value={[captionFontSize]}
                      min={32}
                      max={120}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setCaptionFontSize(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Text Color</Label>
                    <input type="color" value={captionTextColor} onChange={(e) => setCaptionTextColor(e.target.value)} className="w-8 h-8 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Highlight Color</Label>
                    <input type="color" value={captionHighlightColor} onChange={(e) => setCaptionHighlightColor(e.target.value)} className="w-8 h-8 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Outline Color</Label>
                    <input type="color" value={captionOutlineColor} onChange={(e) => setCaptionOutlineColor(e.target.value)} className="w-8 h-8 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                </div>

                {/* Hook overrides */}
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Hook Box</p>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Font Size ({hookFontSize})</Label>
                    <Slider
                      value={[hookFontSize]}
                      min={24}
                      max={80}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setHookFontSize(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Text Color</Label>
                    <input type="color" value={hookTextColor} onChange={(e) => setHookTextColor(e.target.value)} className="w-8 h-8 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Background</Label>
                    <input type="color" value={hookBgColor} onChange={(e) => setHookBgColor(e.target.value)} className="w-8 h-8 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Export Resolution */}
          <div className="space-y-3">
            <Label className="flex items-center gap-2"><Maximize className="h-4 w-4 text-primary" /> Export Resolution</Label>
            <div className="grid grid-cols-2 gap-2">
              {[
                { value: "1080x1920", label: "1080×1920", desc: "Full HD 9:16" },
                { value: "1440x2560", label: "1440×2560", desc: "2K 9:16" },
                { value: "2160x3840", label: "2160×3840", desc: "4K 9:16" },
                { value: "720x1280", label: "720×1280", desc: "HD 9:16" },
                { value: "1920x1080", label: "1920×1080", desc: "Full HD 16:9" },
                { value: "2560x1440", label: "2560×1440", desc: "2K 16:9" },
                { value: "3840x2160", label: "3840×2160", desc: "4K 16:9" },
                { value: "540x960", label: "540×960", desc: "SD 9:16" },
              ].map((res) => (
                <button
                  key={res.value}
                  type="button"
                  onClick={() => setExportResolution(res.value)}
                  className={`border rounded-lg p-2 text-left transition-colors ${
                    exportResolution === res.value
                      ? "bg-primary/10 border-primary"
                      : "border-border/60 hover:bg-muted/50"
                  }`}
                >
                  <div className="text-xs font-medium">{res.label}</div>
                  <div className="text-[10px] text-muted-foreground">{res.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <Button variant="secondary" className="w-full text-xs" onClick={handleSave}>
            Save Configuration
          </Button>

        </div>

        <div className="p-5 border-t border-border/30 bg-muted/20 sticky bottom-0">
          <Button 
            className="w-full gap-2 text-md font-bold shadow-lg shadow-primary/20 py-6"
            onClick={handleExportNow}
            disabled={exportMutation.isPending || updateMutation.isPending}
          >
            {exportMutation.isPending ? "Starting Export..." : "Generate Vertical Clip"}
            {!exportMutation.isPending && <ArrowRight className="h-5 w-5" />}
          </Button>
          <p className="text-[10px] text-center text-muted-foreground mt-3">Renders a {exportResolution} MP4 file with burnt-in captions.</p>
        </div>

      </div>
    </div>
  );
}
