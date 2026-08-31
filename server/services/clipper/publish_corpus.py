"""One clip's seven R7 answers, assembled from everything on disk.

`publish_checks` knows how to read each signal. This knows where each signal
lives, which is a different job and a messier one: the caption position is in
the `.ass`, the boundary needs the candidate the clip was cut from, the source's
own captions are a property of the PROJECT rather than the clip, and the chrome
detector needs the rendered file and several seconds of OCR per frame.

WHAT IT WILL NOT DO IS GUESS. Every path here either finds its input or reports
which one it could not find, and `publish_checks` turns that into `unavailable`
rather than into a pass. The point of R7 is a verdict that says what it rests
on, and an assembler that quietly substitutes a default would take that away at
the last step.

MATCHING A CLIP TO ITS CANDIDATE. The sidecar records the source window it was
cut from and the candidate records the same window, so they are matched on both
edges. 100 of the 101 stored clips match exactly; the one that does not matches
only on its start, and is reported as unmatched rather than attached to the
nearest thing — R5a moved 261 window ends, so "close on one edge" is exactly the
shape a stale match would take.

THE SOURCE-CAPTION VERDICT IS PER PROJECT, and it is the expensive one at about
five seconds of OCR per source, so it is computed once and shared by every clip
of that project. The chrome verdict is per EXPORT and costs a few seconds per
sampled frame, which is why it is opt-in: an answer nobody asked for is not
worth an hour of GPU.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from services.clipper import publish_checks as pk
from services.clipper import publish_preflight as pf

__all__ = ["assemble", "preflight_for"]

#: How close two window edges have to be to be the same window. Tight on
#: purpose: R5a moved 261 window ends by a median of 0.10s, so a loose match
#: would attach a clip to the candidate it USED to be cut from.
_EDGE_S = 0.01


def _style(sidecar: dict) -> Any:
    """The caption style, which the clipper has stored as a repr since it shipped.

    A SUB-RECORD THAT IS NOT A RECORD IS NOT AN EMPTY ONE. `(x or {}).get(...)`
    returns the list itself for `caption_plan: [1]`, and `.get` on a list
    raises — out of a loop over the corpus, so the audit reported on the clips
    before the corrupt one and never said it had stopped. `caption_corpus.measure`
    already refuses this shape by name; this path did not, and a refusal one
    function upstream does not protect a read two functions along.
    """
    plan = sidecar.get("caption_plan")
    if not isinstance(plan, dict):
        return None
    style = plan.get("style")
    if isinstance(style, str):
        try:
            return ast.literal_eval(style)
        except Exception:
            return None
    return style


def _own_caption_layer(placement: Any) -> bool | None:
    """Did THIS export burn a ClipForge caption layer — True, False, or unknown.

    The second half of the duplicate-caption question, and the half a reject
    used to be issued without. `caption_corpus.measure` reads the `.ass` beside
    the render, so `caption_y_source == "ass"` means a caption layer was burned
    in with a position of its own, and `no_ass` means there was no layer to
    burn. Anything else — an unreadable file, no `\\pos`, more than one — is a
    file that could not answer, which is not a no.

    IT IS THE BURN INSTRUCTION, NOT A FRAME READ, and the difference is not
    academic here: the R6 defect was an `.ass` event libass drew into a
    transparent bar. So this establishes that a layer was ASKED for. The
    residual travels in the check's evidence.
    """
    if not isinstance(placement, dict):
        return None
    if placement.get("caption_y_source") == "ass":
        return True
    if placement.get("caption_y_why_not_ass") == "no_ass":
        return False
    return None


def _candidate(analysis: Path, sidecar: dict) -> tuple[dict | None, str | None]:
    """`(the candidate this clip was cut from, why not)`.

    BOTH EDGES, and not the nearest start. R5a moved 261 window ends, so a
    match on one edge is the exact shape a stale attachment would take.
    """
    start, end = sidecar.get("source_start"), sidecar.get("source_end")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (start, end)):
        return None, "sidecar_has_no_source_window"
    path = analysis / "candidates.json"
    if not path.exists():
        return None, "no_candidates_on_disk"
    try:
        cands = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, "candidates_unreadable"
    if not isinstance(cands, list):
        return None, "candidates_not_a_list"
    for cand in cands:
        if not isinstance(cand, dict):
            continue
        if (isinstance(cand.get("start"), (int, float))
                and isinstance(cand.get("end"), (int, float))
                and abs(cand["start"] - start) < _EDGE_S
                and abs(cand["end"] - end) < _EDGE_S):
            return cand, None
    return None, "no_candidate_matches_this_window_on_both_edges"


def _boundary_view(cand: dict | None, words: Any) -> tuple[Any, str | None]:
    """`boundary_completion`'s verdict for the candidate, or why there is none.

    THE WORDS ARE THE WHOLE TRANSCRIPT, and passing `cand["words"]` instead was
    a defect that silently disabled the two checks the gate names. The candidate
    carries `_neighbourhood(...)`'s `inside` list, which drops a word straddling
    either edge — it is neither before the cut, after it, nor wholly inside —
    and a straddling word is EXACTLY what `start_inside_word` and
    `end_inside_word` look for. `_straddled`'s docstring says so. Fed the
    candidate's own list, `_straddled` searched the one collection built by
    removing what it was searching for, and every window came back clean on the
    defect R5a spent a batch removing from 261 of them.

    Reproduced on one window: `eligible=True` with the candidate's words,
    `False` / `end_inside_word` with the transcript's.

    So the transcript is required rather than defaulted. It lives in the DB and
    this module is DB-free, so the caller loads it — and a caller who does not
    gets `unavailable`, not a pass.
    """
    if cand is None:
        return None, None
    if not isinstance(words, list) or not words:
        return None, "no_transcript_words"
    from services.clipper.boundary_completion import boundary_view

    try:
        return boundary_view(cand, words,
                             max_s=float(cand.get("end", 0.0))
                             - float(cand.get("start", 0.0))), None
    except Exception as exc:
        return None, f"boundary_view_raised_{type(exc).__name__}"


def assemble(path: Path, *, source_captions: Any = None,
             chrome: Any = None, words: Any = None) -> dict:
    """Everything one clip's seven checks need, and what is missing.

    `source_captions`, `chrome` and `words` are passed in rather than computed:
    the first is a property of the project, the second costs seconds of OCR per
    frame, and the third lives in the database, which this module does not
    touch. The caller decides how often each is paid for; a caller that supplies
    none of them gets `unavailable`, never a pass.
    """
    from services.clipper import caption_contrast as cc
    from services.clipper.caption_corpus import measure

    out: dict[str, Any] = {"project": path.parent.parent.name,
                           "clip": path.stem, "missing": []}
    try:
        sidecar = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["missing"].append(f"sidecar_unreadable_{type(exc).__name__}")
        sidecar = None
    if not isinstance(sidecar, dict):
        out["missing"].append("sidecar_not_a_record")
        sidecar = None

    placement = measure(path) if sidecar is not None else None
    own_layer = _own_caption_layer(placement)
    if isinstance(placement, dict) and placement.get("refused"):
        out["missing"].append(f"placement_refused_{placement['refused']}")
        placement = None

    contrast = cc.verdict(_style(sidecar)) if sidecar is not None else None
    if isinstance(contrast, dict) and contrast.get("refused"):
        out["missing"].append(f"contrast_refused_{','.join(contrast['refused'])}")

    cand, why = _candidate(path.parent.parent / "analysis", sidecar or {})
    if why:
        out["missing"].append(why)
    view, view_why = _boundary_view(cand, words)
    if view_why:
        out["missing"].append(view_why)

    if source_captions is None:
        out["missing"].append("no_source_caption_verdict")
    if chrome is None:
        out["missing"].append("no_chrome_verdict")

    out["own_caption_layer"] = own_layer
    out["inputs"] = {"sidecar": sidecar, "placement": placement,
                     "contrast": contrast, "boundary_view": view,
                     "source_captions": source_captions, "chrome": chrome}
    return out


def preflight_for(path: Path, *, source_captions: Any = None,
                  chrome: Any = None, words: Any = None,
                  corrections: tuple[str, ...] = ()) -> dict:
    """One clip's assembled inputs, run through the seven checks and the verdict.

    EVERY CLIP GETS A ROW, INCLUDING THE ONE THAT BROKE. A corrupt sub-record
    used to raise out of the loop, so the audit printed the clips before it,
    exited on the traceback, and never said which clip it had stopped at. The
    named refusals below are the ones anybody has reproduced; this catch is for
    the next shape of corruption, and it makes the run fail LOUDLY — the reason
    reaches `missing_inputs`, and `missing_inputs` reaches the exit code.
    """
    try:
        got = assemble(path, source_captions=source_captions, chrome=chrome,
                       words=words)
    except Exception as exc:
        report = pf.preflight({}, corrections=corrections)
        report["project"] = path.parent.parent.name
        report["clip"] = path.stem
        report["missing_inputs"] = [f"assemble_raised_{type(exc).__name__}"]
        return report
    inputs = got["inputs"]
    checks = pk.checks_for(
        inputs["sidecar"], chrome=inputs["chrome"],
        placement=inputs["placement"], contrast=inputs["contrast"],
        source_captions=inputs["source_captions"],
        boundary_view=inputs["boundary_view"],
        own_caption_layer=got["own_caption_layer"])
    report = pf.preflight(checks, corrections=corrections)
    report["project"] = got["project"]
    report["clip"] = got["clip"]
    # WHICH INPUTS WERE NOT THERE, beside the verdict rather than instead of it.
    # `unavailable` says a check could not be made; this says why, and the two
    # are different halves of the same sentence.
    report["missing_inputs"] = got["missing"]
    return report
