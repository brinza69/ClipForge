"""Executing the source-caption treatment: the patch, and the overlay that lays it in.

`source_treatment` decides and gates; this module does the work the gate
allows (SC-addendum §4, SC-addendum-v2 §4–§6, SC batch 2):

    prepare(clip, project, decision, ...)  -> None ("leave the source alone"),
                                              a source patch, or a refusal
    overlay_prefix(...)                    -> the filter laid in ahead of
                                              `[0:v]split=2`
    refuse_treated_publish(sidecar)        -> the guard `_publish_export` runs

THE RECIPE IS SC1's, PORTED, NOT REDESIGNED (sc1_treat.py, sc1_render.py:79–93):
a raw yuv420p patch of the source rows the lines occupy, tagged tv/bt709,
overlaid on the SOURCE frame before the crop with
`eof_action=pass:repeatlast=0:format=yuv420,format=yuv420p`, one spare
untouched trailing frame. The differences, each forced by the whole-clip mask:

  * the patch covers the mask's whole WINDOW and copies decoded source pixels
    on every frame it does not treat, so the patch-maker's decode must equal
    the encoder's — the same ffmpeg executable does both, and its build is
    recorded in the manifest;
  * a pixel is only ever changed inside the treated line's own `rect`; SC1's
    separate glyph rows and band rows collapse to that rect;
  * the pts offset is COMPUTED per render from the seek the encode will use
    and the file's start time (`pts_offset`), never the −1 frame SC1
    measured against the old b23c export;
  * every decoded frame is bound to its own integer pts, read back from
    ffmpeg (`showinfo`), never assumed from its position in the stream
    (codex-verdict-next-10 §3 found exactly that gap in `whole_extents`).

Every parameter comes from the versioned params record, an ARGUMENT
(codex-verdict-next-13 §1): nothing here is an SC1 constant. There is no patch
cache; each attempt builds its own patch and manifest in its own scratch dir.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any

from services.clipper import caption_policy
from services.clipper import source_treatment as st
from services.clipper.source_treatment import (BLUR, DYNAMIC, ERASE, NONE, STATIC,
                                               SourceTreatmentRefused, canonical_bytes,
                                               sha256_bytes)
from services.clipper.source_treatment_patch import geometry, protect_report, write_patch  # noqa: F401

#: What this executor can run. `gate` refuses anything else.
IMPLEMENTED: frozenset[str] = frozenset({BLUR, ERASE})
PARAMS_SCHEMA = "clipper_source_treatment_params_v1"
#: The only colour the patch is tagged with — untagged, ffmpeg pulled the main
#: stream to yuva420p with csp `unknown` and converted through RGB: 1–3 luma
#: levels on every pixel of every frame (SC1, caught by the identity control).
TAGS = ("tv", "bt709")
#: `exports/sct<n>/` — the versioned directories a treated render may land in.
VERSIONED = re.compile(r"^sct[1-9][0-9]*$")
#: The destination only `handle_export` passes (SC3, codex-verdict-next-33 §3): its own staged file, for a
#: BLUR-ONLY treatment. R4b's `_publish_export` stays the one publisher and checks the staged sidecar again.
PUBLISH_BLUR = "publish_blur"
_GRAPH_HEAD = "[0:v]split=2[a][b];"


# ── what the clip and the decision say ───────────────────────────────────────

def configured(clip: Any, project: Any) -> bool:
    """True when the clip or its project STORES a treatment. No column exists
    before batch 3, so this reads the attributes batch 3 adds and nothing else."""
    cfg = getattr(project, "clipper_settings", None)
    return (getattr(clip, "source_caption_treatment", None) is not None
            or (isinstance(cfg, dict) and cfg.get("source_caption_treatment") is not None))


def _burned(clip: Any, project: Any) -> Any:
    own = getattr(clip, "source_has_burned_captions", None)
    if isinstance(own, bool):
        return own
    cfg = getattr(project, "clipper_settings", None)
    return cfg.get(caption_policy.SETTING) if isinstance(cfg, dict) else None


def label(decision: Any) -> dict:
    """The sidecar's label block. Not fingerprinted: `decided_by` does not change
    a pixel (SC-addendum-v2 §5)."""
    t = decision.get("source_treatment") if isinstance(decision, dict) else None
    if not isinstance(t, dict):
        return {"treatment": NONE, "decided_by": st.DEFAULT, "scope": "default", "active": False}
    return {k: t.get(k) for k in ("treatment", "decided_by", "scope", "active")}


def load_params(path: Any) -> dict:
    """The versioned, immutable params record — canonical bytes that still hash
    to the `.sha256` written beside them."""
    path = Path(str(path))
    try:
        data = path.read_bytes()
        stamp = Path(f"{path}.sha256").read_text(encoding="utf-8").split()
    except OSError as e:
        raise SourceTreatmentRefused("params_missing", str(e)) from None
    if stamp[:1] != [sha256_bytes(data)]:
        raise SourceTreatmentRefused("params_missing", f"{path.name} no longer hashes to its .sha256")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SourceTreatmentRefused("params_missing", str(e)) from None
    if (not isinstance(doc, dict) or doc.get("schema") != PARAMS_SCHEMA
            or doc.get("immutable") is not True or canonical_bytes(doc) != data
            or not isinstance(doc.get("version"), str) or not doc["version"]
            or not isinstance(doc.get("values"), dict)):
        raise SourceTreatmentRefused("params_missing", "not an immutable canonical params record")
    return {"version": doc["version"], "values": doc["values"], "file": str(path),
            "sha256": sha256_bytes(data)}


# ── the gate, re-run on the decision about to be encoded ─────────────────────

_INPUT_KEYS = frozenset({"project", "clip", "mask_path", "params_path"})


def revalidate(decision: Any, clip: Any, project: Any, *, src: str) -> dict | None:
    """None when the source is left alone; else the treatment re-derived NOW.

    The gate runs first (`source_treatment.gate`, every refusal its own state).
    For an executable treatment the mask is loaded from its file again — bytes,
    source identity, window for the clip's CURRENT bounds, glyph PNGs — the
    params record re-read, and the configuration re-resolved from the inputs
    the decision names. Its `window` and segments must be exactly the
    decision's: a decision stored or edited in between is refused, never
    executed as it stands (codex-verdict-next-14 §1)."""
    policy = decision.get("caption_policy") if isinstance(decision, dict) else None
    verdict = st.gate(decision, configured=configured(clip, project),
                      layer=policy.get("action") if isinstance(policy, dict) else None,
                      source_has_burned_captions=_burned(clip, project),
                      path=DYNAMIC if isinstance(decision, dict) and decision.get("dyn") else STATIC,
                      implemented=IMPLEMENTED)
    if verdict["state"] == "none":
        return None
    inputs = decision.get("source_treatment_inputs")
    if not isinstance(inputs, dict) or _INPUT_KEYS - set(inputs):
        raise SourceTreatmentRefused("treatment_inputs_missing",
                                     f"an active decision must name {sorted(_INPUT_KEYS)}")
    from services.clipper.proxy_provenance import media_identity
    from services.clipper.source_treatment_mask import load_mask

    identity = media_identity(src, inputs.get("source_identity_reuse"))
    mask = load_mask(inputs["mask_path"], source_identity=identity, clip_id=clip.id,
                     clip_start=clip.start_time, clip_end=clip.end_time)
    params = load_params(inputs["params_path"])
    again = st.resolve(inputs["project"], inputs["clip"], mask,
                       {"version": params["version"], "values": params["values"]})
    if again.get("state") == "refused":
        raise SourceTreatmentRefused(again["reason"], again["detail"])
    carried = decision["source_treatment"]
    if again != carried:
        differ = sorted(k for k in set(again) | set(carried) if again.get(k) != carried.get(k))
        raise SourceTreatmentRefused("decision_mask_mismatch",
                                     f"the decision's {differ} are not what the loaded mask resolves to")
    _executable(again["params"], mask, again["per_line"])
    return {"resolved": again, "mask": mask, "params": params, "identity": identity}


def _executable(values: dict, mask: dict, segs: list[dict]) -> None:
    """The params record in a shape this executor runs, and the mask's lines
    carrying the same footprint rule and dilation as the record."""
    import cv2

    def no(why: str) -> SourceTreatmentRefused:
        return SourceTreatmentRefused("treatment_not_implemented", why)

    stream = mask["doc"]["source"]["stream"]
    if (stream.get("pix_fmt"), stream.get("color_range"), stream.get("color_space")) != ("yuv420p",) + TAGS:
        raise no(f"source {stream.get('pix_fmt')}/{stream.get('color_range')}/{stream.get('color_space')}")
    b = values.get(BLUR)
    if b is not None and not (
            isinstance(b.get("sigma"), list) and len(b["sigma"]) == 2
            and all(isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0 for s in b["sigma"])
            and st.is_int(b.get("context_px")) and b["context_px"] >= 0
            and b.get("context_edge") == "reflect"):
        raise no(f"blur params {b!r}")
    e = values.get(ERASE)
    if e is not None:
        fp, dil, r = e.get("footprint"), e.get("dilation"), e.get("telea_radius")
        if not (e.get("method") == "opencv_inpaint_telea" and isinstance(fp, dict)
                and all(st.is_int(fp.get(k)) for k in ("tophat", "tophat_min", "luma_min"))
                and isinstance(fp.get("persist"), (int, float)) and isinstance(dil, dict)
                and dil.get("shape") == "ellipse" and st.is_int(dil.get("size")) and dil["size"] % 2
                and isinstance(r, list) and len(r) == 2 and all(st.is_int(x) and x > 0 for x in r)
                and e.get("temporal_smoothing", "none") == "none"):
            raise no(f"erase params {e!r}")
        if not cv2.__version__.startswith(str(e.get("opencv_version"))):
            raise no(f"opencv {cv2.__version__}, the record was made with {e.get('opencv_version')}")
        for lid in sorted({s["line_id"] for s in segs if s["treatment"] == ERASE}):
            line = mask["lines"][lid]
            if line.get("footprint_rule") != fp or line.get("dilation") != dil:
                raise SourceTreatmentRefused("mask_invalid",
                                             f"{lid}: footprint/dilation are not the params record's")


def check_destination(out: Any, project: Any, *, destination: Any, resolved: dict | None = None) -> None:
    """A treated render is never a normal export (SC-addendum-v2 §4): the caller
    says `destination="versioned"`, and inside exports_dir only
    `exports/sct<n>/<file>` is accepted — never `export_path`, never the
    staging names beside it. The original is never overwritten.

    The one exception is SC3's blur (next-33 §3): `PUBLISH_BLUR`, only for a treatment whose every
    segment is blur, and only into an export's own staged name (`.<clip>.<job>-<nonce>.mp4` in
    exports_dir). Erase is never promoted, however it got into the decision."""
    from services.clipper import storage

    if destination == PUBLISH_BLUR:
        segs = (resolved or {}).get("per_line") or []
        if not segs or (resolved or {}).get("treatment") != BLUR or any(s["treatment"] != BLUR for s in segs):
            raise SourceTreatmentRefused("erase_not_offered", "only a blur-only treatment is published")
        target = Path(out).resolve()
        if not (target.parent == storage.paths(project.id)["exports_dir"].resolve()
                and target.name.startswith(".") and target.suffix == ".mp4"):
            raise SourceTreatmentRefused("destination_not_versioned", f"{target} is not an export's staged file")
        return
    if destination != "versioned":
        raise SourceTreatmentRefused("treated_export_not_promoted",
                                     f"destination {destination!r}: a treated render is not promoted")
    target = Path(out).resolve()
    exports = storage.paths(project.id)["exports_dir"].resolve()
    if target.is_relative_to(exports) and not (target.parent.parent == exports
                                               and VERSIONED.match(target.parent.name)):
        raise SourceTreatmentRefused("destination_not_versioned",
                                     f"{target} is inside {exports} but not under exports/sct<n>/")


def prepare(clip: Any, project: Any, decision: Any, *, src: str, out: Any = None,
            destination: Any = None, attempt: dict | None = None,
            scratch_root: Any = None) -> dict | None:
    """Gate, revalidate, check the destination, build THIS attempt's patch.

    None when there is nothing to treat. `out` is the file an export writes
    (checked by `check_destination`); the editor still passes `scratch_root`
    instead, having no destination. The scratch dir is the attempt's own."""
    ctx = revalidate(decision, clip, project, src=src)
    if ctx is None:
        return None
    if out is not None:
        check_destination(out, project, destination=destination, resolved=ctx["resolved"])
    attempt = attempt or {"job_id": None, "nonce": uuid.uuid4().hex}
    root = Path(out).parent if out is not None else Path(scratch_root)
    stem = Path(out).stem if out is not None else "still"
    scratch = root / f".{stem}.st-{attempt['nonce']}"
    try:
        return build_patch(src, ctx, scratch=scratch, attempt=attempt,
                           start=float(clip.start_time or 0.0))
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def discard(patch: dict | None) -> None:
    if patch is not None:
        shutil.rmtree(patch["scratch"], ignore_errors=True)


# ── the clock ────────────────────────────────────────────────────────────────

def seek_arg(start: float) -> str:
    """The `-ss` text `build_dynamic_cmd` writes. The record corroborates the
    argv's `-ss` against the manifest, so a drift here refuses the render."""
    return f"{max(0.0, start):.3f}"


def pts_offset(clock: dict, k_first: int, seek: str, format_start_time: Any) -> int:
    """The pts patch frame 0 (source k_first) carries, on the source's own tb.

    ffmpeg re-bases an input seeked with `-ss` by `-(ss + format start_time)`,
    in microseconds rescaled to the stream's tb with round-half-away. Frame k
    then arrives at `k*step + start_pts - shift`. The patch frame for k is laid
    a QUARTER of a step earlier — SC1's 1/120 s at 30 fps — so overlay's
    "latest frame not after" picks it for k, and not for k-1, under any ±1-tick
    rounding. A file whose audio starts before its video has a negative format
    start time; ignoring it would slide every patch frame by that much."""
    tb = Fraction(clock["time_base"])
    step = clock["pts_step"]
    if step < 4:
        raise SourceTreatmentRefused("source_clock_refused", f"pts_step {step} leaves no margin")
    micro = round(Fraction(str(format_start_time or 0)) * 10**6) + round(Fraction(seek) * 10**6)
    exact = Fraction(micro, 10**6) / tb
    shift = math.floor(abs(exact) + Fraction(1, 2)) * (1 if exact >= 0 else -1)
    return k_first * step + clock["start_pts"] - shift - step // 4


def overlay_prefix(*, time_base: str, pts_step: int, pts_offset: int, x: int, y: int) -> str:
    """The treatment, ahead of the graph's `split=2`. The patch is input #2 (`[1:v]`)."""
    return (f"[1:v]setparams=range={TAGS[0]}:colorspace={TAGS[1]},settb={time_base},"
            f"setpts=N*{pts_step}{pts_offset:+d}[stpatch];"
            f"[0:v][stpatch]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0"
            f":format=yuv420,format=yuv420p[stsrc]")


def prefixed(graph: str, overlay: str) -> str:
    """`build_dynamic_filtergraph`'s graph with the treated source in front."""
    if not graph.startswith(_GRAPH_HEAD):
        raise ValueError("the dynamic graph no longer starts with [0:v]split=2")
    return f"{overlay};[stsrc]split=2[a][b];{graph[len(_GRAPH_HEAD):]}"


def patch_input_args(patch: dict) -> list[str]:
    return ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{patch['w']}x{patch['h']}",
            "-framerate", str(patch["fps"]), "-i", str(patch["file"])]


def build_patch(src: str, ctx: dict, *, scratch: Path, attempt: dict, start: float) -> dict:
    """This attempt's patch and `treatment_manifest.json`, verified before use."""
    import cv2

    from services.clipper.proxy_provenance import ffmpeg_build
    from services.clipper.source_treatment_manifest import treated_frames, verify_manifest
    from services.clipper.source_treatment_mask import IDENTITY_STREAM_KEYS

    resolved, mask, params, identity = ctx["resolved"], ctx["mask"], ctx["params"], ctx["identity"]
    clock = mask["clock"]
    scratch.mkdir(parents=True, exist_ok=False)
    geom = geometry(mask, resolved["per_line"], resolved["params"])
    patch_file = scratch / "patch.yuv"
    made = write_patch(src, mask, resolved["per_line"], resolved["params"], geom, patch_file)
    seek = seek_arg(start)
    off = pts_offset(clock, resolved["window"]["k_first"], seek,
                     (identity.get("format") or {}).get("start_time"))
    overlay = overlay_prefix(time_base=clock["time_base"], pts_step=clock["pts_step"],
                             pts_offset=off, x=made["x"], y=made["y"])
    build = ffmpeg_build()
    doc = {"schema": st.MANIFEST_SCHEMA, "attempt": attempt,
           "source": {"sha256": identity["sha256"], "sha256_how": identity["sha256_how"],
                      "stream": {k: identity["stream"].get(k) for k in IDENTITY_STREAM_KEYS}},
           "mask": {"sha256": mask["sha256"]},
           "glyphs": [{"line_id": lid, "png_sha256": line["glyph_png"]["sha256"]}
                      for lid, line in sorted(mask["lines"].items())],
           "params": {"version": resolved["params_version"], "values": resolved["params"]},
           "params_record": {"file": params["file"], "sha256": params["sha256"]},
           "window": resolved["window"], "pts_offset": off, "seek": seek,
           "format_start_time": (identity.get("format") or {}).get("start_time"),
           "frames": treated_frames(resolved),
           "patch": {"file": str(patch_file), "sha256": made["sha256"], "bytes": made["bytes"],
                     "frames": made["frames"], "pix_fmt": "yuv420p", "w": made["w"], "h": made["h"],
                     "x": made["x"], "y": made["y"], "tags": "/".join(TAGS),
                     "spare_trailing_frame": True},
           "overlay_filter": overlay, "footprint_px": made["footprint_px"],
           "protect": protect_report(mask, resolved["per_line"]),
           "generator": {"module_sha256": sha256_bytes(Path(__file__).read_bytes()),
                         "ffmpeg_build": {k: build.get(k) for k in
                                          ("version_line", "configuration_sha256", "exe_sha256")},
                         "opencv_version": cv2.__version__}}
    data = canonical_bytes(doc)
    manifest = scratch / "treatment_manifest.json"
    manifest.write_bytes(data)
    checked = verify_manifest(manifest, expected_sha256=sha256_bytes(data), attempt=attempt,
                              scratch_dir=scratch, resolved=resolved, mask=mask)
    return {"scratch": str(scratch), "manifest_path": str(manifest),
            "manifest_sha256": checked["manifest_sha256"], "resolved": resolved,
            "file": str(patch_file), "w": made["w"], "h": made["h"], "fps": clock["fps"],
            "overlay_filter": overlay}


# ── after the encode ─────────────────────────────────────────────────────────

def sidecar_identity(record: Any, patch: dict | None) -> dict:
    """The v3 `source_treatment_identity`, taken from the RECORD (what the encode
    consumed), never from the decision. An executed patch without a corroborated
    record refuses the attempt: no sidecar, so nothing to publish."""
    from services.clipper.source_treatment_manifest import NONE_IDENTITY

    if patch is None:
        return dict(NONE_IDENTITY)
    t = record.get("source_treatment") if isinstance(record, dict) else None
    if not (isinstance(t, dict) and t.get("corroborated") is True and isinstance(t.get("identity"), dict)
            and t["patch"]["sha256_after"] == t["patch"]["sha256_before"]):
        raise SourceTreatmentRefused("record_not_corroborated", "the encode's record does not bind the patch")
    return t["identity"]


def refuse_treated_publish(sidecar_path: Any, *, blur: bool = False) -> None:
    """`_publish_export`'s guard, the only place that renames over the original:
    a staged sidecar that carries a treatment — or cannot be read to prove it
    carries none — is never published (SC-addendum-v2 §4).

    `blur=True` (SC3, `_publish_export` only) lets through a sidecar whose label AND v3 identity are an
    active BLUR on every segment; anything else stays refused, erase and unreadable included."""
    from services.clipper import render_input

    try:
        body = json.loads(Path(sidecar_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SourceTreatmentRefused("treated_export_not_promoted", f"sidecar unreadable: {e}") from None
    if not isinstance(body, dict):
        raise SourceTreatmentRefused("treated_export_not_promoted", "the sidecar is not an object")
    labelled = body.get("source_treatment")
    if blur and _blur_only(body, labelled):
        return
    if labelled is not None and (not isinstance(labelled, dict) or labelled.get("treatment") != NONE):
        raise SourceTreatmentRefused("treated_export_not_promoted", f"labelled {labelled!r}")
    if (body.get("fingerprint_schema") == render_input.FINGERPRINT_SCHEMA_V3
            and render_input.treatment_identity_state(body) != render_input.TREATMENT_NONE):
        raise SourceTreatmentRefused("treated_export_not_promoted",
                                     f"identity {render_input.treatment_identity_state(body)}")


def _blur_only(body: dict, labelled: Any) -> bool:
    """A v3 sidecar whose label and identity both say an active blur, on every segment."""
    from services.clipper import render_input

    ident = body.get("source_treatment_identity")
    return (isinstance(labelled, dict) and labelled.get("treatment") == BLUR and labelled.get("active") is True
            and body.get("fingerprint_schema") == render_input.FINGERPRINT_SCHEMA_V3
            and render_input.treatment_identity_state(body) == render_input.TREATMENT_ACTIVE
            and ident["treatment"] == BLUR
            and all(isinstance(s, dict) and s.get("treatment") == BLUR for s in ident["per_line"]))
