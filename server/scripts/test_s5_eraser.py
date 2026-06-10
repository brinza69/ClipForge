"""
S5 anti-relic end-to-end test on a real source clip (throwaway harness).

Chain under test: transcribe (word timestamps) → auto_locate_caption_band →
detect_caption_displays → audit_caption_coverage → inpaint_region → re-OCR
the band of the OUTPUT (any text = leak). Optionally re-OCRs a previous
erase output as the baseline for comparison.

Usage (WSL, GPU free — stop the backend first):
    server/.venv/bin/python server/scripts/test_s5_eraser.py SRC.mp4 [OLD_ERASED.mp4]

Exit 0 = zero leaks in the new output.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

# /tmp dies with WSL auto-shutdown between invocations — keep results on /mnt/f.
DUMP = _SERVER.parent / "data" / "temp" / "s5_test"
OUT = DUMP / "erased_new.mp4"


def transcribe_words(src: str):
    from faster_whisper import WhisperModel

    model_name, device = "medium", "cuda"
    cfgp = _SERVER.parent / "data" / "whisper_config.json"
    if cfgp.exists():
        try:
            c = json.loads(cfgp.read_text())
            model_name = c.get("model") or model_name
        except Exception:
            pass
    print(f"[1/6] transcribing with {model_name}/cuda…", flush=True)
    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type="float16")
    seg_iter, info = model.transcribe(src, word_timestamps=True, vad_filter=True)
    words, speech = [], []
    for seg in seg_iter:
        speech.append((float(seg.start), float(seg.end)))
        for w in (seg.words or []):
            wt = (w.word or "").strip()
            if wt:
                words.append({"word": wt, "start": float(w.start), "end": float(w.end)})
    del model
    try:
        import gc, torch
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:
        pass
    print(f"      {len(words)} words, {len(speech)} speech segments, "
          f"lang={info.language}, {time.time()-t0:.0f}s", flush=True)
    return words, speech


def scan_band(path: str, band, tag: str) -> int:
    import cv2
    from services.caption_detector import _get_reader
    reader = _get_reader()
    bx, by, bw, bh = band
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / 5)))
    i = checked = leaks = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            checked += 1
            crop = fr[by:by + bh, bx:bx + bw]
            if crop.size:
                for _b, text, conf in reader.readtext(crop):
                    if conf and conf > 0.3 and text and text.strip():
                        leaks += 1
                        p = DUMP / f"leak_{tag}_{i:06d}.png"
                        cv2.imwrite(str(p), fr)
                        print(f"      LEAK[{tag}] @t={i/fps:.2f}s: {text!r} conf={conf:.2f}",
                              flush=True)
                        break
        i += 1
    cap.release()
    print(f"      [{tag}] {checked} frames checked, {leaks} leaks", flush=True)
    return leaks


def count_frames(path: str) -> int:
    import cv2
    cap = cv2.VideoCapture(path)
    n = 0
    while cap.grab():
        n += 1
    cap.release()
    return n


def dump_midpoint_frames(path: str, segs, n_dump: int = 6) -> None:
    """Dump full frames at the midpoints of the first n_dump segments for a
    human eyeball pass (faint ghosts can pass OCR)."""
    import cv2
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    wanted = sorted(int(((s["start_t"] + s["end_t"]) / 2) * fps)
                    for s in segs[:n_dump])
    i = k = 0
    while k < len(wanted):
        ok, fr = cap.read()
        if not ok:
            break
        if i == wanted[k]:
            cv2.imwrite(str(DUMP / f"eyeball_{i:06d}.png"), fr)
            k += 1
        i += 1
    cap.release()


def main() -> int:
    src = sys.argv[1]
    old = sys.argv[2] if len(sys.argv) > 2 else None
    DUMP.mkdir(parents=True, exist_ok=True)

    words, speech = transcribe_words(src)

    from services.caption_detector import auto_locate_caption_band, detect_caption_displays
    print("[2/6] auto-locating caption band…", flush=True)
    roi = auto_locate_caption_band(src, speech_intervals=speech)
    print(f"      band = {roi}", flush=True)
    if not roi:
        print("FAIL: no band found")
        return 2
    band = (roi["x"], roi["y"], roi["w"], roi["h"])

    print("[3/6] detecting displays…", flush=True)
    segs = detect_caption_displays(src, roi=roi)
    print(f"      {len(segs)} displays", flush=True)

    from services.caption_audit import audit_caption_coverage
    print("[4/6] coverage audit…", flush=True)
    extra = audit_caption_coverage(src, roi=roi, segments=segs, transcript_words=words)
    widened = sum(1 for s in segs if s.get("mask_kind") == "tight_widened")
    print(f"      {widened} suspects widened, {len(extra)} fallback segments", flush=True)
    segs.extend(extra)
    segs.sort(key=lambda s: s["start_t"])

    print("[5/6] inpainting…", flush=True)
    t0 = time.time()
    from services.inpaint import inpaint_region
    asyncio.run(inpaint_region(src, str(OUT), segments=segs))
    src_n, out_n = count_frames(src), count_frames(str(OUT))
    print(f"      done in {time.time()-t0:.0f}s → {OUT} "
          f"(frames src={src_n} out={out_n})", flush=True)
    if out_n < src_n - 2:
        print("FAIL: output is missing frames — encode is corrupt")
        return 2

    print("[6/6] verifying (re-OCR)…", flush=True)
    import cv2
    cap = cv2.VideoCapture(src)
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    full = (0, 0, vw, vh)
    if old:
        scan_band(old, band, "old_band")
        scan_band(old, full, "old_full")   # locate the user's relics anywhere
    new_leaks = scan_band(str(OUT), band, "new_band")
    new_full = scan_band(str(OUT), full, "new_full")
    dump_midpoint_frames(str(OUT), segs)
    print(f"\nRESULT: NEW band leaks = {new_leaks}, full-frame hits = {new_full} "
          f"(full-frame includes legit scene text); dumps in {DUMP}", flush=True)
    return 0 if new_leaks == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
