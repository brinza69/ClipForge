// RX1 (codex-verdict-next-36 §3): a save that turns ClipForge's captions on over a reaction framing checks
// where they land — with the plan the render builds when none is stored. When the words carry no timing,
// no layer can be built, so nothing was checked: the save goes through, and these lines say so.

export type CaptionPlacement = { verified: boolean; reason: string | null } | null | undefined;

const UNBUILT: Record<string, string> = {
  no_transcript: "proiectul nu are transcriere",
  no_timed_words: "cuvintele din clip nu au timpi",
  incomplete_timing: "unele cuvinte din clip nu au timpi",
  empty_plan: "nu rămâne niciun text de afișat",
};

export function unbuiltReason(reason: string | null): string {
  return UNBUILT[reason ?? ""] ?? reason ?? "motiv necunoscut";
}

/** The line to show after a save; null when the placement was checked or nothing burns. */
export function placementNotice(p: CaptionPlacement): string | null {
  if (!p || p.verified) return null;
  return `Amplasarea subtitrării ClipForge nu a fost verificată: ${unbuiltReason(p.reason)}. `
    + "Cu datele actuale stratul nu poate fi construit; exportul iese fără el și spune de ce.";
}
