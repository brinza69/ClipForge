"""
ClipForge — AI Stream Clipper: the half of Pass D that can see.

`review.py` checks the things a rule can state: a caption on a UI panel, a blank
frame, a crop slicing the framed face. This asks the questions a rule cannot —
did the moment the clip is about actually HAPPEN on screen, and could somebody
who has never watched this stream follow it.

Three decisions, each with a measurement behind it.

**It reads the RENDERED file, where the local half reads the proxy.** The local
half runs before the encode on purpose, because a finding that arrives first can
be acted on. This one cannot act on anything — it is advisory — so it is worth
more seeing exactly what ships, captions burned in and crop applied, than a
reconstruction of it.

**gpt-5.6-terra, not gpt-4o.** `clipper_llm_judge_model` still says `gpt-4o` and
that model is no longer on OpenAI's pricing page. Measured on three real clips,
Terra names the specific thing it cannot find — "gameplay and seven-ingot
mistake are not visible", "armor claim is not shown" — where the cheaper Luna
gives a general "hard to follow". The difference is the whole reason for the
pass. Measured cost: ~1230 input and ~230 output tokens per clip at low detail,
which is about four cents for a project of eight clips.

**Low detail, six frames.** High detail costs 3429 input tokens against 1230 and
did not change an answer in testing. Six frames across a 20-40 second clip is
one every few seconds, which is enough to see whether an event happened.

A local model was tried first and is recorded in the session-4 handoff:
`qwen3-vl:4b-instruct` answered APPROVE to a clip whose caption covered 66% of
the game UI and to one where 65% of the framed face was outside the crop — the
same answer it gave to a clean clip. The 8B does not fit in 8 GB and timed out
past ten minutes a clip. This is not a preference for the paid path; it is what
the measurements left.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Sequence

from services.clipper.review import Finding

logger = logging.getLogger("clipforge.clipper.review.vision")

__all__ = ["VISION_PROMPT_VERSION", "sample_frames", "build_prompt",
           "findings_from", "review_rendered"]

VISION_PROMPT_VERSION = "vision_v1"

_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_TIMEOUT_S = 120.0

# Frames are read from a 1080x1920 render and sent down-scaled. 512 wide is
# ~500 image tokens; the model still reads burned captions at that size, which
# was checked before choosing it.
_SEND_WIDTH = 512

PROMPT = """You are reviewing a vertical short-form clip cut from a live stream.
The {n} images are frames sampled evenly across it, in order.

The clip is supposed to be about: "{about}"

Answer ONLY with JSON, no other text:
{{"on_screen": true/false,
  "readable": true/false,
  "problem": "<at most 12 words, or empty>"}}

on_screen - does the thing the clip is about actually happen in these frames?
readable  - could someone who has never seen this stream follow what is going on?"""


def sample_frames(path: Path | str, count: int = 6) -> list[bytes]:
    """`count` JPEGs spread evenly across a rendered clip.

    Returns [] rather than raising: a reviewer that cannot read the file must
    not cost the export it was meant to comment on.
    """
    try:
        import cv2
    except Exception as exc:                                    # pragma: no cover
        logger.warning("review_vision: opencv unavailable (%s)", exc)
        return []

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        logger.warning("review_vision: could not open %s", path)
        return []
    out: list[bytes] = []
    try:
        total = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        if total <= 1:
            return []
        for i in range(max(1, count)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / count))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            if w > _SEND_WIDTH:
                frame = cv2.resize(frame, (_SEND_WIDTH, int(h * _SEND_WIDTH / w)),
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                out.append(buf.tobytes())
    except Exception as exc:                                    # pragma: no cover
        logger.warning("review_vision: sampling failed (%s)", exc, exc_info=True)
    finally:
        cap.release()
    return out


def build_prompt(about: str, n: int) -> str:
    """The question, with the clip's own subject in it.

    Without the subject the model can only describe what it sees, and describing
    is not reviewing — the useful answer is whether the picture matches the
    thing the clip was cut for.
    """
    text = " ".join((about or "").split())[:400] or "an unspecified moment"
    return PROMPT.format(about=text, n=n)


def findings_from(answer: str) -> list[Finding]:
    """Turn the model's JSON into Findings, or nothing.

    Every verdict is `revise`. This pass never rejects: it is one opinion from
    six still frames, and a clip deleted on that basis cannot be argued with.
    Anything unparseable is dropped rather than guessed at (§31).
    """
    raw = (answer or "").strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    problem = str(data.get("problem") or "").strip()[:120]
    out: list[Finding] = []
    if data.get("on_screen") is False:
        out.append(Finding(
            "payoff_off_screen", "revise", 0.0,
            problem or "the moment the clip is about is not visible in it"))
    if data.get("readable") is False and not out:
        # Only when the payoff IS on screen: otherwise this is the same
        # complaint twice, and two findings read as two problems.
        out.append(Finding(
            "hard_to_follow", "revise", 0.0,
            problem or "a viewer new to this stream could not follow it"))
    return out


async def review_rendered(path: Path | str, about: str, *, model: str,
                          api_key: str, frames: int = 6) -> dict[str, Any]:
    """Ask the model about a rendered clip. Never raises.

    Returns `{"findings": [...], "model": ..., "usage": {...}, "warnings": [...]}`
    so the caller can merge the findings into the review the local half produced
    and record what the call cost.
    """
    import httpx

    result: dict[str, Any] = {"findings": [], "model": model,
                              "prompt_version": VISION_PROMPT_VERSION,
                              "usage": {}, "warnings": []}
    images = sample_frames(path, frames)
    if not images:
        result["warnings"].append("no frames could be read from the render")
        return result

    content: list[dict] = [{"type": "text", "text": build_prompt(about, len(images))}]
    for blob in images:
        b64 = base64.b64encode(blob).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                      "detail": "low"}})

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model,
                      "messages": [{"role": "user", "content": content}]},
            )
        if r.status_code != 200:
            result["warnings"].append(f"vision review HTTP {r.status_code}")
            logger.warning("review_vision: %s -> %s", model, r.text[:300])
            return result
        body = r.json()
        result["usage"] = body.get("usage") or {}
        answer = (body["choices"][0]["message"].get("content") or "")
    except Exception as exc:                                    # pragma: no cover
        result["warnings"].append(f"vision review failed: {exc}")
        logger.warning("review_vision: call failed", exc_info=True)
        return result

    result["findings"] = [f.as_dict() for f in findings_from(answer)]
    return result


def merge(review: dict[str, Any] | None, vision: dict[str, Any]) -> dict[str, Any]:
    """Fold a vision result into the local half's review.

    The verdict is recomputed over BOTH sets, which is what `review.verdict`
    was written to allow — the seam this module was supposed to slot into.
    """
    from services.clipper.review import Finding as _F
    from services.clipper.review import verdict as _verdict

    out = dict(review or {"verdict": "APPROVE", "findings": [], "warnings": []})
    out["findings"] = list(out.get("findings") or []) + list(vision.get("findings") or [])
    out["warnings"] = list(out.get("warnings") or []) + list(vision.get("warnings") or [])
    out["vision"] = {k: vision.get(k) for k in ("model", "prompt_version", "usage")}
    out["verdict"] = _verdict([
        _F(f.get("kind", ""), f.get("severity", "revise"), f.get("at", 0.0),
           f.get("detail", ""))
        for f in out["findings"]
    ])
    return out
