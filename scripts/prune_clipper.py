"""Reclaim disk from finished clipper projects, without losing a deliverable.

Measured on this rig: `data/clipper` is 64 GB and **92% of it is the original
source videos**. Proxies are 0.2-0.5 GB, frames and analysis are single-digit
megabytes, and the exports — the only thing that is actually a product — are
under a gigabyte in total. Nothing has ever pruned any of it.

What this removes and what that costs:

  source/    the original download. Removing it means the project can no
             longer EXPORT a new clip, because the renderer opens the source
             once per export. Everything already exported stays on disk, and
             the pipeline's own error for this case already exists and says
             "re-run the analysis to fetch it again".
  proxy/     the 480p copy every analysis pass reads. Removing it means a
             re-score or a Pass D review has nothing to measure. Cheap to
             rebuild IF the source is still there, impossible if it is not —
             so `--proxies` refuses on a project whose source is already gone.
  frames/    sampled JPEGs. Rebuildable from the proxy.

Never touched: exports/, analysis/, previews/, thumbs/. The first is the
product and the rest are small and expensive to recompute.

DRY RUN BY DEFAULT. Deleting sixty gigabytes should take a second, deliberate
sentence, not a typo.

    python scripts/prune_clipper.py                     # what would go
    python scripts/prune_clipper.py --sources --apply   # do it
    python scripts/prune_clipper.py --sources --keep-recent 3 --apply
    python scripts/prune_clipper.py --only 9a414e1f8a86 --sources --apply
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
DATA = Path(sys.argv[0]).parent if False else _REPO / "data" / "clipper"

# In the order it is safe to remove them: least useful first.
PRUNABLE = ("frames", "proxy", "source")
KEEP_ALWAYS = ("exports", "analysis", "previews", "thumbs")


def size_of(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def projects() -> list[Path]:
    if not DATA.exists():
        return []
    return sorted((p for p in DATA.iterdir()
                   if p.is_dir() and not p.name.startswith("_")),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def content_signature(path: Path, window: int = 4 * 1024 * 1024) -> str:
    """First and last few MB plus the exact size.

    Hashing 48 GB to prove a duplicate would cost more than the duplicate does.
    Two video files that agree on their first 4 MB, their last 4 MB and their
    byte count are the same file; nothing else produces that by accident.
    """
    import hashlib

    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as handle:
        digest.update(handle.read(window))
        if size > 2 * window:
            handle.seek(-window, 2)
            digest.update(handle.read(window))
    digest.update(str(size).encode())
    return digest.hexdigest()


def prune_duplicates(*, apply: bool) -> int:
    """Extra copies of a source that is already on disk. Loses nothing.

    Two of them exist on this rig and both are accidents of how a project is
    made rather than anything anyone chose:

      * `create_project` MOVES a staged upload into the project's source dir,
        and `_ingest_local` then COPIED it to `source.mp4` — so every project
        held its source twice. Fixed at the source on 2026-08-17; this clears
        what the old path already wrote.
      * `_uploads/batch/` keeps the download that was copied into the project,
        so a batch-created project has a third copy.

    `source.mp4` is the one kept, because that is the name the pipeline writes
    and every other name is a leftover.
    """
    freed = 0
    print("--- redundant copies (identical content, one kept) ---")
    for project in projects():
        source_dir = project / "source"
        canonical = next((f for f in source_dir.glob("source.*") if f.is_file()), None)
        if canonical is None:
            continue
        keep = content_signature(canonical)
        for extra in sorted(source_dir.iterdir()):
            if not extra.is_file() or extra == canonical:
                continue
            if content_signature(extra) != keep:
                print(f"  {project.name:14s} {extra.name:26s} DIFFERENT — kept")
                continue
            freed += extra.stat().st_size
            print(f"  {project.name:14s} {extra.name:26s} "
                  f"{human(extra.stat().st_size):>9s}  duplicate of {canonical.name}")
            if apply:
                extra.unlink()

        # And the staging copy, matched against this project's own source.
        for staged in sorted((DATA / "_uploads" / "batch").glob("*")):
            if not staged.is_file():
                continue
            if content_signature(staged) == keep:
                freed += staged.stat().st_size
                print(f"  {project.name:14s} _uploads/{staged.name:17s} "
                      f"{human(staged.stat().st_size):>9s}  duplicate of {canonical.name}")
                if apply:
                    staged.unlink()

    print(f"\n{'freed' if apply else 'would free'}: {human(freed)} "
          f"— every source still on disk")
    if not apply:
        print("nothing was deleted. Add --apply.")
    return freed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duplicates", action="store_true",
                    help="remove byte-identical extra copies of a source, keeping "
                         "one. Loses nothing. Start here.")
    ap.add_argument("--sources", action="store_true",
                    help="remove the original downloads (92%% of the space). NOTE: "
                         "the eleven labelled sources are the detectors' test "
                         "corpus — see docs/source-labels.md before using this.")
    ap.add_argument("--proxies", action="store_true",
                    help="also remove proxies — only where the source remains")
    ap.add_argument("--frames", action="store_true", help="also remove sampled frames")
    ap.add_argument("--only", metavar="ID", help="one project instead of all")
    ap.add_argument("--keep-recent", type=int, default=0, metavar="N",
                    help="leave the N most recently touched projects alone")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete. Without this it only reports.")
    args = ap.parse_args()

    if args.duplicates:
        prune_duplicates(apply=args.apply)
        if not (args.sources or args.proxies or args.frames):
            return
        print()

    wanted = [k for k, on in (("frames", args.frames), ("proxy", args.proxies),
                              ("source", args.sources)) if on]

    found = projects()
    if args.only:
        found = [p for p in found if p.name == args.only]
        if not found:
            raise SystemExit(f"no such project: {args.only}")
    elif args.keep_recent > 0:
        kept = found[:args.keep_recent]
        print(f"leaving the {len(kept)} most recent alone: "
              + ", ".join(p.name for p in kept) + "\n")
        found = found[args.keep_recent:]

    total_now = freed = 0
    print(f"{'project':16s} {'total':>10s} {'source':>10s} {'proxy':>9s} "
          f"{'exports':>9s}   would free")
    for project in found:
        parts = {k: size_of(project / k) for k in PRUNABLE + KEEP_ALWAYS}
        total = sum(parts.values())
        total_now += total

        removable = list(wanted)
        note = ""
        # A proxy without its source cannot be rebuilt, and every analysis pass
        # reads the proxy. Removing both leaves a project that can neither
        # export nor be re-scored — which is deletion, and deletion belongs to
        # the API, not to a disk-space script.
        if "proxy" in removable and (parts["source"] == 0 or "source" in removable):
            removable.remove("proxy")
            note = "  (proxy kept: unrebuildable without the source)"

        gain = sum(parts[k] for k in removable)
        freed += gain
        print(f"{project.name:16s} {human(total):>10s} {human(parts['source']):>10s} "
              f"{human(parts['proxy']):>9s} {human(parts['exports']):>9s}   "
              f"{human(gain):>9s}{note}")

        if args.apply:
            for key in removable:
                target = project / key
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)

    print()
    print(f"on disk now: {human(total_now)}")
    if not wanted:
        print("nothing selected — pass --sources (and optionally --frames/--proxies)")
        return
    print(f"{'freed' if args.apply else 'would free'}: {human(freed)}  "
          f"({freed / total_now:.0%})" if total_now else "")
    if not args.apply:
        print("\nnothing was deleted. Add --apply.")
    else:
        print("\nExports and analysis are untouched. A project whose source is "
              "gone can still be browsed and its existing clips replayed; "
              "exporting a NEW clip from it needs the source fetched again.")


if __name__ == "__main__":
    main()
