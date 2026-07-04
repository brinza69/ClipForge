"use client";

// One storyboard row: narration/subtitle/prompt text, copy-prompt button,
// audio preview, drag-drop image slot, status chip, reorder buttons.

import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Copy, ImagePlus, X, ChevronUp, ChevronDown, Check } from "lucide-react";
import { toast } from "sonner";
import type { DoodleScene } from "@/types/doodle";

interface Props {
  projectId: string;
  scene: DoodleScene;
  isFirst: boolean;
  isLast: boolean;
  onImageUploaded: (index: number, scene: DoodleScene) => void;
  onImageRemoved: (index: number) => void;
  onMove: (index: number, direction: -1 | 1) => void;
}

function statusChip(scene: DoodleScene) {
  if (!scene.image_path) return { label: "Missing image", cls: "border-destructive/40 text-destructive" };
  if (scene.audio_duration == null) return { label: "No audio", cls: "border-amber-500/40 text-amber-400" };
  return { label: "Ready", cls: "border-emerald-500/40 text-emerald-400" };
}

export function SceneRow({ projectId, scene, isFirst, isLast, onImageUploaded, onImageRemoved, onMove }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const chip = statusChip(scene);

  const uploadFile = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/images/${scene.index}`, {
        method: "POST",
        body: form,
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Upload failed (${r.status})`);
      }
      const updated: DoodleScene = await r.json();
      onImageUploaded(scene.index, updated);
    } catch (e: any) {
      toast.error(`Scene ${scene.index + 1}: upload failed`, { description: e.message });
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) uploadFile(file);
  };

  const removeImage = async () => {
    try {
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/images/${scene.index}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`Remove failed (${r.status})`);
      onImageRemoved(scene.index);
    } catch (e: any) {
      toast.error("Failed to remove image", { description: e.message });
    }
  };

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(scene.image_prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Clipboard write failed");
    }
  };

  return (
    <div className="rounded-lg border border-border/40 bg-card/60 p-3 space-y-2.5">
      <div className="flex items-start gap-3">
        <div className="flex flex-col items-center gap-1 shrink-0 pt-0.5">
          <Badge variant="outline" className="text-[11px]">#{scene.index + 1}</Badge>
          <div className="flex flex-col">
            <button type="button" disabled={isFirst} onClick={() => onMove(scene.index, -1)} className="disabled:opacity-30 hover:text-primary transition-colors">
              <ChevronUp className="h-3.5 w-3.5" />
            </button>
            <button type="button" disabled={isLast} onClick={() => onMove(scene.index, 1)} className="disabled:opacity-30 hover:text-primary transition-colors">
              <ChevronDown className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <div className="flex-1 min-w-0 space-y-1.5">
          <p className="text-sm">{scene.narration}</p>
          <p className="text-[11px] text-muted-foreground italic">&ldquo;{scene.subtitle}&rdquo;</p>

          <div className="rounded-md border border-border/30 bg-background/60 p-2">
            <div className="flex items-start justify-between gap-2">
              <p className={`text-[11px] text-muted-foreground flex-1 ${expanded ? "" : "line-clamp-2"}`}>
                {scene.image_prompt}
              </p>
              <div className="flex items-center gap-1 shrink-0">
                <button type="button" onClick={() => setExpanded((x) => !x)} className="text-[11px] text-muted-foreground hover:text-foreground">
                  {expanded ? "Less" : "More"}
                </button>
                <Button variant="ghost" size="icon-xs" onClick={copyPrompt} title="Copy prompt">
                  {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                </Button>
              </div>
            </div>
          </div>

          {scene.audio_path && scene.audio_duration != null && (
            <div className="flex items-center gap-2">
              <audio controls className="h-7 max-w-xs" src={`/worker-doodle/${projectId}/${scene.audio_path}`} />
              <span className="text-[11px] text-muted-foreground">{scene.audio_duration.toFixed(1)}s</span>
            </div>
          )}
        </div>

        <div className="shrink-0 flex flex-col items-end gap-1.5">
          <Badge variant="outline" className={chip.cls}>{chip.label}</Badge>

          {scene.image_path ? (
            <div className="relative group">
              <img
                src={`/worker-doodle/${projectId}/${scene.image_path}`}
                alt={`Scene ${scene.index + 1}`}
                className="h-20 w-32 rounded-md object-cover border border-border/40"
              />
              <button
                type="button"
                onClick={removeImage}
                className="absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-destructive text-white opacity-0 group-hover:opacity-100 transition-opacity"
                title="Remove image"
              >
                <X className="h-3 w-3" />
              </button>
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`absolute inset-0 rounded-md cursor-pointer ${dragOver ? "bg-primary/20 border-2 border-primary border-dashed" : ""}`}
                title="Drop or click to replace"
              />
            </div>
          ) : (
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`flex h-20 w-32 flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed cursor-pointer transition-colors ${
                dragOver ? "border-primary bg-primary/10" : "border-border/50 hover:border-primary/40"
              }`}
            >
              <ImagePlus className="h-4 w-4 text-muted-foreground" />
              <span className="text-[10px] text-muted-foreground">{uploading ? "Uploading…" : "Drop image"}</span>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadFile(f); e.target.value = ""; }}
          />
        </div>
      </div>
    </div>
  );
}
