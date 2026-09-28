// Which reasoning engine picks the moments.
//
// `null` is the first choice and the initial one: send nothing and let the
// server's default stand. That is NOT the same as picking "Signals" — the
// backend resolves its default from config.py, so an operator who turned the
// story engine on there keeps it. A value posted from here would silently take
// it back off, which is the trap `trim_silence` fell into.
//
// The list holds only modes the API will accept. `story_v2` exists in the
// backend and is refused until it has been compared against legacy on a
// corpus — offering it would put a mode on screen that the server rejects.

import { Label } from "@/components/ui/label";
import { Pill } from "@/components/clipper/pill";
import type { ReasoningMode } from "@/types/clipper";

const CHOICES: { id: ReasoningMode | null; label: string; hint: string }[] = [
  { id: null, label: "Server default", hint: "Whatever this rig is configured to use." },
  { id: "legacy", label: "Signals", hint: "Loud, clean moments. No model involved." },
  {
    id: "llm_nominate",
    label: "Nominated",
    hint: "Adds an LLM pass that names moments, plus the judge.",
  },
  {
    id: "story_v1",
    label: "Story",
    hint: "Payoff first: what happened, then the earliest start that carries it.",
  },
  {
    id: "story_v2_shadow",
    label: "Story v2 (shadow)",
    hint: "Records what v2 would choose, for comparison. Your board stays as it is.",
  },
];

export function ReasoningModeField({
  value,
  onChange,
}: {
  value: ReasoningMode | null;
  onChange: (mode: ReasoningMode | null) => void;
}) {
  return (
    <div className="space-y-2">
      <Label className="text-xs">Reasoning engine</Label>
      <div className="flex flex-wrap gap-2">
        {CHOICES.map((c) => (
          <Pill key={c.id ?? "default"} active={value === c.id} onClick={() => onChange(c.id)}>
            {c.label}
          </Pill>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        {CHOICES.find((c) => c.id === value)?.hint}
      </p>
    </div>
  );
}
