"""§R7's caption check: duplicated, covering something, or unreadable.

Split out of `publish_checks` at the 500-line limit, and it is a subject of its
own anyway: the other six checks read one artefact each, this one reconciles
four — the project's source-caption verdict, whether THIS export burned a layer
of its own, what the caption covers per shot, and whether the palette can be
read at all. Re-exported from `publish_checks` so callers did not have to move.

THE ORDER IS THE RULE, and it is the same order every check in this batch owes:
a MEASURED defect is a `fail` whatever else could not be looked at; only then do
missing halves make it `unavailable`; a `pass` needs every half demonstrated.
"""

from __future__ import annotations

from typing import Any

from services.clipper import publish_preflight as pf

__all__ = ["ON_A_FACE", "TWO_LAYERS", "captions"]


#: The one caption defect a bounded correction can act on, named here because
#: `bounded_correction` has to recognise it and a substring match on the reason
#: string would let `highlight_floor_2.33` buy a caption move.
ON_A_FACE = "the_caption_sits_on_a_face"
#: Both layers demonstrated. The word says both, because a reject that rests on
#: the source alone is a reject about the SOURCE.
TWO_LAYERS = "the_source_carries_captions_and_so_does_this_export"


def captions(placement: Any, contrast: Any, source: Any,
             *, own_layer: bool | None = None) -> dict:
    """Duplicated, covering something, or unreadable — three halves, one answer.

    THE ORDER IS THE RULE, and getting it wrong is what this function did:
    a MEASURED defect is a fail whatever else could not be looked at; only then
    do missing halves make it unavailable; a pass needs every half demonstrated.
    Before, a caption measured to sit on a face came back `unavailable` because
    nobody had a UI detector — the strongest fact in the record silenced by the
    weakest.

    IT ALSO READS THE CONTRACT IT IS GIVEN. It looked for `lands_on`, `worst`
    and `refused`; `caption_corpus.measure` sends `on_face`, `worst_share`,
    `placement_refused` and `placement_unavailable`. `lands_on` was never there,
    so `ON_FACE in placement["lands_on"]` was `in []` on every clip in the
    corpus and the face signal reached nothing.

    A DUPLICATE LAYER NEEDS BOTH LAYERS. `source_captions` is a verdict about
    the PROJECT's proxy; on its own it says the source has burned text
    somewhere, which is not the same sentence as "this export shows two caption
    tracks". `own_layer` is the second half: whether ClipForge burned its own
    layer into this render. With both, the reject is about the export. With only
    the first it is about the source, and it used to be issued anyway — while
    the inverse, no source verdict at all, could reach a PASS on a check whose
    first word is "not duplicated".

    WHAT THE REJECT STILL DOES NOT ESTABLISH, carried in its own evidence
    rather than in a comment: the detector samples the whole proxy, so it cannot
    say the source's caption band was on screen during THIS window. And
    `own_layer` comes from the `.ass`, which is the burn instruction rather than
    a frame read — the R6 defect was exactly an `.ass` event that libass drew
    into nothing.
    """
    from services.clipper import source_captions as scap

    demonstrated: list[str] = []
    unestablished: list[str] = []
    evidence: dict[str, Any] = {"source_captions": None, "own_layer": own_layer,
                                "source_band_window_coverage": "unavailable"}
    duplicate = False

    # --- half one: is there a second caption layer -------------------------
    state = source.get("state") if isinstance(source, dict) else None
    evidence["source_captions"] = state
    evidence["source_detector_calibrated"] = (
        source.get("calibrated") if isinstance(source, dict) else None)
    if state == scap.PRESENT:
        if own_layer is True:
            duplicate = True
            demonstrated.append(TWO_LAYERS)
            evidence["band"] = source.get("band")
        elif own_layer is None:
            unestablished.append("source_carries_captions_and_whether_this_"
                                 "export_adds_its_own_is_not_established")
        # `own_layer is False` is one layer, not two. The source captions the
        # clip and we added nothing over them.
    elif state != scap.ABSENT:
        # `unknown`, or no verdict supplied at all. Not "the source is clean" —
        # that reading is how a check named `not_duplicated` reached PASS
        # without ever asking the question.
        unestablished.append("no_source_caption_verdict")

    # --- A SUPPRESSED LAYER IS NOT EVALUATED FROM THE ASS IT DID NOT USE ---
    # Six failures on go ghost said `own_layer: false` and
    # `the_caption_sits_on_a_face` in the same record. Both halves came from
    # `caption_corpus.measure`, which reads the `.ass` beside the render — and
    # after a `suppress` that file describes a layer nobody burned. The 15
    # pilotf81b exports still carry one from an earlier run, so a check that
    # relied on the file being gone would be wrong on the corpus as it stands;
    # the decision is what settles it, not the leftovers.
    #
    # IT DOES NOT BECOME A PASS. Suppressing our layer leaves the SOURCE's
    # subtitle as the only text on screen, and whether that is legible in the
    # delivered frame is unverified — a different question, with no measurement
    # behind it yet.
    if own_layer is False:
        evidence["suppressed_layer"] = True
        unestablished.append("our_layer_was_suppressed_so_the_source_subtitles_"
                             "legibility_is_what_matters_and_is_unmeasured")
        if demonstrated:
            return pf.check(
                pf.FAIL, why=",".join(sorted(demonstrated)),
                severity=pf.REJECTABLE if duplicate else pf.REVISABLE,
                evidence={**evidence, "unestablished": sorted(unestablished)})
        return pf.check(pf.UNAVAILABLE, why=",".join(sorted(unestablished)),
                        evidence=evidence)

    # --- half two: can the palette be read at all --------------------------
    # BOTH LEGS HAVE TO BE THERE. `{}` is a dict with no `refused` key, so it
    # walked into this branch, found neither `fill` nor `highlight` to object
    # to, and left the palette half looking demonstrated — a check reading a
    # verdict nobody produced. `fill` is the one leg every style has; a style
    # with no highlight paints every word in the fill, so `highlight` may be
    # absent, but `caption_contrast` always emits the key.
    _legs_present = (isinstance(contrast, dict) and "fill" in contrast
                     and "highlight" in contrast
                     and isinstance(contrast.get("fill"), dict))
    if isinstance(contrast, dict) and not contrast.get("refused") and _legs_present:
        accepted = contrast.get("accepted_shortfall")
        for part in ("fill", "highlight"):
            leg = contrast.get(part)
            if isinstance(leg, dict) and not leg.get("clears_large_text"):
                if accepted:
                    # A SHORTFALL A HUMAN ACCEPTED IS NOT A DEFECT THIS FINDS
                    # AGAIN. `Neon Pop` and `Viral Gradient` were decided on
                    # 2026-08-31 and the acceptance is keyed on the palette, so
                    # a repaint that makes either worse stops matching and the
                    # gate reopens by itself.
                    evidence.setdefault("accepted_shortfalls", []).append(
                        {part: leg.get("floor"), "why": accepted})
                else:
                    demonstrated.append(f"{part}_floor_{leg.get('floor')}")
    else:
        unestablished.append("contrast_unavailable")

    # --- half three: what the caption covers -------------------------------
    if isinstance(placement, dict) and not placement.get("placement_refused"):
        if placement.get("on_face") is True:
            # MEASURED. It is a fail even with the other two signals missing:
            # a face under the caption does not become less true because nobody
            # built a UI detector.
            demonstrated.append(ON_A_FACE)
            evidence["worst_share"] = placement.get("worst_share")
        if placement.get("worst_share_complete") is not True:
            # A floor is not a coverage. `over_ui_panel` and `over_source_text`
            # have no per-shot detector, so the worst case is never established
            # and this check can never reach PASS on its own account.
            unestablished.append("placement_not_established")
            evidence["placement_unavailable"] = placement.get(
                "placement_unavailable")
    else:
        unestablished.append("placement_unavailable")

    if demonstrated:
        return pf.check(
            pf.FAIL, why=",".join(sorted(demonstrated)),
            # A duplicate layer is nothing a correction can move; a caption on a
            # face is the one thing that can.
            severity=pf.REJECTABLE if duplicate else pf.REVISABLE,
            evidence={**evidence, "unestablished": sorted(unestablished)})
    if unestablished:
        return pf.check(pf.UNAVAILABLE, why=",".join(sorted(unestablished)),
                        evidence=evidence)
    return pf.check(pf.PASS, evidence=evidence)

