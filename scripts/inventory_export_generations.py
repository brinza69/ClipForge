"""Name and hash every generation of exports that is already on disk.

    python scripts/inventory_export_generations.py
    python scripts/inventory_export_generations.py --project pilotf81b

WHY. Four directories of similar files are not distinguishable by looking at
them. `pilotf81b` holds `exports/`, `exports_pre_caption_fix/` and
`exports_pre_replan/`, 60 files and about half a gigabyte each, and nothing on
disk says which is which or whether any of them has drifted. Codex asked for the
generations to be kept apart with an inventory and hashes for the MP4s and the
sidecars; `export_generations.preserve` writes one for every generation it
creates from now on, and this writes them for the ones that already exist.

IT COPIES NOTHING AND CHANGES NOTHING. Reading 6.45 GB and writing a few
kilobytes of JSON.

AND IT DOES NOT WRITE INTO `exports/`. Six places in this repo read
`exports/*.json` as a clip's sidecar, and one stray file has taken the R0 gate
down before. The inventories go to `<project>/generations/<name>.json`, outside
every directory anything else globs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="", help="one project, or all of them")
    args = ap.parse_args()

    from services.clipper import export_generations as eg

    if not DATA.is_dir():
        print(f"REFUSED: no corpus at {DATA}")
        return 2
    projects = ([DATA / args.project] if args.project
                else sorted(p for p in DATA.iterdir() if p.is_dir()))
    written = skipped = unreadable = 0
    for proj in projects:
        if not proj.is_dir():
            print(f"REFUSED: {proj} is not there")
            return 2
        dirs = [proj / "exports"] + [proj / n for n in eg.generations(proj)]
        for d in dirs:
            if not d.is_dir():
                continue
            got = eg.inventory(d)
            if not got["files"]:
                # An empty directory is reported, not silently passed over: a
                # generation that holds nothing is a fact about the corpus.
                print(f"  {proj.name:16} {d.name:26} EMPTY")
                skipped += 1
                continue
            out_dir = proj / "generations"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{d.name}.json").write_text(
                json.dumps(got, indent=1), encoding="utf-8")
            unreadable += got["unreadable"]
            written += 1
            print(f"  {proj.name:16} {d.name:26} {len(got['files']):>4} files "
                  f"{got['bytes'] / 1e6:>8.0f} MB"
                  + (f"  {got['unreadable']} UNREADABLE" if got["unreadable"]
                     else ""))
    print(f"\n{written} inventories written, {skipped} empty directories, "
          f"{unreadable} unreadable files")
    # An unreadable file inside a generation is a corpus that cannot be fully
    # identified, and the exit code says so rather than the run looking clean.
    return 2 if unreadable else 0


if __name__ == "__main__":
    raise SystemExit(main())
