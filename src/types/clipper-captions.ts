// The caption plan, as the backend hands it over.
//
// Split out of `clipper.ts` with the reasoning types, for the same reason: that
// file was over the repo's 500-line limit and R2 added to it. Re-exported from
// `clipper.ts`, so no import had to move.

export interface CaptionChunk {
  text: string;
  start: number;
  end: number;
}

export interface CaptionPlan {
  chunks: CaptionChunk[];
  style: Record<string, unknown>;
  x_pct: number;
  y_pct: number;
  scale: number;
  preset_id: string;
}
