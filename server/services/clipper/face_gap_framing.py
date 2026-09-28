"""Consumer: authorize motion proposals from face_gap tracking records.

Authorization gates (in order, ALL must pass):
  Target row:
    - entry in [abs_t0, abs_t1), t is finite non-bool numeric
    - state == "empty", boxes == [] exactly
    - decoded_space == "reencoded_window", address_basis == "opencv_ffmpeg_metadata"
    - clock == "source_requested"
    - entry.frame_index and decoded_t are valid (_is_valid_fi/_is_valid_dt)
  Motion record:
    - motion.method == METHOD_VERSION, state == "tracked"
    - target_frame_index matches entry.frame_index exactly
    - target_decoded_t matches entry.decoded_t within _DT_TOL
    - age_s: finite non-bool numeric, 0 < age_s <= BUDGET_S
    - |target_decoded_t - seed_decoded_t - age_s| <= _DT_TOL
    - target_decoded_t > seed_decoded_t (PTS ordering)
    - target_frame_index > seed_frame_index
  Seed row:
    - found by (seed_frame_index, seed_decoded_t), singleton detected box
    - decoded_space == "reencoded_window", address_basis == "opencv_ffmpeg_metadata"
    - clock == "source_requested"
    - seed entry t is finite non-bool numeric, < target entry t
    - motion.seed_box matches seed_entry.boxes[0] element-by-element (float)
    - seed source-scaled (abs_t, cx, cy, w, h) exactly matches one elected span
  Geometry:
    - seed centre (source px) inside delivered footprint of base rect
    - tracked box (scaled to source) within [0, src_w] × [0, src_h]
    - proposal rect has positive w and h

Every entry in the time window that carries a motion key enters either used or
refused. Silently skipped means no motion key. All refused categories append to
the refused list, so len(refused) == sum(all per-category counts).
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Sequence

from services.clipper.dynamic_cameras import _rect
from services.clipper.dynamic_face_envelope import _footprint
from services.clipper.face_gap import (
    BUDGET_S as _BUDGET_S,
    METHOD_VERSION as _METHOD,
    _DT_TOL,
    _is_valid_fi,
    _is_valid_dt,
)

_DECODED_SPACE = "reencoded_window"
_ADDRESS_BASIS = "opencv_ffmpeg_metadata"
_CLOCK = "source_requested"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _finite_num(v) -> bool:
    """Strict: finite numeric, not bool."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)))


def _valid_box(b) -> bool:
    """All four values finite non-bool numeric; w > 0 and h > 0."""
    if not (isinstance(b, (list, tuple)) and len(b) == 4):
        return False
    return (all(_finite_num(v) for v in b[:4])
            and float(b[2]) > 0 and float(b[3]) > 0)


def _boxes_eq(a, b) -> bool:
    """Float element-wise equality on first 4 elements."""
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
            and len(a) >= 4 and len(b) >= 4):
        return False
    return all(float(av) == float(bv) for av, bv in zip(a[:4], b[:4]))


def _matches_elected(abs_t: float, cx: float, cy: float, w: float, h: float,
                     spans: Sequence[tuple]) -> bool:
    """True when all five fields match exactly one elected span."""
    for et, ecx, ecy, ew, eh in spans:
        if (float(et) == abs_t and float(ecx) == cx and float(ecy) == cy
                and float(ew) == w and float(eh) == h):
            return True
    return False


def _seed_ok(entry: dict) -> bool:
    """A seed row passes decoded_space, address_basis, clock, t, address checks."""
    return (entry.get("decoded_space") == _DECODED_SPACE
            and entry.get("address_basis") == _ADDRESS_BASIS
            and entry.get("clock") == _CLOCK
            and _finite_num(entry.get("t"))
            and _is_valid_fi(entry.get("frame_index"))
            and _is_valid_dt(entry.get("decoded_t")))


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def motion_proposals(
    face_track: Sequence[dict],
    elected_spans: Sequence[tuple],   # (abs_t, src_cx, src_cy, src_w, src_h)
    t0: float,
    t1: float,
    mult: float,
    headroom: float,
    framing_span: float,
    src_w: int,
    src_h: int,
    clip_start: float,
    sx: float,
    sy: float,
    base: dict,
) -> tuple[list[dict], dict]:
    """One rect proposal per authorized tracked motion target inside [t0, t1).

    Every entry in the time window carrying a motion key enters used or refused.
    Returns (proposals, scope) where:
      scope["used"]    — list of per-proposal provenance dicts
      scope["refused"] — list of per-refusal dicts (all categories)
      scope["tracked_used"], ["tracked_refused"], ["unmatched_seed"],
            ["unknown_method"], ["fp_unavailable"]
    len(refused) == tracked_refused + unmatched_seed + unknown_method
    """
    abs_t0 = clip_start + t0
    abs_t1 = clip_start + t1

    base_fp = _footprint(base, src_w, src_h)
    fp_unavailable = base_fp is None

    # Seed lookup — strict: decoded_space, address_basis, clock, t, address
    seed_lookup: dict[tuple, dict] = {}
    for entry in (face_track or []):
        if not isinstance(entry, dict):
            continue
        if (entry.get("state") == "detected"
                and isinstance(entry.get("boxes"), list)
                and len(entry["boxes"]) == 1
                and _seed_ok(entry)):
            fi, dt = entry.get("frame_index"), entry.get("decoded_t")
            seed_lookup[(int(fi), float(dt))] = entry

    proposals: list[dict] = []
    used_list: list[dict] = []
    refused_list: list[dict] = []
    tracked_used = tracked_refused = unmatched_seed = unknown_method = 0

    def _refuse(reason: str, cat: str = "tracked_refused", **extra):
        nonlocal tracked_refused, unmatched_seed, unknown_method
        if cat == "unmatched_seed":
            unmatched_seed += 1
        elif cat == "unknown_method":
            unknown_method += 1
        else:
            tracked_refused += 1
        refused_list.append({"reason": reason,
                              "target_frame_index": entry.get("frame_index"),
                              "target_decoded_t": entry.get("decoded_t"),
                              **extra})

    for entry in (face_track or []):
        if not isinstance(entry, dict):
            continue

        # Time-window check — strict t, not _f
        t_raw = entry.get("t")
        if not _finite_num(t_raw):
            continue
        abs_t = float(t_raw)
        if not (abs_t0 <= abs_t < abs_t1):
            continue

        motion = entry.get("motion")
        if not isinstance(motion, dict):
            continue   # no motion key → not an attempted target

        # Target row validity gates — failures produce refused entries
        if (entry.get("state") != "empty" or entry.get("boxes") != []
                or entry.get("decoded_space") != _DECODED_SPACE
                or entry.get("address_basis") != _ADDRESS_BASIS
                or entry.get("clock") != _CLOCK
                or not _is_valid_fi(entry.get("frame_index"))
                or not _is_valid_dt(entry.get("decoded_t"))):
            _refuse("invalid_target_row")
            continue

        method = motion.get("method")
        if method != _METHOD:
            unknown_method += 1
            refused_list.append({"reason": "unknown_method", "method": method,
                                  "target_frame_index": entry.get("frame_index"),
                                  "target_decoded_t": entry.get("decoded_t")})
            continue

        tracker_state = motion.get("state")
        if tracker_state != "tracked":
            _refuse("motion_not_tracked", tracker_reason=motion.get("reason"),
                    tracker_state=tracker_state)
            continue

        # Target address in motion must match entry's address
        m_tfi = motion.get("target_frame_index")
        m_tdt = motion.get("target_decoded_t")
        e_fi, e_dt = entry.get("frame_index"), entry.get("decoded_t")
        if not (_is_valid_fi(m_tfi) and _is_valid_dt(m_tdt)):
            _refuse("invalid_motion_target_address")
            continue
        if int(m_tfi) != int(e_fi):
            _refuse("target_frame_index_mismatch",
                    motion_tfi=int(m_tfi), entry_fi=int(e_fi))
            continue
        if abs(float(m_tdt) - float(e_dt)) > _DT_TOL:
            _refuse("target_decoded_t_mismatch")
            continue

        # Age: finite non-bool numeric, 0 < age_s <= BUDGET_S
        age_raw = motion.get("age_s")
        if not _finite_num(age_raw):
            _refuse("invalid_age")
            continue
        age_f = float(age_raw)
        if not (0 < age_f <= _BUDGET_S):
            _refuse("age_out_of_bounds", age_s=age_f)
            continue

        # PTS fields
        s_dt_raw = motion.get("seed_decoded_t")
        if not _is_valid_dt(s_dt_raw):
            _refuse("invalid_seed_decoded_t")
            continue
        s_dt_f = float(s_dt_raw)
        t_dt_f = float(m_tdt)

        # Age consistency: |target_decoded_t - seed_decoded_t - age_s| <= _DT_TOL
        if abs(t_dt_f - s_dt_f - age_f) > _DT_TOL:
            _refuse("age_pts_inconsistent",
                    expected_age=round(t_dt_f - s_dt_f, 9), stored_age=age_f)
            continue

        # PTS ordering
        if not (t_dt_f > s_dt_f):
            _refuse("pts_ordering_violated")
            continue

        # Frame index ordering (not just PTS)
        s_fi_raw = motion.get("seed_frame_index")
        if not _is_valid_fi(s_fi_raw):
            _refuse("invalid_seed_frame_index", cat="unmatched_seed")
            continue
        if int(m_tfi) <= int(s_fi_raw):
            _refuse("frame_index_ordering_violated",
                    target_fi=int(m_tfi), seed_fi=int(s_fi_raw))
            continue

        # Locate seed row
        seed_entry = seed_lookup.get((int(s_fi_raw), s_dt_f))
        if seed_entry is None:
            _refuse("seed_row_not_found", cat="unmatched_seed",
                    seed_frame_index=int(s_fi_raw), seed_decoded_t=s_dt_f)
            continue

        # seed_box must match actual singleton box
        m_seed_box = motion.get("seed_box")
        actual_seed_box = seed_entry["boxes"][0]
        if not (_valid_box(m_seed_box) and _valid_box(actual_seed_box)
                and _boxes_eq(m_seed_box, actual_seed_box)):
            _refuse("seed_box_mismatch", cat="unmatched_seed",
                    seed_frame_index=int(s_fi_raw))
            continue

        # Source time ordering: target.t > seed.t
        seed_t = seed_entry.get("t")
        if not (_finite_num(seed_t) and abs_t > float(seed_t)):
            _refuse("source_time_ordering_violated")
            continue

        # Seed must exactly match one elected span (all 5 fields)
        sb = actual_seed_box
        seed_src_cx = (float(sb[0]) + float(sb[2]) / 2.0) * sx
        seed_src_cy = (float(sb[1]) + float(sb[3]) / 2.0) * sy
        seed_src_w  = float(sb[2]) * sx
        seed_src_h  = float(sb[3]) * sy
        seed_abs_t  = float(seed_t)
        if not (float(sb[0]) * sx >= 0 and float(sb[1]) * sy >= 0
                and (float(sb[0]) + float(sb[2])) * sx <= src_w
                and (float(sb[1]) + float(sb[3])) * sy <= src_h):
            _refuse("seed_box_outside_source_frame", cat="unmatched_seed")
            continue
        if not _matches_elected(seed_abs_t, seed_src_cx, seed_src_cy,
                                seed_src_w, seed_src_h, elected_spans):
            _refuse("seed_not_in_elected_spans", cat="unmatched_seed",
                    seed_frame_index=int(s_fi_raw), seed_abs_t=seed_abs_t)
            continue

        # Seed centre must lie inside base delivered footprint
        if fp_unavailable:
            _refuse("footprint_unavailable")
            continue
        if not (base_fp[0] <= seed_src_cx <= base_fp[2]
                and base_fp[1] <= seed_src_cy <= base_fp[3]):
            _refuse("seed_centre_outside_footprint",
                    seed_cx=seed_src_cx, seed_cy=seed_src_cy)
            continue

        # Tracked box: valid, positive dims, scaled within source bounds
        mbox = motion.get("box")
        if not _valid_box(mbox):
            _refuse("invalid_tracked_box")
            continue
        mx  = float(mbox[0]) * sx
        my  = float(mbox[1]) * sy
        mw  = float(mbox[2]) * sx
        mh  = float(mbox[3]) * sy
        if not (mx >= 0 and my >= 0 and mx + mw <= src_w and my + mh <= src_h):
            _refuse("tracked_box_outside_source_frame",
                    box=[mx, my, mw, mh])
            continue

        obs_span = max(framing_span, mw, mh)
        p = _rect(0, obs_span * mult, mx + mw / 2.0, my + mh / 2.0,
                  headroom, src_w, src_h)
        if p.get("w", 0) > 0 and p.get("h", 0) > 0:
            proposals.append(p)
            tracked_used += 1
            used_list.append({
                "method": _METHOD,
                "seed_frame_index": int(s_fi_raw),
                "seed_decoded_t": s_dt_f,
                "seed_box": list(actual_seed_box),
                "target_frame_index": int(e_fi),
                "target_decoded_t": float(e_dt),
                "age_s": age_f,
                "box": list(mbox),
                "seed_source_requested_t": seed_abs_t,
                "target_source_requested_t": abs_t,
                "decoded_space": _DECODED_SPACE,
                "quality": deepcopy(motion.get("quality")),
            })
        else:
            _refuse("proposal_rect_invalid")

    scope = {
        "tracked_used": tracked_used,
        "tracked_refused": tracked_refused,
        "unmatched_seed": unmatched_seed,
        "unknown_method": unknown_method,
        "fp_unavailable": fp_unavailable,
        "used": used_list,
        "refused": refused_list,
    }
    return proposals, scope
