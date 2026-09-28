"""Import one approved source-caption mask into its project's store (SC3, codex-verdict-next-33 §3).

    cd server && python scripts/import_source_caption_mask.py --project <id> --mask <dir>/<sha>.json \
        --params <dir>/<version>.json [--dry-run]

The OFFLINE step that makes blur available for a clip: nothing a client sends is a mask's approval. Refuses —
exit 1, nothing copied — unless every check passes, in this order:
  1. the mask's bytes are canonical and hash to its file name;
  2. the clip it names belongs to the project (the DB is opened READ-ONLY);
  3. the project's source hashes IN FULL to the mask's source (`media_identity`, no reuse);
  4. `load_mask` accepts it for the clip's CURRENT bounds, glyph PNGs included, from where it is now;
  5. the params record matches its `.sha256` and carries blur values; a different record already in the
     store is refused (the store keeps exactly one);
  6. copied into a staging directory inside the store, validated again FROM THE COPY, then moved into
     place as the mask's OWN directory (`<sha>/`, glyphs inside); a mask already installed is re-validated
     and left as it is.
`source_identity.json` records the full-hash identity, which renders reuse only while path and partial
fingerprint are unchanged (`media_identity(reuse=...)`). Denominators are printed before the verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from services.clipper import source_treatment as st  # noqa: E402
from services.clipper import source_treatment_render as treat  # noqa: E402
from services.clipper import source_treatment_store as store  # noqa: E402
from services.clipper.proxy_provenance import media_identity  # noqa: E402
from services.clipper.source_treatment_mask import load_mask  # noqa: E402


class Refused(Exception):
    pass


def _clip_row(project_id: str, clip_id: str) -> tuple[float, float, str]:
    db = sqlite3.connect(f"file:{Path(settings.db_path).as_posix()}?mode=ro", uri=True)
    try:
        got = db.execute("SELECT c.start_time, c.end_time, p.video_path FROM clips c JOIN projects p "
                         "ON p.id = c.project_id WHERE c.id = ? AND c.project_id = ?",
                         (clip_id, project_id)).fetchone()
    finally:
        db.close()
    if got is None:
        raise Refused(f"clip {clip_id} is not a clip of project {project_id}")
    return float(got[0]), float(got[1]), str(got[2] or "")


def _glyph_files(doc: dict) -> list[str]:
    return sorted({line["glyph_png"]["file"] for line in doc["lines"]})


def run(project_id: str, mask: Path, params: Path, *, dry_run: bool = False) -> dict:
    raw = mask.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    if mask.stem != sha or st.canonical_bytes(doc) != raw:
        raise Refused(f"{mask.name}: not canonical or its bytes hash to {sha}")
    clip_id = (doc.get("clip") or {}).get("clip_id")
    start, end, src = _clip_row(project_id, clip_id)
    if not src or not Path(src).is_file():
        raise Refused(f"the project's source is not on disk: {src!r}")
    identity = media_identity(src)                                   # full hash, no reuse
    try:
        load_mask(mask, source_identity=identity, clip_id=clip_id, clip_start=start, clip_end=end)
        record = treat.load_params(params)
    except st.SourceTreatmentRefused as r:
        raise Refused(f"{r.reason}: {r.detail}") from None
    if not isinstance(record["values"].get(st.BLUR), dict):
        raise Refused("the params record has no blur values")
    root = store.store_dir(project_id)
    existing = sorted((root / "params").glob("*.json"))
    if existing and (len(existing) != 1 or existing[0].read_bytes() != params.read_bytes()):
        raise Refused(f"the store already holds another params record: {[p.name for p in existing]}")
    glyphs = _glyph_files(doc)
    target = store.mask_dir(project_id, sha)
    out = {"project": project_id, "clip": clip_id, "mask_sha256": sha, "glyphs": len(glyphs),
           "params": record["version"], "source_sha256": identity["sha256"], "dry_run": dry_run,
           "already_installed": target.exists()}
    if dry_run:
        return out
    stage = root / f".import-{uuid.uuid4().hex[:8]}"
    try:
        # Each mask in ITS OWN directory (next-34 R2): a second mask naming the same glyph path can never
        # replace the first one's. The directory is content-addressed, so one already there is the same
        # mask — re-validated, never overwritten.
        if not target.exists():
            for rel in glyphs:
                (stage / sha / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(mask.parent / rel, stage / sha / rel)
            shutil.copyfile(mask, stage / sha / mask.name)
        if not existing:
            (stage / "params").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(params, stage / "params" / params.name)
            shutil.copyfile(f"{params}.sha256", stage / "params" / f"{params.name}.sha256")
        copied = (stage / sha / mask.name) if not target.exists() else (target / mask.name)
        try:                                                        # again, FROM THE COPY
            load_mask(copied, source_identity=identity, clip_id=clip_id, clip_start=start, clip_end=end)
            treat.load_params(stage / "params" / params.name if not existing else existing[0])
        except st.SourceTreatmentRefused as r:
            raise Refused(f"the copy does not validate: {r.reason}: {r.detail}") from None
        if (stage / sha).exists():
            (stage / sha).rename(target)
        if (stage / "params").exists():
            (root / "params").mkdir(parents=True, exist_ok=True)
            for f in sorted((stage / "params").iterdir()):
                f.replace(root / "params" / f.name)
        (root / store.IDENTITY_FILE).write_text(json.dumps(identity, indent=1, sort_keys=True), encoding="utf-8")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--mask", required=True, type=Path)
    ap.add_argument("--params", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(f"checks: 6 | mask {a.mask.name} | project {a.project}")
    try:
        got = run(a.project, a.mask, a.params, dry_run=a.dry_run)
    except (Refused, OSError, ValueError, KeyError) as e:
        print(f"REFUSED: {e}")
        return 1
    print("IMPORTED" if not a.dry_run else "WOULD IMPORT", json.dumps(got, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
