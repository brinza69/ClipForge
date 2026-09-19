import type { Rect } from "@/types/clipper";

export interface SourceFrame {
  width: number;
  height: number;
  time: number;
  version: string;
  start: number;
  end: number;
}

export function readSourceFrame(headers: Headers): SourceFrame {
  const number = (name: string) => {
    const raw = headers.get(name);
    if (!raw?.trim() || !Number.isFinite(Number(raw))) {
      throw new Error("Cadrul nu are coordonate și timp verificabile. Reîncarcă-l.");
    }
    return Number(raw);
  };
  const frame = {
    width: number("X-Source-Width"), height: number("X-Source-Height"),
    time: number("X-Source-Time"), version: headers.get("X-Source-Version") ?? "",
    start: number("X-Clip-Start"), end: number("X-Clip-End"),
  };
  if (!frame.version || ![frame.width, frame.height].every(
    (n) => Number.isInteger(n) && n >= 2 && n % 2 === 0,
  ) || frame.start < 0 || frame.end <= frame.start ||
    frame.time < frame.start || frame.time >= frame.end) {
    throw new Error("Cadrul nu poate fi folosit pentru această încadrare.");
  }
  return frame;
}

export const REACTION_ASPECT = 1080 / 768;
type Point = { x: number; y: number };

/** The overlay and request share these exact even SOURCE pixels. */
export function selectionRect(
  start: Point, end: Point, width: number, height: number, reaction: boolean,
): Rect | null {
  if (![start.x, start.y, end.x, end.y, width, height].every(Number.isFinite) ||
    width < 2 || height < 2) return null;
  const even = (n: number) => Math.floor(n / 2) * 2;
  const x1 = even(Math.max(0, Math.min(width, start.x)));
  const y1 = even(Math.max(0, Math.min(height, start.y)));
  const x2 = even(Math.max(0, Math.min(width, end.x)));
  const y2 = even(Math.max(0, Math.min(height, end.y)));
  let w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
  if (reaction) {
    h = even(Math.min(h, w / REACTION_ASPECT));
    w = Math.round(h * REACTION_ASPECT / 2) * 2;
  }
  if (w < 2 || h < 2) return null;
  return { x: x2 < x1 ? x1 - w : x1, y: y2 < y1 ? y1 - h : y1, w, h };
}
