"use client";

import { AlertTriangle } from "lucide-react";

interface Props {
  missingIndexes: number[];
}

export function MissingImagesBanner({ missingIndexes }: Props) {
  if (missingIndexes.length === 0) return null;
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
      <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
      <p>
        {missingIndexes.length} scene{missingIndexes.length === 1 ? "" : "s"} missing an image
        (#{missingIndexes.map((i) => i + 1).join(", #")}). Render is disabled until every scene has an
        image, or you can render with placeholder frames.
      </p>
    </div>
  );
}
