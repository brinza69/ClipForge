"""Batch R6: what every caption palette guarantees, and which two cannot.

§R6 asks whether the caption "passes the contrast" on the blurred letterbox
band. The corpus says that is not a corner case — 27 of the 27 clips with a
`fit` shot and known geometry have the caption on that band — and the answer
turns out not to need a single frame: an outlined glyph separates from ANY
backdrop by one of its two colours, so there is a floor.

    python scripts/audit_caption_contrast.py
    python scripts/audit_caption_contrast.py --json

TWO POPULATIONS, and they are not the same question.

The seven SHIPPING PRESETS are what any future clip can be given. The STYLES ON
DISK are what was actually burned into the 101 exports. A preset nobody has used
can still be wrong, and a style in use can be one no preset defines any more —
so both are counted, with their own denominators.

WHAT FAILS THE RUN:

    a style that cannot be read        an unreadable colour is not a black one
    a fill below the large-text bar    the body text is unreadable by the only
                                       published standard available
    a highlight below it               the word the animation paints is the one
                                       the eye goes to
    an empty population                a pass over nothing is not a pass

The last one matters here more than usual: this script's whole method is a sweep
over colour pairs, and a sweep over an empty list is silent and green.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from services.captioner_presets import DEFAULT_PRESETS  # noqa: E402
from services.clipper import caption_contrast as cc  # noqa: E402


def _styles_on_disk() -> tuple[list[tuple[str, Any]], list[str]]:
    """`(styles found, refusals)` across the export sidecars.

    A sidecar that cannot be read gets a refusal rather than vanishing. The
    style is sometimes a repr rather than JSON, which `caption_plan` has stored
    since the clipper shipped; a repr that will not parse is also a refusal and
    not an absent style.
    """
    found: list[tuple[str, Any]] = []
    refused: list[str] = []
    for path in sorted(DATA.glob("*/exports/*.json")):
        name = f"{path.parent.parent.name}/{path.stem}"
        try:
            side = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            refused.append(f"{name}: sidecar_unreadable {type(exc).__name__}")
            continue
        style = ((side.get("caption_plan") or {}).get("style")
                 if isinstance(side, dict) else None)
        if isinstance(style, str):
            try:
                style = ast.literal_eval(style)
            except Exception:
                refused.append(f"{name}: style_repr_unparsable")
                continue
        found.append((name, style))
    return found, refused


def _rows(population: list[tuple[str, Any]]) -> list[dict]:
    out = []
    for name, style in population:
        told = cc.verdict(style)
        out.append({"name": name, **told})
    return out


def _print(title: str, rows: list[dict]) -> None:
    print(f"{title}: {len(rows)}")
    if not rows:
        return
    print(f"  {'':40} {'fill':>6} {'highlight':>10}")
    for row in rows:
        if row["refused"]:
            print(f"  {row['name']:40} REFUSED {','.join(row['refused'])}")
            continue
        fill = row["fill"]
        high = row["highlight"]
        mark = "" if fill["clears_large_text"] else "  <- fill below 3.0"
        hmark = ("" if high is None or high["clears_large_text"]
                 else "  <- highlight below 3.0")
        print(f"  {row['name']:40} {fill['floor']:6.2f} "
              f"{(high['floor'] if high else 0.0):10.2f}{mark}{hmark}")


def _failures(presets: list[dict], disk: list[dict],
              disk_refused: list[str]) -> list[str]:
    bad: list[str] = []
    if not presets:
        bad.append("no presets found — a sweep over nothing is silent and green")
    if not disk and not disk_refused:
        bad.append("no styles on disk — a sweep over nothing is silent and green")
    for line in disk_refused:
        bad.append(f"sidecar refused: {line}")
    for row in presets + disk:
        if row["refused"]:
            bad.append(f"{row['name']}: refused ({', '.join(row['refused'])})")
            continue
        if not row["fill"]["clears_large_text"]:
            bad.append(f"{row['name']}: fill floor {row['fill']['floor']} is "
                       f"below {cc.LARGE_TEXT_MIN}")
        high = row["highlight"]
        if high is not None and not high["clears_large_text"]:
            bad.append(f"{row['name']}: highlight floor {high['floor']} is "
                       f"below {cc.LARGE_TEXT_MIN}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    presets = _rows([(str(p.get("name") or key), p)
                     for key, p in sorted(DEFAULT_PRESETS.items())])
    on_disk, refused = _styles_on_disk()
    # ONE ROW PER DISTINCT PALETTE, not one per export. 99 identical rows would
    # bury the two that differ, and the question is about palettes.
    seen: dict[tuple, str] = {}
    unique: list[tuple[str, Any]] = []
    for name, style in on_disk:
        key = (str((style or {}).get("text_color")),
               str((style or {}).get("highlight_color")),
               str((style or {}).get("outline_color")))
        if key in seen:
            continue
        seen[key] = name
        unique.append((f"{name} (x{sum(1 for n, s in on_disk if (str((s or {}).get('text_color')), str((s or {}).get('highlight_color')), str((s or {}).get('outline_color'))) == key)})",
                       style))
    disk = _rows(unique)

    bad = _failures(presets, disk, refused)
    out = {
        "presets": presets,
        "sidecars_read": len(on_disk),
        "sidecars_refused": refused,
        "distinct_palettes_on_disk": disk,
        "thresholds": {"large_text": cc.LARGE_TEXT_MIN,
                       "normal_text": cc.NORMAL_TEXT_MIN,
                       "calibrated": False},
        "failures": bad,
    }
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        _print("shipping presets", presets)
        print()
        print(f"sidecars read: {len(on_disk)}")
        print(f"  refused:     {len(refused)}")
        for line in refused:
            print(f"    {line}")
        print()
        _print("distinct palettes on disk", disk)
        print()
        print(f"thresholds are WCAG 2.1 AA, borrowed and not calibrated: "
              f"{cc.LARGE_TEXT_MIN} large / {cc.NORMAL_TEXT_MIN} normal")
        print()
        for line in bad:
            print(f"FAIL: {line}")
        if not bad:
            print("every palette clears the large-text bar on both colours")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
