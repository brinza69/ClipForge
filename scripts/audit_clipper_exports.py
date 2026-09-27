"""Rerun the export audit that produced the v3 baseline.

Batch R0. The numbers in `docs/handover/areas/clipper/CURRENT.md` — 58 clips,
1.341 shots, 29,3/min, 116 invisible cuts — were measured by hand once. This
turns them into a gate: any agent can run it, on any subset of projects, and
compare like for like.

    python scripts/audit_clipper_exports.py pilotf81b pilotee0e pilot6b38 pilot2c8a
    python scripts/audit_clipper_exports.py --all --json --output audit.json

Reads only. It never writes into a project directory: a report that mutates the
corpus it is measuring cannot be rerun to check itself. `--output` writes where
you point it and nowhere else.

The metric definitions live in `server/services/clipper/edit_quality.py`; this
file is the CLI and the formatting, so the rules stay testable without argv.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

# Derived, never hardcoded — the same note as in render_dynamic_clip.py. The
# rig runs a second backend against the same tree, and a hardcoded data dir
# audits whichever one the author happened to have.
DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from services.clipper import edit_quality as eq  # noqa: E402
from services.clipper import edit_quality_totals as totals  # noqa: E402


def _projects(names: list[str], every: bool) -> list[str]:
    if names:
        return names
    if not every:
        return []
    # Any project that has exports at all, not just ones with sidecars. Keying
    # on `*.json` excluded a project whose every clip was rendered and never
    # stamped — the corpus with the worst problem was the one that disappeared.
    return sorted(p.name for p in DATA.iterdir()
                  if p.is_dir() and (p / "exports").is_dir()
                  and (any((p / "exports").glob("*.mp4"))
                       or any((p / "exports").glob("*.json"))))


def _exports(project_id: str) -> tuple[list[tuple[str, dict]], dict[str, list[str]]]:
    """Every export in one project: the parsed sidecars, and what is wrong.

    Enumerated over the union of the mp4s and the sidecars, MEASURED over their
    intersection. Globbing `*.json` was the first version and it made a rendered
    clip whose sidecar was never written vanish from the corpus entirely. The
    fix for that then went too far the other way and measured orphans: a plan
    whose mp4 is gone describes a video that does not exist, and counting its
    shots puts a file nobody can watch into the baseline.

    The five ways an export can be incomplete are findings, never clips:

    - `missing_sidecar` — an mp4 with no plan beside it;
    - `orphan_sidecar`  — a plan whose mp4 is gone;
    - `unreadable_sidecar` — a plan that exists and does not parse, or parses to
      something that is not a sidecar at all. A bare `[]` used to reach
      `clip_report` and take the whole run down with a TypeError: a corrupt file
      crashed the audit instead of being reported by it;
    - `mislabelled_sidecar` — a plan that parses, but names a different clip than
      the file it sits beside, or a different project than the one being audited. It describes a video that is not there, and
      measuring it puts another clip's shots into this one's project;
    - `unidentified_sidecar` — a plan that names no clip or no project at all.
      Assuming it belongs where it was found is the same unproven leap, made
      silently.

    Each sidecar is returned WITH the stem it was found under, so the report can
    say which file a row came from.
    """
    exports = DATA / project_id / "exports"
    problems: dict[str, list[str]] = {
        "missing_sidecar": [], "orphan_sidecar": [], "unreadable_sidecar": [],
        "mislabelled_sidecar": [], "unidentified_sidecar": []}
    if not exports.is_dir():
        return [], problems

    videos = {p.stem for p in exports.glob("*.mp4")}
    plans = {p.stem for p in exports.glob("*.json")}
    parsed: list[tuple[str, dict]] = []
    for stem in sorted(videos | plans):
        path = exports / f"{stem}.json"
        if stem not in plans:
            problems["missing_sidecar"].append(f"{project_id}/{stem}.mp4")
            continue
        if stem not in videos:
            problems["orphan_sidecar"].append(str(path))
            continue
        try:
            sidecar = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            problems["unreadable_sidecar"].append(str(path))
            continue
        if not isinstance(sidecar, dict):
            problems["unreadable_sidecar"].append(str(path))
            continue
        # The identity check runs on EVERY sidecar, not only on the ones that
        # happen to carry a usable id. Gating it on `isinstance(str)` meant a
        # plan with no identity at all — key absent, `null`, a number — was
        # measured as though it belonged to the file it sits beside, which is
        # the same unproven assumption the check exists to refuse.
        clip_id = sidecar.get("clip_id")
        project = sidecar.get("project_id")
        if not isinstance(clip_id, str) or not clip_id                 or not isinstance(project, str) or not project:
            problems["unidentified_sidecar"].append(str(path))
            continue
        if clip_id != stem:
            problems["mislabelled_sidecar"].append(f"{path} names clip {clip_id}")
            continue
        # The other half of the same identity, and it was missing: a plan copied
        # in from another project has a `project_id` that says so, and measuring
        # it puts another source's edit into this one's baseline.
        if project != project_id:
            problems["mislabelled_sidecar"].append(
                f"{path} names project {project}")
            continue
        parsed.append((stem, sidecar))
    return parsed, problems


def _fmt(value, spec: str = "") -> str:
    if value == eq.UNAVAILABLE:
        return eq.UNAVAILABLE
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
    return format(value, spec) if spec else str(value)


def _print_clips(rows: list[dict]) -> None:
    print(f"{'clip':<14}{'shots':>6}{'/min':>7}{'min_s':>7}"
          f"{'equiv':>7}{'gaps':>6}{'lead_s':>8}{'tail_s':>8}")
    for r in rows:
        print(f"{str(r['clip_id']):<14}{_fmt(r['shots']):>6}"
              f"{_fmt(r['shots_per_minute'], '.1f'):>7}"
              f"{_fmt(r['min_shot_s'], '.3f'):>7}"
              f"{_fmt(r['equivalent_cuts']):>7}"
              f"{_fmt(r['non_contiguous_boundaries']):>6}"
              f"{_fmt(r['lead_in_s'], '.3f'):>8}"
              f"{_fmt(r['tail_s'], '.3f'):>8}")


def _print_totals(label: str, t: dict) -> None:
    print(f"\n{label}")

    def row(name: str, value: str) -> None:
        print(f"  {name:<26}{value}")

    row("clips", f"{t['clips']}   (static exports: {t['static_exports']})")
    row("duration", f"{_fmt(t['duration_s'], '.2f')} s   "
                    f"clock: {_fmt(t['duration_clocks'])}")
    row("shots", f"{_fmt(t['shots'])}   "
                 f"({_fmt(t['shots_per_minute_pooled'], '.4f')}/min pooled)")
    if "shots_per_minute_source_mean" in t:
        # A different question, not a better answer: pooled is the corpus rate,
        # the mean weighs each source equally. They disagree, and printing one
        # of them alone is how a gaming baseline gets read as a general one.
        row("", f"({_fmt(t['shots_per_minute_source_mean'], '.4f')}/min mean of "
                f"{t['coverage_sources']} sources)")
    row("shortest shot", f"{_fmt(t['min_shot_s'], '.3f')} s")
    row("composition", _fmt(t["composition"]))
    row("regime", _fmt(t["regime"]))
    row("equivalent fit->fit cuts", _fmt(t["equivalent_cuts"]))
    row("trim-induced source jumps", _fmt(t["trim_jumps"]))
    row("undecidable boundaries", _fmt(t["undecidable_boundaries"]))
    row("non-contiguous boundaries", _fmt(t["non_contiguous_boundaries"]))
    row("start on first word",
        f"{_fmt(t['clips_starting_on_first_word'])}/{t['clips']}")
    row(f"tail <= {eq.TAIL_TIGHT_S * 1000:.0f}ms",
        f"{_fmt(t['clips_with_tight_tail'])}/{t['clips']}")
    row("duplicate captions", _fmt(t["captions_duplicate_declared"]))
    row("render versions", _fmt(t["render_versions"]))
    row("fingerprints", _fmt(t["fingerprints"]))
    if t["defects"]:
        row("defects", _fmt(t["defects"]))
    row("coverage", _fmt(t["coverage"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*", help="project ids to audit")
    ap.add_argument("--all", action="store_true",
                    help="every project under the data dir that has sidecars")
    ap.add_argument("--clips", action="store_true", help="per-clip table")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--output", help="write the JSON report here instead of stdout")
    args = ap.parse_args()

    names = _projects(args.projects, args.all)
    if not names:
        ap.error("name at least one project, or pass --all")

    projects: list[dict] = []
    all_clips: list[dict] = []
    incomplete: dict[str, list[str]] = {
        "missing_sidecar": [], "orphan_sidecar": [], "unreadable_sidecar": [],
        "mislabelled_sidecar": [], "unidentified_sidecar": []}
    for project_id in names:
        sidecars, problems = _exports(project_id)
        for kind, paths in problems.items():
            incomplete[kind].extend(paths)
        rows = [{**eq.clip_report(s), "file": f"{project_id}/{stem}.json"}
                for stem, s in sidecars]
        projects.append(totals.project_report(project_id, rows))
        all_clips.append(rows)

    flat = [r for rows in all_clips for r in rows]
    macro = totals.macro_report(projects, flat)
    # A script called a gate that always exits 0 is a report. Structural
    # problems have to reach whatever runs it without a human reading the
    # output: an incomplete export, an artifact the module refused to measure,
    # or a fingerprint that no longer matches its plan. An UNSTAMPED sidecar is
    # not a failure — the 58 pilot exports predate the key — but a stamped one
    # that disagrees with its own plan is.
    mismatched = macro["fingerprints"].get(eq.FINGERPRINT_MISMATCH, 0)
    # A v3 record whose source-treatment identity cannot be read is not "no
    # treatment": it fails the gate on its own row (SC-addendum-v2 §5).
    mismatched += macro["fingerprints"].get(eq.FINGERPRINT_TREATMENT_UNREADABLE, 0)
    # A gap is a structurally invalid edit — shots that do not join — and it
    # used to pass the gate because it is a metric rather than a "defect". An
    # UNDECIDABLE boundary is different and deliberately does NOT fail: a plan
    # written before `composition` existed is old, not corrupt.
    gaps = macro["non_contiguous_boundaries"]
    gaps = gaps if isinstance(gaps, int) else 0
    integrity_ok = (not any(incomplete.values()) and not macro["defects"]
                    and not mismatched and not gaps)
    report = {"projects": projects, "macro": macro,
              "incomplete_exports": incomplete, "integrity_ok": integrity_ok}
    if args.clips:
        report["clips"] = flat

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.output}")
        return 0 if integrity_ok else 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if integrity_ok else 2

    for project_id, rows in zip(names, all_clips):
        if args.clips:
            print(f"\n=== {project_id} ===")
            _print_clips(rows)
    for project in projects:
        _print_totals(project["project_id"], project)
    if len(projects) > 1:
        _print_totals(f"MACRO ({macro['projects']} projects)", macro)
    for kind, paths in incomplete.items():
        if paths:
            print(f"\n{len(paths)} {kind}:")
            for path in paths:
                print(f"  {path}")
    print(f"\nintegrity_ok: {integrity_ok}")
    return 0 if integrity_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
