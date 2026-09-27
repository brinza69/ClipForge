"""BURST1r2 part 2 (codex next-20 §2, next-17 §3): the caption warning's facts on the card.

`caption_display` on GET /api/clipper/projects/{id} (`clips[]`) and GET /api/clipper/clips/{id} —
the contract is B/BURST-UI-contract.md. The render states come from the production `_write_ass`
wherever it can produce them, are stored where a render stores them (the export sidecar, the preview
job row via `_publish_preview`), and are read back through the router.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from database import async_session
from models import ClipModel, JobModel, ProjectModel
from services.clipper import caption_display, storage
from services.clipper.captions import build_caption_plan
from workers import clipper_captions
from workers import clipper_render_jobs as jobs

RECORD = {"caption_filter": True, "ass_sha256": "a" * 64}


def _built(words, start=100.0, end=110.0):
    return build_caption_plan({"start": start, "end": end},
                              {"segments": [{"start": start, "end": end, "text": "", "words": words}]},
                              preset_id="bold_impact", max_words=3, position="bottom", layout={})


def _spoken(*spans):
    return [{"word": w, "start": s, "end": e} for w, s, e in spans]


WELL_TIMED = _spoken(("one", 101.0, 101.3), ("two", 101.4, 101.7), ("three", 101.8, 102.1))
# the tail of the clip: six point words in its last 10 ms, which settle cannot show (no_time)
TAIL = [{"word": f"w{i}", "start": 109.990 + 0.001 * i, "end": 109.990 + 0.001 * i} for i in range(6)]
# a burst of point words, as on 1da8: five cards share 0.46 s, 92 ms each (short, drawn). Not four:
# 4 x 115 ms is short in the plan, but two of them round to 120 ms on the .ass clock.
BURST = [{"word": w, "start": 100.6, "end": 100.6} for w in "abcdefghijklmno"] + _spoken(
    ("us", 101.06, 101.3), ("from", 101.3, 101.5))


def _render(plan, drop=None, tmp=None):
    """The state a burn of `plan` leaves, written by the production writer."""
    import tempfile

    state = {"origin": "stored", "outcome": "burn", "reason": None}
    clipper_captions._write_ass(SimpleNamespace(id="api", caption_plan=plan),
                                Path(tmp or tempfile.mkdtemp()), drop, None, state)
    return state


async def _seed(ident, plan, *, status="exported", exported=True, preview=False):
    async with async_session() as session:
        session.add(ProjectModel(id=ident, title="cd", source_kind="file", video_path="",
                                 status="ready", processing_mode="clipping", duration=1000.0,
                                 width=320, height=180, fps=24, clipper_settings={"fps": 24}))
        session.add(ClipModel(
            id=f"{ident}c", project_id=ident, title="c", start_time=100.0, end_time=110.0,
            duration=10.0, transcript_text="words", status=status, caption_plan=plan,
            export_path=str(storage.export_path(ident, f"{ident}c")) if exported else None,
            export_job_id=f"{ident}x" if exported else None,
            preview_path=str(storage.preview_path(ident, f"{ident}c")) if preview else None))
        await session.commit()
    return f"{ident}c"


def _sidecar(ident, cid, plan, state, *, drop=None, record=RECORD, mp4=True):
    path = storage.export_path(ident, cid).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if mp4:                         # the file the `exported` row points at (BURST R2 reads it)
        path.with_suffix(".mp4").write_bytes(b"mp4")
    path.write_text(json.dumps({"clip_id": cid, "caption_plan": plan, "caption_plan_state": state,
                                "drop_spans": drop if drop is not None else [],
                                "render_record": record}), encoding="utf-8")
    return path


async def _card(client, ident):
    r = await client.get(f"/api/clipper/projects/{ident}")
    assert r.status_code == 200, r.text
    return r.json()["clips"][0]["caption_display"]


async def _exported(client, ident, plan, state, **kw):
    cid = await _seed(ident, plan)
    _sidecar(ident, cid, plan, state, **kw)
    return await _card(client, ident)


# ── the four kinds of fact ──────────────────────────────────────────────────

async def test_total_missing_every_card_has_no_time(client):
    plan = _built(TAIL)
    # nothing was drawn, so no .ass was written and the encode carried no subtitles filter
    view = await _exported(client, "cdall", plan, _render(plan),
                           record={"caption_filter": False, "ass_sha256": None})
    assert (view["level"], view["level_from"]) == ("limited", "export")
    ex = view["export"]
    assert (ex["state"], ex["current"], ex["missing"]) == ("limited", True, "all")
    assert (ex["outcome"], ex["reason"]) == ("unavailable", "no_card_representable")
    assert [(c["text"], c["why"], c["clock"], c["export_at"]) for c in ex["facts"]["unshown_cards"]] == [
        ("w0 w1 w2", "no_time", "plan", 10.0), ("w3 w4 w5", "no_time", "plan", 10.0)]
    assert ex["identity"] == {"attempt_job_id": "cdallx", "ass_sha256": None,
                              "caption_filter": False}
    # the CURRENT PLAN says the same, on its own clock, by chunk index, with the limit
    assert view["plan"]["state"] == "limited" and view["plan"]["clock"] == "plan"
    assert [(c["index"], c["why"]) for c in view["plan"]["unshown_cards"]] == [(0, "no_time"),
                                                                               (1, "no_time")]
    assert view["plan"]["limits"][0]["unshown"] == [0, 1]


async def test_partial_missing_a_no_time_card_is_converted_to_the_export_clock(client):
    plan = _built(WELL_TIMED + TAIL)
    drop = [[2.0, 3.0]]                          # one second of dead air before the tail
    view = await _exported(client, "cdpart", plan, _render(plan, drop), drop=drop)
    ex = view["export"]
    assert (ex["state"], ex["missing"], view["level"]) == ("limited", "some", "limited")
    # plan clock 10.0, one removed second before it: 9.0 in the mp4
    assert [(c["start"], c["clock"], c["export_at"]) for c in ex["facts"]["unshown_cards"]] == [
        (10.0, "plan", 9.0), (10.0, "plan", 9.0)]
    assert ex["facts"]["ass_agrees"] is True


async def test_a_card_the_remap_left_under_one_tick_is_on_the_export_clock(client):
    plan = {"chunks": [{"text": "one", "start": 1.0, "end": 1.12},
                       {"text": "next", "start": 1.5, "end": 2.0}],
            "preset_id": "bold_impact", "display": {"rule": caption_display.RULE, "limits": []}}
    drop = [[1.001, 1.12]]
    view = await _exported(client, "cdtick", plan, _render(plan, drop), drop=drop)
    ex = view["export"]
    assert (ex["state"], ex["missing"]) == ("limited", "some")
    assert ex["facts"]["unshown_cards"] == [{"text": "one", "start": 1.0, "end": 1.001,
                                             "why": "under_one_tick", "clock": "export",
                                             "export_at": 1.0}]
    assert view["plan"]["state"] == "clean"       # the plan had room; the remap took it


async def test_a_short_only_burst_names_the_short_cards(client):
    plan = _built(BURST)
    view = await _exported(client, "cdshort", plan, _render(plan))
    ex = view["export"]
    assert (ex["state"], ex["missing"], view["level"]) == ("limited", "none", "limited")
    short = ex["facts"]["short_cards"]
    assert ex["facts"]["short_count"] == len(short) == 5 and ex["facts"]["unshown_cards"] == []
    assert all(c["clock"] == "export" and c["end"] - c["start"] < 0.12 for c in short)
    assert [(c["start"], c["end"]) for c in view["plan"]["short_cards"]] == [
        (c["start"], c["end"]) for c in short]      # no dead air: the two clocks coincide here


async def test_ass_agrees_false_is_unverified_not_a_success(client):
    state = {"origin": "stored", "outcome": "burn", "reason": None,
             "display": {"overlapping_pairs": 0, "empty_events": 0, "short_cards": 0,
                         "short_intervals": [], "unshown_cards": [], "ass_agrees": False,
                         "plan_settled": True, "plan_limits": 0}}
    plan = _built(TAIL)                          # the plan's own limit does not outrank it
    view = await _exported(client, "cdagree", plan, state)
    assert (view["export"]["state"], view["level"]) == ("unverified", "unverified")
    assert view["plan"]["state"] == "limited"


async def test_a_clean_report_without_a_burning_encode_is_unverified(client):
    plan = _built(WELL_TIMED)
    view = await _exported(client, "cdfilter", plan, _render(plan),
                           record={"caption_filter": None, "ass_sha256": None})
    assert (view["export"]["state"], view["level"]) == ("unverified", "unverified")


# ── which artefact, and whether it is still the current one ─────────────────

async def test_an_export_from_before_the_report_is_unknown_never_clean(client):
    plan = _built(WELL_TIMED)
    view = await _exported(client, "cdold", plan, {"origin": "stored", "outcome": "burn",
                                                   "reason": None})
    assert (view["export"]["state"], view["export"]["facts"]) == ("not_measured", None)
    assert view["level"] == "unknown"
    view = await _exported(client, "cdolder", plan, None)          # no caption state at all
    assert (view["export"]["state"], view["level"]) == ("not_measured", "unknown")


async def test_an_edit_makes_the_export_stale(client):
    plan = _built(WELL_TIMED)
    view = await _exported(client, "cdstale", plan, _render(plan))
    assert (view["export"]["state"], view["level"]) == ("verified", "verified")
    edited = copy.deepcopy(plan)
    edited["chunks"][0]["text"] = "ONE!"
    r = await client.patch("/api/clipper/clips/cdstalec", json={"caption_plan": edited})
    assert r.status_code == 200, r.text
    r = await client.get("/api/clipper/clips/cdstalec")
    view = r.json()["caption_display"]
    ex = view["export"]
    assert (ex["state"], ex["found_state"], ex["current"]) == ("stale", "verified", False)
    assert ex["stale_reason"] == "clip_not_exported"
    assert view["level"] == "not_rendered" and view["level_from"] is None


async def test_a_plan_changed_under_an_exported_clip_is_stale(client):
    plan = _built(WELL_TIMED)
    cid = await _seed("cdplan", plan)
    _sidecar("cdplan", cid, _built(BURST), _render(_built(BURST)))       # burned another plan
    view = await _card(client, "cdplan")
    assert (view["export"]["state"], view["export"]["stale_reason"]) == ("stale", "plan_changed")
    assert view["export"]["found_state"] == "limited" and view["level"] == "not_rendered"
    # a plan BUILT for the render, and the clip has stored one since
    _sidecar("cdplan", cid, plan, {**_render(plan), "origin": "built"})
    assert (await _card(client, "cdplan"))["export"]["stale_reason"] == "plan_changed"


async def test_an_unreadable_sidecar_is_unverified(client):
    cid = await _seed("cdbad", _built(WELL_TIMED))
    path = storage.export_path("cdbad", cid).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    view = await _card(client, "cdbad")
    assert (view["export"]["state"], view["level"]) == ("unreadable", "unverified")


async def test_a_plan_or_a_sidecar_that_cannot_be_read_is_never_clean(client):
    bad = {"chunks": [{"text": "a", "start": "soon", "end": 1.0}],
           "display": {"rule": caption_display.RULE, "limits": []}}
    await _seed("cdplanbad", bad, status="approved", exported=False)
    view = await _card(client, "cdplanbad")
    assert view["plan"] == {"state": "unreadable"} and view["level"] == "not_rendered"
    plan = _built(WELL_TIMED)
    cid = await _seed("cdother", plan)
    _sidecar("cdother", cid, plan, _render(plan))
    path = storage.export_path("cdother", cid).with_suffix(".json")
    side = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**side, "clip_id": "someone_else"}), encoding="utf-8")
    view = await _card(client, "cdother")
    assert (view["export"]["state"], view["level"]) == ("unreadable", "unverified")


def test_the_plan_names_cards_with_text_by_index_and_the_minimum_is_not_short():
    plan = _built(TAIL)
    plan["chunks"].insert(0, {"text": " ", "start": 0.0, "end": 0.0, "display": "no_time"})
    plan["chunks"] += [{"text": "at", "start": 11.0, "end": 11.12},
                       {"text": "under", "start": 12.0, "end": 12.119}]
    facts = caption_display.plan_facts(plan, 0.12)
    assert [c["index"] for c in facts["unshown_cards"]] == [1, 2]
    assert [c["text"] for c in facts["short_cards"]] == ["under"]
    assert caption_display.plan_facts(None, 0.12) == {"state": "no_plan"}
    assert caption_display.plan_facts(["x"], 0.12) == {"state": "unreadable"}


async def test_no_render_the_plan_alone_speaks(client):
    await _seed("cdnone", _built(TAIL), status="approved", exported=False)
    view = await _card(client, "cdnone")
    assert view["export"] == {"state": "none", "current": False}
    assert view["preview"] == {"state": "none", "current": False}
    assert (view["level"], view["level_from"]) == ("limited", "plan")
    await _seed("cdlegacy", {"chunks": [{"text": "a", "start": 0, "end": 1}]}, exported=False)
    view = await _card(client, "cdlegacy")
    assert view["plan"] == {"state": "not_settled"} and view["level"] == "not_rendered"


async def _preview(ident, plan, state, tmp_path, final="done"):
    """A published preview, through `_publish_preview`: the attempt's file and report are selected on
    the clip (`preview_record`, BURST R1). The job is claimed as the queue claims it."""
    import datetime as dt

    from workers.clipper_preview_publish import attempt_path

    cid = await _seed(ident, plan, status="approved", exported=False)
    att = {"job_id": f"{ident}p", "attempt": 1, "worker": "api-worker"}
    async with async_session() as session:
        session.add(JobModel(id=f"{ident}p", project_id=ident, clip_id=cid,
                             type="clipper_preview", status="running", metadata_json="{}",
                             worker_id="api-worker", attempt_count=1,
                             lease_expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=10)))
        await session.commit()
        clip = await session.get(ClipModel, cid)
        project = await session.get(ProjectModel, ident)
        seen = jobs._preview_inputs(clip, project)
    attempt, out = tmp_path / f"{ident}.mp4", attempt_path(storage.preview_path(ident, cid), att)
    attempt.write_bytes(b"mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    assert await jobs._publish_preview(clip, seen, attempt, out, state, att) is None
    async with async_session() as session:
        (await session.get(JobModel, f"{ident}p")).status = final
        await session.commit()
    return cid


async def test_a_published_preview_carries_its_own_report(client, tmp_path):
    plan = _built(BURST)
    await _preview("cdprev", plan, _render(plan), tmp_path)
    view = await _card(client, "cdprev")
    pv = view["preview"]
    assert (pv["state"], pv["current"], pv["identity"]) == ("limited", True,
                                                           {"job_id": "cdprevp", "attempt": 1})
    assert pv["facts"]["short_count"] == 5
    assert (view["level"], view["level_from"]) == ("limited", "preview")
    r = await client.patch("/api/clipper/clips/cdprevc", json={"start_time": 100.5})
    assert r.status_code == 200, r.text
    pv = (await _card(client, "cdprev"))["preview"]
    assert (pv["state"], pv["found_state"], pv["stale_reason"]) == ("stale", "limited",
                                                                    "preview_cleared")


async def test_a_preview_that_published_and_then_failed_still_describes_its_file(client, tmp_path):
    # the report is written with the publish; a step after it failing does not unpublish the file
    plan = _built(BURST)
    await _preview("cdpfail", plan, _render(plan), tmp_path, final="failed")
    assert (await _card(client, "cdpfail"))["preview"]["state"] == "limited"


async def test_a_preview_whose_report_is_not_known_is_unknown(client, tmp_path):
    plan = _built(WELL_TIMED)
    cid = await _seed("cdprun", plan, status="approved", exported=False, preview=True)
    async with async_session() as session:          # a newer preview is still rendering
        session.add(JobModel(id="cdprunp", project_id="cdprun", clip_id=cid,
                             type="clipper_preview", status="running", metadata_json="{}"))
        await session.commit()
    view = await _card(client, "cdprun")
    assert (view["preview"]["state"], view["preview"]["current"]) == ("not_measured", True)
    assert view["preview"]["identity"] == {"job_id": None}     # the running job published nothing
    assert view["level"] == "unknown"
    # a verified export does not vouch for a preview nobody measured
    cid = await _seed("cdpexp", plan, preview=True)
    _sidecar("cdpexp", cid, plan, _render(plan))
    async with async_session() as session:
        session.add(JobModel(id="cdpexpp", project_id="cdpexp", clip_id=cid,
                             type="clipper_preview", status="done", metadata_json="{}"))
        await session.commit()
    view = await _card(client, "cdpexp")
    assert (view["export"]["state"], view["preview"]["state"]) == ("verified", "not_measured")
    assert (view["level"], view["level_from"]) == ("unknown", "preview")


# ── what makes the warning go away ──────────────────────────────────────────

async def test_a_verified_regeneration_clears_the_warning(client):
    burst = _built(BURST)
    view = await _exported(client, "cdclear", burst, _render(burst))
    assert view["level"] == "limited"
    # the person fixes the words; the next export's file is checked and has nothing to report
    fixed = _built(WELL_TIMED)
    state = _render(fixed)
    assert state["display"] == {}
    async with async_session() as session:
        (await session.get(ClipModel, "cdclearc")).caption_plan = fixed
        await session.commit()
    _sidecar("cdclear", "cdclearc", fixed, state)
    view = await _card(client, "cdclear")
    assert (view["level"], view["level_from"]) == ("verified", "export")
    assert (view["export"]["state"], view["export"]["missing"], view["plan"]["state"]) == (
        "verified", "none", "clean")
