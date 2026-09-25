"""GET /api/jobs answers directly with and without the trailing slash.

The sidebar badge asks for `/api/jobs?status=…`. Before the alias FastAPI
answered 307 to an absolute backend URL, which took the browser out of the
/worker-api proxy (A1 browser baseline, data/claude-master-20260924/A/
badge-finding.md). A `done` job is used so no queue processor picks it up.
"""
from __future__ import annotations

import uuid


async def _job(status: str) -> str:
    from database import async_session
    from models import JobModel

    pid = "jobsnr-" + uuid.uuid4().hex[:5]
    async with async_session() as s:
        s.add(JobModel(project_id=pid, type="clipper_score", status=status))
        await s.commit()
    return pid


async def test_both_spellings_answer_200_without_a_redirect(client):
    pid = await _job("done")
    bare = await client.get(f"/api/jobs?project_id={pid}&status=done,failed")
    slash = await client.get(f"/api/jobs/?project_id={pid}&status=done,failed")
    for r in (bare, slash):
        assert r.status_code == 200, r.text
        assert "location" not in r.headers
    assert bare.json() == slash.json()
    assert [j["project_id"] for j in bare.json()] == [pid]


async def test_the_filter_still_applies_on_the_bare_path(client):
    pid = await _job("done")
    r = await client.get(f"/api/jobs?project_id={pid}&status=queued,running")
    assert r.status_code == 200 and r.json() == []
