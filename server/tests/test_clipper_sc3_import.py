"""SC3: the offline mask import, through its `main()` — the exit code belongs to the run.

An approved mask for a project's clip lands in the project's store and makes blur available; a mask whose
bytes do not hash to its name, a params record that no longer matches its `.sha256`, or a second params
record refuses with exit 1 and copies nothing. `--dry-run` checks everything and writes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sc3_fixtures import make_clip, row
from services.clipper import source_treatment_store as store
from services.clipper.proxy_provenance import media_identity
from test_clipper_source_treatment import write_mask
from test_clipper_source_treatment_render import encode_source, mask_doc, params_record

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_source_caption_mask as tool  # noqa: E402


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return encode_source(tmp_path_factory.mktemp("sc3-import") / "plain.mp4")


async def _approved(src, tmp_path):
    pid, cid, _ = await make_clip(src, with_mask=False)
    mask = write_mask(tmp_path, mask_doc(media_identity(src), clip_id=cid))
    return pid, cid, mask, params_record(tmp_path)


def _args(pid, mask, params, *extra):
    return ["--project", pid, "--mask", str(mask), "--params", str(params), *extra]


def _stored(pid) -> list[str]:
    root = store.store_dir(pid)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


async def test_an_approved_mask_is_imported_and_makes_blur_available(src, tmp_path):
    pid, cid, mask, params = await _approved(src, tmp_path)
    assert tool.main(_args(pid, mask, params)) == 0
    files = _stored(pid)
    own = mask.stem
    assert f"{own}/{mask.name}" in [f.replace("\\", "/") for f in files]
    assert store.IDENTITY_FILE in files
    assert any(f.startswith("params") and f.endswith(".json") for f in files)
    assert any(f.startswith(own) and f.endswith(".png") for f in files)
    from database import async_session
    from models import ProjectModel

    async with async_session() as s:
        project = await s.get(ProjectModel, pid)
    got = store.availability(await row(cid), project, str(src))["blur"]
    assert got["available"] is True and got["mask_sha256"] == mask.stem


async def test_a_dry_run_writes_nothing(src, tmp_path):
    pid, _cid, mask, params = await _approved(src, tmp_path)
    assert tool.main(_args(pid, mask, params, "--dry-run")) == 0
    assert _stored(pid) == []


async def test_a_mask_whose_bytes_do_not_hash_to_its_name_is_refused(src, tmp_path):
    pid, _cid, mask, params = await _approved(src, tmp_path)
    renamed = mask.with_name("0" * 64 + ".json")
    mask.rename(renamed)
    assert tool.main(_args(pid, renamed, params)) == 1
    assert _stored(pid) == []


async def test_a_params_record_that_no_longer_matches_is_refused(src, tmp_path):
    pid, _cid, mask, params = await _approved(src, tmp_path)
    params.write_bytes(params.read_bytes().replace(b"64", b"65"))
    assert tool.main(_args(pid, mask, params)) == 1
    assert _stored(pid) == []


async def test_a_second_params_record_in_the_store_is_refused(src, tmp_path):
    pid, _cid, mask, params = await _approved(src, tmp_path)
    other = params_record(tmp_path / "other", version="sc-synth-v2")
    (store.store_dir(pid) / "params").mkdir(parents=True)
    (store.store_dir(pid) / "params" / other.name).write_bytes(other.read_bytes())
    assert tool.main(_args(pid, mask, params)) == 1
    assert not any(mask.stem in f for f in _stored(pid))


async def _project(pid):
    from database import async_session
    from models import ProjectModel

    async with async_session() as s:
        return await s.get(ProjectModel, pid)


async def test_a_revised_mask_with_the_same_glyph_path_coexists_with_the_first(src, tmp_path):
    """next-34 R2: both imports succeed and BOTH masks stay valid — a glyph of one never replaces the other's."""
    import hashlib
    import json

    from services.clipper import source_treatment as st
    pid, cid, first, params = await _approved(src, tmp_path)
    assert tool.main(_args(pid, first, params)) == 0
    revised = json.loads(first.read_bytes())
    line = revised["lines"][0]
    second_dir = tmp_path / "second"
    (second_dir / "g").mkdir(parents=True)
    for other in revised["lines"]:
        (second_dir / other["glyph_png"]["file"]).write_bytes((first.parent / other["glyph_png"]["file"]).read_bytes())
    glyph = second_dir / line["glyph_png"]["file"]
    glyph.write_bytes(glyph.read_bytes() + b" revised")
    line["glyph_png"]["sha256"] = hashlib.sha256(glyph.read_bytes()).hexdigest()
    raw = st.canonical_bytes(revised)
    second = second_dir / f"{hashlib.sha256(raw).hexdigest()}.json"
    second.write_bytes(raw)
    assert tool.main(_args(pid, second, params)) == 0
    clip, project = await row(cid), await _project(pid)
    for sha in (first.stem, second.stem):
        store.validate(clip, project, str(src), sha)
    assert {m["mask_sha256"] for m in store.availability(clip, project, str(src))["blur"]["masks"]
            if m["ok"]} == {first.stem, second.stem}


async def test_the_same_mask_imported_again_is_left_as_it_is(src, tmp_path):
    pid, _cid, mask, params = await _approved(src, tmp_path)
    assert tool.main(_args(pid, mask, params)) == 0
    before = {f: (store.store_dir(pid) / f).read_bytes() for f in _stored(pid) if f != store.IDENTITY_FILE}
    assert tool.main(_args(pid, mask, params)) == 0
    assert {f: (store.store_dir(pid) / f).read_bytes() for f in _stored(pid) if f != store.IDENTITY_FILE} == before
