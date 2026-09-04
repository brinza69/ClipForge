"""Should this export burn a caption layer of its own — and who decided.

THE DEFECT THIS EXISTS TO STOP RECREATING. 37 of 101 stored clips are rejected
by the publish preflight for carrying two caption tracks, and on 31 August a
human confirmed it on 4 of 4 clips watched: under ClipForge's layer, the
source's own burned subtitles are still visible. Re-rendering those projects
with the layer switched on would produce the same 37 defects again, from a
corpus that now knows better.

WHY THE DETECTOR DOES NOT DECIDE THIS. `source_captions` answers the question
and answered it correctly — its `present` on `pilotf81b` is exactly what the
human saw — but its thresholds were chosen with the answer visible on four
sources, and it says so: `calibrated: false` rides with every verdict. A
detector that has never been calibrated must not silently remove the captions
from somebody's export, because the failure is invisible and expensive: a clip
ships with no readable text at all and nothing reports it.

So the switch is a HUMAN's, and the detector's verdict travels beside it as a
suggestion. That is not caution for its own sake — it is the difference between
a measurement and a decision, and this module keeps them apart on purpose:

    the human said so    -> applied
    the detector says so -> recorded, and the layer still burns

WHEN THE DETECTOR IS CALIBRATED, this is the one place that has to change, and
the shape of the change is a single branch rather than a search through the
render path.
"""

from __future__ import annotations

from typing import Any

__all__ = ["BURN", "SUPPRESS", "DECIDED_BY", "SETTING", "decide"]

#: What the render does with ClipForge's own caption layer.
BURN = "burn"
SUPPRESS = "suppress"

#: Who decided it. `default` is not a decision anybody made — it is what happens
#: when nobody has said anything, and naming it keeps it out of the same bucket
#: as a human's answer.
HUMAN = "human"
DEFAULT = "default"
DECIDED_BY: tuple[str, ...] = (HUMAN, DEFAULT)

#: The project setting a human sets. Three-valued on purpose: `True` and `False`
#: are answers and absent is not one of them, which is what lets `default` mean
#: "nobody has looked" instead of "somebody said no".
SETTING = "source_has_burned_captions"


def decide(setting: Any = None, detector: Any = None) -> dict:
    """`{"action", "why", "decided_by", "detector"}` — burn or suppress, and why.

    `setting` is the project's own three-valued answer; `detector` is a
    `source_captions` verdict, recorded and never applied.
    """
    from services.clipper import source_captions as scap

    state = detector.get("state") if isinstance(detector, dict) else None
    calibrated = (detector.get("calibrated")
                  if isinstance(detector, dict) else None)
    out: dict[str, Any] = {
        "schema": "clipper_caption_policy_v1",
        "detector": state if state in scap.STATES else None,
        # It rides here for the same reason it rides on the verdict itself: a
        # reader deciding whether to trust this needs to know the detector never
        # was calibrated, and a field nobody has to look up is the way to say it.
        "detector_calibrated": bool(calibrated) if calibrated is not None else None,
    }

    if setting is True:
        out.update({"action": SUPPRESS, "decided_by": HUMAN,
                    "why": "a_person_declared_the_source_already_carries_captions"})
        return out
    if setting is False:
        out.update({"action": BURN, "decided_by": HUMAN,
                    "why": "a_person_declared_the_source_carries_none"})
        return out

    # NOBODY HAS SAID, so the layer burns — which is the behaviour that has
    # always shipped, and the safe direction: a duplicate layer is visible and
    # fixable, a missing one is a clip with no text and nothing to notice it by.
    out.update({"action": BURN, "decided_by": DEFAULT,
                "why": ("the_detector_says_the_source_has_captions_and_nobody_"
                        "has_confirmed_it")
                if state == scap.PRESENT else "nobody_has_declared_the_source"})
    return out
