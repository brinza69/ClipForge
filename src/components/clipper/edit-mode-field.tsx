// Which editing grammar the renderer cuts with.
//
// `null` is the first choice and the initial one, for the same reason as the
// reasoning mode beside it: send nothing and let the server's default stand.
// The backend resolves it from config.py, so an operator who turned the shadow
// on there keeps it — a value posted from here would quietly take it back off.
//
// The list holds only modes the API will accept. `content_aware` exists in the
// backend and is refused until the profiles have been compared against the
// legacy render on a corpus; offering it would put a mode on screen that the
// server rejects.

import { Label } from "@/components/ui/label";
import { Pill } from "@/components/clipper/pill";
import type { EditMode } from "@/types/clipper";

const CHOICES: { id: EditMode | null; label: string; hint: string }[] = [
  { id: null, label: "Server default", hint: "Whatever this rig is configured to use." },
  {
    id: "legacy_dynamic",
    label: "Current edit",
    hint: "One grammar for every source: the renderer exactly as it is today.",
  },
  {
    id: "content_aware_shadow",
    label: "Profile (shadow)",
    hint: "Records the grammar this clip's content type would get. Your render stays as it is.",
  },
];

export function EditModeField({
  value,
  onChange,
}: {
  value: EditMode | null;
  onChange: (mode: EditMode | null) => void;
}) {
  return (
    <div className="space-y-2">
      <Label className="text-xs">Editing grammar</Label>
      <div className="flex flex-wrap gap-2">
        {CHOICES.map((choice) => (
          <Pill
            key={choice.id ?? "default"}
            active={value === choice.id}
            onClick={() => onChange(choice.id)}
          >
            {choice.label}
          </Pill>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        {CHOICES.find((choice) => choice.id === value)?.hint}
      </p>
    </div>
  );
}
