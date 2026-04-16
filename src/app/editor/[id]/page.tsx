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
import { ArrowLeft, Download, Scissors, MonitorPlay, Film, ArrowRight, Palette, ChevronDown, ChevronUp, Maximize, SplitSquareHorizontal, Tag, Type, Move, Eye } from "lucide-react";
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
  const [hookBgEnabled, setHookBgEnabled] = useState<boolean>(true);
  const [hookBoxSize, setHookBoxSize] = useState<number>(24);
  const [hookBoxWidth, setHookBoxWidth] = useState<number>(24);
  const [hookDurationSeconds, setHookDurationSeconds] = useState<number>(4);
  const [hookX, setHookX] = useState<number>(50);
  const [hookY, setHookY] = useState<number>(32);
  const [subtitleX, setSubtitleX] = useState<number>(50);
  const [subtitleY, setSubtitleY] = useState<number>(74);
  const [styleOverridesOpen, setStyleOverridesOpen] = useState<boolean>(false);
  const [exportResolution, setExportResolution] = useState<string>("1080x1920");
  const [previewMode, setPreviewMode] = useState<"9:16" | "16:9" | "original">("9:16");

  // Split settings (persisted for convenience, but surfaced only in export flow)
  const [splitMode, setSplitMode] = useState<string>("off");
  const [splitPartsCount, setSplitPartsCount] = useState<number>(2);

  // Title overlay settings (full_video_parts mode)
  const [titleText, setTitleText] = useState<string>("");
  const [titleFontSize, setTitleFontSize] = useState<number>(46);
  const [titleX, setTitleX] = useState<number>(50);
  const [titleY, setTitleY] = useState<number>(18);
  const [titleBoxSize, setTitleBoxSize] = useState<number>(24);
  const [titleBoxWidth, setTitleBoxWidth] = useState<number>(24);
  const [titleBgEnabled, setTitleBgEnabled] = useState<boolean>(true);

  // Part label settings
  const [partLabelFontSize, setPartLabelFontSize] = useState<number>(32);
  const [partLabelBoxSize, setPartLabelBoxSize] = useState<number>(14);
  const [partLabelTextColor, setPartLabelTextColor] = useState<string>("#FFFFFF");
  const [partLabelBgColor, setPartLabelBgColor] = useState<string>("#000000");
  const [partLabelX, setPartLabelX] = useState<number>(88);
  const [partLabelY, setPartLabelY] = useState<number>(10);

  // Creator tag (watermark) settings
  const [creatorTagEnabled, setCreatorTagEnabled] = useState<boolean>(false);
  const [creatorTagText, setCreatorTagText] = useState<string>("@yourhandle");
  const [creatorTagX, setCreatorTagX] = useState<number>(50);
  const [creatorTagY, setCreatorTagY] = useState<number>(92);
  const [creatorTagOpacity, setCreatorTagOpacity] = useState<number>(0.7);
  const [creatorTagFontSize, setCreatorTagFontSize] = useState<number>(32);

  // Destination: Google Drive folder link
  const [driveFolderLink, setDriveFolderLink] = useState<string>("");
  const [driveUploading, setDriveUploading] = useState<boolean>(false);

  // Section toggles
  const [positionSectionOpen, setPositionSectionOpen] = useState<boolean>(false);
  const [creatorTagOpen, setCreatorTagOpen] = useState<boolean>(false);
  // Export flow: show split settings panel in export area
  const [exportSplitOpen, setExportSplitOpen] = useState<boolean>(false);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Highlight bg color is mirrored from the active preset (some presets render the
  // highlighted word inside an opaque box — we mirror that faithfully in the preview).
  const [captionHighlightBgColor, setCaptionHighlightBgColor] = useState<string>("");

  // Preset defaults for syncing style overrides when preset changes.
  // These MUST stay in sync with DEFAULT_PRESETS in server/services/captioner.py.
  const PRESET_DEFAULTS: Record<string, { fontSize: number; textColor: string; highlightColor: string; outlineColor: string; highlightBgColor?: string }> = {
    bold_impact:     { fontSize: 72, textColor: "#FFFFFF", highlightColor: "#FFD700", outlineColor: "#000000" },
    clean_minimal:   { fontSize: 62, textColor: "#FFFFFF", highlightColor: "#00D4FF", outlineColor: "#000000" },
    neon_pop:        { fontSize: 74, textColor: "#FFFFFF", highlightColor: "#FF3366", outlineColor: "#1A0033" },
    classic_white:   { fontSize: 62, textColor: "#FFFFFF", highlightColor: "#FFFFFF", outlineColor: "#000000" },
    karaoke_yellow:  { fontSize: 68, textColor: "#FFFFFF", highlightColor: "#FFE600", outlineColor: "#000000", highlightBgColor: "#FFE600" },
    boxed_white:     { fontSize: 64, textColor: "#FFFFFF", highlightColor: "#FFFFFF", outlineColor: "#000000", highlightBgColor: "#000000" },
    viral_gradient:  { fontSize: 76, textColor: "#FFFFFF", highlightColor: "#FF6B35", outlineColor: "#000000" },
  };

  const videoRef = useRef<HTMLVideoElement>(null);
  const bgVideoRef = useRef<HTMLVideoElement>(null);
  const lastCaptionUpdateRef = useRef<number>(0);
  const bgSyncRafRef = useRef<number | null>(null);

  // Preview container ref + scale factor (container_height_px / 1920). Used to
  // scale font sizes and paddings so the preview matches the 1080x1920 export.
  const previewContainerRef = useRef<HTMLDivElement>(null);
  const [previewScale, setPreviewScale] = useState<number>(0.37);
  useEffect(() => {
    const el = previewContainerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const update = () => {
      const h = el.clientHeight;
      if (h > 0) setPreviewScale(h / 1920);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [previewMode, exportResolution]);

  // Compute whether the preview should render a blurred backdrop. Both the
  // explicit blurred reframe mode AND landscape export (which letterboxes into
  // a 9:16 container) fill the pad area with the same-source blur in the
  // exporter, so the editor mirrors that truth for a faithful preview.
  const needsBlurredBg = (() => {
    if (reframeMode === "blurred") return true;
    try {
      const [w, h] = (exportResolution || "1080x1920").split("x").map(Number);
      return w > h;
    } catch { return false; }
  })();

  // RAF-based sync loop: keep bgVideo.currentTime locked to foreground video
  useEffect(() => {
    if (!needsBlurredBg) return;
    let running = true;
    const sync = () => {
      if (!running) return;
      const fg = videoRef.current;
      const bg = bgVideoRef.current;
      if (fg && bg) {
        // Sync time only when drift exceeds threshold (avoids constant seeking)
        if (Math.abs(bg.currentTime - fg.currentTime) > 0.05) {
          bg.currentTime = fg.currentTime;
        }
        // Sync play state
        if (!fg.paused && bg.paused) bg.play().catch(() => {});
        if (fg.paused && !bg.paused) bg.pause();
      }
      bgSyncRafRef.current = requestAnimationFrame(sync);
    };
    bgSyncRafRef.current = requestAnimationFrame(sync);
    return () => {
      running = false;
      if (bgSyncRafRef.current) cancelAnimationFrame(bgSyncRafRef.current);
    };
  }, [needsBlurredBg]);

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

    const presetId = clip.caption_preset_id || "bold_impact";
    const defaults = PRESET_DEFAULTS[presetId] || PRESET_DEFAULTS.bold_impact;
    setCaptionFontSize(clip.caption_font_size || defaults.fontSize);
    setCaptionTextColor(clip.caption_text_color || defaults.textColor);
    setCaptionHighlightColor(clip.caption_highlight_color || defaults.highlightColor);
    setCaptionOutlineColor(clip.caption_outline_color || defaults.outlineColor);
    setCaptionHighlightBgColor(defaults.highlightBgColor || "");
    setHookFontSize(clip.hook_font_size || 46);
    setHookTextColor(clip.hook_text_color || "#FFFFFF");
    setHookBgColor(clip.hook_bg_color || "#0A0A0A");
    setHookBgEnabled(clip.hook_bg_enabled ?? true);
    setHookBoxSize(clip.hook_box_size || 24);
    setHookBoxWidth(clip.hook_box_width || clip.hook_box_size || 24);
    setHookDurationSeconds(clip.hook_duration_seconds || 4);
    setHookX(clip.hook_x ?? 50);
    setHookY(clip.hook_y ?? 32);
    setSubtitleX(clip.subtitle_x ?? 50);
    setSubtitleY(clip.subtitle_y ?? 74);
    if (clip.export_resolution) setExportResolution(clip.export_resolution);

    // Split settings
    setSplitMode(clip.split_mode || "off");
    setSplitPartsCount(clip.split_parts_count || 2);
    setPartLabelFontSize(clip.part_label_font_size || 32);
    setPartLabelBoxSize(clip.part_label_box_size || 14);
    setPartLabelTextColor(clip.part_label_text_color || "#FFFFFF");
    setPartLabelBgColor(clip.part_label_bg_color || "#000000");
    setPartLabelX(clip.part_label_x ?? 88);
    setPartLabelY(clip.part_label_y ?? 10);
    setTitleText(clip.title_text || "");
    setTitleFontSize(clip.title_font_size || 46);
    setTitleX(clip.title_x ?? 50);
    setTitleY(clip.title_y ?? 18);
    setTitleBoxSize(clip.title_box_size || 24);
    setTitleBoxWidth(clip.title_box_width || clip.title_box_size || 24);
    setTitleBgEnabled(clip.title_bg_enabled ?? true);

    // Creator tag hydration
    setCreatorTagEnabled(clip.creator_tag_enabled ?? false);
    setCreatorTagText(clip.creator_tag_text || "@yourhandle");
    setCreatorTagX(clip.creator_tag_x ?? 50);
    setCreatorTagY(clip.creator_tag_y ?? 92);
    setCreatorTagOpacity(clip.creator_tag_opacity ?? 0.7);
    setCreatorTagFontSize(clip.creator_tag_font_size ?? 32);
    if (clip.creator_tag_enabled) setCreatorTagOpen(true);

    // Destination link
    setDriveFolderLink(clip.drive_folder_link || "");

    // Auto-open export split panel if split was previously configured
    if (clip.split_mode && clip.split_mode !== "off") setExportSplitOpen(true);

    // Prepare caption preview data
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
    setCaptionHighlightBgColor(defaults.highlightBgColor || "");
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

  // Compute split info for preview. No max per-part duration cap — the user
  // picks how many parts they want and that's what they get.
  const clipDuration = endTime - startTime;
  const computeSplitParts = (): number => {
    if (splitMode === "off") return 1;
    if (splitMode === "auto") {
      // Sensible default: ~60s per part, clamped to [2, 20]
      const n = Math.max(2, Math.ceil(clipDuration / 60));
      return Math.min(20, n);
    }
    if (splitMode === "manual") {
      return Math.max(1, splitPartsCount);
    }
    return 1;
  };
  const effectiveParts = computeSplitParts();

  // Check if export is landscape (letterbox mode)
  const isLandscapeExport = (() => {
    try {
      const [w, h] = exportResolution.split("x").map(Number);
      return w > h;
    } catch { return false; }
  })();

  const buildSavePayload = () => {
    const override = captionOverrideText.trim();
    return {
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
      hook_bg_enabled: hookBgEnabled,
      hook_box_size: hookBoxSize,
      hook_box_width: hookBoxWidth,
      hook_duration_seconds: hookDurationSeconds,
      hook_x: hookX,
      hook_y: hookY,
      subtitle_x: subtitleX,
      subtitle_y: subtitleY,
      export_resolution: exportResolution,
      split_mode: splitMode,
      split_parts_count: splitPartsCount,
      part_label_font_size: partLabelFontSize,
      part_label_box_size: partLabelBoxSize,
      part_label_text_color: partLabelTextColor,
      part_label_bg_color: partLabelBgColor,
      part_label_x: partLabelX,
      part_label_y: partLabelY,
      title_text: titleText,
      title_font_size: titleFontSize,
      title_x: titleX,
      title_y: titleY,
      title_box_size: titleBoxSize,
      title_box_width: titleBoxWidth,
      title_bg_enabled: titleBgEnabled,
      creator_tag_enabled: creatorTagEnabled,
      creator_tag_text: creatorTagText,
      creator_tag_x: creatorTagX,
      creator_tag_y: creatorTagY,
      creator_tag_opacity: creatorTagOpacity,
      creator_tag_font_size: creatorTagFontSize,
      drive_folder_link: driveFolderLink || null,
      transcript_text: override ? override : (clip?.transcript_text ?? undefined),
      transcript_segments: override
        ? [{ start: startTime, end: endTime, text: override }]
        : originalCaptionSegments ?? undefined,
    };
  };

  const handleSave = () => {
    updateMutation.mutate(buildSavePayload());
  };

  const handlePreviewFinal = async () => {
    try {
      setPreviewLoading(true);
      // Save settings first so backend renders with current config
      await updateMutation.mutateAsync(buildSavePayload());
      // Open the preview endpoint in a new tab — the backend renders and streams the MP4
      const previewUrl = api.clips.previewUrl(clipId);
      window.open(previewUrl, "_blank");
      toast.success("Preview rendering — a new tab will open with the video");
    } catch (err) {
      const e = err as { message?: string };
      toast.error(e?.message || "Preview failed");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleExportNow = async () => {
    try {
      await updateMutation.mutateAsync(buildSavePayload());
      await exportMutation.mutateAsync();
    } catch (err) {
      const e = err as { message?: string };
      toast.error(e?.message || "Export failed");
    }
  };

  const sourceVideoUrl = VIDEO_URL(project?.id || "", project?.video_path);
  const isFullVideoMode = (project?.processing_mode || "clipping") === "full_video_parts";

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
          {/* Preview container: always 9:16 aspect when landscape export selected */}
          <div
            ref={previewContainerRef}
            className={`relative overflow-hidden rounded-lg border border-border/40 bg-black ${
              previewMode === "9:16" || isLandscapeExport ? "h-full max-h-full aspect-[9/16]" :
              previewMode === "16:9" ? "w-full aspect-[16/9]" :
              "w-full aspect-video"
            }`}
          >
            {/* Blurred background — mirrors the exporter's split/overlay path.
                Shown whenever the real render will include a blurred fill: either
                explicit blurred reframe mode OR landscape export (letterbox). */}
            {needsBlurredBg && (
              <video
                ref={bgVideoRef}
                src={sourceVideoUrl}
                muted
                playsInline
                preload="metadata"
                className="absolute inset-0 w-full h-full object-cover scale-125 pointer-events-none"
                style={{ filter: "blur(40px) brightness(0.85)" }}
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
                // RAF sync loop handles bg play/pause, but kickstart immediately for responsiveness
                if (bgVideoRef.current && needsBlurredBg) bgVideoRef.current.play().catch(() => {});
              }}
              onPause={() => {
                if (bgVideoRef.current) bgVideoRef.current.pause();
              }}
              onSeeked={() => {
                // Immediate sync on seek for responsiveness (RAF loop is backup)
                if (bgVideoRef.current && needsBlurredBg && videoRef.current) {
                  bgVideoRef.current.currentTime = videoRef.current.currentTime;
                }
              }}
              onTimeUpdate={() => {
                const fg = videoRef.current;
                if (!fg) return;

                const tAbs = fg.currentTime;

                if (tAbs >= endTime) {
                  fg.currentTime = startTime;
                  if (bgVideoRef.current) bgVideoRef.current.currentTime = startTime;
                }

                const t = Math.min(Math.max(fg.currentTime, startTime), endTime);
                const elapsed = t - startTime;

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
                  const ratio = (t - startTime) / Math.max(clipDuration, 0.001);
                  const idx = Math.min(Math.max(Math.floor(ratio * tokens.length), 0), tokens.length - 1);
                  const word = tokens[idx];
                  const groupStart = Math.max(0, idx - (maxWordsPerLine - 1));
                  const group = tokens.slice(groupStart, idx + 1);
                  setCurrentCaptionGroup(group);
                  setCurrentCaptionWord(word);
                  return;
                }

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
              {/* Hook box */}
              {hookText.trim().length > 0 && previewElapsed <= hookDurationSeconds && (
                <div
                  className={`absolute max-w-[82%] ${hookBgEnabled ? "rounded-2xl border border-white/8 shadow-2xl backdrop-blur-sm" : ""}`}
                  style={{
                    backgroundColor: hookBgEnabled ? hookBgColor + "F2" : "transparent",
                    padding: `${Math.max(2, hookBoxSize * previewScale)}px ${Math.max(2, hookBoxWidth * previewScale)}px`,
                    left: `${hookX}%`,
                    top: `${hookY}%`,
                    transform: "translate(-50%, -50%)",
                  }}
                >
                  <div
                    className="leading-snug font-bold text-center break-words"
                    style={{
                      color: hookTextColor,
                      fontSize: `${Math.max(8, hookFontSize * previewScale)}px`,
                      maxWidth: `${Math.max(120, 700 * previewScale)}px`,
                      wordBreak: "break-word",
                      textShadow: hookBgEnabled ? "none" : "0 2px 4px #000, 0 0 2px #000",
                    }}
                  >
                    {hookText}
                  </div>
                </div>
              )}

              {/* Title overlay preview — visible the entire duration in full_video_parts mode */}
              {isFullVideoMode && titleText.trim().length > 0 && (
                <div
                  data-testid="preview-title-box"
                  className={`absolute max-w-[82%] ${titleBgEnabled ? "rounded-2xl border border-white/8 shadow-2xl backdrop-blur-sm" : ""}`}
                  style={{
                    backgroundColor: titleBgEnabled ? "#0A0A0AF2" : "transparent",
                    padding: `${Math.max(2, titleBoxSize * previewScale)}px ${Math.max(2, titleBoxWidth * previewScale)}px`,
                    left: `${titleX}%`,
                    top: `${titleY}%`,
                    transform: "translate(-50%, -50%)",
                  }}
                >
                  <div
                    className="leading-snug font-bold text-center break-words"
                    style={{
                      color: "#FFFFFF",
                      fontSize: `${Math.max(8, titleFontSize * previewScale)}px`,
                      maxWidth: `${Math.max(120, 700 * previewScale)}px`,
                      wordBreak: "break-word",
                      textShadow: titleBgEnabled ? "none" : "0 2px 4px #000, 0 0 2px #000",
                    }}
                  >
                    {titleText}
                  </div>
                </div>
              )}

              {/* Part label preview — only when split export is active */}
              {splitMode !== "off" && effectiveParts > 1 && (
                <div
                  className="absolute rounded-lg"
                  style={{
                    left: `${partLabelX}%`,
                    top: `${partLabelY}%`,
                    transform: "translate(-50%, -50%)",
                    backgroundColor: partLabelBgColor + "CC",
                    padding: `${Math.max(2, partLabelBoxSize * previewScale)}px ${Math.max(4, partLabelBoxSize * previewScale * 1.4)}px`,
                  }}
                >
                  <span
                    className="font-bold whitespace-nowrap"
                    style={{
                      color: partLabelTextColor,
                      fontSize: `${Math.max(8, partLabelFontSize * previewScale)}px`,
                    }}
                  >
                    Part 1/{effectiveParts}
                  </span>
                </div>
              )}

              {/* Creator tag (watermark) — shown entire clip duration with opacity */}
              {creatorTagEnabled && creatorTagText.trim().length > 0 && (
                <div
                  data-testid="preview-creator-tag"
                  className="absolute"
                  style={{
                    left: `${creatorTagX}%`,
                    top: `${creatorTagY}%`,
                    transform: "translate(-50%, -50%)",
                    opacity: creatorTagOpacity,
                    pointerEvents: "none",
                  }}
                >
                  <div
                    className="font-bold whitespace-nowrap"
                    style={{
                      color: "#FFFFFF",
                      fontSize: `${Math.max(8, creatorTagFontSize * previewScale)}px`,
                      textShadow: "0 2px 4px #000, 0 0 2px #000",
                    }}
                  >
                    {creatorTagText}
                  </div>
                </div>
              )}

              {/* Captions — bottom-anchored at subtitleY% to match the ASS export's
                  alignment=2 + marginv=((100-subtitleY)/100 * 1920) interpretation. */}
              {currentCaptionGroup.length > 0 && currentCaptionWord && (
                <div
                  className="absolute"
                  style={{
                    left: `${subtitleX}%`,
                    top: `${subtitleY}%`,
                    transform: "translate(-50%, -100%)",
                    maxWidth: "94%",
                  }}
                >
                  <div
                    className="font-extrabold tracking-wide text-center whitespace-nowrap"
                    style={{
                      fontSize: `${Math.max(8, captionFontSize * previewScale)}px`,
                      color: captionTextColor,
                      textShadow: `0 2px 8px ${captionOutlineColor}E6, 0 0 2px ${captionOutlineColor}CC`,
                      lineHeight: 1.1,
                    }}
                  >
                    {currentCaptionGroup.map((w, i) => {
                      const isHighlight = w === currentCaptionWord;
                      if (isHighlight && captionHighlightBgColor) {
                        // Karaoke/Boxed-style: opaque bg box, black text inside
                        return (
                          <span
                            key={`${w}-${i}`}
                            style={{
                              backgroundColor: captionHighlightBgColor,
                              color: "#000000",
                              padding: `${Math.max(2, 4 * previewScale * 10)}px ${Math.max(4, 8 * previewScale * 10)}px`,
                              borderRadius: `${Math.max(2, 6 * previewScale * 10)}px`,
                              marginLeft: i === 0 ? 0 : `${Math.max(2, 4 * previewScale * 10)}px`,
                              display: "inline-block",
                              textShadow: "none",
                            }}
                          >
                            {w}
                          </span>
                        );
                      }
                      return (
                        <span
                          key={`${w}-${i}`}
                          style={
                            isHighlight
                              ? {
                                  color: captionHighlightColor,
                                  display: "inline-block",
                                  transform: "scale(1.05)",
                                  marginLeft: i === 0 ? 0 : `${Math.max(2, 4 * previewScale * 10)}px`,
                                }
                              : {
                                  opacity: 0.95,
                                  marginLeft: i === 0 ? 0 : `${Math.max(2, 4 * previewScale * 10)}px`,
                                }
                          }
                        >
                          {w}
                        </span>
                      );
                    })}
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
              if (bgVideoRef.current && needsBlurredBg) bgVideoRef.current.currentTime = arr[0];
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
          {/* Hook Text Editor (clipping mode only) */}
          {!isFullVideoMode && (
            <div className="space-y-3">
              <Label className="flex items-center gap-2"><Scissors className="h-4 w-4 text-primary" /> Auto Hook Text</Label>
              <div className="text-[10px] text-muted-foreground mb-1">
                This text appears as a bold box at the start of the clip. Adjust duration and box size in Style Overrides.
              </div>
              <Input
                value={hookText}
                onChange={(e) => setHookText(e.target.value)}
                placeholder="e.g. This changes everything..."
                className="bg-card w-full"
              />
            </div>
          )}

          {/* Title Box Editor (full_video_parts mode only) */}
          {isFullVideoMode && (
            <div className="space-y-3" data-testid="title-box-section">
              <Label className="flex items-center gap-2"><Type className="h-4 w-4 text-primary" /> Title Box</Label>
              <div className="text-[10px] text-muted-foreground mb-1">
                Persistent title overlay shown for the entire duration of every exported part.
              </div>
              <Input
                data-testid="title-text-input"
                value={titleText}
                onChange={(e) => setTitleText(e.target.value)}
                placeholder="e.g. Episode 1 — Full Interview"
                className="bg-card w-full"
              />
              <div className="space-y-2 pt-2">
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">Show BG Box</Label>
                  <button
                    type="button"
                    onClick={() => setTitleBgEnabled(!titleBgEnabled)}
                    className={`relative w-10 h-5 rounded-full transition-colors ${titleBgEnabled ? "bg-primary" : "bg-muted"}`}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${titleBgEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                  </button>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">Font Size ({titleFontSize})</Label>
                  <Slider
                    value={[titleFontSize]}
                    min={24}
                    max={80}
                    step={2}
                    onValueChange={(val: number | readonly number[]) => setTitleFontSize(Array.isArray(val) ? val[0] : val)}
                    className="flex-1"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">Box Height ({titleBoxSize})</Label>
                  <Slider
                    value={[titleBoxSize]}
                    min={8}
                    max={60}
                    step={2}
                    onValueChange={(val: number | readonly number[]) => setTitleBoxSize(Array.isArray(val) ? val[0] : val)}
                    className="flex-1"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">Box Width ({titleBoxWidth})</Label>
                  <Slider
                    value={[titleBoxWidth]}
                    min={8}
                    max={80}
                    step={2}
                    onValueChange={(val: number | readonly number[]) => setTitleBoxWidth(Array.isArray(val) ? val[0] : val)}
                    className="flex-1"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">X ({titleX}%)</Label>
                  <Slider
                    value={[titleX]}
                    min={5}
                    max={95}
                    step={1}
                    onValueChange={(val: number | readonly number[]) => setTitleX(Array.isArray(val) ? val[0] : val)}
                    className="flex-1"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">Y ({titleY}%)</Label>
                  <Slider
                    value={[titleY]}
                    min={5}
                    max={95}
                    step={1}
                    onValueChange={(val: number | readonly number[]) => setTitleY(Array.isArray(val) ? val[0] : val)}
                    className="flex-1"
                  />
                </div>
              </div>
            </div>
          )}

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
                {!isFullVideoMode && (
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
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Box Height ({hookBoxSize})</Label>
                    <Slider
                      value={[hookBoxSize]}
                      min={8}
                      max={60}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setHookBoxSize(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Box Width ({hookBoxWidth})</Label>
                    <Slider
                      value={[hookBoxWidth]}
                      min={8}
                      max={80}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setHookBoxWidth(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Duration ({hookDurationSeconds}s)</Label>
                    <Slider
                      value={[hookDurationSeconds]}
                      min={1}
                      max={15}
                      step={0.5}
                      onValueChange={(val: number | readonly number[]) => setHookDurationSeconds(Array.isArray(val) ? val[0] : val)}
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
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Show BG Box</Label>
                    <button
                      type="button"
                      onClick={() => setHookBgEnabled(!hookBgEnabled)}
                      className={`relative w-10 h-5 rounded-full transition-colors ${hookBgEnabled ? "bg-primary" : "bg-muted"}`}
                    >
                      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${hookBgEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                    </button>
                  </div>
                </div>
                )}
              </div>
            )}
          </div>

          {/* Position Controls */}
          <div className="space-y-3">
            <button
              type="button"
              onClick={() => setPositionSectionOpen(!positionSectionOpen)}
              className="flex items-center gap-2 w-full text-sm font-semibold text-left"
            >
              <Move className="h-4 w-4 text-primary" />
              Overlay Positions
              {positionSectionOpen ? <ChevronUp className="h-3 w-3 ml-auto" /> : <ChevronDown className="h-3 w-3 ml-auto" />}
            </button>
            <p className="text-[10px] text-muted-foreground">Adjust position of hook and subtitles on the video.</p>

            {positionSectionOpen && (
              <div className="space-y-4 pt-1">
                {/* Hook position */}
                {!isFullVideoMode && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Hook Position</p>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">X ({hookX}%)</Label>
                    <Slider
                      value={[hookX]}
                      min={5}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setHookX(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Y ({hookY}%)</Label>
                    <Slider
                      value={[hookY]}
                      min={5}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setHookY(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                </div>
                )}

                {/* Subtitle position */}
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Subtitle Position</p>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Y ({subtitleY}%)</Label>
                    <Slider
                      value={[subtitleY]}
                      min={10}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setSubtitleY(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Creator Tag / Watermark */}
          <div className="space-y-3">
            <button
              type="button"
              onClick={() => setCreatorTagOpen(!creatorTagOpen)}
              className="flex items-center gap-2 w-full text-sm font-semibold text-left"
            >
              <Tag className="h-4 w-4 text-primary" />
              Creator Tag
              {creatorTagOpen ? <ChevronUp className="h-3 w-3 ml-auto" /> : <ChevronDown className="h-3 w-3 ml-auto" />}
            </button>
            <p className="text-[10px] text-muted-foreground">Persistent handle/watermark shown for the full clip duration in preview and export.</p>

            {creatorTagOpen && (
              <div className="space-y-3 pt-1">
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[100px]">Show Creator Tag</Label>
                  <button
                    type="button"
                    data-testid="creator-tag-toggle"
                    onClick={() => setCreatorTagEnabled(!creatorTagEnabled)}
                    className={`relative w-10 h-5 rounded-full transition-colors ${creatorTagEnabled ? "bg-primary" : "bg-muted"}`}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${creatorTagEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                  </button>
                </div>
                <Input
                  data-testid="creator-tag-text"
                  value={creatorTagText}
                  onChange={(e) => setCreatorTagText(e.target.value)}
                  placeholder="@yourhandle"
                  className="bg-card w-full"
                  disabled={!creatorTagEnabled}
                />
                <div className={`space-y-3 ${creatorTagEnabled ? "" : "opacity-50 pointer-events-none"}`}>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Font Size ({creatorTagFontSize})</Label>
                    <Slider
                      value={[creatorTagFontSize]}
                      min={16}
                      max={72}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setCreatorTagFontSize(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Opacity ({Math.round(creatorTagOpacity * 100)}%)</Label>
                    <Slider
                      value={[Math.round(creatorTagOpacity * 100)]}
                      min={10}
                      max={100}
                      step={5}
                      onValueChange={(val: number | readonly number[]) => {
                        const v = Array.isArray(val) ? val[0] : val;
                        setCreatorTagOpacity(v / 100);
                      }}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">X ({creatorTagX}%)</Label>
                    <Slider
                      value={[creatorTagX]}
                      min={5}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setCreatorTagX(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[100px]">Y ({creatorTagY}%)</Label>
                    <Slider
                      value={[creatorTagY]}
                      min={5}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setCreatorTagY(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
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
                { value: "1080x1920", label: "1080x1920", desc: "Full HD 9:16" },
                { value: "1440x2560", label: "1440x2560", desc: "2K 9:16" },
                { value: "2160x3840", label: "2160x3840", desc: "4K 9:16" },
                { value: "720x1280", label: "720x1280", desc: "HD 9:16" },
                { value: "1920x1080", label: "1920x1080", desc: "Full HD 16:9" },
                { value: "2560x1440", label: "2560x1440", desc: "2K 16:9" },
                { value: "3840x2160", label: "3840x2160", desc: "4K 16:9" },
                { value: "540x960", label: "540x960", desc: "SD 9:16" },
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
            {isLandscapeExport && (
              <div className="text-[10px] text-yellow-500 bg-yellow-500/10 p-2 rounded">
                16:9 selected — output will be letterboxed into 9:16 vertical format (no cropping).
              </div>
            )}
          </div>

          <Button variant="secondary" className="w-full text-xs" onClick={handleSave}>
            Save Configuration
          </Button>

        </div>

        {/* EXPORT / DOWNLOAD SECTION — split controls live here */}
        <div className="p-5 border-t border-border/30 bg-muted/20 space-y-4">
          {/* Export mode toggle */}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => { setSplitMode("off"); setExportSplitOpen(false); }}
              className={`flex-1 border rounded-lg p-2 text-center text-xs font-medium transition-colors ${
                splitMode === "off"
                  ? "bg-primary/10 border-primary text-primary"
                  : "border-border/60 hover:bg-muted/50 text-muted-foreground"
              }`}
            >
              <Film className="h-3.5 w-3.5 mx-auto mb-1" />
              Single Clip
            </button>
            <button
              type="button"
              onClick={() => { if (splitMode === "off") setSplitMode("auto"); setExportSplitOpen(true); }}
              className={`flex-1 border rounded-lg p-2 text-center text-xs font-medium transition-colors ${
                splitMode !== "off"
                  ? "bg-primary/10 border-primary text-primary"
                  : "border-border/60 hover:bg-muted/50 text-muted-foreground"
              }`}
            >
              <SplitSquareHorizontal className="h-3.5 w-3.5 mx-auto mb-1" />
              Split into Parts
            </button>
          </div>

          {/* Split settings — shown only when split mode is active */}
          {splitMode !== "off" && exportSplitOpen && (
            <div className="space-y-3 bg-card/60 rounded-lg p-3 border border-border/30">
              {/* Split mode selector */}
              <div className="grid grid-cols-2 gap-2">
                {(["auto", "manual"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setSplitMode(mode)}
                    className={`border rounded-lg p-1.5 text-center transition-colors ${
                      splitMode === mode
                        ? "bg-primary/10 border-primary"
                        : "border-border/60 hover:bg-muted/50"
                    }`}
                  >
                    <div className="text-[11px] font-medium capitalize">{mode}</div>
                  </button>
                ))}
              </div>

              {/* Manual parts count */}
              {splitMode === "manual" && (
                <div className="flex items-center justify-between gap-3">
                  <Label className="text-xs whitespace-nowrap min-w-[80px]">Parts ({splitPartsCount})</Label>
                  <Slider
                    value={[splitPartsCount]}
                    min={2}
                    max={100}
                    step={1}
                    onValueChange={(val: number | readonly number[]) => setSplitPartsCount(Array.isArray(val) ? val[0] : val)}
                    className="flex-1"
                  />
                </div>
              )}

              {/* Split info */}
              <div className="text-[10px] text-muted-foreground bg-muted/30 p-2 rounded">
                {clipDuration.toFixed(1)}s clip → {effectiveParts} part{effectiveParts > 1 ? "s" : ""} (~{(clipDuration / effectiveParts).toFixed(1)}s each)
              </div>

              {/* Part label styling — collapsible */}
              <button
                type="button"
                onClick={() => setPositionSectionOpen(!positionSectionOpen)}
                className="flex items-center gap-1.5 w-full text-[11px] font-medium text-muted-foreground"
              >
                <Tag className="h-3 w-3" />
                Part Label Style
                {positionSectionOpen ? <ChevronUp className="h-2.5 w-2.5 ml-auto" /> : <ChevronDown className="h-2.5 w-2.5 ml-auto" />}
              </button>

              {positionSectionOpen && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[80px]">Font ({partLabelFontSize})</Label>
                    <Slider
                      value={[partLabelFontSize]}
                      min={16}
                      max={64}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setPartLabelFontSize(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[80px]">Box ({partLabelBoxSize})</Label>
                    <Slider
                      value={[partLabelBoxSize]}
                      min={4}
                      max={40}
                      step={2}
                      onValueChange={(val: number | readonly number[]) => setPartLabelBoxSize(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[80px]">Text</Label>
                    <input type="color" value={partLabelTextColor} onChange={(e) => setPartLabelTextColor(e.target.value)} className="w-7 h-7 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[80px]">Bg</Label>
                    <input type="color" value={partLabelBgColor} onChange={(e) => setPartLabelBgColor(e.target.value)} className="w-7 h-7 rounded border border-border/60 cursor-pointer bg-transparent" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[80px]">X ({partLabelX}%)</Label>
                    <Slider
                      value={[partLabelX]}
                      min={5}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setPartLabelX(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs whitespace-nowrap min-w-[80px]">Y ({partLabelY}%)</Label>
                    <Slider
                      value={[partLabelY]}
                      min={5}
                      max={95}
                      step={1}
                      onValueChange={(val: number | readonly number[]) => setPartLabelY(Array.isArray(val) ? val[0] : val)}
                      className="flex-1"
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Google Drive destination */}
          <div className="space-y-2 bg-card/60 rounded-lg p-3 border border-border/30">
            <Label className="text-xs flex items-center gap-2">
              <Download className="h-3.5 w-3.5 text-primary" /> Google Drive folder (optional)
            </Label>
            <Input
              data-testid="drive-folder-link"
              value={driveFolderLink}
              onChange={(e) => setDriveFolderLink(e.target.value)}
              placeholder="https://drive.google.com/drive/folders/..."
              className="bg-card w-full text-xs"
            />
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="flex-1 text-[11px]"
                disabled={!driveFolderLink.trim()}
                onClick={async () => {
                  try {
                    const res = await api.clips.driveValidate(driveFolderLink.trim());
                    if (res.valid) toast.success(`Valid Drive folder (id ${res.folder_id})`);
                    else toast.error(res.reason || "Invalid Drive folder link");
                  } catch (e) {
                    const err = e as { message?: string };
                    toast.error(err?.message || "Validation failed");
                  }
                }}
              >
                Validate Link
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="flex-1 text-[11px]"
                disabled={!driveFolderLink.trim() || driveUploading || !clip.export_path}
                onClick={async () => {
                  try {
                    setDriveUploading(true);
                    await updateMutation.mutateAsync(buildSavePayload());
                    const res = await api.clips.driveUpload(clipId, driveFolderLink.trim());
                    if (res.status === "uploaded") {
                      toast.success(`Uploaded ${res.uploaded?.length ?? 0} file(s) to Drive`);
                    } else if (res.status === "blocked_missing_credentials") {
                      toast.error("Drive upload blocked — Google Drive API credentials not configured on server");
                    } else {
                      toast.error(res.reason || "Upload failed");
                    }
                  } catch (e) {
                    const err = e as { message?: string };
                    toast.error(err?.message || "Drive upload failed");
                  } finally {
                    setDriveUploading(false);
                  }
                }}
              >
                {driveUploading ? "Uploading..." : "Upload to Drive"}
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Paste a Google Drive folder share link. Link is validated locally; actual upload requires server-side Drive credentials.
            </p>
          </div>

          <Button
            variant="secondary"
            className="w-full gap-2 text-sm py-4"
            onClick={handlePreviewFinal}
            disabled={previewLoading || updateMutation.isPending}
          >
            {previewLoading ? "Rendering Preview..." : "Preview Final"}
            {!previewLoading && <Eye className="h-4 w-4" />}
          </Button>
          <p className="text-[10px] text-center text-muted-foreground">
            Renders first 10s at low-res in a new tab — same look as final export.
          </p>

          <Button
            className="w-full gap-2 text-md font-bold shadow-lg shadow-primary/20 py-6"
            onClick={handleExportNow}
            disabled={exportMutation.isPending || updateMutation.isPending}
          >
            {exportMutation.isPending ? "Starting Export..." : splitMode !== "off" && effectiveParts > 1 ? `Export ${effectiveParts} Parts` : "Export Clip"}
            {!exportMutation.isPending && <ArrowRight className="h-5 w-5" />}
          </Button>
          <p className="text-[10px] text-center text-muted-foreground mt-1">
            {splitMode !== "off" && effectiveParts > 1
              ? `Renders ${effectiveParts} MP4 files with burnt-in captions and part labels.`
              : `Renders a ${isLandscapeExport ? "9:16 letterboxed" : exportResolution} MP4 file with burnt-in captions.`
            }
          </p>
        </div>

      </div>
    </div>
  );
}
