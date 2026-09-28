"""Readiness must fail closed when a core dependency is unavailable."""

import pytest


pytestmark = pytest.mark.asyncio


async def test_readiness_is_503_when_the_queue_is_not_serving(client, monkeypatch):
    from job_queue import job_queue

    monkeypatch.setattr(job_queue, "_ready", False)

    response = await client.get("/api/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] is True
    assert body["checks"]["directories"] is True
    assert body["checks"]["job_queue"] is False


async def test_readiness_is_200_when_all_dependencies_are_ready(client, monkeypatch):
    from job_queue import job_queue

    monkeypatch.setattr(job_queue, "_ready", True)

    response = await client.get("/api/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
