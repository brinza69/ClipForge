"""Parallel results must never advertise or serve incomplete outputs."""

from routers.parallel import _all_variant_views, _is_usable_output, _variant_view


def test_parallel_output_requires_regular_file_with_usable_bytes(tmp_path):
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"x" * 1024)
    assert not _is_usable_output(tiny)

    good = tmp_path / "good.mp4"
    good.write_bytes(b"x" * 1025)
    assert _is_usable_output(good)

    output_dir = tmp_path / "directory.mp4"
    output_dir.mkdir()
    assert not _is_usable_output(output_dir)


def test_variant_view_marks_tiny_outputs_and_parts_unavailable(tmp_path):
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"x" * 100)

    view = _variant_view({
        "index": 0,
        "final_path": str(tiny),
        "parts": [{"part": 1, "of": 1, "path": str(tiny)}],
    })

    assert view["file_available"] is False
    assert view["file_size"] == 0
    assert view["parts"][0]["available"] is False


def test_all_variant_views_keeps_success_and_failure_order():
    views = _all_variant_views({
        "results": [{"index": 0, "name": "first"}],
        "variant_failures": [{
            "index": 1, "name": "second", "label": "second", "error": "quota",
        }],
    })

    assert [view["index"] for view in views] == [0, 1]
    assert views[0]["status"] == "done"
    assert views[1]["status"] == "failed"
    assert views[1]["file_available"] is False
    assert views[1]["error"] == "quota"
