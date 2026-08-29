// WHICH grammar the renderer cuts with, and what a clip resolved to.
//
// Split out of `clipper.ts` when Batch R2 pushed it to the 500-line limit —
// at exactly 500 the rule is already broken in spirit, since the next line
// breaks it in fact. R2 introduced the seam itself: these two types are about
// the editing decision, not about the clip, the project or the settings.
//
// Re-exported from `clipper.ts`, so no import had to move.

/**
 * `legacy_dynamic` is the renderer as it is today, and the rollback.
 * `content_aware_shadow` resolves the clip's editing profile and records it
 * beside the export while the delivered render stays exactly the same — which
 * is how the new grammar becomes comparable without changing anyone's clip.
 *
 * `content_aware` — the profile actually cutting the clip — is known to the
 * backend and refused, because no clip has been cut to a profile yet. It is
 * left out here so the form cannot offer a mode the API will reject.
 */
export type EditMode = "legacy_dynamic" | "content_aware_shadow";

/**
 * The editing grammar a clip resolves to, as the backend computed it.
 *
 * `cuts_per_min` is a CHOSEN guardrail, not a measurement — see the module
 * docstring in `edit_profiles.py`. Do not present it as calibrated.
 */
export interface EditProfile {
  profile: string;
  reason:
    | "type"
    | "low_confidence"
    | "missing_confidence"
    | "unknown_type"
    | "override"
    | "invalid_confidence";
  content_type: string;
  confidence: number | null;
  origin: string | null;
  cuts_per_min: number[];
  rule: string;
  regimes: string[];
}
