/** User-selected source window; this is not an automatic coverage measurement. */
export interface ReactionBinding {
  schema: "clipper_reaction_binding_v1";
  source_version: string;
  source_start: number;
  source_end: number;
  src_w: number;
  src_h: number;
  by: "human";
}
