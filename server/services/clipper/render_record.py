"""What was actually sent to ffmpeg, taken from the call that ran.

`caption_policy` records the DECISION and `output_identity` records the FILE.
Between them sits the thing neither can reach: the arguments the renderer was
actually given. The policy says "suppress"; whether the encode that ran carried
a `subtitles=` filter is a different sentence, and the corpus has already shown
the two coming apart — 15 exports were rendered under a suppression decision
with an `.ass` from an earlier run still sitting beside them, and every check
that read the file instead of the call got the wrong answer.

SO IT IS TAKEN FROM THE ARGV, at the moment of execution, and from nothing else.
Not from the `ass_path` the caller passed — that is the intention, and the
filtergraph is what ffmpeg read. Not reconstructed afterwards from the project's
setting, which is the same reconstruction `own_caption_layer` had to stop
making. The path this module hashes is the one it found INSIDE the filter
string, so a call that was handed a subtitle file and did not use it records
`caption_filter: false` with no hash, which is the truth about that encode.

WHAT IT DOES NOT CLAIM. That the text was drawn. A `subtitles=` filter in the
argv proves the filter was configured; §R6's defect was an `.ass` event libass
drew into a transparent bar, and no reading of the command line can see that.
The only instrument for it is a frame read of the delivered mp4, which the
acceptance batch owes and this does not pretend to replace.

AND IT NEVER ENTERS THE INPUT FINGERPRINT. It describes the output, so folding
it into the recipe digest would make a re-render of the same plan change its own
recipe — the same reason `output_identity` is written after the fingerprint and
not into it. The `caption_policy` that DECIDED the call does belong there, and
that is a separate change in `render_input`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

__all__ = ["SCHEMA", "record", "ass_events", "require_corroborated", "confirm_patch"]

SCHEMA = "clipper_render_record_v1"

#: `build_dynamic_filtergraph` emits `subtitles=filename='...'`; the static path
#: uses the same filter. Both spellings ffmpeg accepts are matched, because a
#: recogniser that knows only the spelling in use today reports `false` the day
#: somebody switches to the other one — and `false` here is a claim, not a gap.
# A chained filter can follow an input label directly: `[v]subtitles=...`.
# The static export uses exactly that spelling; missing `]` reported an absent
# layer while the real MP4 carried it (shared-export integration fixture).
_FILTER = re.compile(r"(?:^|[,;\[\]\s])(subtitles|ass)\s*=", re.IGNORECASE)
_FILENAME = re.compile(r"(?:filename\s*=\s*)?'((?:[^'\\]|\\.)*)'")

#: `Dialogue: 0,0:00:01.23,0:00:02.34,...`
_DIALOGUE = re.compile(r"^Dialogue:\s*[^,]*,([^,]+),([^,]+),", re.MULTILINE)

NO_ARGV = "the_command_was_not_a_sequence_of_strings"
NO_FILE = "the_filter_names_a_file_that_is_not_there"
UNREADABLE = "the_file_could_not_be_read"
NO_PATH_IN_FILTER = "the_filter_does_not_name_a_file"


def _unescape(raw: str) -> str:
    r"""Undo exactly what `escape_filter_path` does, and nothing else.

    That function turns backslashes into forward slashes and escapes the drive
    colon: `C:\x\y.ass` becomes `C\:/x/y.ass`. So the only escape to undo is
    `\:`. A general "drop every backslash" rule looks equivalent and is not —
    handed a Windows path that was never escaped it produces
    `C:UsersvladoAppData...`, a string that opens nothing, and the record would
    then have reported the file as missing.
    """
    return raw.replace("\\:", ":")


def _digest(path: Path) -> tuple[str | None, int | None, str | None]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None, NO_FILE
    except Exception:
        return None, None, UNREADABLE
    return hashlib.sha256(data).hexdigest(), len(data), None


#: The three answers, kept apart because two of them look alike from outside.
EVENTS_READ = "read"
EVENTS_EMPTY = "empty_demonstrated"
EVENTS_REFUSED = "refused"


def ass_events(path: Any) -> dict:
    """The dialogue events in one `.ass`, on the EXPORT's clock.

    IT IS THE EXPORT'S CLOCK AND NOT THE CLIP'S, and that is a fact about
    `clipper_captions._write_ass` rather than a hope: it applies
    `dead_air.remap_overlays(overlays, drop_spans)` before writing, because
    libass positions against absolute times and a caption left on the untrimmed
    clock drifts further out of sync with every second cut. So the intervals
    here are directly comparable with the mp4.

    THE INTERVALS, NOT THE COUNT. A count with a first start and a last end
    says nothing about the coverage between them: forty events and two events
    can share both endpoints. `intervals` is the merged, sorted union of the
    events' own spans — word-level overlays overlap heavily, so the union is
    what "there was text on screen" actually means — and `covered_s` is how
    much of the export it adds up to.

    THREE ANSWERS, and `state` names them so a consumer cannot collapse the
    first two: events were READ, the file was read and demonstrably holds none,
    or nothing could be read at all. An empty caption track and an unreadable
    one are the pair this whole batch exists to keep apart.
    """
    out: dict[str, Any] = {"state": EVENTS_REFUSED, "events": None,
                           "intervals": None, "covered_s": None,
                           "first_s": None, "last_start_s": None,
                           "last_end_s": None, "refused": None}
    if path is None:
        out["refused"] = NO_FILE
        return out
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        out["refused"] = NO_FILE
        return out
    except Exception:
        out["refused"] = UNREADABLE
        return out
    spans: list[tuple[float, float]] = []
    for raw_start, raw_end in _DIALOGUE.findall(text):
        s, e = _timestamp(raw_start), _timestamp(raw_end)
        if s is None or e is None or e < s:
            # ONE UNREADABLE LINE FAILS THE FILE. A partial parse would report
            # a caption track that ends earlier than it does, which is a
            # measurement of the parser rather than of the export. An end before
            # its start is unreadable too: it is a perfectly parseable pair of
            # timestamps and there is no span it could describe.
            out["refused"] = UNREADABLE
            return out
        spans.append((s, e))
    out["events"] = len(spans)
    if not spans:
        out["state"] = EVENTS_EMPTY
        out["intervals"], out["covered_s"] = [], 0.0
        return out
    out["state"] = EVENTS_READ
    merged: list[list[float]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out["intervals"] = [[round(a, 3), round(b, 3)] for a, b in merged]
    out["covered_s"] = round(sum(b - a for a, b in merged), 3)
    out["first_s"] = round(min(s for s, _ in spans), 3)
    out["last_start_s"] = round(max(s for s, _ in spans), 3)
    out["last_end_s"] = round(max(e for _, e in spans), 3)
    return out


def _timestamp(raw: str) -> float | None:
    parts = raw.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError:
        return None
    if m < 0 or s < 0 or h < 0:
        return None
    return h * 3600 + m * 60 + s


def record(argv: Any, *, ass_path: Any = None, treatment_manifest: Any = None) -> dict:
    """`clipper_render_record_v1` for one encode. Never raises.

    `ass_path` is recorded as the file the caller MEANT to burn, beside — never
    instead of — what the argv actually names. When they differ, that difference
    is the finding.

    `treatment_manifest` is the source patch THIS attempt built (SC batch 2).
    With it, or with a patch input in the argv, the record gains
    `source_treatment` (`_treatment`); an untreated encode's record is exactly
    what it was before the key existed.
    """
    out: dict[str, Any] = {
        "schema": SCHEMA, "caption_filter": None, "ass_path": None,
        "ass_sha256": None, "ass_bytes": None, "ass_refused": None,
        "ass_events": None, "argv_sha256": None, "argv_len": None,
        "ass_path_offered": str(ass_path) if ass_path else None,
        "offered_matches_used": None,
    }
    if (not isinstance(argv, Sequence) or isinstance(argv, (str, bytes))
            or not all(isinstance(a, str) for a in argv)):
        # `caption_filter` stays None. A command nobody could read is not a
        # command with no subtitle filter in it.
        out["ass_refused"] = NO_ARGV
        if treatment_manifest is not None:
            out["source_treatment"] = {"schema": TREATMENT_SCHEMA, "corroborated": False,
                                       "refused": "record_not_corroborated", "detail": NO_ARGV}
        return out
    if treatment_manifest is not None or _inputs(argv)[1:]:
        out["source_treatment"] = _treatment(argv, treatment_manifest)
    out["argv_len"] = len(argv)
    out["argv_sha256"] = hashlib.sha256(
        "\x00".join(argv).encode("utf-8", "replace")).hexdigest()
    # NUL as the separator, not a space: an argument may contain a
    # space, and two different commands that differ only in where the
    # boundaries fall would otherwise digest the same.

    used: str | None = None
    found = False
    for arg in argv:
        hit = _FILTER.search(arg)
        if not hit:
            continue
        found = True
        name = _FILENAME.search(arg, hit.end() - 1)
        if name:
            used = _unescape(name.group(1))
            break
    out["caption_filter"] = found
    if not found:
        # DEMONSTRATED ABSENCE, and it is allowed to be one: the argv is the
        # whole of what ffmpeg was told. `offered_matches_used` still says
        # whether a file was handed over and ignored.
        out["offered_matches_used"] = False if ass_path else None
        return out
    if used is None:
        out["ass_refused"] = NO_PATH_IN_FILTER
        return out

    out["ass_path"] = used
    out["ass_sha256"], out["ass_bytes"], out["ass_refused"] = _digest(Path(used))
    out["ass_events"] = ass_events(used)
    if ass_path:
        try:
            out["offered_matches_used"] = (
                Path(str(ass_path)).resolve() == Path(used).resolve())
        except Exception:
            out["offered_matches_used"] = None
    return out


# ── the source-caption treatment, read from the argv (SC-addendum-v2 §6) ─────

TREATMENT_SCHEMA = "clipper_render_record_source_treatment_v1"


def _inputs(argv: Sequence[str]) -> list[tuple[list[str], str]]:
    """`(options before it, path)` for each `-i`, in input order."""
    out, opts = [], []
    for i, arg in enumerate(argv):
        if arg == "-i" and i + 1 < len(argv):
            out.append((opts, argv[i + 1]))
            opts = []
        elif i == 0 or argv[i - 1] != "-i":
            opts.append(arg)
    return out


def _opt(opts: list[str], name: str) -> str | None:
    return opts[opts.index(name) + 1] if name in opts[:-1] else None


def _treatment(argv: Sequence[str], manifest: Any) -> dict:
    """The patch the encode READ, corroborated against the manifest this attempt
    wrote: the argv's patch path is the manifest's file, the file hashes to the
    manifest's patch now, the argv's graph opens with the manifest's overlay,
    its `-ss` is the seek the pts offset was computed for, and the manifest is
    the one the attempt verified. Any gap → `corroborated: false` with the
    reason, and `require_corroborated` refuses the encode."""
    rec: dict[str, Any] = {
        "schema": TREATMENT_SCHEMA, "manifest_path": None, "manifest_sha256": None,
        "patch": None, "overlay_filter": None, "seek": None, "pts_offset": None,
        "mask_sha256": None, "params_version": None, "per_line": None, "ffmpeg_build": None,
        "identity": None, "identity_sha256": None, "corroborated": False, "refused": None}

    def no(reason: str, detail: str = "") -> dict:
        rec["refused"], rec["detail"] = reason, detail
        return rec

    inputs = _inputs(argv)
    raw = [i for i, (opts, _) in enumerate(inputs) if _opt(opts, "-f") == "rawvideo"]
    if not isinstance(manifest, dict):
        return no("manifest_missing", "the argv reads a second input and no manifest names it")
    if raw != [1]:
        return no("record_not_corroborated", f"raw patch inputs at {raw}; the graph reads [1:v]")
    opts, patch_path = inputs[1]
    try:
        data = Path(str(manifest["manifest_path"])).read_bytes()
        doc = json.loads(data.decode("utf-8"))
        patch = doc["patch"]
    except Exception as e:  # missing, unreadable, not a manifest: the same refusal
        return no("manifest_missing", repr(e))
    rec["manifest_path"] = str(manifest["manifest_path"])
    rec["manifest_sha256"] = hashlib.sha256(data).hexdigest()
    if rec["manifest_sha256"] != manifest.get("manifest_sha256"):
        return no("manifest_hash_mismatch", "not the manifest this attempt verified")
    try:
        same = Path(patch_path).resolve() == Path(str(patch.get("file"))).resolve()
    except Exception:
        same = False
    if not same:
        return no("manifest_of_another_attempt", f"argv reads {patch_path}, manifest names {patch.get('file')}")
    sha, size, _ = _digest(Path(patch_path))
    rec["patch"] = {"path": patch_path, "sha256_before": sha, "sha256_after": None, "bytes": size}
    if sha is None or sha != patch.get("sha256") or size != patch.get("bytes"):
        return no("patch_hash_mismatch", f"{patch_path} is not the manifest's patch")
    if (_opt(opts, "-s") != f"{patch.get('w')}x{patch.get('h')}"
            or _opt(opts, "-pix_fmt") != patch.get("pix_fmt")):
        return no("record_not_corroborated", "the argv's patch geometry is not the manifest's")
    rec["overlay_filter"] = doc.get("overlay_filter")
    graph = _opt(list(argv), "-filter_complex") or ""
    if not isinstance(rec["overlay_filter"], str) or not graph.startswith(
            rec["overlay_filter"] + ";[stsrc]split=2"):
        return no("record_not_corroborated", "the argv's graph does not open with the manifest's overlay")
    rec["seek"] = _opt(inputs[0][0], "-ss")
    if rec["seek"] != doc.get("seek"):
        return no("record_not_corroborated", f"argv seeks {rec['seek']}, the offset is for {doc.get('seek')}")
    from services.clipper import source_treatment_manifest as stm

    try:
        identity = stm.fingerprint_identity(manifest["resolved"], doc)
        rec.update({"pts_offset": doc["pts_offset"], "mask_sha256": doc["mask"]["sha256"],
                    "params_version": doc["params"]["version"],
                    "per_line": [[f["k_from"], f["k_to"], f["treatment"], f["line_id"]]
                                 for f in doc["frames"] if f["treatment"] is not None],
                    "ffmpeg_build": doc["generator"]["ffmpeg_build"], "identity": identity,
                    "identity_sha256": stm.treatment_identity_sha256(identity)})
    except Exception as e:
        return no("record_not_corroborated", repr(e))
    rec["corroborated"] = True
    return rec


def require_corroborated(rec: Any) -> None:
    """Before the encode: a treated argv with a record that does not bind it is
    refused — the attempt fails, no sidecar, no publish."""
    from services.clipper.source_treatment import REASONS, SourceTreatmentRefused

    t = rec.get("source_treatment") if isinstance(rec, dict) else None
    if not isinstance(t, dict) or t.get("corroborated") is not True:
        reason = t.get("refused") if isinstance(t, dict) else None
        raise SourceTreatmentRefused(reason if reason in REASONS else "record_not_corroborated",
                                     str((t or {}).get("detail", "no source_treatment record")))


def confirm_patch(rec: dict) -> None:
    """After the encode: the patch is hashed again and must not have changed."""
    from services.clipper.source_treatment import SourceTreatmentRefused

    require_corroborated(rec)
    t = rec["source_treatment"]
    t["patch"]["sha256_after"] = _digest(Path(t["patch"]["path"]))[0]
    if t["patch"]["sha256_after"] != t["patch"]["sha256_before"]:
        t["corroborated"], t["refused"] = False, "patch_hash_mismatch"
        raise SourceTreatmentRefused("patch_hash_mismatch", "the patch changed during the encode")
