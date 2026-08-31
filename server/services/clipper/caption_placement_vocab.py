"""The closed lists Batch R6's placement report may use — vocabulary only.

Split out of `caption_placement` at the 500-line limit, on the pattern
`dynamic_rhythm_vocab` already set. No logic lives here, which is the point: a
reader who wants to know what the report is ALLOWED to say reads one short file,
and a reviewer can check the three axes against each other without reading the
arithmetic that fills them.

THE THREE AXES, and keeping them apart is what this batch is about:

    LANDS_ON     what was measured to be there
    UNAVAILABLE  what nobody supplied
    REFUSALS     what somebody supplied wrongly

They are not interchangeable. Folding refusals into `unavailable` made a NaN
caption height report "nobody set one"; folding unavailable into a zero is the
mistake R0 opened this whole plan with.
"""

from __future__ import annotations

#: What the caption can land on, as a closed list. `LANDS_ON`, and not
#: `CONFLICTS`, which is what it was called: three of these four are things the
#: caption COVERS and the fourth is a place it SITS. Calling the letterbox a
#: conflict in the name while the comment beside it said "not a conflict by
#: itself" left the reader two answers and no way to choose, and a reader who
#: takes the name is told the opposite of what the code does.
ON_FACE = "over_face"
ON_UI = "over_ui_panel"
ON_SOURCE_TEXT = "over_source_text"
#: The letterboxed strip on a `fit` shot. NOT AN OCCLUSION — a caption there
#: covers nothing of the source — and NOT A DEMONSTRATED READ either. The
#: renderer fills the strip with a BLURRED COPY OF THE FRAME rather than with
#: black (`render_v3_letterbox`), so "it is only bars, the text is fine" is a
#: claim about a renderer this repo stopped shipping. §R6 says the blurred band
#: may host captions only if it passes contrast, and nothing here measures
#: contrast. Reported, kept out of `share`, and decided by somebody else.
ON_LETTERBOX = "on_letterbox_band"
LANDS_ON: tuple[str, ...] = (ON_FACE, ON_UI, ON_SOURCE_TEXT, ON_LETTERBOX)
#: The three that are OCCLUSIONS, which is exactly what `share` is a union of.
#: The letterbox is not one: a caption there covers nothing of the source. That
#: is not a claim that the text is legible against it — `render_v3_letterbox`
#: fills the strip with a blurred copy of the frame, and §R6 wants a contrast
#: measurement before anything sits there.
OCCLUSIONS: tuple[str, ...] = (ON_FACE, ON_UI, ON_SOURCE_TEXT)

#: Why a check could not be made. Each one keeps its window in the denominator.
NO_FACES = "no_face_track"
NO_PANELS = "no_ui_detection"
NO_TEXT = "no_text_detection"
NO_SHOTS = "no_shot_list"
NO_CAPTION = "no_caption_plan"
#: The caller supplied no per-shot evidence at all. Distinct from a shot whose
#: entry is missing one signal: this is nobody having mapped anything.
NO_EVIDENCE = "no_per_shot_evidence"
#: A `fit` shot whose source dimensions nobody supplied. Distinct from a source
#: that is already upright and measurably has no letterbox.
NO_GEOMETRY = "no_source_dimensions"
#: A shot that does not say how it is composed. The face and text signals do not
#: depend on the composition and are still measured; only the letterbox question
#: has no answer. Distinct from a shot that names a composition nobody defined.
NO_COMPOSITION = "shot_does_not_say_how_it_is_composed"
UNAVAILABLE: tuple[str, ...] = (NO_FACES, NO_PANELS, NO_TEXT, NO_SHOTS,
                                NO_CAPTION, NO_EVIDENCE, NO_GEOMETRY,
                                NO_COMPOSITION)

# --- and why a record was REFUSED, which is not the same as unmeasured -------
#
# A corrupt record is not a record with no measurements in it. Folding the two
# together let a NaN caption height report `no_caption_plan` as well — "nobody
# set one" out of a record where somebody set a bad one — and let a shot whose
# evidence was a number report all three signals as merely missing, with a
# `worst` computed over it. A refusal keeps the record out of every count it
# would otherwise join.

#: A caption height that is not a finite fraction of the frame.
BAD_CAPTION_Y = "caption_y_not_a_fraction"
#: One shot's evidence entry is not a record.
BAD_EVIDENCE = "evidence_entry_not_a_record"
#: More evidence entries than there are shots. The caller mapped rectangles for
#: frames this clip does not have, which means its idea of the edit and the
#: edit disagree — and the extra entries used to be sliced off in silence.
MORE_EVIDENCE_THAN_SHOTS = "more_evidence_entries_than_shots"
#: An entry in the shot list that is not a shot.
BAD_SHOT = "shot_entry_not_a_record"
#: A composition outside the closed list. Not a `crop`, which is what silently
#: falling through to the `crop` branch had been saying.
BAD_COMPOSITION = "composition_not_in_the_closed_list"
#: A container that cannot be walked. Each one of these used to raise straight
#: out of a module whose whole contract is that its absence changes nothing.
BAD_SHOTS = "shot_list_not_a_sequence"
BAD_EVIDENCE_LIST = "evidence_list_not_a_sequence"
BAD_RECTS = "rectangle_list_not_a_sequence"
#: An output height or a source dimension that is not a usable number.
BAD_OUT_H = "output_height_not_a_positive_number"
BAD_SOURCE_SIZE = "source_dimensions_not_numbers"
REFUSALS: tuple[str, ...] = (BAD_CAPTION_Y, BAD_EVIDENCE, BAD_SHOT,
                             MORE_EVIDENCE_THAN_SHOTS,
                             BAD_COMPOSITION, BAD_SHOTS, BAD_EVIDENCE_LIST,
                             BAD_RECTS, BAD_OUT_H, BAD_SOURCE_SIZE)

#: The two compositions `dynamic_geometry` emits. A `crop` fills the output with
#: a 9:16 window on the source; a `fit` puts the whole frame in the middle and
#: blurs the rest.
#:
#: IT IS ENFORCED, and it was not. The code asked `composition == "fit"` and let
#: everything else fall through to the `crop` branch — so a shot labelled
#: `wobble`, or one whose composition key was a number, came back reported as a
#: full-bleed crop with no letterbox and no complaint. A closed list that
#: nothing checks is a comment.
COMPOSITIONS: tuple[str, ...] = ("crop", "fit")

__all__ = ["LANDS_ON", "OCCLUSIONS", "UNAVAILABLE", "REFUSALS", "COMPOSITIONS",
           "ON_FACE", "ON_UI", "ON_SOURCE_TEXT", "ON_LETTERBOX",
           "NO_FACES", "NO_PANELS", "NO_TEXT", "NO_SHOTS", "NO_CAPTION",
           "NO_EVIDENCE", "NO_GEOMETRY", "NO_COMPOSITION",
           "BAD_CAPTION_Y", "BAD_EVIDENCE", "BAD_SHOT", "BAD_COMPOSITION",
           "MORE_EVIDENCE_THAN_SHOTS",
           "BAD_SHOTS", "BAD_EVIDENCE_LIST", "BAD_RECTS", "BAD_OUT_H",
           "BAD_SOURCE_SIZE"]
