"""Does the delivered crop keep the SOURCE's own burned subtitle — D-sublot 2.

`publish_captions` answers `unavailable` for every export that suppressed our
caption layer, and it names the missing measurement in its own reason string:
"our layer was suppressed so the source subtitle's legibility is what matters
and is unmeasured". This is that measurement.

WHY IT IS NOT A CROP-PLANNER CHANGE, which is what the batch was scoped as.
Measured on `pilotf81b` (go ghost), the source that started this: the subtitle
band runs x 0.25..0.725 of a 2560-wide frame — 1216 pixels — and every 9:16
window on a 2560x1440 source is 810 pixels wide. The band does not fit in the
frame at any position, so "move the crop to keep the subtitle" has no solution
to move to. A planner change would have produced a smaller cut and reported it
as a fix.

    band          x  640..1856   (1216 px)
    face camera   x  858..1668   ( 810 px)  ->  66.6% of the band survives
    game camera   x 1718..2528   ( 810 px)  ->  11.3% survives

So what this module owes is the FACT, per export, in a form the publish check
can act on — and the choice between letterboxing the whole frame, re-enabling
our own layer, or accepting the cut belongs to whoever reads it.

FRACTIONS TRAVEL AND PIXELS DO NOT. The detector runs on the PROXY and reports
the band as fractions of that frame, so this converts straight to source pixels
without a proxy scale factor — and therefore without `evidence_map`'s
`proxy_dimensions_unknown` refusal, which exists for detectors that report
pixels. Nothing here may be handed a pixel box.

IT READS THE DELIVERED WINDOW, never `shot["rect"]`. 837 of the corpus's 1,965
`crop` shots have a delivered window that differs from the planner's rectangle,
so the rectangle would answer a question about a picture nobody received.
`evidence_map.crop_window` is the renderer's own arithmetic.

AND A SHOT NOBODY COULD MAP IS NOT A SHOT THAT KEPT IT. Refusals are counted
and reported on their own row; they never enter the worst case, and they stop
`kept` from being reachable. A measured cut still wins over them, for the same
reason it does in `publish_captions`: a defect that was seen does not stop being
seen because something else could not be looked at.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["KEPT", "CUT", "UNAVAILABLE", "STATES", "NO_BAND", "NO_SHOTS",
           "NO_GEOMETRY", "NOT_PRESENT", "IMPOSSIBLE", "survival",
           "survival_for"]

KEPT = "kept"
CUT = "cut"
UNAVAILABLE = "unavailable"
STATES: tuple[str, ...] = (KEPT, CUT, UNAVAILABLE)

#: No band to check. NOT "the crop keeps it" — a source with no detected
#: subtitle has nothing to lose, and a source whose detector failed has an
#: unknown amount to lose, and the caller has to be able to tell those apart.
NO_BAND = "no_source_caption_band"
NO_SHOTS = "no_shots_to_check"
#: The project's verdict is not `present`, so there is no subtitle to preserve
#: and nothing to measure. Its own reason: routing it through `NO_BAND` — or
#: worse, through whichever guard happened to fire first — names the wrong
#: thing as missing, and a reason string is the only place the caller learns
#: what to go and get.
NOT_PRESENT = "the_source_was_not_judged_to_carry_captions"
NO_GEOMETRY = "source_dimensions_unknown"
#: How far outside the crop the band may poke and still count as kept, in
#: CANVAS pixels. It is not a tolerance for how much text may be lost: the band
#: is built from fractions and the crop from integer sizes, so the two disagree
#: in the last bits of a float, and comparing areas against exactly 1.0 reported
#: a fully contained band as cut. Half a canvas pixel cannot hide a character.
_CONTAINED_SLACK_PX = 0.5
#: The band is wider than any window the aspect ratio allows. Reported as its
#: own reason because it is the one finding a re-framing cannot act on.
IMPOSSIBLE = "the_band_is_wider_than_any_9_16_window_on_this_source"


def _fraction(value: Any) -> float | None:
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    if got != got or got in (float("inf"), float("-inf")):
        return None
    return got


def _band_rect(band: Any, src_w: int, src_h: int
               ) -> tuple[float, float, float, float] | None:
    """The band's union rectangle in SOURCE pixels, or None.

    `extent` is `None` on a band no box ever landed in, and that absence must
    not read as a zero-sized rectangle — a zero-width band survives every crop
    and would report a clean keep on a source nobody measured.
    """
    if not isinstance(band, dict):
        return None
    extent = band.get("extent")
    if not isinstance(extent, dict):
        return None
    got = [_fraction(extent.get(k)) for k in ("x0", "x1", "y0", "y1")]
    if any(v is None for v in got):
        return None
    x0, x1, y0, y1 = got  # type: ignore[misc]
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        return None
    return (x0 * src_w, y0 * src_h, (x1 - x0) * src_w, (y1 - y0) * src_h)


def _window(shot: dict, style: Any, src_w: int, src_h: int):
    """The delivered crop for one shot: `(x, y, w, h)` or a refusal string."""
    from services.clipper import evidence_map as em

    return em.crop_window(shot, src_w=src_w, src_h=src_h,
                          style=style if isinstance(style, dict) else {})


def survival(band: Any, shots: Sequence[dict] | None, style: Any,
             src_w: Any, src_h: Any) -> dict:
    """`source_caption_survival_v1`: how much of the band the viewer receives.

    `worst_visible` is the smallest area fraction of the band that survives any
    ONE shot, over the shots that could be mapped. `kept` needs every shot
    mapped and every one of them whole; a single measured shortfall is `cut`.
    """
    from services.clipper.dynamic_geometry import canvas_size

    out: dict[str, Any] = {
        "schema": "source_caption_survival_v1", "state": UNAVAILABLE,
        "why": None, "shots": 0, "measured": 0, "refused": 0,
        "refusals": [], "worst_visible": None, "worst_shot": None,
        "cut_shots": 0,
        "band_w_px": None, "window_w_px": None, "fits_at_all": None,
    }
    sw, sh = _fraction(src_w), _fraction(src_h)
    if not sw or not sh or sw < 1 or sh < 1:
        out["why"] = NO_GEOMETRY
        return out
    sw, sh = int(sw), int(sh)
    rect = _band_rect(band, sw, sh)
    if rect is None:
        out["why"] = NO_BAND
        return out
    if not isinstance(shots, Sequence) or isinstance(shots, (str, bytes)):
        out["why"] = NO_SHOTS
        return out
    shots = [s for s in shots if isinstance(s, dict)]
    out["shots"] = len(shots)
    if not shots:
        out["why"] = NO_SHOTS
        return out

    _cw, _ch, off_y = canvas_size(sw, sh)
    bx, by, bw, bh = rect
    by += off_y                      # the pad is applied before the crop
    area = bw * bh
    out["band_w_px"] = round(bw, 1)

    windows = [_window(s, style, sw, sh) for s in shots]

    # THE ONE THING A RE-FRAME CANNOT FIX, and it is computed from the NARROWEST
    # window the clip actually uses: a band wider than that has no position
    # inside it. Reported even when the worst case is fine, because it is the
    # difference between "move the crop" and "there is nowhere to move it to".
    widths = [float(w[2]) for w in windows if not isinstance(w, str)]
    if widths:
        out["window_w_px"] = round(min(widths), 1)
        out["fits_at_all"] = bool(bw <= min(widths))

    worst: float | None = None
    worst_shot: Any = None
    refusals: list[str] = []
    measured = 0
    cut_shots = 0
    for shot, crop in zip(shots, windows):
        if isinstance(crop, str):
            refusals.append(crop)
            continue
        cx, cy, cw, ch = crop
        x0, y0 = max(bx, cx), max(by, cy)
        x1, y1 = min(bx + bw, cx + cw), min(by + bh, cy + ch)
        visible = 0.0 if (x1 <= x0 or y1 <= y0) else (x1 - x0) * (y1 - y0) / area
        # CONTAINMENT decides `kept`, not the area ratio. The two are the same
        # question and only one of them survives the arithmetic: `visible`
        # divides a difference of floats by a product of floats, and a band the
        # crop contains exactly came out at 0.9999999999999996.
        poke = max(cx - bx, (bx + bw) - (cx + cw),
                   cy - by, (by + bh) - (cy + ch))
        measured += 1
        if worst is None or visible < worst:
            worst, worst_shot = visible, shot.get("index")
        if poke > _CONTAINED_SLACK_PX:
            cut_shots += 1

    out["measured"] = measured
    out["refused"] = len(refusals)
    out["refusals"] = sorted(set(refusals))
    if worst is None:
        out["why"] = ",".join(out["refusals"]) or NO_SHOTS
        return out
    out["worst_visible"] = round(worst, 4)
    out["worst_shot"] = worst_shot
    out["cut_shots"] = cut_shots
    if cut_shots:
        out["state"] = CUT
        out["why"] = IMPOSSIBLE if out["fits_at_all"] is False else None
        return out
    if refusals:
        # Every shot that could be read keeps it whole, and some could not be
        # read. That is not a demonstrated keep.
        out["why"] = ",".join(out["refusals"])
        return out
    out["state"] = KEPT
    return out


def survival_for(sidecar: Any, source_captions: Any) -> dict:
    """The measurement straight off a stored plan and its project's verdict.

    Pure — the plan carries `shots`, `style`, `src_w` and `src_h`, and the
    detector's band is already in fractions — so the seven checks can ask for it
    without a file read, and `survival` stays injectable for a test.

    THE BAND IS ONLY ASKED FOR WHEN THE SOURCE IS `present`. An `absent` or
    `unknown` verdict still carries a band — `classify` returns the best one it
    saw either way — and measuring the crop against a band the detector refused
    to call a subtitle would report "the crop cuts the source's subtitle" about
    a source that has none.
    """
    from services.clipper import source_captions as scap

    if (not isinstance(source_captions, dict)
            or source_captions.get("state") != scap.PRESENT):
        return {**survival(None, None, None, None, None), "why": NOT_PRESENT}
    plan = (sidecar or {}).get("dynamic_plan") if isinstance(sidecar, dict) else None
    if not isinstance(plan, dict):
        return survival(source_captions.get("band"), None, None, None, None)
    return survival(source_captions.get("band"), plan.get("shots"),
                    plan.get("style"), plan.get("src_w"), plan.get("src_h"))
