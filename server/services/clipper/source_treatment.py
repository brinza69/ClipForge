"""What to do with the SOURCE's own burned subtitle — and whether it can be done.

`caption_policy` decides ClipForge's OWN layer (burn or suppress). This module
decides the other track: the text already burned into the source pixels, which
is blurred, erased or left alone (`none`). They are separate decisions on
purpose (SC addendum §1): a person choosing to replace the source's captions is
not the same fact as the source carrying them, and folding one into the other is
how 37 clips shipped with two caption tracks.

THE SHAPE, per SC addendum v2 and codex-verdict-next-10 §1:

    resolve(project, clip, mask, params)  -> the effective configuration, or a
                                             refusal that is its own state
    gate(decision, ...)                   -> "execute" / "none", or raises
    source_treatment_mask.load_mask       -> the mask, verified from its bytes
    source_treatment_manifest             -> manifest check + the fingerprint

DB-free and wired to nothing yet (SC batch 1). Nothing here knows a clip, a
project row or a file layout beyond the paths it is handed.

WHY A REFUSAL IS NOT `none`. `none` is a decision: "leave the source's text
alone". A mask that cannot be read, a split nobody authorised, a treatment the
executor cannot run — each of those is "we do not know how to make this
picture", and reading it as `none` would ship the untreated text under a
configuration that asked for it gone. So a refusal carries `state: refused` and
NO `treatment` key at all; there is nothing for a reader to mistake for one.

WHY THE SEAM IS IN FRAMES. SC1's two fragment renders both treated k 7158 —
a boundary written in seconds was inclusive on both sides (SC-addendum-v2 §1).
Every boundary here is a source frame index k, half-open `[k_from, k_to)`.
"""

from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from services.clipper.caption_policy import BURN

NONE, BLUR, ERASE = "none", "blur", "erase"
#: `band` was not chosen by the person, so it is not built (SC addendum §1).
TREATMENTS: tuple[str, ...] = (NONE, BLUR, ERASE)

#: `default` is what happens when nobody said anything; a person's explicit
#: `none` is `human`, so it never looks like the default.
HUMAN, DEFAULT = "human", "default"

DYNAMIC, STATIC = "dynamic", "static"
#: Lot 1 treats only the dynamic path; both pilot clips render dynamic [SC1-2].
SUPPORTED_PATHS: tuple[str, ...] = (DYNAMIC,)

CONFIG_SCHEMA = "clipper_source_treatment_config_v1"
IDENTITY_SCHEMA = "clipper_source_treatment_v1"
MASK_SCHEMA = "clipper_source_caption_mask_v1"
MANIFEST_SCHEMA = "clipper_source_treatment_manifest_v1"

#: An open edge of a line (`k_on`/`k_off` never observed). It is carried as this
#: word, never as an invented first or last frame (codex-verdict-next-10 §3).
CONTINUES = "continues_beyond_observation"

#: The APPROVED record of each split a person answered — independent of any
#: candidate config (codex-verdict-next-13 §1 R2). A config names a record by
#: its question id and nothing else: the answer file's hash, the clip, the
#: source, the line, the frame and the treatment on each side come from HERE,
#: so a config cannot supply its own approval. The answer file is still read,
#: and its bytes must hash to `answer_sha256` below. Only `split_answer`
#: authorises a split; (a) and (b) are whole-line overrides and need none.
QUESTIONS: dict[str, dict[str, Any]] = {
    "sc-split-line-F4F5": {
        "label": "linia F4/F5", "split_answer": "c", "offered": "c_switch_at_238.6.mp4",
        # A\editorial-review\answers\de1-sc-2026-09-26T23-18-49.txt
        "answer_sha256": "3a39378ddc123107858474f2371aff51c09d52a5da7cab29ef40f0aed0eed1e3",
        "clip_id": "b23c14c41495",
        # B\gates\sc0\identity.json, full hash
        "source_sha256": "351a96b6b462a21e95dc31466ead17b190858ac695327faedc60443a397405b4",
        "line_id": "b23c-L30", "k_split": 7158, "pts_split": 3664896,
        # what the person chose: blur before k_split, erase from it (R1)
        "before": "blur", "after": "erase",
    },
}

REASONS: tuple[str, ...] = (
    "setting_invalid", "params_missing", "mask_missing", "mask_hash_mismatch",
    "mask_unreadable", "mask_not_canonical", "mask_invalid", "mask_source_mismatch",
    "mask_clip_mismatch", "source_clock_refused", "mask_window_does_not_cover_clip",
    "unknown_frames_in_window", "unverified_run", "glyph_missing", "glyph_hash_mismatch",
    "override_splits_a_line", "split_pts_mismatch",
    "decision_without_treatment", "treatment_unreadable", "static_path_unsupported",
    "path_unsupported", "treatment_not_implemented", "burn_over_untreated_source_captions",
    "manifest_missing", "manifest_hash_mismatch", "manifest_invalid",
    "manifest_of_another_attempt", "manifest_mask_mismatch", "manifest_params_mismatch",
    "manifest_frames_mismatch", "patch_missing", "patch_hash_mismatch",
    # SC batch 2 — the executor, the destination guard and the render record
    "treatment_inputs_missing", "decision_mask_mismatch", "patch_build_failed",
    "record_not_corroborated", "treated_export_not_promoted", "destination_not_versioned",
)


class SourceTreatmentRefused(Exception):
    """The treatment cannot be resolved or executed. Its own state, never `none`."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown refusal reason {reason!r}")
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail

    def as_dict(self) -> dict:
        return {"schema": CONFIG_SCHEMA, "state": "refused",
                "reason": self.reason, "detail": self.detail}


def canonical_bytes(obj: Any) -> bytes:
    """Sorted keys, `,`/`:` separators, UTF-8, one trailing LF (SC-addendum-v2 §2.2).

    The mask and the manifest are stored in exactly these bytes, so their
    sha256 is a hash of the content and not of somebody's editor settings."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8") + b"\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_int(x: Any) -> bool:
    """An int that is not a bool — `True == 1` would pass a verdict as a frame."""
    return isinstance(x, int) and not isinstance(x, bool)


# ── resolve ──────────────────────────────────────────────────────────────────

_CLIP_KEYS = frozenset({"treatment", "decided_by", "mask_sha256", "overrides"})
_PROJECT_KEYS = frozenset({"treatment", "decided_by"})
_OVERRIDE_KEYS = frozenset({"k_from", "k_to", "treatment", "split"})
_SPLIT_KEYS = frozenset({"line_id", "k_split", "pts_split", "authorisation"})
#: A reference to an approved record, never a copy of it: no `answer_sha256`,
#: `answer` or `offered` here is authority (next-13 §1 R2).
_AUTH_KEYS = frozenset({"question_id", "answer_file"})


def _setting(value: Any, keys: frozenset, what: str) -> dict | None:
    """A stored value, or None when nothing is stored. A stored value is a
    person's decision, so it must say `decided_by: human`."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - keys:
        raise SourceTreatmentRefused("setting_invalid", f"{what}: not a {sorted(keys)} object")
    t = value.get("treatment")
    if not isinstance(t, str) or t not in TREATMENTS:
        raise SourceTreatmentRefused("setting_invalid", f"{what}: treatment {t!r}")
    if value.get("decided_by") != HUMAN:
        raise SourceTreatmentRefused("setting_invalid",
                                     f"{what}: decided_by {value.get('decided_by')!r}")
    return value


def resolve(project_setting: Any = None, clip_setting: Any = None,
            mask: dict | None = None, params: Any = None) -> dict:
    """The effective configuration for one clip, or `{"state": "refused", ...}`.

    `project_setting` is `{treatment, decided_by}`; `clip_setting` adds
    `mask_sha256` and `overrides` (temporal overrides live only on the clip, on
    the source clock). The clip's value wins whole — its overrides come with it.
    `mask` is what `source_treatment_mask.load_mask` returned for this clip;
    `params` is the params-version record `{version, values: {blur:…, erase:…}}`
    (the SC1 numbers live there, never here — SC-addendum-v2 §7)."""
    try:
        return _resolve(project_setting, clip_setting, mask, params)
    except SourceTreatmentRefused as r:
        return r.as_dict()


def _resolve(project_setting: Any, clip_setting: Any, mask: dict | None, params: Any) -> dict:
    clip = _setting(clip_setting, _CLIP_KEYS, "clip")
    project = _setting(project_setting, _PROJECT_KEYS, "project")
    chosen, scope = ((clip, "clip") if clip is not None
                     else (project, "project") if project is not None else (None, "default"))
    base = chosen["treatment"] if chosen else NONE
    out: dict[str, Any] = {"schema": CONFIG_SCHEMA, "state": "resolved", "treatment": base,
                           "decided_by": HUMAN if chosen else DEFAULT, "scope": scope}
    overrides = clip.get("overrides", []) if clip is not None else []
    if not isinstance(overrides, list):
        raise SourceTreatmentRefused("setting_invalid", "overrides is not a list")
    if base == NONE and not overrides:
        out["active"] = False
        return out

    if mask is None:
        raise SourceTreatmentRefused("mask_missing", "an active treatment needs a verified mask")
    if clip is not None and clip.get("mask_sha256") != mask["sha256"]:
        raise SourceTreatmentRefused("mask_hash_mismatch",
                                     f"clip names {clip.get('mask_sha256')!r}, mask is {mask['sha256']}")
    parsed, approved = _overrides(overrides, mask)
    used = sorted({t for t in [base] + [o["treatment"] for o in parsed] if t != NONE})
    if not used:
        for rec in approved:  # nothing is treated, so neither side is what was approved
            _check_sides([], rec)
        out["active"] = False
        return out
    values = _params(params, used)
    k_first, k_last = mask["window"]
    segs = _segments(mask, base, parsed, values)
    for rec in approved:
        _check_sides(segs, rec)
    out.update({"active": True, "mask_sha256": mask["sha256"],
                "params_version": params["version"], "params": values,
                "window": {"k_first": k_first, "k_last": k_last}, "per_line": segs})
    return out


def _check_sides(segs: list[dict], rec: dict) -> None:
    """The person approved a TREATMENT on each side of the seam, not a time
    (next-13 §1 R1): the resolved line must be `before` up to k_split and
    `after` from it. Read off the resolved segments, so the base, the override
    and any second override all count — an inverted pair is refused, and so is
    `none` on either side (that side then has no segment)."""
    line, k = rec["line_id"], rec["k_split"]
    up_to = next((s["treatment"] for s in segs if s["line_id"] == line and s["k_to"] == k), None)
    from_k = next((s["treatment"] for s in segs if s["line_id"] == line and s["k_from"] == k), None)
    if (up_to, from_k) != (rec["before"], rec["after"]):
        raise SourceTreatmentRefused(
            "override_splits_a_line",
            f"split not authorised: {line} resolves to {up_to!r} before k {k} and {from_k!r} "
            f"from it; the person chose {rec['before']!r} then {rec['after']!r}")


def _params(params: Any, used: list[str]) -> dict:
    if (not isinstance(params, dict) or not isinstance(params.get("version"), str)
            or not params["version"] or not isinstance(params.get("values"), dict)):
        raise SourceTreatmentRefused("params_missing", "no params-version record")
    values = {}
    for t in used:
        if not isinstance(params["values"].get(t), dict):
            raise SourceTreatmentRefused("params_missing", f"no params for {t}")
        values[t] = params["values"][t]
    return values


def _overrides(overrides: list, mask: dict) -> tuple[list[dict], list[dict]]:
    """The overrides sorted, and the approved record of every split they make."""
    lo, hi = mask["window"][0], mask["window"][1] + 1
    parsed = []
    for o in overrides:
        if not isinstance(o, dict) or set(o) - _OVERRIDE_KEYS:
            raise SourceTreatmentRefused("setting_invalid", "override is not an override object")
        k_from, k_to, t = o.get("k_from"), o.get("k_to"), o.get("treatment")
        if not (is_int(k_from) and is_int(k_to) and lo <= k_from < k_to <= hi):
            raise SourceTreatmentRefused("setting_invalid",
                                         f"override [{k_from!r}, {k_to!r}) not inside [{lo}, {hi})")
        if not isinstance(t, str) or t not in TREATMENTS:
            raise SourceTreatmentRefused("setting_invalid", f"override treatment {t!r}")
        parsed.append(o)
    parsed.sort(key=lambda o: o["k_from"])
    for a, b in zip(parsed, parsed[1:]):
        if b["k_from"] < a["k_to"]:
            raise SourceTreatmentRefused("setting_invalid", "overrides overlap")
    approved = [rec for rec in (_check_boundaries(o, mask) for o in parsed) if rec is not None]
    return parsed, approved


def _check_boundaries(o: dict, mask: dict) -> dict | None:
    """The whole-line rule, with its one authorised exception (SC-addendum-v2 §1).

    A boundary strictly inside a line's run of frames splits the line. That is
    refused unless the override's `split` names that line and that exact k, and
    the person's recorded answer authorises it. The runs are already clipped to
    the window, so a boundary on the window's own edge never splits one."""
    needs = [(b, line_id) for b in (o["k_from"], o["k_to"])
             for line_id, (a, e) in mask["line_runs"].items() if a < b < e]
    split = o.get("split")
    if not needs:
        if split is not None:
            raise SourceTreatmentRefused("setting_invalid", "a split where no line is split")
        return
    if len(needs) > 1:
        raise SourceTreatmentRefused("override_splits_a_line",
                                     f"{len(needs)} split lines in one override: {needs}")
    boundary, line_id = needs[0]
    if split is None:
        raise SourceTreatmentRefused("override_splits_a_line",
                                     f"k {boundary} is inside {line_id} and no answer authorises it")
    if not isinstance(split, dict) or set(split) != _SPLIT_KEYS:
        raise SourceTreatmentRefused("setting_invalid", "split is not a split object")
    if not (is_int(split["k_split"]) and is_int(split["pts_split"])):
        raise SourceTreatmentRefused("setting_invalid", "k_split/pts_split are not integers")
    if split["line_id"] != line_id:
        raise SourceTreatmentRefused("override_splits_a_line",
                                     f"split names {split['line_id']!r}, the boundary is in {line_id}")
    if split["k_split"] != boundary:
        raise SourceTreatmentRefused("override_splits_a_line",
                                     f"split names k {split['k_split']}, the boundary is k {boundary}")
    clock = mask["clock"]
    if split["pts_split"] != split["k_split"] * clock["pts_step"] + clock["start_pts"]:
        raise SourceTreatmentRefused("split_pts_mismatch",
                                     f"pts {split['pts_split']} is not k {split['k_split']}")
    return _authorise(split, mask)


_ANSWER_LINE = re.compile(r"^\s*\d+\.\s*(?P<clip>[0-9a-f]+),\s*(?P<label>[^:]+):\s*"
                          r"(?P<answer>[a-z])\b(?P<rest>.*)$")
_SECONDS = re.compile(r"(\d+)[,.](\d+)\s*s\b")


def _authorise(split: dict, mask: dict) -> dict:
    """The approved record the split refers to, checked against this mask and
    against the person's answer read NOW. Returns the record (its sides are
    checked on the resolved segments, `_check_sides`).

    Everything is compared with the RECORD, never with a value the config
    carries (next-13 §1 R2): the exact clip and source, the line, k and pts,
    the file's bytes' hash; then in the file, the question's label, the split
    option and the seconds the person read — converted back through the mask's
    own clock, they must name exactly the record's pts. So a missing file,
    another answer, another clip, source or line, or another k is the same
    refusal as no answer at all."""
    def no(why: str) -> SourceTreatmentRefused:
        return SourceTreatmentRefused("override_splits_a_line", f"split not authorised: {why}")

    auth = split["authorisation"]
    if not isinstance(auth, dict) or set(auth) != _AUTH_KEYS:
        raise no("authorisation is not an authorisation object")
    q = QUESTIONS.get(auth["question_id"])
    if q is None:
        raise no(f"unknown question {auth['question_id']!r}")
    if mask["clip_id"] != q["clip_id"]:
        raise no(f"the answer is about clip {q['clip_id']!r}, the mask is {mask['clip_id']!r}")
    if mask["doc"]["source"]["sha256"] != q["source_sha256"]:
        raise no(f"the answer is about source {q['source_sha256']}")
    if split["line_id"] != q["line_id"]:
        raise no(f"the answer is about line {q['line_id']!r}, not {split['line_id']!r}")
    if (split["k_split"], split["pts_split"]) != (q["k_split"], q["pts_split"]):
        raise no(f"the answer is about k {q['k_split']} (pts {q['pts_split']})")
    try:
        data = Path(auth["answer_file"]).read_bytes()
    except (OSError, TypeError) as e:
        raise no(f"answer file unreadable: {e}") from None
    if sha256_bytes(data) != q["answer_sha256"]:
        raise no("answer file's sha256 is not the approved record's")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise no("answer file is not UTF-8") from None
    found = [m for m in map(_ANSWER_LINE.match, text.splitlines())
             if m and m["label"].strip() == q["label"]]
    if len(found) != 1:
        raise no(f"{len(found)} answer lines for {q['label']!r}")
    m = found[0]
    if not q["clip_id"].startswith(m["clip"]):
        raise no(f"the file's answer is about clip {m['clip']!r}, the record is {q['clip_id']!r}")
    if m["answer"] != q["split_answer"]:
        raise no(f"the file records {m['answer']!r}")
    secs = _SECONDS.findall(m["rest"])
    if len(secs) != 1:
        raise no(f"{len(secs)} times in the answer")
    said = Fraction(f"{secs[0][0]}.{secs[0][1]}")
    if Fraction(q["pts_split"]) * Fraction(mask["clock"]["time_base"]) != said:
        raise no(f"the person chose {float(said)} s, the record is pts {q['pts_split']}")
    return q


def _segments(mask: dict, base: str, overrides: list[dict], values: dict) -> list[dict]:
    """Each line's run of frames cut into whole runs of one treatment.

    Overrides act on LINES: a `none_observed` frame is never treated whatever
    an override covers. Untreated runs are left out — this is the treated set."""
    lines = mask["lines"]
    segs: list[dict] = []
    for line_id, (a, e) in sorted(mask["line_runs"].items(), key=lambda kv: kv[1][0]):
        cuts = sorted({a, e} | {b for o in overrides for b in (o["k_from"], o["k_to"]) if a < b < e})
        for lo, hi in zip(cuts, cuts[1:]):
            t = next((o["treatment"] for o in overrides if o["k_from"] <= lo < o["k_to"]), base)
            if t == NONE:
                continue
            if segs and segs[-1]["line_id"] == line_id and segs[-1]["k_to"] == lo \
                    and segs[-1]["treatment"] == t:
                segs[-1]["k_to"] = hi
                continue
            line = lines[line_id]
            segs.append({"line_id": line_id, "k_from": lo, "k_to": hi, "treatment": t,
                         "params": values[t],
                         "line_on": CONTINUES if line["k_on"] is None else line["k_on"],
                         "line_off": CONTINUES if line["k_off"] is None else line["k_off"]})
    return segs


# ── gate ─────────────────────────────────────────────────────────────────────

def gate(decision: Any, *, configured: Any, layer: Any, source_has_burned_captions: Any,
         path: Any, implemented: frozenset[str] = frozenset()) -> dict:
    """Execute, leave alone, or raise `SourceTreatmentRefused` — before any `.ass`.

    `decision["source_treatment"]` is what `resolve` returned. `configured` is
    True when the clip or its project stores a treatment: a decision without the
    key is then refused, so a script building its own decision cannot drop it.
    `implemented` names the treatments the executor on this path can run;
    lot 1 has no executor, so by default every active treatment is refused.

    ANY active treatment that cannot be executed is refused, whatever the layer
    — `suppress` included (codex-verdict-next-10 §1). The burn check is an
    extra guard against doubling: burning ClipForge's layer over a source whose
    text stays untouched recreates the two-track defect."""
    st = decision.get("source_treatment") if isinstance(decision, dict) else None
    if st is None:
        if configured is not False:
            raise SourceTreatmentRefused("decision_without_treatment",
                                         "the clip is configured but the decision carries no treatment")
        st = {"schema": CONFIG_SCHEMA, "state": "resolved", "treatment": NONE,
              "decided_by": DEFAULT, "scope": "default", "active": False}
    if not isinstance(st, dict) or st.get("schema") != CONFIG_SCHEMA:
        raise SourceTreatmentRefused("treatment_unreadable", "not a resolved treatment")
    if st.get("state") == "refused":
        reason = st.get("reason")
        raise SourceTreatmentRefused(reason if reason in REASONS else "treatment_unreadable",
                                     str(st.get("detail", "")))
    if st.get("state") != "resolved" or not isinstance(st.get("active"), bool):
        raise SourceTreatmentRefused("treatment_unreadable", f"state {st.get('state')!r}")

    _check_structure(st)
    if st["active"]:
        if path not in SUPPORTED_PATHS:
            raise SourceTreatmentRefused(
                "static_path_unsupported" if path == STATIC else "path_unsupported", f"path {path!r}")
        # `params` holds exactly the treatments in use, the base one included
        # even when no line of the window falls under it — `_check_structure`
        # has made sure it is there and non-empty, so nothing is inferred from
        # a missing key (next-13 §1 R3).
        missing = sorted(set(st["params"]) - set(implemented))
        if missing:
            raise SourceTreatmentRefused("treatment_not_implemented", f"{missing} on {path}")
        return {"state": "execute", "treatment": st}

    if layer == BURN and source_has_burned_captions is True:
        raise SourceTreatmentRefused("burn_over_untreated_source_captions",
                                     "the source's text stays and the layer burns over it")
    return {"state": "none", "treatment": st}


_ACTIVE_KEYS = frozenset({"mask_sha256", "params_version", "params", "window", "per_line"})
_SEGMENT_KEYS = frozenset({"line_id", "k_from", "k_to", "treatment", "params", "line_on", "line_off"})


def _check_structure(st: dict) -> None:
    """A resolved treatment the gate may act on has the shape `resolve` builds
    — checked BEFORE the executor, so an incomplete object is never executed
    and an inactive one never hides an asked-for treatment (next-13 §1 R3)."""
    def bad(why: str) -> SourceTreatmentRefused:
        return SourceTreatmentRefused("treatment_unreadable", why)

    base = st.get("treatment")
    if not isinstance(base, str) or base not in TREATMENTS:
        raise bad(f"treatment {base!r}")
    if not st["active"]:
        if base != NONE or set(st) & _ACTIVE_KEYS:
            raise bad(f"inactive, yet treatment {base!r} / {sorted(set(st) & _ACTIVE_KEYS)}")
        return
    if _ACTIVE_KEYS - set(st):
        raise bad(f"an active treatment without {sorted(_ACTIVE_KEYS - set(st))}")
    if not isinstance(st["mask_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", st["mask_sha256"]):
        raise bad(f"mask_sha256 {st['mask_sha256']!r}")
    params = st["params"]
    if (not isinstance(params, dict) or not params or set(params) - {BLUR, ERASE}
            or not all(isinstance(v, dict) and v for v in params.values())):
        raise bad(f"params {params!r}")
    if not isinstance(st["params_version"], str) or not st["params_version"]:
        raise bad("no params version")
    if base != NONE and base not in params:
        raise bad(f"the base treatment {base!r} has no params")
    if not isinstance(st["per_line"], list):
        raise bad("per_line is not a list")
    for s in st["per_line"]:
        if (not isinstance(s, dict) or set(s) != _SEGMENT_KEYS
                or not isinstance(s["treatment"], str) or s["treatment"] not in params
                or s["params"] != params[s["treatment"]]
                or not (is_int(s["k_from"]) and is_int(s["k_to"]) and s["k_from"] < s["k_to"])):
            raise bad(f"segment {s!r}")
