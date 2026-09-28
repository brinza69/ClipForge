"""D2r-2: the last corrections before D2 closes (closure-3 verdict).

K1' an ASR point timestamp (`end == start`) is timing, not a gap: admitted by
    `_window_timing`, and never counted where the builder then drops the word;
K7  a preview renders into its OWN attempt files and publishes only while the
    inputs it rendered from are still the row's and no newer export published —
    a rejected preview changes neither the file, `preview_path` nor `warnings`;
K8  `warnings` that is not a list of str reads as `[]` (and is logged) through
    `clip_to_dict`, and the writers leave a mixed list alone.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import asyncio
import hashlib
import logging
from pathlib import Path

import pytest
from sqlalchemy import update

from database import async_session
from job_attempt import CLAIMED_ATTEMPT, ClaimedAttempt
from models import ClipModel, JobModel, ProjectModel, TranscriptModel
from services.clipper import storage
from services.clipper.serialize import clip_to_dict
from workers import clipper_captions as captions
from workers import clipper_render_jobs as jobs
from workers.clipper_preview_publish import attempt_path
from workers import clipper_render_plan as planning

UNAVAILABLE = {"origin": None, "outcome": "unavailable", "reason": "no_timed_words"}


_LAYOUT_ONLY = ["Layout: kept from analysis"]


async def _seed(ident, *, segments=None, caption_plan=None, warnings=_LAYOUT_ONLY,
                start=0.0, end=3.0):
    project = ProjectModel(id=ident, title="d2r2", source_kind="file", video_path="",
                           width=320, height=180, fps=24,
                           clipper_settings={"dynamic_edit": False, "trim_silence": False,
                                             "fps": 24})
    clip = ClipModel(id=ident, project_id=ident, title="alternative", start_time=start,
                     end_time=end, duration=end - start, transcript_text="said words",
                     is_alternative=True, caption_plan=caption_plan, layout_plan=None,
                     warnings=warnings,
                     status="candidate")
    async with async_session() as session:
        session.add(project)
        session.add(clip)
        if segments is not None:
            session.add(TranscriptModel(project_id=ident, language="en", full_text="x",
                                        segments=segments))
        await session.commit()
    return await planning._load(ident)


async def _row(ident):
    async with async_session() as session:
        return await session.get(ClipModel, ident)


# ── K1': a point timestamp is timing ──────────────────────────────────────────

def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


# The window is [10, 13]. Each case: the words of one segment [9, 14], the
# reason `_plan_for_render` gives, and the tokens the built plan burns — which
# is the concordance: a word the rule counted must be in the plan, and a word
# the builder drops at the boundary must not be what admitted the window.
K1_CASES = {
    "isolated_point": ([_w("a", 10.2, 10.5), _w("b", 11.0, 11.0), _w("c", 11.5, 11.9)],
                       None, ["a", "b", "c"]),
    "burst_of_points": ([_w("a", 10.1, 10.3)] + [_w(t, 11.0, 11.0) for t in "bcdef"]
                        + [_w("g", 12.0, 12.5)], None, list("abcdefg")),
    "every_word_a_point": ([_w("a", 10.5, 10.5), _w("b", 11.0, 11.0), _w("c", 12.0, 12.0)],
                           None, ["a", "b", "c"]),
    "point_at_clip_start_beside_a_word": ([_w("edge", 10.0, 10.0), _w("in", 11.0, 11.4)],
                                          None, ["in"]),
    "point_at_clip_end_beside_a_word": ([_w("in", 11.0, 11.4), _w("edge", 13.0, 13.0)],
                                        None, ["in"]),
    "only_a_point_at_clip_start": ([_w("edge", 10.0, 10.0)], "no_timed_words", None),
    "only_a_point_at_clip_end": ([_w("edge", 13.0, 13.0)], "no_timed_words", None),
    "straddling_both_edges": ([_w("first", 9.5, 10.4), _w("last", 12.8, 13.5)],
                              None, ["first", "last"]),
    "point_beside_an_untimed_segment": (None, "incomplete_timing", None),
    "end_before_start_still_refused": ([_w("a", 10.2, 10.5), _w("b", 11.0, 10.9)],
                                       "incomplete_timing", None),
    "missing_end_still_refused": ([_w("a", 10.2, 10.5), {"word": "b", "start": 11.0}],
                                  "incomplete_timing", None),
}


@pytest.mark.parametrize("case", list(K1_CASES))
async def test_k1_point_timestamps_are_admitted_and_agree_with_the_builder(monkeypatch, case):
    words, reason, tokens = K1_CASES[case]
    if words is None:     # Codex's mixed counterexample, with a point word: still refused
        segments = [{"start": 9.0, "end": 10.6, "text": "p", "words": [_w("p", 10.5, 10.5)]},
                    {"start": 11.0, "end": 13.0, "text": "invented clock"}]
    else:
        segments = [{"start": 9.0, "end": 14.0, "text": "x", "words": words}]

    async def read(*_a):
        return {"segments": segments}

    monkeypatch.setattr(captions, "_project_transcript", read)
    clip, project = await _seed(f"k1p-{case}"[:40], start=10.0, end=13.0)
    built, state = await captions._plan_for_render(clip, project, {}, "burn")
    if reason is not None:
        assert state == {"origin": None, "outcome": "unavailable", "reason": reason}
        return
    assert state == {"origin": "built", "outcome": "burn", "reason": None}
    burned = [w["word"] for c in built.caption_plan["chunks"] for w in c["words"]]
    assert burned == tokens
    for chunk in built.caption_plan["chunks"]:
        assert all(0.0 <= w["start"] <= w["end"] <= 3.0 for w in chunk["words"])


# ── K7: a preview publishes only what is still current ────────────────────────

class _Queue:
    worker_id = "d2r2-worker"

    async def update_progress(self, *_):
        pass

    def is_cancelled(self, _):
        return False


class _Render:
    """Stands in for `render_preview`: writes this job's bytes to the file it is
    told to render, records the `.ass` it was handed, and can hold mid-render."""

    def __init__(self, monkeypatch):
        import services.clipper.render as static_render

        self.payload, self.holds, self.seen = {}, {}, {}
        monkeypatch.setattr(static_render, "render_preview", self)
        monkeypatch.setattr(jobs, "_source_path", lambda _: "unused.mp4")

    async def __call__(self, _src, _cand, _plan, ass, out, **_kw):
        # Before D2r-2 the preview rendered straight to the published path, which
        # names no job: that is the job started last.
        job = next((j for j in self.payload if f".{j}-" in Path(out).name), self.last)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(self.payload[job])
        self.seen[job] = {"out": Path(out), "ass": Path(ass) if ass else None,
                          "ass_text": Path(ass).read_text(encoding="utf-8") if ass else None}
        if job in self.holds:
            rendering, go = self.holds[job]
            rendering.set()
            await go.wait()

    async def start(self, ident, job, payload, hold=True):
        """Held: returns the resumption — await it to let the render finish."""
        self.payload[job], self.last = payload, job
        if hold:
            self.holds[job] = (asyncio.Event(), asyncio.Event())
        # The row as the queue leaves it when it claims a job: publication checks it (BURST R1).
        async with async_session() as session:
            session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_preview",
                                 status="running", worker_id=_Queue.worker_id, attempt_count=1,
                                 lease_expires_at=datetime.utcnow() + timedelta(minutes=10),
                                 metadata_json="{}"))
            await session.commit()
        CLAIMED_ATTEMPT.set(ClaimedAttempt(job, 1, _Queue.worker_id))   # the claim's identity (R1c)
        task = asyncio.create_task(jobs.handle_preview(job, ident, ident, {}, _Queue()))
        if not hold:
            return task
        await asyncio.wait_for(self.holds[job][0].wait(), 30)

        async def resume():
            self.holds[job][1].set()
            await task
        return resume()

    async def run(self, ident, job, payload):
        await (await self.start(ident, job, payload, hold=False))


def _published(ident, job) -> Path:
    """The file `job`'s first attempt publishes: an immutable per-attempt name (BURST R1)."""
    return attempt_path(storage.preview_path(ident, ident), {"job_id": job, "attempt": 1})


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _attempts(ident) -> list[str]:
    return sorted(p.name for p in storage.preview_path(ident, ident).parent.iterdir()
                  if p.name.startswith(f".{ident}."))


async def _snapshot(ident):
    row = await _row(ident)
    # The file the row SELECTS — per-attempt names since BURST R1.
    return {"file": _sha(Path(row.preview_path)) if row.preview_path else None,
            "preview_path": row.preview_path, "warnings": row.warnings}


STORED = {"chunks": [{"text": "SAVED", "start": 0.2, "end": 1.0, "words": []}],
          "style": {}, "x_pct": 0.5, "y_pct": 0.7}
UNTIMED = [{"start": 0.0, "end": 3.0, "text": "nobody timed this"}]


async def _edit(client, ident, how):
    if how == "clip_caption_plan":
        r = await client.patch(f"/api/clipper/clips/{ident}", json={"caption_plan": STORED})
        assert r.status_code == 200, r.text
    elif how == "clip_window":
        r = await client.patch(f"/api/clipper/clips/{ident}", json={"start_time": 0.5})
        assert r.status_code == 200, r.text
    else:
        async with async_session() as session:
            project = await session.get(ProjectModel, ident)
            if how == "project_watermark":
                project.clipper_settings = {**project.clipper_settings, "watermark_text": "@x"}
            else:                                            # project_dimensions
                project.width = 640
            await session.commit()


@pytest.mark.parametrize("how", ["clip_caption_plan", "clip_window", "project_watermark",
                                 "project_dimensions"])
async def test_k7a_an_edit_during_a_preview_rejects_it_and_changes_nothing(
        monkeypatch, client, how):
    ident = f"k7a{how[:10]}{how[-4:]}"
    await _seed(ident, segments=UNTIMED)
    render = _Render(monkeypatch)
    await render.run(ident, f"{ident}-p0", b"P0")          # an earlier, published preview
    task = await render.start(ident, f"{ident}-p1", b"P1")
    await _edit(client, ident, how)
    before = await _snapshot(ident)
    # The earlier file at its own per-attempt name (BURST R1): a clip edit unselects it, and the
    # refused preview must leave it exactly as it was.
    p0 = _published(ident, f"{ident}-p0")
    assert _sha(p0) == hashlib.sha256(b"P0").hexdigest()

    with pytest.raises(RuntimeError, match="changed while it rendered"):
        await task
    assert await _snapshot(ident) == before
    assert _sha(p0) == hashlib.sha256(b"P0").hexdigest()
    assert _attempts(ident) == []


async def test_k7b_an_old_preview_does_not_replace_a_newer_one(monkeypatch, client):
    ident = "k7bnewer"
    await _seed(ident, segments=UNTIMED)
    render = _Render(monkeypatch)
    old = await render.start(ident, f"{ident}-old", b"OLD")
    await _edit(client, ident, "clip_caption_plan")
    await render.run(ident, f"{ident}-new", b"NEW")
    newer = await _snapshot(ident)
    assert newer["file"] == hashlib.sha256(b"NEW").hexdigest()
    assert newer["preview_path"] == str(_published(ident, f"{ident}-new"))
    # The new preview burned the saved plan, so it took the caption report back.
    assert newer["warnings"] == ["Layout: kept from analysis"]

    with pytest.raises(RuntimeError, match="changed while it rendered"):
        await old
    assert await _snapshot(ident) == newer
    assert _attempts(ident) == []


async def test_k7c_an_old_preview_does_not_follow_a_newer_export(monkeypatch, tmp_path):
    ident = "k7cexport"
    await _seed(ident, segments=UNTIMED)
    render = _Render(monkeypatch)
    await render.run(ident, f"{ident}-p0", b"P0")
    task = await render.start(ident, f"{ident}-p1", b"P1")

    job = f"{ident}-e"
    staged, out = tmp_path / "staged.mp4", tmp_path / "out.mp4"
    staged.write_bytes(b"mp4")
    staged.with_suffix(".json").write_text("{}", encoding="utf-8")
    async with async_session() as session:
        session.add(JobModel(id=job, project_id=ident, clip_id=ident, type="clipper_export",
                             status="running", worker_id=_Queue.worker_id))
        await session.execute(update(ClipModel).where(ClipModel.id == ident)
                              .values(status="exporting", export_job_id=job))
        await session.commit()
    assert await jobs._publish_export(job, ident, _Queue.worker_id, staged, out, None,
                                      caption_state={"origin": "stored", "outcome": "burn",
                                                     "reason": None})
    before = await _snapshot(ident)
    assert (await _row(ident)).status == "exported"

    with pytest.raises(RuntimeError, match="changed while it rendered"):
        await task
    assert await _snapshot(ident) == before
    assert _attempts(ident) == []


async def test_k7d_unchanged_inputs_publish_file_row_and_warnings(monkeypatch):
    ident = "k7dcontrol"
    await _seed(ident, segments=UNTIMED)
    render = _Render(monkeypatch)
    await render.run(ident, f"{ident}-p", b"FRESH")

    after = await _snapshot(ident)
    assert after["file"] == hashlib.sha256(b"FRESH").hexdigest()
    assert after["preview_path"] == str(_published(ident, f"{ident}-p"))
    assert after["warnings"][0] == "Layout: kept from analysis"
    assert "no_timed_words" in after["warnings"][1] and "last preview" in after["warnings"][1]
    # It rendered into its own attempt file, not the published path.
    assert render.seen[f"{ident}-p"]["out"] != _published(ident, f"{ident}-p")
    assert _attempts(ident) == []


async def test_k7e_attempt_files_are_isolated_including_the_ass(monkeypatch):
    ident = "k7eisolate"
    await _seed(ident, caption_plan=STORED)
    render = _Render(monkeypatch)
    first = await render.start(ident, f"{ident}-a", b"A")
    a = render.seen[f"{ident}-a"]
    assert a["ass"] is not None and "SAVED" in a["ass_text"]

    # A second preview of the same inputs runs to the end and publishes.
    await render.run(ident, f"{ident}-b", b"B")
    b = render.seen[f"{ident}-b"]
    assert b["ass"] != a["ass"] and b["ass"].parent != a["ass"].parent
    assert b["out"] != a["out"] and not b["ass"].exists()    # b cleaned up its own
    # ...and left a's files exactly as a wrote them.
    assert a["out"].read_bytes() == b"A"
    assert a["ass"].read_text(encoding="utf-8") == a["ass_text"]
    published = await _snapshot(ident)
    assert published["file"] == hashlib.sha256(b"B").hexdigest()

    # a is then rejected (a project setting changed); its cleanup removes its
    # own files and nothing else.
    async with async_session() as session:
        project = await session.get(ProjectModel, ident)
        project.clipper_settings = {**project.clipper_settings, "watermark_text": "@y"}
        await session.commit()
    with pytest.raises(RuntimeError):
        await first
    assert not a["out"].exists() and not a["ass"].exists()
    assert await _snapshot(ident) == published
    assert _attempts(ident) == []


async def test_k7_a_legacy_warnings_value_neither_blocks_nor_allows_publication(
        monkeypatch, client):
    """`warnings=None` from `_caption_warnings` means "leave the list", never
    "do not publish" — and it does not rescue a preview whose inputs moved."""
    ident = "k7legacy"
    legacy = ["ok", 2]
    await _seed(ident, segments=UNTIMED, warnings=legacy)
    render = _Render(monkeypatch)
    await render.run(ident, f"{ident}-p0", b"P0")
    published = await _snapshot(ident)
    assert published == {"file": hashlib.sha256(b"P0").hexdigest(),
                         "preview_path": str(_published(ident, f"{ident}-p0")), "warnings": legacy}

    task = await render.start(ident, f"{ident}-p1", b"P1")
    await _edit(client, ident, "clip_window")
    before = await _snapshot(ident)
    with pytest.raises(RuntimeError):
        await task
    assert await _snapshot(ident) == before


def test_k7_the_input_list_covers_what_the_preview_reads():
    """Pins the list (D2r2-result.md cites where each is read)."""
    from types import SimpleNamespace

    clip = SimpleNamespace(caption_plan=None, caption_preset_id=None, start_time=0.0,
                           end_time=3.0, transcript_text="t", source_has_burned_captions=None,
                           layout_plan=None, content_type=None)
    project = SimpleNamespace(clipper_settings={}, width=320, height=180, video_path="v",
                              content_type=None, content_type_override=None)
    base = jobs._preview_inputs(clip, project)
    for obj, field, value in [
            (clip, "caption_plan", {}), (clip, "caption_preset_id", "x"), (clip, "start_time", 1.0),
            (clip, "end_time", 2.0), (clip, "transcript_text", "u"),
            (clip, "source_has_burned_captions", True), (clip, "layout_plan", {}),
            (clip, "content_type", "gaming"), (project, "clipper_settings", {"a": 1}),
            (project, "width", 640), (project, "height", 360), (project, "video_path", "w"),
            (project, "content_type", "irl"), (project, "content_type_override", "irl")]:
        old = getattr(obj, field)
        setattr(obj, field, value)
        assert jobs._preview_inputs(clip, project) != base, field
        setattr(obj, field, old)


# ── K8: the read path and a mixed list ────────────────────────────────────────

@pytest.mark.parametrize("stored,answer", [
    ({"a": 1}, []), ("ab", []), (["ok", 2], []),
    (["one", "two"], ["one", "two"]), (None, []),
], ids=["dict", "string", "mixed_list", "list_control", "null_control"])
async def test_k8_clip_to_dict_answers_a_list_and_leaves_the_row(caplog, stored, answer):
    ident = f"k8{type(stored).__name__[:4]}{len(str(stored))}"
    await _seed(ident, warnings=stored)
    row = await _row(ident)
    with caplog.at_level(logging.WARNING):
        body = clip_to_dict(row)
    assert body["warnings"] == answer
    logged = [r for r in caplog.records if ident in r.getMessage() and "warnings" in r.getMessage()]
    assert bool(logged) is (answer == [] and stored is not None)
    assert (await _row(ident)).warnings == stored


def test_k8_a_writer_leaves_a_mixed_list_alone():
    assert captions._caption_warnings(["ok", 2], UNAVAILABLE) is None
    assert captions._caption_warnings(["ok", None], {"outcome": "burn"}) is None
    assert captions._caption_warnings(["ok"], {"outcome": "burn"}) == ["ok"]
