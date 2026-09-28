"""SC3 test fixtures: a project whose mask store holds a validated synthetic mask for one clip.

The source, the mask and the params record are SC batch 2's (`test_clipper_source_treatment_render`): a
lossless synthetic 9:16 source and a mask that `load_mask` accepts for it. Here they are written into the
PROJECT's store (`source_treatment_store.store_dir`), the way the offline import leaves them.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from services.clipper import source_treatment as st
from services.clipper import source_treatment_store as store
from services.clipper.proxy_provenance import media_identity
from test_clipper_source_treatment import _png
from test_clipper_source_treatment_render import H, START, W, mask_doc, params_record


def write_store(pid: str, cid: str, src: Path, *, doc: dict | None = None) -> str:
    """Mask + glyphs (in the mask's own directory) + params into the project's store. Returns the sha256."""
    root = store.store_dir(pid)
    doc = doc or mask_doc(media_identity(src), clip_id=cid)
    data = st.canonical_bytes(doc)
    sha = hashlib.sha256(data).hexdigest()
    own = store.mask_dir(pid, sha)
    (own / "g").mkdir(parents=True, exist_ok=True)
    for line in doc["lines"]:
        (own / "g" / f"{line['id']}.png").write_bytes(_png(line["id"]))
    (own / f"{sha}.json").write_bytes(data)
    if not (root / "params").is_dir():
        params_record(root)
    return sha


async def make_clip(src: Path, *, with_mask: bool = True, burned=True, status: str = "approved",
                    start: float = START, end: float = 3.0, **clip_kwargs) -> tuple[str, str, str | None]:
    """(project id, clip id, mask sha or None): rows as the API leaves them, and the store."""
    from database import async_session
    from models import ClipModel, ProjectModel

    pid = "sc3-" + uuid.uuid4().hex[:8]
    cid = pid + "-c"
    sha = write_store(pid, cid, src) if with_mask else None
    async with async_session() as s:
        s.add(ProjectModel(id=pid, title="sc3", source_kind="file", video_path=str(src), status="ready",
                           processing_mode="clipping", duration=3.0, width=W, height=H, fps=30,
                           clipper_settings={"fps": 30}))
        s.add(ClipModel(id=cid, project_id=pid, title="c", start_time=start, end_time=end,
                        duration=end - start, transcript_text="", status=status,
                        source_has_burned_captions=burned, **clip_kwargs))
        await s.commit()
    return pid, cid, sha


async def row(cid: str):
    from database import async_session
    from models import ClipModel

    async with async_session() as s:
        return await s.get(ClipModel, cid)


async def events(cid: str) -> list:
    from sqlalchemy import select

    from database import async_session
    from models import ClipFeedbackModel

    async with async_session() as s:
        return list((await s.execute(select(ClipFeedbackModel).where(
            ClipFeedbackModel.clip_id == cid))).scalars())
