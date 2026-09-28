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

__all__ = ["assemble", "preflight_for", "ABSENT", "REFUSED", "UNREADABLE",
           "MISSING_KINDS", "is_integrity_failure"]

#: WHY AN INPUT IS NOT HERE, as a typed answer rather than a sentence somebody
#: downstream has to grep. The audit used to sort these by looking for
#: substrings — `not_a_record`, `unreadable` — in the reason text, and that is
#: not a contract: five separately corrupt inputs walked through it with exit 0
#: and `integrity: true`, because their producers happened to phrase the refusal
#: differently. A NaN caption height, a NaN face coordinate, a NaN outline
#: width, a `style` that is a list, and a transcript that is the number 7.
#:
#: The distinction that matters is not the wording, it is which of three things
#: happened, and only the producer knows:
ABSENT = "absent"          #: nobody supplied it. A corpus of these is a finding.
REFUSED = "refused"        #: supplied, and wrong. Somebody's mistake.
UNREADABLE = "unreadable"  #: present and could not be parsed at all.
MISSING_KINDS: tuple[str, ...] = (ABSENT, REFUSED, UNREADABLE)

#: The contrast module files absences and wrongness under one `refused` list,
#: because for ITS purpose both mean "no verdict". They are different here.
_CONTRAST_ABSENT = frozenset({"no_caption_style", "style_has_no_text_colour",
                              "style_has_no_outline_colour",
                              "style_has_no_outline_width"})


def _missing(name: str, kind: str, detail: Any = None) -> dict:
    if kind not in MISSING_KINDS:
        raise ValueError(f"kind must be one of {MISSING_KINDS}, got {kind!r}")
    return {"input": name, "kind": kind, "detail": detail}


def is_integrity_failure(entry: Any) -> bool:
    """Whether one missing-input entry means the RUN cannot be trusted.

    An absence is a finding — most of the seven checks have no input for most
    clips and that is what R7 exists to report. A refusal or an unreadable
    record is different: the figures then describe a smaller corpus than the
    header claims, which is the defect this whole plan opened on.
    """
    return (isinstance(entry, dict)
            and entry.get("kind") in (REFUSED, UNREADABLE))

#: How close two window edges have to be to be the same window. Tight on
#: purpose: R5a moved 261 window ends by a median of 0.10s, so a loose match
#: would attach a clip to the candidate it USED to be cut from.
_EDGE_S = 0.01


def _style(sidecar: dict) -> tuple[Any, dict | None]:
    """`(the caption style, why there is none)`.

    Stored as a repr since the clipper shipped, so a string is expected and
    parsed. What is NOT expected is a `caption_plan` that is a list or a `style`
    that is one, and those are refusals rather than absences — the difference
    decides whether a run's figures may be quoted.

    A SUB-RECORD THAT IS NOT A RECORD IS NOT AN EMPTY ONE. `(x or {}).get(...)`
    returns the list itself for `caption_plan: [1]`, and `.get` on a list
    raises — out of a loop over the corpus, so the audit reported on the clips
    before the corrupt one and never said it had stopped. `caption_corpus.measure`
    already refuses this shape by name; this path did not, and a refusal one
    function upstream does not protect a read two functions along.
    """
    plan = sidecar.get("caption_plan")
    if plan is None:
        return None, None
    if not isinstance(plan, dict):
        return None, _missing("caption_plan", REFUSED, "not_a_record")
    style = plan.get("style")
    if style is None:
        return None, None
    if isinstance(style, str):
        try:
            return ast.literal_eval(style), None
        except Exception:
            return None, _missing("caption_style", UNREADABLE, "not_a_literal")
    if not isinstance(style, dict):
        # A `style` that is a list reached `caption_contrast` as "not a dict",
        # came back `no_caption_style`, and was filed as an ABSENCE — so a
        # corrupt record read as an export that simply has no captions.
        return None, _missing("caption_style", REFUSED, "not_a_record")
    return style, None


def _own_caption_layer(placement: Any, sidecar: Any = None) -> bool | None:
    """Did THIS export burn a ClipForge caption layer — True, False, or unknown.

    The second half of the duplicate-caption question, and the half a reject
    used to be issued without. `caption_corpus.measure` reads the `.ass` beside
    the render, so `caption_y_source == "ass"` means a caption layer was burned
    in with a position of its own.

    A MISSING `.ass` IS NOT A `False`. The `.ass` is the instruction, the mp4 is
    the artefact, and deleting the instruction after the burn removes nothing
    from the video — a file never written, cleaned up, or written elsewhere all
    look identical, and reading any of them as a demonstrated absence is what
    lets the duplicate check PASS on a clip nobody established anything about.

    THE ONE THING THAT CAN SAY `False` IS A DECLARATION TIED TO THE RENDER, and
    `caption_policy` is now that declaration: a render that suppressed the layer
    because a person said the source already carries captions recorded the
    decision and who made it. That is a fact about the encode, not an inference
    from a missing file.

    IT IS THE BURN INSTRUCTION, NOT A FRAME READ, even when present, and the
    difference is not academic: the R6 defect was an `.ass` event libass drew
    into a transparent bar. So this establishes that a layer was ASKED for. The
    residual travels in the check's evidence.
    """
    policy = sidecar.get("caption_policy") if isinstance(sidecar, dict) else None
    if isinstance(policy, dict):
        from services.clipper import caption_policy as cp

        if policy.get("action") == cp.SUPPRESS:
            return False
        # A recorded `burn` is NOT a `True` here. It says a layer was asked for;
        # whether libass drew one is the `.ass` question below, and the R6
        # defect was exactly an event drawn into nothing.
    if not isinstance(placement, dict):
        return None
    if placement.get("caption_y_source") == "ass":
        return True
    return None


def _candidate(analysis: Path, sidecar: dict) -> tuple[dict | None, dict | None]:
    """`(the candidate this clip was cut from, why not)`.

    BOTH EDGES, and not the nearest start. R5a moved 261 window ends, so a
    match on one edge is the exact shape a stale attachment would take.
    """
    start, end = sidecar.get("source_start"), sidecar.get("source_end")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (start, end)):
        return None, _missing("source_window", ABSENT, "sidecar_has_none")
    path = analysis / "candidates.json"
    if not path.exists():
        return None, _missing("candidates", ABSENT, "not_on_disk")
    try:
        cands = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, _missing("candidates", UNREADABLE, "not_json")
    if not isinstance(cands, list):
        return None, _missing("candidates", REFUSED, "not_a_list")
    for cand in cands:
        if not isinstance(cand, dict):
            continue
        if (isinstance(cand.get("start"), (int, float))
                and isinstance(cand.get("end"), (int, float))
                and abs(cand["start"] - start) < _EDGE_S
                and abs(cand["end"] - end) < _EDGE_S):
            return cand, None
    return None, _missing("candidate", ABSENT, "no_window_matches_on_both_edges")


def _boundary_view(cand: dict | None, words: Any) -> tuple[Any, dict | None]:
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
        return None, _missing("transcript_words", ABSENT, "caller_supplied_none")
    from services.clipper.boundary_completion import boundary_view

    try:
        return boundary_view(cand, words,
                             max_s=float(cand.get("end", 0.0))
                             - float(cand.get("start", 0.0))), None
    except Exception as exc:
        return None, _missing("boundary_view", UNREADABLE,
                              f"raised_{type(exc).__name__}")


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
    miss: list[dict] = out["missing"]
    try:
        sidecar = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        miss.append(_missing("sidecar", UNREADABLE, type(exc).__name__))
        sidecar = None
    if sidecar is not None and not isinstance(sidecar, dict):
        miss.append(_missing("sidecar", REFUSED, "not_a_record"))
        sidecar = None

    placement = measure(path) if sidecar is not None else None
    own_layer = _own_caption_layer(placement, sidecar)
    if isinstance(placement, dict) and placement.get("refused"):
        # `measure` refuses only shapes somebody supplied wrongly — an
        # unreadable sidecar, a sub-record that is not a record, a caption
        # height that is not a fraction. Every one of them is an integrity
        # failure rather than a gap in the corpus.
        miss.append(_missing("placement", REFUSED, placement["refused"]))
        placement = None
    if isinstance(placement, dict):
        # THE FACE EVIDENCE HAS ITS OWN REFUSALS, and they never surfaced: a
        # NaN face coordinate came back as `box_is_not_four_finite_numbers`
        # inside `faces` and stopped there, so a corrupt track read as a clip
        # with nothing measured on it.
        for reason in (placement.get("faces") or {}).get("refused") or []:
            miss.append(_missing("face_evidence", REFUSED, str(reason)))

    style, style_why = _style(sidecar) if sidecar is not None else (None, None)
    if style_why:
        miss.append(style_why)
    contrast = cc.verdict(style) if sidecar is not None else None
    for reason in (contrast or {}).get("refused") or []:
        # The contrast module files "nobody set a colour" and "this is not a
        # colour" under one list, because for its purpose both mean no verdict.
        # A NaN outline width is not a missing one.
        miss.append(_missing(
            "contrast", ABSENT if reason in _CONTRAST_ABSENT else REFUSED,
            str(reason)))

    cand, why = _candidate(path.parent.parent / "analysis", sidecar or {})
    if why:
        miss.append(why)
    view, view_why = _boundary_view(cand, words)
    if view_why:
        miss.append(view_why)

    if source_captions is None:
        miss.append(_missing("source_captions", ABSENT, "not_computed"))
    if chrome is None:
        miss.append(_missing("chrome", ABSENT, "not_computed"))

    # THE SIDECAR HAS TO BE THE ONE FOR THIS FILE. `p/exports/a.json` declaring
    # `clip_id: b` or another project's id used to pass without comment, and
    # every figure in the run would then be about a clip the row does not name.
    # R0's gate already refuses that population; a later audit must not
    # reintroduce a more weakly checked one.
    if isinstance(sidecar, dict):
        for field, expected in (("clip_id", out["clip"]),
                                ("project_id", out["project"])):
            got = sidecar.get(field)
            if got is not None and str(got) != expected:
                miss.append(_missing("sidecar", REFUSED,
                                     f"{field}_is_{got!r}_not_{expected!r}"))
        # And a render with no plan beside it is a clip nothing can be said
        # about — R0 calls it an orphan and exits 2.
        if not path.with_suffix(".mp4").exists():
            miss.append(_missing("export", ABSENT, "no_mp4_beside_the_sidecar"))

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
        report["missing_inputs"] = [
            _missing("assemble", UNREADABLE, f"raised_{type(exc).__name__}")]
        return report
    inputs = got["inputs"]
    checks = pk.checks_for(
        inputs["sidecar"], chrome=inputs["chrome"],
        placement=inputs["placement"], contrast=inputs["contrast"],
        source_captions=inputs["source_captions"],
        boundary_view=inputs["boundary_view"],
        own_caption_layer=got["own_caption_layer"],
        # The mp4 beside the sidecar. Provenance needs both halves — the recipe
        # digest AND the file the render measured — and only the assembler
        # knows where the file is.
        export_path=path.with_suffix(".mp4"))
    report = pf.preflight(checks, corrections=corrections)
    report["project"] = got["project"]
    report["clip"] = got["clip"]
    # WHICH INPUTS WERE NOT THERE, beside the verdict rather than instead of it.
    # `unavailable` says a check could not be made; this says why, and the two
    # are different halves of the same sentence.
    report["missing_inputs"] = got["missing"]
    return report
