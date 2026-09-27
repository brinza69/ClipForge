"""
ClipForge — AI Stream Clipper: the rows of `window_addressed` (window_address.compose).

Pure: the tie and the addresser are handed in. Three kinds, never converted
into one another (B/clock-composition-addendum.md §1.4, codex-verdict-next-22 §2):
a POINT per face sample and per motion read, a SPAN per motion pair, an
AGGREGATE for the panels. Every sample is exactly one row with one state, so
the counts rebuild the denominator.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Callable

TIME_TOLERANCE = Fraction(1, 1000)          # OpenCV reports ms; the encoder clock is exact
Addresser = Callable[[int], dict]


def _tb(s: str) -> tuple[int, int]:
    n, d = str(s).split("/")
    return int(n), int(d)


def _refused(why: str | None, at: str, **facts: Any) -> dict:
    return {**facts, "state": "refused", "reason": why, "refused_at": at}


def enc_time(tie: dict, j: int) -> Fraction:
    """Encoder time of window frame j relative to the FIRST window frame, exact.
    Never relative to the first face sample, and no constant: the time_base is the row's."""
    tb = _tb(tie["enc_time_base"])
    return Fraction((tie["frames"][j]["enc_pts"] - tie["frames"][0]["enc_pts"]) * tb[0], tb[1])


def frame_of(fi: Any, seconds: Any, tie: dict) -> tuple[int | None, str | None]:
    """(window frame, None) when index fi has a stats row and the time OpenCV
    reported for it is that frame's encoder time; else (None, the refusal)."""
    if fi is None or isinstance(fi, bool) or not isinstance(fi, int):
        return None, "sample_unreadable (no frame index)"
    if not 0 <= fi < len(tie["frames"]):
        return None, f"sample_frame_unknown (no stats row for window frame {fi})"
    if (seconds is None or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)):
        return None, f"sample_frame_unknown (no decoded time for window frame {fi})"
    want = enc_time(tie, fi)
    if abs(Fraction(str(seconds)) - want) > TIME_TOLERANCE:
        return None, f"sample_frame_unknown (decoded {seconds} s != encoder {float(want)} s of frame {fi})"
    return fi, None


def read_frame(r: int, reads: dict[int, dict], tie: dict) -> tuple[int | None, str | None]:
    """A sequential read ordinal as a window frame: POS_FRAMES before it must be r and
    POS_MSEC after it frame r's encoder time. That names the frame; it does not prove
    the pixels are that frame's (CC1-C0 §5.1: read 39 showed frame 38)."""
    log = reads.get(r)
    if log is None:
        return None, f"sample_frame_unknown (read {r} is not in the read log)"
    pf = log.get("pos_frames")
    if pf is None or not float(pf).is_integer() or int(pf) != r:
        return None, f"sample_frame_unknown (POS_FRAMES {pf} before read {r})"
    ms = log.get("pos_msec")
    return frame_of(r, None if ms is None else ms / 1000.0, tie)


def _tracking(mo: dict, tie: dict, address: Addresser) -> dict:
    """continue_gaps' track, kept apart from the raw observation: its seed and its
    target each addressed from their own index and decoded time."""
    t = {k: mo.get(k) for k in ("state", "method", "reason", "age_s")}
    for who in ("seed", "target"):
        fi = mo.get(f"{who}_frame_index")
        if fi is None:
            t[who] = None
            continue
        j, why = frame_of(fi, mo.get(f"{who}_decoded_t"), tie)
        t[who] = {"frame_index": fi, **(_refused(why, "sample") if why else address(j))}
    return t


def face_row(i: int, s: dict, tie: dict, address: Addresser) -> dict:
    """One face sample as a point. `observation` is the raw detector state, copied,
    never promoted by a track and never set for a frame that was not read."""
    facts = {"i": i, "kind": "point", "observation": s.get("state"),
             "frame_index": s.get("frame_index"), "decoded_t": s.get("decoded_t")}
    if s.get("state") == "unreadable" or s.get("frame_index") is None:
        row = _refused(f"sample_unreadable ({s.get('reason')})", "sample", **facts)
    elif s.get("address_basis") != "opencv_ffmpeg_metadata":
        row = _refused(f"sample_frame_unknown (address basis {s.get('address_basis')!r})", "sample", **facts)
    else:
        j, why = frame_of(s["frame_index"], s.get("decoded_t"), tie)
        row = _refused(why, "sample", **facts) if why else {**facts, **address(j)}
    if isinstance(s.get("motion"), dict):
        row["tracking"] = _tracking(s["motion"], tie, address)
    return row


def _pair(a: tuple, b: tuple, step: int, tie: dict) -> dict:
    (ja, pa), (jb, pb) = a, b
    if ja is None or jb is None:
        return _refused("endpoint_frame_unknown", "pair")
    sa, sb = tie["frames"][ja]["slot"], tie["frames"][jb]["slot"]
    if sb - sa != step:
        return _refused(f"pair_not_steady (slots {sa} and {sb} are {sb - sa} apart; the reader "
                        f"stepped {step} frames)", "pair", slots=[sa, sb])
    bad = next((p for p in (pa, pb) if p["state"] != "addressed"), None)
    if bad is not None:
        return _refused(f"endpoint_not_addressed ({bad['reason']})", "pair", slots=[sa, sb])
    if pb["source_pts"] <= pa["source_pts"] or pb["source_time_base"] != pa["source_time_base"]:
        return _refused("not_steady (the two ends do not show increasing source frames)", "pair",
                        slots=[sa, sb])
    ptb = _tb(tie["proxy_time_base"])
    fa, fb = tie["frames"][ja], tie["frames"][jb]
    return {"state": "span", "reason": None, "window_frames": [ja, jb], "slots": [sa, sb],
            "proxy_duration": str(Fraction((fb["proxy_pts"] - fa["proxy_pts"]) * ptb[0], ptb[1])),
            "source_time_base": pb["source_time_base"],
            "source_pts_after": pa["source_pts"], "source_pts_through": pb["source_pts"],
            "source_index_after": pa["source_index"], "source_index_through": pb["source_index"]}


def motion_rows(n: int, seen: dict | None, tie: dict, address: Addresser) -> list[dict]:
    """Entry k of region_motion: its read's point (detail, ui) and its pair (motion,
    focus), on the step and the read ordinals region_motion EXECUTED."""
    if seen is None or not seen.get("opened"):
        why = "reads_unobserved" if seen is None else "capture_not_opened"
        return [_refused(why, "reads", k=k) for k in range(n)]
    sampled = seen.get("sampled") or []
    if len(sampled) != n:
        return [_refused(f"motion_reads_inconsistent ({len(sampled)} sampled reads, {n} values)",
                         "reads", k=k) for k in range(n)]
    reads = {x["index"]: x for x in seen.get("reads") or []}
    out, prev = [], None
    for k, r in enumerate(sampled):
        j, why = read_frame(r, reads, tie)
        cur = (j, _refused(why, "read") if why else address(j))
        pair = ({"state": "motion_init", "reason": "no previous frame: initialisation, not a measured span"}
                if k == 0 else _pair(prev, cur, seen["step"], tie))
        out.append({"k": k, "read": r, "state": pair["state"], "reason": pair["reason"],
                    "point": cur[1], "pair": pair})
        prev = cur
    return out


def panels_aggregate(seen: dict | None, tie: dict, address: Addresser) -> dict:
    """The reads ui_panels actually sampled, first and last addressed; no time per rectangle."""
    if seen is None:
        return _refused("reads_unobserved", "reads")
    if not seen.get("opened"):
        return _refused("capture_not_opened (its [] is not 'no panels')", "reads")
    sampled = seen.get("sampled") or []
    if not sampled:
        return _refused("no_frame_read", "reads", stop=seen.get("stop"))
    reads = {x["index"]: x for x in seen.get("reads") or []}
    pts = []
    for r in sampled:
        j, why = read_frame(r, reads, tie)
        pts.append({"read": r, **(_refused(why, "read") if why else address(j))})
    ok = sum(1 for p in pts if p["state"] == "addressed")
    return {"state": "aggregate", "reason": None, "sampled_reads": sampled,
            "first": pts[0], "last": pts[-1], "observed": ok, "refused": len(pts) - ok}


def refused_rows(n_faces: int, n_motion: int, reason: str | None) -> dict:
    return {"faces": [_refused(reason, "window", i=i) for i in range(n_faces)],
            "motion": [_refused(reason, "window", k=k) for k in range(n_motion)],
            "panels": _refused(reason, "window")}


def _by(rows: list[dict]) -> dict:
    by: dict[str, int] = {}
    for r in rows:
        key = r["state"] if r["state"] != "refused" else "refused: " + str(r["reason"]).split(" ")[0]
        by[key] = by.get(key, 0) + 1
    return by


def counts(rows: dict) -> dict:
    """Denominator first. Every face sample and motion entry is one row."""
    faces, motion = rows["faces"], rows["motion"]
    return {"denominator": {"faces": len(faces), "motion": len(motion)},
            "faces": _by(faces),
            "faces_tracked": sum(1 for r in faces if (r.get("tracking") or {}).get("state") == "tracked"),
            "motion": _by(motion),
            "motion_points_addressed": sum(1 for r in motion if (r.get("point") or {}).get("state") == "addressed"),
            "panels": rows["panels"]["state"]}
