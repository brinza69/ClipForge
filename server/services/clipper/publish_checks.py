"""The seven §R7 checks, answered from one clip's artefacts.

`publish_preflight` owns the vocabulary and the verdict. This owns the reading:
it takes a sidecar and whatever else is on disk and produces one `check()` per
item on §R7's list.

THE RULE THIS FILE IS WRITTEN AROUND. Every one of the seven has to be able to
come back `unavailable`, and most of them will. That is not a gap in the module,
it is the answer: a publish decision today rests on less than the list suggests,
and the point of R7 is that the verdict says so instead of approving anyway.

THE ORDER INSIDE EVERY CHECK, and six of the seven had it wrong somewhere:

    1. a MEASURED defect is a `fail`, whatever else could not be looked at
    2. otherwise a half nobody could look at makes it `unavailable`
    3. `pass` needs every half of the check's own name demonstrated

Step 3 is the one that kept being skipped, and it is where a check quietly
becomes a claim nobody made: a name with an "and" in it that passes on one half
is asserting the other.

WHAT EACH ONE READS, and where it stops:

    geometry_and_duration     `edit_quality.clip_report`, BOTH halves — a
                              duration with no composition beside it is not a
                              geometry, and `{"duration": 30}` used to pass
    cut_equivalence_...       the same report's `equivalent_cuts`. The profile
                              rhythm has NO source — §4's own `UNMEASURED` list
                              says so — so cuts>0 fails and cuts==0 is
                              unavailable, never a pass for both halves
    subject_present_...       the DELIVERED shots, not the profile: a `crop`
                              needs its anchor's target, a `fit` needs no face.
                              All-`fit` clips pass; a crop needs
                              `regime_view.target_basis`, which no stored
                              sidecar carries
    usable_frame_in_fit...    `source_chrome` on the rendered export
    captions_...              three halves: `source_captions` AND whether this
                              export burned its own layer (a duplicate needs
                              both), `caption_corpus.measure` for what the
                              caption covers, `caption_contrast` for whether the
                              palette can be read — honouring the shortfalls a
                              human accepted
    boundary_complete         `boundary_completion`'s DEFECTS, blocking and
                              technical. Not its `eligible`, which is a verdict
                              about the moment and can be True on a repair
                              nobody applied to this export
    provenance_complete       `edit_quality.fingerprint_status`, RECOMPUTED. A
                              mismatch fails; a match is not provenance — the
                              recipe never touches the delivered file

A CHECK NEVER RAISES. A preflight that dies on one clip reports on the clips
before it and says nothing about the one that killed it, which is worse than any
verdict it could have returned.
"""

from __future__ import annotations

from typing import Any

from services.clipper import publish_preflight as pf
# Split out at the 500-line limit and re-exported so callers did not have to
# move — the same arrangement `edit_quality` has with `render_input`. It is a
# subject of its own: the other six checks read one artefact each and that one
# reconciles four.
from services.clipper.publish_captions import ON_A_FACE, TWO_LAYERS, captions

__all__ = ["geometry", "equivalence", "subject", "frame", "captions",
           "boundary", "provenance", "checks_for", "ON_A_FACE", "TWO_LAYERS"]


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _overlaps(shot: dict, seg: dict) -> bool:
    """Do a shot and an evidence segment share any time at all.

    Half-open on purpose: a segment that ENDS exactly where the shot begins
    describes seconds the shot does not contain, and counting it would let a
    neighbour's evidence answer for this one.
    """
    a0, a1 = _num(shot.get("t0")), _num(shot.get("t1"))
    b0, b1 = _num(seg.get("t0")), _num(seg.get("t1"))
    if None in (a0, a1, b0, b1):
        return False
    return a0 < b1 and b0 < a1


def _report(sidecar: Any) -> dict | None:
    from services.clipper.edit_quality import clip_report

    try:
        return clip_report(sidecar)
    except Exception:
        return None


def geometry(sidecar: Any) -> dict:
    """Duration AND geometry — the check is named for both, so it needs both.

    A DURATION IS NOT A GEOMETRY. `{"duration": 30}` used to pass this: the
    sidecar carried a plausible clock, no defect could be raised against a shot
    list that was not there, and the check called it a pass — which claimed the
    frame had been looked at out of a record that has no frame in it. Composition
    is `unavailable` in exactly that case, so the two halves are now asked for
    separately and a missing half is missing.
    """
    from services.clipper.edit_quality import UNAVAILABLE as EQ_UNAVAILABLE

    report = _report(sidecar)
    if report is None:
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    defects = [d for d in (report.get("defects") or [])]
    if defects:
        # A composition that is present and impossible, or a duration that is,
        # is corruption rather than age — `edit_quality` decides which, and this
        # only carries the answer. MEASURED DEFECT FIRST: a demonstrated defect
        # outranks a half nobody could look at.
        return pf.check(pf.FAIL, why=",".join(sorted(defects)),
                        severity=pf.REJECTABLE, evidence=defects)
    # COMPOSITION IS A DISTRIBUTION, NOT A SCALAR. `clip_report` returns the
    # string `"unavailable"` only when the shot list itself could not be read;
    # when it CAN be read it returns a counter, and a counter of unknowns looks
    # like `{"unavailable": 19}`. Comparing the whole field to the sentinel is
    # therefore false for every one of those, and 31 of the 89 clips this check
    # passed had every single shot's composition unknown. It is the same
    # mistake, one level in, as the one this function exists to fix: a container
    # is not the scalar it contains.
    composition = report.get("composition")
    known = (composition != EQ_UNAVAILABLE and isinstance(composition, dict)
             and bool(composition)
             and EQ_UNAVAILABLE not in composition)
    missing = []
    if report.get("duration_s") == EQ_UNAVAILABLE:
        missing.append("no_duration")
    if not known:
        missing.append("no_composition")
    evidence = {"duration_s": report.get("duration_s"),
                "clock": report.get("duration_clock"),
                "composition": composition,
                "shots": report.get("shots")}
    if missing:
        return pf.check(pf.UNAVAILABLE, why=",".join(missing), evidence=evidence)
    return pf.check(pf.PASS, evidence=evidence)


def equivalence(sidecar: Any) -> dict:
    """Cuts that change nothing, and the profile's rhythm.

    ONLY THE FIRST HALF IS ANSWERABLE, and that is the whole of this function's
    honesty. `dynamic_rhythm_pace` compares against §4's bands and never
    enforces them, and §4's own `UNMEASURED` list says `talking_head` asks for a
    reframe "at a clear idea or emotion" which nothing measures — so the rhythm
    half has no source at all.

    Zero equivalent cuts therefore does NOT pass this check. It passed before,
    and the claim it was making was "equivalence and rhythm are both fine" out
    of a run in which rhythm was never looked at. The count survives as
    evidence; the composite verdict does not.
    """
    report = _report(sidecar)
    if report is None:
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    cuts = report.get("equivalent_cuts")
    if cuts == "unavailable":
        return pf.check(pf.UNAVAILABLE, why="shot_list_unreadable_or_absent")
    if cuts:
        # MEASURED DEFECT FIRST. A cut between two shots that deliver the same
        # picture is a cut the viewer cannot see — R1 found 116 across the
        # pilots — and it stands whether or not the other half could be looked
        # at.
        return pf.check(pf.FAIL, why=f"{cuts}_cuts_change_nothing",
                        severity=pf.REVISABLE, evidence=cuts)
    return pf.check(pf.UNAVAILABLE, why="profile_rhythm_is_not_evaluated",
                    evidence={"equivalent_cuts": 0})


def subject(sidecar: Any) -> dict:
    """Whether the subject is present where the DELIVERED TREATMENT needs one.

    THE REQUIREMENT COMES FROM THE SHOT, NOT FROM THE PROFILE. There is no list
    of which profiles require a subject, and inventing one in the same breath as
    checking it would be writing the requirement to fit the answer. But the
    requirement does not have to come from a profile at all: it is a property of
    what the render actually did. A `crop` puts a 9:16 window somewhere on the
    source because an anchor said to, and if the anchor was not on the subject
    the clip is 1080 pixels of the wrong thing — so a crop needs evidence of its
    target. A `fit` keeps the whole frame; a diagram, a map, a gameplay wide
    shot are all complete in it, and it needs no face.

    So a clip whose delivered shots are all `fit` PASSES, and that is a real
    answer rather than a shrug. A clip with any `crop` needs the anchor's
    provenance, which is `target_basis` in R3b's `regime_view` — recorded by
    `clipper_shadow_views` and absent from every sidecar written before it. That
    is `unavailable`, naming the field, and it resolves itself the day a render
    goes through the shadow-view path.

    NOTE WHAT IS STILL NOT CLAIMED. `stable_track` finds a geometrically stable
    cluster, not a person: `target_basis: stable_anchor` says the crop followed
    something consistent, never that it followed the creator. R3b's own comment
    says the same, and identity needs the human gate.
    """
    if not isinstance(sidecar, dict):
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    plan = sidecar.get("dynamic_plan")
    shots = plan.get("shots") if isinstance(plan, dict) else None
    if not isinstance(shots, list) or not shots:
        # Not "no crop shots". A clip with no readable shot list was rendered by
        # the static path or by something this cannot see, and either way the
        # question of what the frame follows has no answer here.
        return pf.check(pf.UNAVAILABLE, why="no_shot_list")
    if any(not isinstance(shot, dict) for shot in shots):
        return pf.check(pf.UNAVAILABLE, why="shot_entry_not_a_record")

    compositions = [shot.get("composition") for shot in shots]
    # ABSENT AND CORRUPT ARE DIFFERENT FACTS, and the corpus is where it shows:
    # 31 of 101 stored clips have no `composition` on any shot, because they
    # were rendered before the key existed. They are OLD, not broken, and the
    # first version of this check reported all 31 as "outside the closed list" —
    # which would have read as 31 corrupt records. The verdict is `unavailable`
    # either way; the reason is what somebody acts on.
    if any(c is None for c in compositions):
        return pf.check(pf.UNAVAILABLE, why="shots_do_not_say_how_they_are_composed",
                        evidence={"shots": len(shots)})
    if any(c not in ("crop", "fit") for c in compositions):
        # Present and outside the list is the other thing. Reading it as a `fit`
        # would buy the pass this check exists to withhold.
        return pf.check(pf.UNAVAILABLE, why="composition_not_in_the_closed_list")

    needs = [i for i, c in enumerate(compositions) if c == "crop"]
    if not needs:
        return pf.check(pf.PASS,
                        evidence={"shots": len(shots), "crop_shots": 0,
                                  "why": "every_delivered_shot_keeps_the_"
                                         "whole_frame"})
    view = sidecar.get("regime_view")
    basis = (view.get("target_basis") if isinstance(view, dict) else None)
    evidence = {"shots": len(shots), "crop_shots": len(needs),
                "target_basis": basis}
    if not basis:
        return pf.check(
            pf.UNAVAILABLE, evidence=evidence,
            why="crop_shots_need_a_target_and_regime_view.target_basis_is_absent")
    if basis == "unanchored_face":
        # A face was followed; nothing established it was the RIGHT face. That
        # is the Moist case — 14 of 14 exports carry the browser's face, and a
        # human confirmed on 31 August that the crop does follow it — and it is
        # neither a demonstrated defect nor a demonstrated target.
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="the_crop_followed_a_face_nothing_anchored")

    # `target_basis` SAYS HOW THE TARGET WAS DEFINED, NOT WHETHER IT WAS THERE,
    # and treating it as the answer passed two states that are the check's own
    # subject: sixteen samples with `evidence.target == 0.0`, and zero samples
    # with `target_covered: false`. A basis is a method; presence is a
    # measurement, and only the measurement can settle this.
    #
    # ONLY THE EVIDENCE IS READ, never the segment's proposed regime. The face
    # timeline is a measurement of the source; the regime beside it is R3b's
    # PROPOSAL, and nothing here demonstrates the delivered shots materialised
    # it — reading it would compare a delivered crop against a plan.
    if view.get("target_covered") is not True:
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="the_target_timeline_did_not_cover_this_clip")
    segments = view.get("segments")
    if not isinstance(segments, list) or not segments:
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="no_per_segment_target_evidence")

    absent, unmeasured = [], []
    for i in needs:
        shot = shots[i]
        seen = present = False
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if _overlaps(shot, seg):
                got = (seg.get("evidence") or {}).get("target")
                covered = (seg.get("evidence_coverage") or {}).get("target")
                if not isinstance(covered, int) or covered <= 0:
                    continue
                if not isinstance(got, (int, float)) or isinstance(got, bool):
                    continue
                seen = True
                # ANY presence at all, which is the weakest claim the evidence
                # supports. A fraction would be a threshold chosen to fit an
                # answer, and §4's bands are already the cautionary tale.
                present = present or float(got) > 0.0
        (unmeasured if not seen else absent if not present else []).append(i)

    evidence.update({"crop_shots_with_no_target": absent,
                     "crop_shots_never_sampled": unmeasured})
    if absent:
        # NOT A DEMONSTRATED ABSENCE, and calling it one was wrong. This branch
        # reported `fail` on 12 clips and a frame refuted it: on
        # `pilot2c8a/ec47597c60f2` at 0.70s the check declares the target absent
        # in 17 of 17 crop shots while the creator fills the delivered frame.
        #
        # `evidence.target` does start from source detections, but it reaches
        # here through the anchor filter and the hysteresis. A zero therefore
        # says THE TRACK LOST HIM, which is a fact about the tracker — and 12 of
        # those shots carry raw detections compatible with the anchor. Missing
        # detection is uncertainty, not proof of an empty frame, which is this
        # repo's oldest rule arriving one layer in.
        #
        # So there is no route to a subject FAILURE today. Demonstrating one
        # needs a signal nobody has: whether the delivered window still contains
        # what the moment is about. Saying so is the honest state; the 12 are
        # kept in the evidence so the next reader can find them.
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="the_anchored_track_lost_the_target_which_is_not_"
                            "evidence_the_frame_is_empty")
    if unmeasured:
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="a_crop_spans_seconds_nobody_sampled")
    return pf.check(pf.PASS, evidence=evidence)


def frame(chrome: Any) -> dict:
    """A usable frame in `fit`, and no dominant browser chrome.

    `source_chrome` answers the second half and only the second half: it is a
    WARNING about publishing, never a verdict, so a `detected` is a revisable
    finding and a `not_detected` is not a pass — its recall is low and
    undemonstrated, and the module says so.
    """
    from services.clipper import source_chrome as sc

    if not isinstance(chrome, dict) or chrome.get("state") not in sc.STATES:
        return pf.check(pf.UNAVAILABLE, why="no_chrome_detection")
    state = chrome["state"]
    if state == sc.DETECTED:
        return pf.check(pf.FAIL, why="player_chrome_in_the_export",
                        severity=pf.REVISABLE,
                        evidence={"frames": chrome.get("frames_with_a_control")})
    if state == sc.UNAVAILABLE:
        return pf.check(pf.UNAVAILABLE,
                        why=chrome.get("why_unavailable") or "undecided")
    # `not_detected` is a weak look, not a clean bill — and the other half of
    # this check, whether a `fit` shot's frame is usable at all, has no source.
    return pf.check(pf.UNAVAILABLE,
                    why="chrome_not_detected_is_not_clean_and_the_fit_frame_"
                        "check_has_no_source")



def boundary(view: Any) -> dict:
    """Does the DELIVERED window start and end in a sane place.

    IT DOES NOT READ `eligible`, and that is the correction. `eligible` is a
    verdict about the MOMENT — whether the window is worth keeping — and R5
    lets it be True on the strength of a repair that `boundary_view` merely
    PROPOSED. Nothing applied that repair to the export sitting on disk, so
    "this moment could be made complete" was being read as "this file's edges
    are good". The same class as `caption_plan.y_pct`: a plan is not the
    delivered artefact.

    So it reads the defects instead, which are measurements of the window as it
    stands, and it takes BOTH of R5's axes:

        blocking    a cut inside a word, an unfinished end, an orphan tail.
                    About the moment, and also about this file.
        technical   `clipped_release`, `dead_tail`. R5 deliberately left these
                    out of `eligible` and said in as many words that whether
                    they may reach a board is R7's question. This is R7. 22 of
                    58 pilot exports end within 50ms of the last word, and that
                    is a property of the delivered file.

    `eligible` was also read loosely enough to accept `0`, `1`, `"false"` and
    `[]` as answers — `0 is False` is False, so an integer walked through the
    `is False` guard into a pass. Nothing here is truth-tested any more; the
    lists are read as lists or the check is unavailable.
    """
    from services.clipper.boundary_completion import BLOCKING, TECHNICAL

    if not isinstance(view, dict):
        return pf.check(pf.UNAVAILABLE, why="no_boundary_view")
    from services.clipper.boundary_completion import DEFECTS, UNKNOWNS

    defects, unknown = view.get("defects"), view.get("unknown")
    if not isinstance(defects, list) or not isinstance(unknown, list):
        # Supplied wrongly, which is not the same as not supplied — and the
        # difference is somebody's mistake rather than a missing transcript.
        return pf.check(pf.UNAVAILABLE, why="boundary_view_is_not_a_record")
    # THE CONTAINER WAS VALIDATED AND THE CONTENTS WERE NOT, which just moved
    # the hole: `defects=[7]` is a list, so it passed the type check, matched
    # nothing in the closed sets, and came out a clean PASS. So did a defect
    # name with a typo in it. A record that speaks a vocabulary this does not
    # know is refused, not read as free of defects.
    if any(str(d) not in DEFECTS for d in defects):
        return pf.check(pf.UNAVAILABLE, why="boundary_defect_not_in_the_closed_list")
    if any(str(u) not in UNKNOWNS for u in unknown):
        return pf.check(pf.UNAVAILABLE, why="boundary_unknown_not_in_the_closed_list")

    found = [str(d) for d in defects if str(d) in BLOCKING | TECHNICAL]
    evidence = {"defects": [str(d) for d in defects],
                "unknown": [str(u) for u in unknown],
                "measurements": view.get("measurements"),
                # KEPT, NOT USED. It is the moment's verdict and a reader
                # comparing the two should be able to see both.
                "eligible_as_a_moment": view.get("eligible")}
    if found:
        return pf.check(pf.FAIL, why=",".join(sorted(set(found))),
                        severity=pf.REVISABLE, evidence=evidence)
    if unknown:
        return pf.check(pf.UNAVAILABLE, why=",".join(str(u) for u in unknown),
                        evidence=evidence)
    return pf.check(pf.PASS, evidence=evidence)


def provenance(sidecar: Any, *, export_path: Any = None) -> dict:
    """Both halves: the recipe was not edited, AND this is the file it produced.

    Recomputing is what makes it a check at all: copying the stored value would
    let a plan edited after the render carry a stale fingerprint and pass. A
    MISMATCH is therefore a real, rejectable finding — the artefact is not the
    one the plan describes.

    A MATCH IS NOT PROVENANCE, and this is the correction. The digest is taken
    over `FINGERPRINT_KEYS`, and `render_input` fills every absent key with
    `None`, so a sidecar carrying nothing but the digest of an empty recipe
    validates perfectly. That check was passing on the self-consistency of a
    projection with nothing in it. Worse, the recipe never touches the delivered
    file: no size, no hash, no duration of the mp4 — `FINGERPRINT_KEYS`' own
    comment says the output size has no shared authority to read it from. So a
    valid digest says the recipe was not edited after the render, and says
    nothing about whether this mp4 is what the recipe produced.

    SO THE SECOND HALF IS A SECOND MEASUREMENT. `output_identity` probes the
    delivered file at render time and records its digest, bytes and dimensions,
    and this compares that record against the file now. The old comment said the
    output size had "no shared authority to read it from"; the delivered file is
    that authority, and reading the artefact rather than agreeing a constant is
    the same move that settled the caption position and the composition count.

    A pass therefore needs all three: a digest that recomputes, a recipe with
    something in it, and a file that still matches what the render measured.
    Every stored clip predates the second half, so they stay `unavailable` — but
    the reason is now a missing field rather than a missing idea.
    """
    from services.clipper.edit_quality import (FINGERPRINT_MISMATCH,
                                               FINGERPRINT_VALID,
                                               fingerprint_status)
    from services.clipper.render_input import FINGERPRINT_KEYS

    if not isinstance(sidecar, dict):
        return pf.check(pf.UNAVAILABLE, why="sidecar_unreadable")
    try:
        status = fingerprint_status(sidecar)
    except Exception:
        return pf.check(pf.UNAVAILABLE, why="fingerprint_unreadable")
    present = [k for k in FINGERPRINT_KEYS if sidecar.get(k) is not None]
    evidence = {"fingerprint_status": status,
                "recipe_keys_present": len(present),
                "recipe_keys": len(FINGERPRINT_KEYS)}
    if status == FINGERPRINT_MISMATCH:
        # Nothing a correction can move, which is what makes it rejectable.
        return pf.check(pf.FAIL, why="fingerprint_mismatch",
                        severity=pf.REJECTABLE, evidence=evidence)
    if status != FINGERPRINT_VALID:
        return pf.check(pf.UNAVAILABLE, why="no_stored_fingerprint",
                        evidence=evidence)
    if not present:
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="the_digest_is_valid_over_an_empty_recipe")

    from services.clipper import output_identity as oid

    recorded = sidecar.get("output_identity")
    evidence["output_identity"] = (
        recorded.get("refused") or "recorded"
        if isinstance(recorded, dict) else None)
    if not isinstance(recorded, dict):
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="the_sidecar_does_not_describe_the_delivered_file")
    if export_path is None:
        # The record exists and nobody offered the file to check it against.
        # That is a caller that did not ask, not a clip that passed.
        return pf.check(pf.UNAVAILABLE, evidence=evidence,
                        why="the_delivered_file_was_not_offered_for_comparison")

    same, why_not = oid.matches(recorded, export_path)
    evidence["file_matches"] = same
    if same is False:
        # The mp4 beside this plan is not the one the render measured. Nothing a
        # correction can move.
        return pf.check(pf.FAIL, why=str(why_not), severity=pf.REJECTABLE,
                        evidence=evidence)
    if same is None:
        return pf.check(pf.UNAVAILABLE, why=str(why_not or "unavailable"),
                        evidence=evidence)
    return pf.check(pf.PASS, evidence=evidence)


def checks_for(sidecar: Any, *, chrome: Any = None, placement: Any = None,
               contrast: Any = None, source_captions: Any = None,
               boundary_view: Any = None,
               own_caption_layer: bool | None = None,
               export_path: Any = None) -> dict[str, dict]:
    """All seven, from whatever is available. Missing inputs stay unavailable."""
    return {
        pf.GEOMETRY: geometry(sidecar),
        pf.EQUIVALENCE: equivalence(sidecar),
        pf.SUBJECT: subject(sidecar),
        pf.FRAME: frame(chrome),
        pf.CAPTIONS: captions(placement, contrast, source_captions,
                              own_layer=own_caption_layer),
        pf.BOUNDARY: boundary(boundary_view),
        pf.PROVENANCE: provenance(sidecar, export_path=export_path),
    }
