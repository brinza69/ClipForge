"use client";

import { useEffect, useRef, useState } from "react";
import { errorDescription, readApiError } from "@/lib/api-error";
import { CLIPPER_API } from "@/types/clipper";

export function ClipFramePreview({ clipId, duration, revision, dirty }: {
  clipId: string;
  duration: number;
  revision: number;
  dirty: boolean;
}) {
  const [at, setAt] = useState(Math.min(0.5, duration / 2));
  const [frame, setFrame] = useState<{
    url: string; at: number; duration: number; captionsOff: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const imageUrl = useRef<string | null>(null);

  useEffect(() => () => {
    if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    // Scrubbing must not launch a new planner/decoder for every pointer event.
    const timer = setTimeout(async () => {
      setLoading(true);
      setError(null);
      setFrame(null);
      try {
        const response = await fetch(
          `${CLIPPER_API}/clips/${clipId}/preview-frame?t=${at.toFixed(3)}&v=${revision}`,
          { signal: controller.signal, cache: "no-store" },
        );
        if (!response.ok) {
          throw new Error(errorDescription(await readApiError(response, "Could not render this frame")));
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
        const url = URL.createObjectURL(blob);
        imageUrl.current = url;
        setFrame({ url, at: Number(response.headers.get("X-Clip-Time")),
          duration: Number(response.headers.get("X-Clip-Duration")),
          captionsOff: response.headers.get("X-Caption-Action") === "suppress" });
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [clipId, at, revision]);

  const length = frame?.duration ?? duration;
  return (
    <div className="space-y-2">
      <div className="flex aspect-[9/16] items-center justify-center rounded-lg border border-border/40 bg-black">
        {loading ? <p role="status" className="p-4 text-center text-xs text-white/70">Preparing the saved edit…</p>
          : error ? <p role="alert" className="p-4 text-xs text-red-400">{error}</p>
          : frame && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={frame.url} alt={`Saved edit at ${frame.at.toFixed(1)} seconds`}
              className="w-full rounded-lg" />
          )}
      </div>
      <input type="range" min={0} max={Math.max(0, length - 0.04)} step={0.1}
        value={Math.min(at, Math.max(0, length - 0.04))}
        onChange={(e) => setAt(Number.parseFloat(e.target.value))}
        className="w-full" aria-label="Frame to preview" />
      <p className="text-center text-[11px] tabular-nums text-muted-foreground">
        +{(frame?.at ?? at).toFixed(1)}s of {length.toFixed(1)}s
      </p>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        The saved edit, with its export framing and captions. Times include removed pauses.
        Render a video preview to check the movement and sound.
      </p>
      {frame?.captionsOff && <p className="text-[11px] text-muted-foreground">
        Added captions are off for this clip.
      </p>}
      {dirty && <p className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-500">
        Save your changes to update this frame.
      </p>}
    </div>
  );
}
