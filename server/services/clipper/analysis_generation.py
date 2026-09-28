"""
ClipForge — AI Stream Clipper: one immutable directory per analysis attempt (OW1).

    analysis/generations/<job_id>-a<attempt_count>/
        signals.json  faces.json  regions.json  [regions_by_segment.json]
        frames_pts.json  frames/frame_NNNNN.jpg ...  generation.json

The attempt writes ONLY here, from its first byte, and nobody reads it until
`projects.analysis_generation` names it — a pointer set in the same short
transaction that schedules scoring (workers/clipper_analysis_publish.py).
Nothing here is ever renamed or rewritten; publication is the DB pointer alone.
The design and its reasons: data/claude-master-20260924/A/ow1/OW1-addendum.md,
amended by codex-verdict-next-24.md §1.

`generation.json` is written LAST and is what a reader checks: it lists
EXACTLY the files the generation needs, frames included, each with its sha256,
and the absences it allows (`regions_by_segment: none`). It does not list
itself. A generation whose manifest is missing, unparseable, incomplete, or
whose files do not hash to it is `unavailable` — an explicit refusal, never
empty signals. Every read re-hashes the bytes it parses, so a file changed
after the check is refused at the read, not trusted from the check.

A project with no pointer is `legacy`: the flat `analysis/*.json` it always
had, readable as before and labelled unverified (next-24 Q2). It is never
upgraded by re-stamping.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.clipper import storage

MANIFEST = "generation.json"
SCHEMA = "analysis_generation/1"
FRAMES_PTS = "frames_pts.json"
REQUIRED_JSON = ("signals", "faces", "regions")
OPTIONAL_JSON = ("regions_by_segment",)
# The names this module serves; every other artifact name stays flat.
OWNED = frozenset(REQUIRED_JSON + OPTIONAL_JSON)

VERIFIED, LEGACY, UNAVAILABLE = "verified", "legacy", "unavailable"


class GenerationUnavailable(RuntimeError):
    """The selected generation cannot be read as a whole. Not a fallback."""


def gen_id_for(job_id: str, attempt_count: int) -> str:
    return f"{job_id}-a{int(attempt_count)}"


def gen_dir(project_id: str, gen_id: str) -> Path:
    storage._check_id(gen_id, "generation")
    return storage.safe_join(project_id, "analysis", "generations", gen_id)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(gdir: Path, name: str, data: Any) -> None:
    """One generation file, compact, numpy-safe, atomic. Off the event loop."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                      default=storage._json_default)
    storage.atomic_write_text(gdir / f"{name}.json", text)


def seal(gdir: Path, header: dict, frames: list[str], by_range_present: bool) -> dict:
    """Hash every file the generation needs and write `generation.json` last.

    `frames` are the JPEGs the attempt actually wrote (its own list, never a
    glob of the directory). Off the event loop and outside any DB lock."""
    names = [f"{n}.json" for n in REQUIRED_JSON] + [FRAMES_PTS]
    if by_range_present:
        names.append("regions_by_segment.json")
    frame_names = [f"frames/{Path(f).name}" for f in frames]
    files = {n: sha256_file(gdir / n) for n in names + frame_names}
    doc = {"schema": SCHEMA, **header, "frames": frame_names, "files": files,
           "absent": [] if by_range_present else ["regions_by_segment.json"],
           "regions_by_segment": "present" if by_range_present else "none"}
    storage.atomic_write_json(gdir / MANIFEST, doc, separators=(",", ":"))
    return doc


def _check_manifest(doc: Any) -> str | None:
    """Why this manifest does not describe a whole generation, or None."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return "manifest schema"
    files, frames, absent = doc.get("files"), doc.get("frames"), doc.get("absent")
    if not isinstance(files, dict) or not isinstance(frames, list) or not isinstance(absent, list):
        return "manifest shape"
    if MANIFEST in files:
        return "manifest lists itself"
    for n in [f"{n}.json" for n in REQUIRED_JSON] + [FRAMES_PTS]:
        if n not in files:
            return f"manifest is missing {n}"
    rbs = "regions_by_segment.json"
    if (rbs in files) == (rbs in absent) or \
            doc.get("regions_by_segment") != ("present" if rbs in files else "none"):
        return "regions_by_segment neither listed nor declared absent"
    if not all(isinstance(f, str) and f.startswith("frames/") and f in files for f in frames):
        return "a frame is not in the file list"
    allowed = {f"{n}.json" for n in REQUIRED_JSON + OPTIONAL_JSON} | {FRAMES_PTS} | set(frames)
    if set(files) - allowed:
        return "manifest lists a file the generation does not own"
    if not all(isinstance(v, str) and len(v) == 64 for v in files.values()):
        return "manifest hash shape"
    return None


@dataclass
class Context:
    """One analysis, opened once and kept for a whole operation (next-24 §1 (1)).

    Scoring, a render and the segment-type cache each open ONE of these and read
    every file through it, so a pointer that moves mid-operation cannot mix two
    generations: the context never re-reads the pointer."""

    project_id: str
    generation: str | None
    state: str
    reason: str | None = None
    manifest: dict = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return gen_dir(self.project_id, self.generation or "")

    @property
    def current(self) -> bool:
        """Verified AND written by today's analysis code: the generation's own
        `analysis_version`, never ingest's `meta` stamp (next-24 §1 (4))."""
        from services.clipper import ANALYSIS_VERSION

        return self.state == VERIFIED and \
            self.manifest.get("analysis_version") == str(ANALYSIS_VERSION)

    def require(self) -> "Context":
        if self.state == UNAVAILABLE:
            raise GenerationUnavailable(
                f"analysis generation {self.generation} is unavailable: {self.reason}")
        return self

    def read(self, name: str) -> Any:
        """The parsed artifact. Legacy: the flat file, as before (None when
        absent). Verified: the generation's file, re-hashed against the manifest;
        a declared absence is None; anything else raises."""
        self.require()
        if self.state == LEGACY or name not in OWNED:
            return storage.read_artifact(self.project_id, name)
        rel = f"{name}.json"
        if rel in self.manifest["absent"]:
            return None
        return json.loads(self._bytes(rel).decode("utf-8"))

    def _bytes(self, rel: str) -> bytes:
        want = self.manifest["files"].get(rel)
        if want is None:
            raise GenerationUnavailable(f"{rel} is not in generation {self.generation}")
        try:
            data = (self.dir / rel).read_bytes()
        except OSError as exc:
            raise GenerationUnavailable(f"{rel} of {self.generation}: {exc}") from exc
        if sha256_bytes(data) != want:
            raise GenerationUnavailable(f"{rel} of {self.generation} does not match its sha256")
        return data

    def regions_at(self, t: float) -> dict:
        """The on-screen layout at time `t`, falling back to the whole-file answer.

        A source whose arrangement changes has no single layout, and using the
        averaged one crops a clip against a frame it was never in.

        Moved verbatim from `clipper_render_plan._regions_for` (OW1), so both of
        its reads come from this one context."""
        for blob in (self.read("regions_by_segment") or []):
            if not isinstance(blob, dict):
                continue
            if float(blob.get("start") or 0.0) <= t <= float(blob.get("end") or 0.0):
                return blob
        return self.read("regions") or {}

    def frames(self) -> list[str]:
        """The sampled JPEGs, in order: the manifest's list, never a glob."""
        self.require()
        if self.state == LEGACY:
            return sorted(str(p) for p in storage.paths(self.project_id)["frames_dir"].glob("*.jpg"))
        return [str(self.dir / f) for f in self.manifest["frames"]]

    def frames_pts_path(self) -> Path:
        self.require()
        if self.state == LEGACY:
            return storage.paths(self.project_id)["analysis_dir"] / FRAMES_PTS
        return self.dir / FRAMES_PTS


def legacy(project_id: str) -> Context:
    return Context(project_id, None, LEGACY, "pre-OW1 flat analysis; attempt provenance unverified")


def open_context(project_id: str, generation: str | None) -> Context:
    """Verify `generation` in full (every listed file re-hashed). Blocking:
    call it through a thread from async code."""
    if generation is None:
        return legacy(project_id)
    try:
        gdir = gen_dir(project_id, generation)
        doc = json.loads((gdir / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Context(project_id, generation, UNAVAILABLE, f"manifest unreadable ({exc})")
    why = _check_manifest(doc)
    if why is None and doc.get("gen_id") != generation:
        why = f"manifest names {doc.get('gen_id')!r}"
    if why is None and not isinstance(doc.get("content_type"), dict):
        why = "manifest has no content_type"
    if why is None:
        for rel, want in doc["files"].items():
            try:
                if sha256_file(gdir / rel) != want:
                    why = f"{rel} does not match its sha256"
                    break
            except OSError:
                why = f"{rel} is missing"
                break
    if why is not None:
        return Context(project_id, generation, UNAVAILABLE, why)
    return Context(project_id, generation, VERIFIED, None, doc)


def open_for(project: Any) -> Context:
    """The context of the generation this project row selected when it was
    loaded — the operation's pin is the row the operation already holds."""
    return open_context(project.id, getattr(project, "analysis_generation", None))


def remove_unpublished(project_id: str, gen_id: str) -> None:
    """Delete one attempt's own generation directory. Only its handler calls
    this, only when it did not publish, and only after its threads ended."""
    shutil.rmtree(gen_dir(project_id, gen_id), ignore_errors=True)
