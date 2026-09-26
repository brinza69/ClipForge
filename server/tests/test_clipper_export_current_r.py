"""R4c: closing R4b (codex-verdict-closure.md Q2, Q3 and the extra reservation).

Q3: a render that consumed no `.ass` names no published one, and publishes
none — a file offered to the encoder and not used is not a burned layer.

Q2: after burn → a render with no caption filter, the OLD `{clip}.ass` still
sits beside the export. `render_record.caption_filter` says what the encode
configured; the sibling file must not be read as the delivered layer.

Reservation: `/export-file` serves only an `exported` clip. The three renames
in `_publish_export` are not atomic with each other; a failure between them,
starting from a clip that WAS exported, must not leave anything downloadable
as the current export — whether the job then fails, is cancelled or recovered.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.clipper import caption_corpus, publish_corpus, render_record
from test_clipper_export_atomic_r import _export
from test_clipper_export_attempt_r import _Encoder, _run, encoder  # noqa: F401
from test_clipper_export_identity_r import _job, _queue, _set_job, _submitted, _take
from test_clipper_mutation_atomicity import _state, api  # noqa: F401
from test_clipper_shared_export import _decision

# One `\pos` y for every event, as every `.ass` in the corpus has, so
# `caption_corpus` can read a delivered position out of it.
_OLD_LAYER = ("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n[Events]\n"
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
              "Dialogue: 0,0:00:00.00,0:00:01.40,Default,,0,0,0,,{\\pos(540,1500)}OLD LAYER\n")


class _Big(_Encoder):
    """The shared encoder, with an mp4 large enough for `is_usable_output`,
    and able to ignore the `.ass` it is offered (`use_ass=False`)."""

    use_ass, n = True, 0

    def __call__(self, src, plan, out, **kw):
        offered = kw.get("ass_path")
        if not self.use_ass:
            kw["ass_path"] = None
        got = super().__call__(src, plan, out, **kw)
        if not self.use_ass:
            got["render_record"] = render_record.record(
                ["ffmpeg", "-i", src, "-filter_complex", "[0:v]null[o]", out],
                ass_path=offered)
        self.n += 1                                   # each render's bytes differ
        with open(out, "ab") as f:
            f.write(b"render %d" % self.n + b"\0" * 2048)
        return {**got, "size": Path(out).stat().st_size}


@pytest.fixture
def big(encoder, monkeypatch):
    """`mode`: burn (layer offered and used), suppress (none offered), unused
    (offered, not in the argv)."""
    from services.clipper import dynamic_render
    from workers import clipper_render_jobs as jobs

    enc = _Big()
    enc.exports, enc.mode = encoder.exports, "burn"

    async def decide(clip, _project, out_dir, **_):
        enc.use_ass = enc.mode != "unused"
        if enc.mode == "suppress":
            return _decision(dynamic=True, burn=False, ass_path=None)
        ass = Path(out_dir) / f"{clip.id}.ass"
        ass.parent.mkdir(parents=True, exist_ok=True)
        ass.write_text(_OLD_LAYER if enc.mode == "burn" else "never burned", encoding="utf-8")
        return _decision(dynamic=True, burn=True, ass_path=str(ass))

    monkeypatch.setattr(jobs, "_decide_render", decide)
    monkeypatch.setattr(dynamic_render, "render_dynamic_clip", enc)
    return enc


def _sidecar(exports: Path, cid: str) -> dict:
    return json.loads((exports / f"{cid}.json").read_text(encoding="utf-8"))


async def _exported(api, tmp_path, big, mode="burn"):
    big.mode = mode
    pid, cid, job = await _submitted(api, tmp_path)
    q = _queue()
    await _take(q, job)
    await _run("A", job, pid, cid, q)
    assert (await _state(cid))[0].status == "exported"
    await _set_job(job, status="done")                 # the queue does this after the handler
    return pid, cid


async def _reexport(api, pid, cid, big, mode):
    big.mode = mode
    resp = await _export(api, cid)
    assert resp.status_code == 200, resp.text
    job = resp.json()["job_id"]
    q = _queue()
    await _take(q, job)
    return job, q


# ---------------------------------------------------------------------------
# Q3: nothing burned → no consumed `.ass` is claimed or published.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["suppress", "unused"])
async def test_a_render_that_burned_nothing_names_and_publishes_no_captions(
        api, tmp_path, big, mode):
    pid, cid = await _exported(api, tmp_path, big, "suppress")
    job, q = await _reexport(api, pid, cid, big, mode)
    (big.exports / f"{cid}.ass").write_text("an older export's captions", encoding="utf-8")
    await _run("B", job, pid, cid, q)
    rec = _sidecar(big.exports, cid)["render_record"]
    assert rec["caption_filter"] is False and rec["ass_sha256"] is None
    assert "ass_published_path" not in rec
    assert rec["offered_matches_used"] is (False if mode == "unused" else None)
    # The file beside the export is still the older one, not this render's offer.
    assert (big.exports / f"{cid}.ass").read_text(encoding="utf-8") == \
        "an older export's captions"


# ---------------------------------------------------------------------------
# Q2: burn, then a render with no filter, with the burned `.ass` left beside it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("then", ["suppress", "unused"])
async def test_a_stale_ass_beside_a_render_with_no_filter_is_not_its_layer(
        api, tmp_path, big, then):
    pid, cid = await _exported(api, tmp_path, big, "burn")
    stale = (big.exports / f"{cid}.ass").read_bytes()
    assert _sidecar(big.exports, cid)["render_record"]["caption_filter"] is True
    job, q = await _reexport(api, pid, cid, big, then)
    await _run("B", job, pid, cid, q)
    assert (big.exports / f"{cid}.ass").read_bytes() == stale      # still there
    side_path = big.exports / f"{cid}.json"
    assert _sidecar(big.exports, cid)["render_record"]["caption_filter"] is False

    row = caption_corpus.measure(side_path)
    assert row.get("refused") is None
    assert row["caption_y_source"] != "ass", row
    assert row["y_pct"] != pytest.approx(1500 / 1920)
    assert row["caption_y_why_not_ass"] == "the_encode_had_no_caption_filter"

    own = publish_corpus.assemble(side_path)["own_caption_layer"]
    # suppress: the policy's declaration; unused: not established — never True.
    assert own is (False if then == "suppress" else None)


# ---------------------------------------------------------------------------
# The extra reservation: /export-file serves only an `exported` clip.
# ---------------------------------------------------------------------------

def _get(api, cid):
    return api.get(f"/api/clipper/clips/{cid}/export-file")


def _refused(resp) -> bool:
    return resp.status_code == 409 and resp.json()["detail"]["error"] == "export_not_current"


async def test_the_current_export_downloads_and_a_running_reexport_does_not(
        api, tmp_path, big):
    pid, cid = await _exported(api, tmp_path, big, "burn")
    first = await _get(api, cid)
    assert first.status_code == 200
    assert first.content == (big.exports / f"{cid}.mp4").read_bytes()
    job, q = await _reexport(api, pid, cid, big, "burn")
    assert _refused(await _get(api, cid))                  # exporting
    await _run("B", job, pid, cid, q)
    done = await _get(api, cid)
    assert done.status_code == 200 and done.content != first.content


class _FailingOs:
    """`os` for `clipper_render_jobs`: the rename INTO `target` raises."""

    def __init__(self, target: Path):
        self.target = target

    def replace(self, src, dst):
        import os

        if Path(dst) == self.target:
            raise PermissionError(13, "held open by another process", str(dst))
        os.replace(src, dst)


@pytest.mark.parametrize("then", ["fail", "cancel", "recover"])
@pytest.mark.parametrize("where", [".json", ".ass"])
async def test_a_publish_that_breaks_between_renames_is_never_the_current_export(
        api, tmp_path, big, monkeypatch, where, then):
    from workers import clipper_render_jobs as jobs

    pid, cid = await _exported(api, tmp_path, big, "burn")
    exports = big.exports
    old = {s: (exports / f"{cid}{s}").read_bytes() for s in (".mp4", ".json", ".ass")}
    job, q = await _reexport(api, pid, cid, big, "burn")
    monkeypatch.setattr(jobs, "os", _FailingOs(exports / f"{cid}{where}"))
    with pytest.raises(PermissionError):
        await _run("B", job, pid, cid, q)
    # The incomplete set this test is about is really on disk: a new mp4 with
    # the old sidecar, or a new mp4 + sidecar with the old `.ass`.
    now = {s: (exports / f"{cid}{s}").read_bytes() for s in (".mp4", ".json", ".ass")}
    assert now[".mp4"] != old[".mp4"]
    assert (now[".json"] == old[".json"]) is (where == ".json")
    assert now[".ass"] == old[".ass"]
    assert _refused(await _get(api, cid))                  # the job has not ended yet

    if then == "fail":
        await q.fail_job(job, "publish failed", owner_id=q.worker_id)
    elif then == "cancel":
        await q.cancel_job(job, owner_id=q.worker_id)
    else:
        await _set_job(job, lease_expires_at=datetime.utcnow() - timedelta(seconds=1))
        await _queue().recover_stuck_jobs()
    row, _ = await _state(cid)
    assert row.status in ("failed", "exporting"), (then, row.status, (await _job(job)).status)
    assert row.export_path == str(exports / f"{cid}.mp4")  # the path is still on the row
    assert _refused(await _get(api, cid))
