"""Batch R7: what a publish decision rests on, across the corpus.

    python scripts/audit_publish_preflight.py
    python scripts/audit_publish_preflight.py --with-source-captions
    python scripts/audit_publish_preflight.py --with-chrome --json

TWO SIGNALS ARE OPT-IN BECAUSE THEY COST OCR. The source-caption verdict is a
property of the PROJECT — about five seconds per source — and the chrome verdict
is per EXPORT at several seconds per sampled frame. Neither is computed unless
asked for, and a run without them says `unavailable` for the checks that need
them rather than passing them.

WHAT THIS REPORTS AND WHAT IT REFUSES TO. The verdict distribution and the
per-check table, with `unavailable` in its own column beside `pass` — never
folded into it. A check nobody could make is the reason `UNDECIDED` exists, and
a report that hid it would put the old defect back one layer up.

THE RUN FAILS on an unreadable sidecar and on an empty corpus. It does NOT fail
on `UNDECIDED`: five of the seven checks have no input for most clips, that is
the finding rather than a regression, and a permanently red gate would bury it.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from services.clipper import publish_preflight as pf  # noqa: E402
from services.clipper.publish_corpus import preflight_for  # noqa: E402


def _source_captions(project: str, reader) -> dict | None:
    """The project's own burned-caption verdict, once for all its clips."""
    from services.clipper import source_captions as sc

    proxy = DATA / project / "proxy" / "proxy.mp4"
    if not proxy.exists():
        return None
    try:
        return sc.detect(str(proxy), reader=reader)
    except Exception:
        # A detector that throws is not a source without captions, and this is
        # the one state that would switch ClipForge's own layer off.
        return None


def _chrome(path: Path, reader) -> dict | None:
    """The export's chrome verdict, cached beside it as `<clip>.chrome.json`.

    CACHED BECAUSE IT COSTS MINUTES PER EXPORT, so a run over the corpus is
    hours and an interruption without a cache throws all of them away — which
    has happened twice in this batch already. The cache is a real artefact next
    to the render, not a scratch file: anything else that wants the verdict can
    read it instead of paying for it again.

    A cached verdict is trusted only if it was measured at the cadence the
    threshold belongs to. `EVERY_S` is stamped into every verdict for exactly
    this reason.
    """
    from services.clipper import source_chrome as sc

    mp4 = path.with_suffix(".mp4")
    if not mp4.exists():
        return None
    cache = path.with_suffix(".chrome.json")
    if cache.exists():
        try:
            got = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            got = None
        if (isinstance(got, dict) and got.get("state") in sc.STATES
                and got.get("every_s") == sc.EVERY_S):
            return got
    try:
        got = sc.detect(str(mp4), reader=reader)
    except Exception:
        return None
    try:
        cache.write_text(json.dumps(got), encoding="utf-8")
    except Exception:
        pass
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-source-captions", action="store_true")
    ap.add_argument("--with-chrome", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = sorted(DATA.glob("*/exports/*.json"))
    reader = None
    if args.with_source_captions or args.with_chrome:
        from services.clipper import source_chrome
        reader = source_chrome._reader()

    per_project: dict[str, dict | None] = {}
    rows: list[dict] = []
    for path in paths:
        project = path.parent.parent.name
        captions = None
        if args.with_source_captions:
            if project not in per_project:
                per_project[project] = _source_captions(project, reader)
                if not args.json:
                    got = per_project[project]
                    print(f"{project}: source captions "
                          f"{(got or {}).get('state', 'unreadable')}", flush=True)
            captions = per_project[project]
        chrome = _chrome(path, reader) if args.with_chrome else None
        row = preflight_for(path, source_captions=captions, chrome=chrome)
        rows.append(row)
        if args.json:
            print(json.dumps({k: v for k, v in row.items() if k != "checks"}),
                  file=sys.stderr, flush=True)

    verdicts = collections.Counter(r["verdict"] for r in rows)
    per_check: dict[str, collections.Counter] = {
        name: collections.Counter(r["checks"][name]["state"] for r in rows)
        for name in pf.CHECKS}
    missing = collections.Counter(m.split("_refused")[0]
                                  for r in rows for m in r["missing_inputs"])

    out = {
        "clips": len(rows),
        "verdicts": dict(verdicts),
        "per_check": {n: dict(c) for n, c in per_check.items()},
        "missing_inputs": dict(missing),
        "with_source_captions": args.with_source_captions,
        "with_chrome": args.with_chrome,
    }

    bad: list[str] = []
    if not rows:
        bad.append("no sidecars found — a pass over nothing is not a pass")
    for row in rows:
        if any(m.startswith("sidecar_") for m in row["missing_inputs"]):
            bad.append(f"{row['project']}/{row['clip']}: sidecar unreadable")
    out["failures"] = bad

    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print()
        print(f"clips {len(rows)}")
        for name in pf.VERDICTS:
            print(f"  {name:10} {verdicts.get(name, 0)}")
        print()
        for name in pf.CHECKS:
            counts = per_check[name]
            print(f"  {name:52} "
                  + "  ".join(f"{s}={counts.get(s, 0):3}" for s in pf.STATES))
        print()
        print("inputs that were not there:")
        for name, count in missing.most_common():
            print(f"  {count:4}  {name}")
        print()
        for line in bad:
            print(f"FAIL: {line}")
        if not bad:
            # NOT "every clip is fine". `UNDECIDED` is the common verdict and it
            # is the finding: most of the seven have no input.
            print("every clip was read; UNDECIDED is a verdict, not a pass")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
