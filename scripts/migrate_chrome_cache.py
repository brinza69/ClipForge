"""One-off: stamp `measured_with` onto chrome caches written before it existed.

WHY THIS IS A MIGRATION AND NOT A RE-RUN. `source_chrome.detect` now records the
constants its answer depended on, so a cached verdict can be checked against the
current detector instead of trusted. The 101 verdicts already on disk cost about
two hours of GPU and predate the field.

WHY STAMPING THEM IS VERIFIED RATHER THAN ASSUMED, which is the whole question —
writing a configuration onto a measurement that was made with a different one is
exactly the failure this field exists to prevent:

  * three of the four constants are ALREADY IN each verdict, because `detect`
    has always written `every_s`, `frames_min` and `conf_min` into its output.
    This refuses any file whose recorded values differ from the module's.
  * the fourth, `SAMPLES_MIN`, is not in the verdict. It entered the repo at
    `dbd1cc0` and has never been changed since — `git log -S "SAMPLES_MIN = "`
    on the module returns that one commit — and every cache file here was
    written after it. So its value during those measurements is established
    from history rather than from optimism.

A file that fails either check is LEFT ALONE and reported. It will simply be
re-measured, which costs a minute and is what should happen to a verdict nobody
can vouch for.

    python scripts/migrate_chrome_cache.py [--apply]
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

#: The one commit that introduced `SAMPLES_MIN`, and the reason its value can be
#: established for a measurement that never recorded it. Named here so the claim
#: is checkable rather than remembered.
SAMPLES_MIN_UNCHANGED_SINCE = "dbd1cc0"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the files; without it, only report")
    args = ap.parse_args()

    from services.clipper import source_chrome as sc

    want = sc.config()
    # The three a verdict records for itself, mapped to the constants they are.
    recorded = {"every_s": "EVERY_S", "frames_min": "FRAMES_MIN",
                "conf_min": "CONF_MIN"}

    stamped = already = refused = 0
    for path in sorted(DATA.glob("*/chrome_cache/*.json")):
        where = f"{path.parent.parent.name}/{path.stem}"
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"REFUSED {where}: unreadable ({type(exc).__name__})")
            refused += 1
            continue
        if not isinstance(got, dict) or got.get("schema") != "source_chrome_v1":
            print(f"REFUSED {where}: not a source_chrome_v1 verdict")
            refused += 1
            continue
        if isinstance(got.get("measured_with"), dict):
            already += 1
            continue
        bad = [f"{field}={got.get(field)!r} but the module has {want[const]!r}"
               for field, const in recorded.items()
               if got.get(field) != want[const]]
        if bad:
            # Measured with something else. Re-measure it rather than relabel it.
            print(f"REFUSED {where}: {'; '.join(bad)}")
            refused += 1
            continue
        stamped += 1
        if args.apply:
            got["measured_with"] = want
            path.write_text(json.dumps(got), encoding="utf-8")

    total = stamped + already + refused
    print()
    print(f"cache files          {total}")
    print(f"  already stamped    {already}")
    print(f"  {'stamped' if args.apply else 'would stamp'}          {stamped}")
    print(f"  refused            {refused}")
    if not args.apply and stamped:
        print("\nnothing was written; re-run with --apply")
    # A refusal is not a failure of the migration — those files get re-measured.
    # An empty run over a directory that should have had files IS one.
    if not total:
        print("FAIL: no cache files found — a migration over nothing is not one")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
