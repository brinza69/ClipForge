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

__all__ = ["BURN", "SUPPRESS", "DECIDED_BY", "SETTING", "decide", "effective"]

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


def decide(setting: Any = None, detector: Any = None, clip_setting: Any = None,
           layer: Any = None) -> dict:
    """`{"action", "why", "decided_by", "detector"}` — burn or suppress, and why.

    `setting` is the project's own three-valued answer; `detector` is a
    `source_captions` verdict, recorded and never applied. `clip_setting` is
    the same three-valued answer for ONE clip; a real bool wins over
    `setting` (`decided_by: human`, `scope: clip`) — `None` defers to the
    project exactly as if `clip_setting` had never been passed.

    `layer` is a person's choice of ClipForge's OWN layer for this clip (SC3, codex-verdict-next-33 §3):
    "burn" or "suppress" wins over both answers for the LAYER only — the answers stay what the source
    carries — and anything else, None included, leaves this function exactly as it was, so a sidecar
    written without a choice is byte-identical. Burning over source text that stays is refused at the
    render (`source_treatment_store.for_render`), not here.
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

    if isinstance(layer, str) and layer in (BURN, SUPPRESS):
        out.update({"action": layer, "decided_by": HUMAN, "scope": "clip",
                    "why": f"a_person_chose_to_{layer}_the_layer"})
        return out

    # A clip's own answer beats the project's. `isinstance(x, bool)`, never
    # `x in (True, False)` — `1 == True` would let an integer through as a
    # verdict nobody gave.
    if isinstance(clip_setting, bool):
        if clip_setting:
            out.update({"action": SUPPRESS, "decided_by": HUMAN, "scope": "clip",
                        "why": "a_person_declared_this_clip_already_carries_captions"})
        else:
            out.update({"action": BURN, "decided_by": HUMAN, "scope": "clip",
                        "why": "a_person_declared_this_clip_carries_none"})
        return out

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


def effective(setting: Any = None, clip_setting: Any = None, layer: Any = None) -> dict:
    """`{"action", "scope", "decided_by", "why"}` for the editor to SHOW.

    Read off `decide()` with the same two arguments the render passes it (the
    render consults no detector either), so the two cannot disagree. A separate
    dict rather than a `scope` added to `decide()`'s project branches: that dict
    is written into export sidecars, and a project-level answer's sidecar must
    stay byte-identical to what it was before a clip could have one.
    """
    got = decide(setting, clip_setting=clip_setting, layer=layer)
    scope = got.get("scope") or ("project" if got["decided_by"] == HUMAN else "default")
    return {"action": got["action"], "scope": scope,
            "decided_by": got["decided_by"], "why": got["why"]}
