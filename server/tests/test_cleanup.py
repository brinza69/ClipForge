"""Safety tests for failed-job workspace cleanup."""

def test_failed_cleanup_preserves_final_output_and_removes_scratch(tmp_path, monkeypatch):
    from config import settings
    from services.cleanup import cleanup_job_workspace

    monkeypatch.setattr(type(settings), "media_dir", property(lambda _self: tmp_path))
    project = tmp_path / "project-1"
    final = project / "video_final.mp4"
    scratch = project / "work" / "partial.mp4"
    final.parent.mkdir(parents=True)
    scratch.parent.mkdir(parents=True)
    final.write_bytes(b"final" * 1000)
    scratch.write_bytes(b"scratch" * 1000)

    stats = cleanup_job_workspace("project-1")

    assert final.exists()
    assert not scratch.exists()
    assert project.exists()
    assert stats["freed_bytes"] == len(b"scratch" * 1000)


def test_metadata_output_is_preserved_even_without_the_standard_filename(
    tmp_path, monkeypatch
):
    from config import settings
    from services.cleanup import cleanup_job_workspace

    monkeypatch.setattr(type(settings), "media_dir", property(lambda _self: tmp_path))
    project = tmp_path / "project-2"
    final = project / "variant-a.mp4"
    scratch = project / "scratch.bin"
    project.mkdir()
    final.write_bytes(b"final" * 1000)
    scratch.write_bytes(b"scratch" * 1000)

    cleanup_job_workspace("project-2", preserve_paths=[str(final)])

    assert final.exists()
    assert not scratch.exists()


def test_explicit_delete_removes_final_output_too(tmp_path, monkeypatch):
    from config import settings
    from services.cleanup import cleanup_job_workspace

    monkeypatch.setattr(type(settings), "media_dir", property(lambda _self: tmp_path))
    project = tmp_path / "project-3"
    (project / "video_final.mp4").parent.mkdir(parents=True)
    (project / "video_final.mp4").write_bytes(b"final" * 1000)

    stats = cleanup_job_workspace("project-3", remove_outputs=True)

    assert not project.exists()
    assert stats["freed_bytes"] > 0
