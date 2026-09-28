// The server's O4 rule (`clip_mutations.range_refusal`), for the editor's warning.
// A range longer than the project's maximum is refused unless it is no longer than
// the clip already was, so a trim, a same-length move or a title-only edit is never
// blocked. `max` is the server's effective limit (`project.max_clip_s_effective`);
// without it nothing is refused here and the server stays the guard
// (codex-verdict-next-15 R1). The previous length comes from the same endpoints as
// the server's, never the rounded `duration` (R2).
export function rangeTooLong(
  before: { start: number; end: number },
  start: number,
  end: number,
  max: number | undefined,
): boolean {
  if (max === undefined || !Number.isFinite(max)) return false;
  const length = end - start;
  const was = Math.max(0, before.end - before.start);
  return length > max + 1e-6 && length > was + 1e-6;
}
