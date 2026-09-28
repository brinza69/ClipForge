"""The evidence behind proxy_clock.VALIDATED_CLOCKS lives in the repo
(tests/data/proxy_clock/) and says what VALIDATED_CLOCKS says it says.

A clean checkout, without data/claude-master-*, must be able to read every file
named as evidence, match its sha256, and find in it the per-rate numbers the
clock claims. No media: the probes' JSON and the scripts that produced them.
"""
from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path

import pytest

from services.clipper import proxy_clock as C

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data" / "proxy_clock"
CLOCK = C.VALIDATED_CLOCKS[0]
EVIDENCE = {e["file"].rsplit("/", 1)[-1]: e for e in CLOCK["evidence"]}
# The copies' names here -> their names in FROZEN.json["files"] (B/gates/…).
COPIES = {
    "synth_24_23.976_60_30_25_59.94.json": "m0v2/synth_24_23.976_60_30_25_59.94.json",
    "test_m0v2.py.txt": "m0v2/test_m0v2.py",
    "synth_probe.py.txt": "m0v2/synth_probe.py",
    "addrlib.py.txt": "m0v2/addrlib.py",
    "mediaio.py.txt": "m0v2/mediaio.py",
    "offlib.py.txt": "offset/offlib.py",
    "synthetic_long.py.txt": "offset/synthetic_long.py",
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _json(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", CLOCK["evidence"], ids=lambda e: e["file"].rsplit("/", 1)[-1])
def test_every_evidence_file_is_in_the_repo_with_its_sha256(entry):
    assert entry["file"].startswith("server/tests/data/proxy_clock/")
    path = REPO / entry["file"]
    assert path.is_file(), entry["file"]
    assert len(entry.get("sha256") or "") == 64, f"{entry['file']} carries no sha256"
    assert _sha(path) == entry["sha256"]


def test_sha256sums_lists_every_file_and_every_hash_matches():
    rows = [ln.split("  ", 1) for ln in (DATA / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    listed = {name: sha for sha, name in rows}
    on_disk = {p.name for p in DATA.iterdir() if p.is_file()} - {"SHA256SUMS"}
    print(f"files {len(on_disk)}, listed {len(listed)}")
    assert set(listed) == on_disk
    assert {n: _sha(DATA / n) for n in listed} == listed


def test_the_copies_are_the_frozen_files():
    files = _json("FROZEN.json")["files"]
    prefix = "data/claude-master-20260924/B/gates/"
    print(f"copies {len(COPIES)}")
    for copy, frozen in COPIES.items():
        assert files[prefix + frozen] == _sha(DATA / copy), copy


def _lib(line: str) -> tuple[str, str]:
    m = re.match(r"^(lib\w+)\s+(\d+)\.\s*(\d+)\.\s*(\d+)\s*/\s*(\d+)\.\s*(\d+)\.\s*(\d+)", line.strip())
    built, runtime = ".".join(m.group(2, 3, 4)), ".".join(m.group(5, 6, 7))
    assert built == runtime
    return m.group(1), built


def test_the_frozen_build_is_the_validated_build():
    ff = _json("FROZEN.json")["ffmpeg"]
    want = CLOCK["ffmpeg_build"]
    assert ff["version_lines"][0] == want["version_line"]
    assert dict(_lib(ln) for ln in ff["libs"]) == want["libs"]
    assert ff["configuration_sha256"] == want["configuration_sha256"]
    assert ff["ffmpeg_exe"]["sha256"] == want["exe_sha256"]


def _claims(name: str) -> dict:
    return {Fraction(r): x for r, x in EVIDENCE[name]["rates"].items()}


def test_the_frozen_probe_has_the_numbers_the_clock_claims():
    rows = {Fraction(r["fps"]): r for r in _json("synth_24_23.976_60_30_25_59.94.json")}
    claims = _claims("synth_24_23.976_60_30_25_59.94.json")
    print(f"rates claimed {len(claims)}, rates in the probe {len(rows)}")
    assert set(claims) == set(rows) == C.VALIDATED_SOURCE_RATES
    for rate, want in claims.items():
        r = rows[rate]
        assert r["state"] == "read" and r["unreadable"] == 0, rate
        assert {"slots": r["proxy_slots"], "sim_mismatches": r["sim_mismatches"],
                "addr_mismatches": r["addr_mismatches"]} == want, rate


def test_the_reproduction_has_the_numbers_the_clock_claims_on_the_validated_build():
    doc = _json("reproduce_synth_600s.json")
    rows = {Fraction(r["fps"]): r for r in doc["results"]}
    claims = _claims("reproduce_synth_600s.json")
    print(f"rates claimed {len(claims)}, rates reproduced {len(rows)}")
    assert set(claims) == set(rows) == C.VALIDATED_SOURCE_RATES
    for rate, want in claims.items():
        r = rows[rate]
        assert r["state"] == "ok" and r["clock"] == "validated" and r["seconds"] == 600, rate
        assert {"slots": r["slots"], "sim_mismatches": r["sim_mismatches"],
                "addr_mismatches": r["addr_mismatches"]} == want, rate
    assert {k: doc["ffmpeg_build"][k] for k in CLOCK["ffmpeg_build"]} == CLOCK["ffmpeg_build"]
