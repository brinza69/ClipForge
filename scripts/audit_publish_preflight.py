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

TWO CONTRACTS, KEPT APART. This is the DESCRIPTIVE audit, and it may finish
green over a corpus that is almost entirely `UNDECIDED`: most of the seven have
no input for most clips, which is the finding rather than a regression, and a
permanently red gate would bury it. The PUBLISH GATE is the other contract —
`APPROVE`, no refusals, evidence that is current — and nothing is wired to it.

WHAT THE DESCRIPTIVE AUDIT STILL OWES IS INTEGRITY, and that is what the exit
code is for: an unreadable sidecar, a record that raised, an empty corpus, or
any refusal the preflight would not read an approval out of. A run that could
not measure what it claims to measure is not a run whose numbers may be quoted.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path
from typing import Any

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


#: Where the OCR cache lives. NOT `exports/<clip>.chrome.json`, which is where
#: it was: six places in this repo enumerate `exports/*.json` as the sidecar of
#: a clip, and a cache file landing in that namespace is read as one. It is not
#: hypothetical — the single file the interrupted run left behind already made
#: `audit_clipper_exports` report `orphan_sidecar` and exit 2, so one cached
#: verdict took R0's baseline gate down with it. A cache that changes the answer
#: of the gate it was collected for is worse than no cache.
_CACHE_DIR = "chrome_cache"

#: The markers that separate CORRUPT from MERELY ABSENT in `missing_inputs`, and
#: the separation is the whole of the exit contract. An export with no caption
#: plan refuses `no_caption_style` and that is an honest absence — a corpus of
#: them is a finding, not a broken run. A `caption_plan` that is the list `[1]`
#: is somebody having supplied a record that is not one, and every figure the
#: run prints is then a fraction of a smaller corpus than the header claims.
#:
#: Substrings rather than a list of names, because the refusals are produced by
#: four modules with their own vocabularies; each of those lists is closed where
#: it lives, and what they share is this shape.
_CORRUPT = ("not_a_record", "not_a_list", "not_a_sequence", "not_a_fraction",
            "unreadable", "assemble_raised_")


def _cache_path(path: Path) -> Path:
    return path.parent.parent / _CACHE_DIR / f"{path.stem}.json"


def _identity(mp4: Path) -> dict:
    """What ties a cached verdict to the file it was measured on.

    Size and mtime rather than a content hash: a re-render always rewrites both,
    which is the case this exists for — the OCR cost minutes and the cache
    would otherwise outlive the file it describes. It is not a claim that two
    files with the same size and mtime are the same file.
    """
    stat = mp4.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _usable(got: Any, mp4: Path) -> bool:
    """Whether a cached verdict still describes THIS file, at THIS detector.

    It used to be accepted on `state` and cadence alone, so it survived a
    re-render of the very export it was about, and it survived a change to the
    thresholds that produced it. Both halves of the answer — the file and the
    configuration — have to still be the ones that were measured.
    """
    from services.clipper import source_chrome as sc

    if not isinstance(got, dict) or got.get("state") not in sc.STATES:
        return False
    if got.get("schema") != "source_chrome_v1":
        return False
    if (got.get("every_s") != sc.EVERY_S or got.get("frames_min") != sc.FRAMES_MIN
            or got.get("conf_min") != sc.CONF_MIN):
        return False
    try:
        return got.get("measured_on") == _identity(mp4)
    except OSError:
        return False


def _chrome(path: Path, reader) -> dict | None:
    """The export's chrome verdict, cached under `<project>/chrome_cache/`.

    CACHED BECAUSE IT COSTS MINUTES PER EXPORT, so a run over the corpus is
    hours and an interruption without a cache throws all of them away — which
    has happened twice in this batch already. It is a real artefact rather than
    a scratch file: anything else that wants the verdict can read it instead of
    paying for it again. What it must not be is a file in a directory whose
    `*.json` means something else.
    """
    from services.clipper import source_chrome as sc

    mp4 = path.with_suffix(".mp4")
    if not mp4.exists():
        return None
    cache = _cache_path(path)
    if cache.exists():
        try:
            got = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            got = None
        if _usable(got, mp4):
            return got
    try:
        got = sc.detect(str(mp4), reader=reader)
    except Exception:
        return None
    try:
        got["measured_on"] = _identity(mp4)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(got), encoding="utf-8")
    except Exception:
        pass
    return got


def _words(project: str) -> list | None:
    """The project's whole transcript, from the database.

    THE WHOLE ONE, and that is the point of loading it at all. The candidate on
    disk carries only the words wholly INSIDE its window, and a word straddling
    an edge is dropped from that list — which is precisely the word
    `start_inside_word` and `end_inside_word` exist to find. Measuring the
    boundary against the candidate's own words asked the check to find what had
    already been removed.
    """
    import asyncio

    from database import async_session
    from models import TranscriptModel
    from sqlalchemy import select

    from services.clipper.candidate_terms import _words_for

    async def _load():
        async with async_session() as session:
            return (await session.execute(
                select(TranscriptModel)
                .where(TranscriptModel.project_id == project).limit(1)
            )).scalar_one_or_none()

    try:
        row = asyncio.run(_load())
    except Exception:
        return None
    if not row or not row.segments:
        return None
    return _words_for({}, {"language": row.language,
                           "segments": row.segments}) or None


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
    transcripts: dict[str, list | None] = {}
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
        if project not in transcripts:
            transcripts[project] = _words(project)
        chrome = _chrome(path, reader) if args.with_chrome else None
        row = preflight_for(path, source_captions=captions, chrome=chrome,
                            words=transcripts[project])
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

    # TWO CONTRACTS, AND THEY ARE NOT THE SAME ONE. This is the DESCRIPTIVE
    # audit: it may finish green over a corpus that is almost all `UNDECIDED`,
    # because five of the seven checks have no input for most clips and that is
    # the finding rather than a regression. A permanently red gate would bury
    # it. The PUBLISH GATE is the other contract — only `APPROVE`, with no
    # refusals — and nothing is wired to it, deliberately.
    #
    # What the descriptive audit does owe is INTEGRITY: every clip read, every
    # refusal surfaced, no answer resting on a record nobody could parse. Those
    # exit non-zero, because a run that could not measure what it claims to
    # measure is not a run whose numbers may be quoted.
    bad: list[str] = []
    if not rows:
        bad.append("no sidecars found — a pass over nothing is not a pass")
    for row in rows:
        where = f"{row['project']}/{row['clip']}"
        for miss in row["missing_inputs"]:
            if any(mark in miss for mark in _CORRUPT):
                bad.append(f"{where}: {miss}")
        for line in row["refused"]:
            # A refusal is somebody's mistake rather than a missing input, and
            # it already stops the verdict reaching APPROVE. It stops the run
            # too: a report the preflight would not read an approval out of is
            # not a report this should exit 0 on.
            bad.append(f"{where}: refused {line}")
    out["failures"] = bad
    out["integrity"] = not bad

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
