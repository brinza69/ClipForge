"use client";

// Parallel Processing — one source link → N output videos. Download,
// transcribe, erase and transcript-cleaning run once and are shared; each
// variant differs only in voice, caption template/style and commentator.
//
// The body lives in <ParallelProcessor>. This page just owns the URL state
// and the page header. The /parallel-sheets page reuses the same processor
// with Sheets-specific extras layered on top.

import { useState } from "react";
import { Layers } from "lucide-react";
import { ParallelProcessor } from "@/components/parallel/parallel-processor";

export default function ParallelPage() {
  const [url, setUrl] = useState("");

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-emerald-400">
          <Layers className="h-5 w-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">Parallel Processing</h1>
          <p className="text-sm text-muted-foreground">One link → multiple videos. Download, transcribe & erase run once.</p>
        </div>
      </div>

      <ParallelProcessor url={url} setUrl={setUrl} />
    </div>
  );
}
