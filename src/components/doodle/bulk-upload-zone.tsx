"use client";

// Drop many files or a .zip → POST /images/bulk. Auto-matches by filename
// convention scene_000.png / bare numbers like "3.png".

import { useRef, useState } from "react";
import { Card } from "@/components/ui/card";
import { UploadCloud } from "lucide-react";
import { toast } from "sonner";
import type { DoodleBulkUploadResult } from "@/types/doodle";

interface Props {
  projectId: string;
  onDone: () => void; // caller reloads the storyboard after a bulk upload
}

export function BulkUploadZone({ projectId, onDone }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    setUploading(true);
    try {
      const form = new FormData();
      for (const f of list) form.append("files", f);
      const r = await fetch(`/worker-api/doodle/projects/${projectId}/images/bulk`, {
        method: "POST",
        body: form,
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Bulk upload failed (${r.status})`);
      }
      const result: DoodleBulkUploadResult = await r.json();
      if (result.unmatched.length > 0) {
        toast.warning(`Matched ${result.matched} — ${result.unmatched.length} unmatched`, {
          description: result.unmatched.slice(0, 5).join(", "),
        });
      } else {
        toast.success(`Matched ${result.matched} image(s)`);
      }
      onDone();
    } catch (e: any) {
      toast.error("Bulk upload failed", { description: e.message });
    } finally {
      setUploading(false);
    }
  };

  return (
    <Card
      className={`p-4 border-2 border-dashed transition-colors ${dragOver ? "border-primary bg-primary/5" : "border-border/50"}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => { e.preventDefault(); setDragOver(false); uploadFiles(e.dataTransfer.files); }}
      onClick={() => inputRef.current?.click()}
      role="button"
    >
      <div className="flex flex-col items-center justify-center gap-2 py-4 text-center cursor-pointer">
        <UploadCloud className="h-6 w-6 text-muted-foreground" />
        <p className="text-sm font-medium">{uploading ? "Uploading…" : "Bulk upload images"}</p>
        <p className="text-[11px] text-muted-foreground">
          Drop multiple files or a .zip here — auto-matched by name (scene_000.png, 3.png, …)
        </p>
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp,application/zip"
        className="hidden"
        onChange={(e) => { if (e.target.files) uploadFiles(e.target.files); e.target.value = ""; }}
      />
    </Card>
  );
}
