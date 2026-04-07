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
import { ArrowLeft, Download, Scissors, MonitorPlay, Film, ArrowRight } from "lucide-react";
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
          <span className="text-xs font-medium">Vertical Preview (9:16)</span>
        </div>
        
        <div className="flex-1 relative flex items-center justify-center p-4">
          <div className="relative w-full aspect-[9/16] overflow-hidden rounded-lg border border-border/40 bg-black">
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
              className="absolute inset-0 w-full h-full object-cover"
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
                <div className="absolute top-[32%] left-1/2 -translate-x-1/2 max-w-[82%] px-7 py-5 rounded-2xl bg-[#0D0D0D]/95 border border-white/8 shadow-2xl backdrop-blur-sm">
                  <div className="text-[17px] leading-snug font-bold text-white text-center">
                    {hookText}
                  </div>
                </div>
              )}

              {/* Captions */}
              {currentCaptionGroup.length > 0 && currentCaptionWord && (
                <div className="absolute bottom-[26%] left-0 right-0 px-6">
                  <div
                    className="text-[28px] font-extrabold tracking-wide text-white text-center"
                    style={{ textShadow: "0 2px 8px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.8)" }}
                  >
                    {currentCaptionGroup.map((w, i) => (
                      <span
                        key={`${w}-${i}`}
                        className={
                          w === currentCaptionWord
                            ? captionPreset === "clean_minimal"
                              ? "px-2 py-1 rounded text-cyan-400"
                              : captionPreset === "neon_pop"
                                ? "px-2 py-1 rounded bg-pink-500 text-white drop-shadow-[0_0_6px_rgba(236,72,153,0.7)]"
                                : captionPreset === "classic_white"
                                  ? "px-2 py-1 rounded"
                                  : captionPreset === "karaoke_yellow"
                                    ? "px-2 py-1 rounded text-yellow-400"
                                    : captionPreset === "boxed_white"
                                      ? "px-2 py-1 rounded bg-black/80 text-white"
                                      : captionPreset === "viral_gradient"
                                        ? "px-2 py-1 rounded text-orange-500"
                                        : "px-2 py-1 rounded bg-yellow-400 text-black"
                            : "mx-1 opacity-90"
                        }
                        style={
                          w !== currentCaptionWord
                            ? { textShadow: "0 1px 4px rgba(0,0,0,0.8)" }
                            : undefined
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
            <RadioGroup value={captionPreset} onValueChange={setCaptionPreset} className="grid grid-cols-3 gap-3">
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
          <p className="text-[10px] text-center text-muted-foreground mt-3">Renders a 1080x1920 MP4 file with burnt-in captions.</p>
        </div>

      </div>
    </div>
  );
}
