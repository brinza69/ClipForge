"""File responses must never serve empty, truncated, or directory paths."""

from fastapi import HTTPException
import pytest

from routers import commentators, doodle


pytestmark = pytest.mark.asyncio


async def test_commentator_video_rejects_incomplete_output(monkeypatch, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"tiny")
    monkeypatch.setattr(commentators.commentators, "_video_path", lambda _id: path)

    with pytest.raises(HTTPException) as exc:
        await commentators.get_video("preset")

    assert exc.value.status_code == 404


async def test_commentator_thumb_serves_only_a_regular_file(monkeypatch, tmp_path):
    path = tmp_path / "thumb.jpg"
    path.write_bytes(b"x" * 2048)
    monkeypatch.setattr(commentators.commentators, "_thumb_path", lambda _id: path)

    response = await commentators.get_thumb("preset")

    assert str(response.path) == str(path)


async def test_doodle_prompt_export_rejects_empty_file(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    prompt_dir = project_dir / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "flow_prompts.csv").touch()

    monkeypatch.setattr(doodle, "_load_or_404", lambda _id: {"scenes": []})
    monkeypatch.setattr(doodle.storage, "project_dir", lambda _id: project_dir)
    monkeypatch.setattr(doodle.storage, "write_prompt_exports", lambda *_args: None)

    with pytest.raises(HTTPException) as exc:
        await doodle.download_prompts_csv("project")

    assert exc.value.status_code == 404


async def test_doodle_prompt_export_serves_a_nonempty_file(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    prompt_dir = project_dir / "prompts"
    prompt_dir.mkdir(parents=True)
    path = prompt_dir / "flow_prompts.json"
    path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(doodle, "_load_or_404", lambda _id: {"scenes": []})
    monkeypatch.setattr(doodle.storage, "project_dir", lambda _id: project_dir)
    monkeypatch.setattr(doodle.storage, "write_prompt_exports", lambda *_args: None)

    response = await doodle.download_prompts_json("project")

    assert str(response.path) == str(path)
