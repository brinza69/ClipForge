"use client";

// Which selection run to review, chosen BEFORE the session (master plan §6, A3).
//
// A board can hold clips from several runs: a rescore keeps exports and clips a
// person worked on, with their old run id. The blind review only compares boards
// that coexisted, so the reviewer picks a run here — the server never guesses
// "the latest". Counts and reasons only: which clip sits on which board stays
// hidden until the result, so this component never names a clip.

import { useEffect, useRef, useState } from "react";

import {
  MISSING_LABELS,
  REVIEW_API,
  type ReviewCohorts,
} from "@/types/clipper-review";

export function ReviewCohortPicker({ projects, value, onChange }: {
  projects: { id: string; title: string }[];
  value: Record<string, string | null>;
  onChange: (next: Record<string, string | null>) => void;
}) {
  const [cohorts, setCohorts] = useState<Record<string, ReviewCohorts | "error">>({});
  const requested = useRef(new Set<string>());

  useEffect(() => {
    for (const p of projects) {
      if (requested.current.has(p.id)) continue;
      requested.current.add(p.id);
      fetch(`${REVIEW_API}/cohorts?project_id=${encodeURIComponent(p.id)}`)
        .then(async (r) => (r.ok ? ((await r.json()) as ReviewCohorts) : ("error" as const)))
        .catch(() => "error" as const)
        .then((c) => setCohorts((s) => ({ ...s, [p.id]: c })));
    }
  }, [projects]);

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Alege rularea de comparat pentru fiecare proiect. Doar o rulare a cărei componență
        poate fi dovedită e eligibilă. Fără alegere pentru toate proiectele, un board cu
        rulări amestecate e refuzat.
      </p>
      {projects.map((p) => {
        const c = cohorts[p.id];
        return (
          <div key={p.id} className="space-y-1 rounded border border-border/50 p-2">
            <div className="text-sm font-medium">{p.title}</div>
            {c === undefined && <p className="text-xs text-muted-foreground">Se încarcă rulările…</p>}
            {c === "error" && <p className="text-xs text-rose-500">Rulările nu au putut fi citite.</p>}
            {c && c !== "error" && (
              <>
                <p className="text-[11px] text-muted-foreground">
                  {c.board_rows === 1 ? "1 rând" : `${c.board_rows} rânduri`} pe board,{" "}
                  {c.cohorts.length === 1 ? "1 rulare" : `${c.cohorts.length} rulări`}
                </p>
                {c.cohorts.map((k) => {
                  const id = k.selection_run_id;
                  const key = id ?? "(fără id)";
                  return (
                    <label key={key} className={`flex items-start gap-2 text-xs ${k.eligible ? "" : "opacity-60"}`}>
                      <input
                        type="radio"
                        name={`cohort-${p.id}`}
                        disabled={!k.eligible}
                        checked={p.id in value && value[p.id] === id}
                        onChange={() => onChange({ ...value, [p.id]: id })}
                      />
                      <span>
                        <span className="font-mono">{key}</span>
                        {` · ${k.members} clipuri (legacy ${k.legacy_members}, shadow ${k.shadow_members})`}
                        {` · media ${k.media.valid}/${k.media.checked}`}
                        {k.eligible
                          ? " · eligibilă"
                          : ` · neeligibilă: ${k.missing.map((m) => MISSING_LABELS[m] ?? m).join(", ")}`}
                      </span>
                    </label>
                  );
                })}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
