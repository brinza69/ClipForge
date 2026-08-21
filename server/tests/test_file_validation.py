"""File-serving guards must reject incomplete or non-file paths."""

from services.file_validation import is_usable_file


def test_is_usable_file_requires_a_regular_file_with_bytes(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.touch()
    assert not is_usable_file(empty)

    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"x" * 1024)
    assert not is_usable_file(tiny)

    good = tmp_path / "good.mp4"
    good.write_bytes(b"x" * 1025)
    assert is_usable_file(good)

    directory = tmp_path / "directory.mp4"
    directory.mkdir()
    assert not is_usable_file(directory)


def test_is_usable_file_handles_invalid_paths(tmp_path):
    assert not is_usable_file(tmp_path / "missing.mp4")
    assert not is_usable_file(None)  # type: ignore[arg-type]
