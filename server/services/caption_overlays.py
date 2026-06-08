"""
ClipForge — Caption Overlays + Preview Renderer

Manual text overlays (CapCut-style "Add text" boxes) plus the live preview
frame endpoint that powers the Caption Studio drag/resize UX.

Two collaborating pieces:

  * `build_overlays_ass(...)` — given a list of overlay dicts and (W, H) of
    the target video, write a libass-compatible .ass file.
  * `render_preview_frame(...)` — extract a single frame from the source
    video at a given time, burn the overlays via libass, return the PNG.

The overlay shape is intentionally a superset of the existing caption-preset
fields so the same JSON-template store powers both auto-captions (highlighted
transcript) and manual overlays.

Overlay dict:
    {
        "text":        "Hello world",
        "start_t":     0.0,           # seconds (within the clip, not source)
        "end_t":       3.0,
        "template_id": "bold_impact", # optional; merges template into style
        "style":       { ... },       # optional inline overrides
        "x_pct":       0.5,           # 0..1, center of the text box, X
        "y_pct":       0.85,          # 0..1, center of the text box, Y
        "scale":       1.0,           # multiplier on font_size (1.0 = template's default)
        "rotation":    0.0,           # degrees, optional
    }
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pysubs2

from config import settings
from services.captioner import DEFAULT_PRESETS, hex_to_ass_color

logger = logging.getLogger("clipforge.caption_overlays")


# ── ffmpeg shell-out helpers (mirroring services/inpaint.py) ────────────────


def _ffmpeg_bin() -> str:
    loc = settings.ffmpeg_location
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return shutil.which("ffmpeg") or "ffmpeg"


def _creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


# ── ASS file generation for manual overlays ─────────────────────────────────


def _resolve_style(overlay: Dict) -> Dict:
    """Merge template (if any) and inline `style` overrides into one dict."""
    base: Dict = {}
    tid = overlay.get("template_id")
    if tid:
        from services.caption_templates import get_template
        tpl = get_template(tid) or DEFAULT_PRESETS.get(tid)
        if tpl:
            base.update(tpl)
    if overlay.get("style"):
        base.update(overlay["style"])
    return base


def _ass_alignment(_unused: str) -> int:
    """
    For manual overlays we always use libass alignment=5 (centered) and
    place the text via \\pos(), because the user is dragging a point on the
    preview and the X/Y they give us is the center of the text box.
    """
    return 5


def build_overlays_ass(
    overlays: List[Dict],
    video_w: int,
    video_h: int,
    output_path: str,
) -> str:
    """
    Build an ASS file for the given overlays at the given resolution.
    Returns the file path it wrote.
    """
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(video_w)
    subs.info["PlayResY"] = str(video_h)
    subs.info["ScaledBorderAndShadow"] = "yes"
    subs.info["WrapStyle"] = "0"

    # One ASS style per unique (font_family, size, colors) tuple so overlays
    # using the same template render efficiently.
    style_cache: Dict[Tuple, str] = {}

    def _get_or_make_style(overlay: Dict) -> str:
        s = _resolve_style(overlay)
        scale = float(overlay.get("scale") or 1.0)
        font_size = int(round(float(s.get("font_size", 64)) * scale))
        key = (
            s.get("font_family", "Arial Black"),
            font_size,
            s.get("text_color", "#FFFFFF"),
            s.get("outline_color", "#000000"),
            int(s.get("outline_width", 4)),
            s.get("shadow_color", "#00000080"),
            float(s.get("shadow_offset", 2)),
            int(s.get("borderstyle", 1)),
            bool(s.get("uppercase", False)),
            bool(s.get("italic", False)),
        )
        if key in style_cache:
            return style_cache[key]
        name = f"ovl{len(style_cache)}"
        style = pysubs2.SSAStyle()
        style.fontname = s.get("font_family", "Arial Black")
        style.fontsize = font_size
        style.bold = s.get("font_weight", "Bold").lower() in ("bold", "black", "heavy")
        style.italic = bool(s.get("italic", False))
        style.primarycolor = pysubs2.Color(*_rgba_from_hex(s.get("text_color", "#FFFFFF")))
        style.outlinecolor = pysubs2.Color(*_rgba_from_hex(s.get("outline_color", "#000000")))
        style.backcolor = pysubs2.Color(*_rgba_from_hex(s.get("shadow_color", "#00000080")))
        style.outline = float(s.get("outline_width", 4))
        style.shadow = float(s.get("shadow_offset", 0))
        style.borderstyle = int(s.get("borderstyle", 1))  # 1=outline+shadow, 3=opaque box
        style.alignment = pysubs2.Alignment.MIDDLE_CENTER  # we use \pos() for placement
        subs.styles[name] = style
        style_cache[key] = name
        return name

    for ovl in overlays:
        text = (ovl.get("text") or "").strip()
        if not text:
            continue
        s = _resolve_style(ovl)
        if s.get("uppercase"):
            text = text.upper()

        style_name = _get_or_make_style(ovl)

        x_px = int(round(float(ovl.get("x_pct", 0.5)) * video_w))
        y_px = int(round(float(ovl.get("y_pct", 0.85)) * video_h))
        rot = float(ovl.get("rotation") or 0.0)

        # Build the ASS text payload: \pos for placement, \frz for rotation,
        # \q2 to prevent libass from wrapping the line at runtime (the user
        # places a single block; line breaks are explicit \N).
        prefix_bits = [f"\\an5", f"\\pos({x_px},{y_px})", "\\q2"]
        if rot:
            prefix_bits.append(f"\\frz{rot:.2f}")
        prefix = "{" + "".join(prefix_bits) + "}"

        # Replace literal newlines with libass \N
        safe_text = text.replace("\r\n", "\n").replace("\n", "\\N")

        start_ms = int(float(ovl.get("start_t", 0.0)) * 1000)
        end_ms = int(float(ovl.get("end_t", 3.0)) * 1000)
        if end_ms <= start_ms:
            end_ms = start_ms + 1000

        evt = pysubs2.SSAEvent(
            start=start_ms,
            end=end_ms,
            style=style_name,
            text=prefix + safe_text,
        )
        subs.events.append(evt)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subs.save(output_path)
    return output_path


def _rgba_from_hex(hex_color: str) -> Tuple[int, int, int, int]:
    """#RRGGBB or #RRGGBBAA → (r, g, b, a) for pysubs2.Color."""
    h = hex_color.lstrip("#")
    if len(h) == 8:
        r, g, b, a = (int(h[i : i + 2], 16) for i in (0, 2, 4, 6))
    elif len(h) == 6:
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
        a = 0
    else:
        return (255, 255, 255, 0)
    return (r, g, b, a)


# ── Preview frame renderer ──────────────────────────────────────────────────


def _preview_cache_dir() -> Path:
    d = Path(settings.temp_dir) / "caption_preview"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _video_frame_key(video_path: str, time_s: float) -> str:
    """Stable hash key for caching the raw source frame on disk."""
    st = Path(video_path).stat()
    raw = f"{video_path}|{st.st_size}|{int(st.st_mtime)}|{time_s:.3f}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def extract_source_frame(video_path: str, time_s: float = 0.5) -> Path:
    """Pull one frame at time_s from the video as a PNG (cached)."""
    key = _video_frame_key(video_path, time_s)
    cache = _preview_cache_dir() / f"frame_{key}.png"
    if cache.exists():
        return cache

    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-ss", f"{max(0.0, time_s):.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(cache),
    ]
    r = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=_creationflags(),
        timeout=60,
    )
    if r.returncode != 0 or not cache.exists():
        raise RuntimeError(
            f"ffmpeg frame-extract failed: {(r.stderr or '')[-400:]}"
        )
    return cache


def probe_video_dims(video_path: str) -> Tuple[int, int]:
    """Return (width, height) of the first video stream, or raise."""
    ffmpeg = _ffmpeg_bin()
    ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).exists() and ffprobe != "ffprobe":
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True, creationflags=_creationflags(),
        timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[-400:]}")
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"ffprobe didn't return dims: {r.stdout!r}")
    return int(lines[0]), int(lines[1])


def render_preview_frame(
    video_path: str,
    overlays: List[Dict],
    time_s: float = 0.5,
) -> bytes:
    """
    Render one frame from `video_path` at `time_s` with the overlays burned
    in via libass. Returns the PNG bytes.

    The libass pass uses our user-fonts dir so freshly uploaded .ttf/.otf
    files Just Work without restarting the server.
    """
    from services.font_manager import fonts_dir

    video_path = str(video_path)
    src_frame = extract_source_frame(video_path, time_s)
    w, h = probe_video_dims(video_path)

    workdir = Path(tempfile.mkdtemp(prefix="cap_prev_", dir=str(_preview_cache_dir())))
    try:
        # Bind overlay start/end around `time_s` if the caller didn't set
        # explicit timing — preview at T should show overlays active at T.
        normalized: List[Dict] = []
        for ovl in overlays:
            o = dict(ovl)
            if "start_t" not in o or "end_t" not in o:
                o.setdefault("start_t", max(0.0, time_s - 5.0))
                o.setdefault("end_t", time_s + 5.0)
            normalized.append(o)

        ass_path = workdir / "overlays.ass"
        build_overlays_ass(normalized, w, h, str(ass_path))

        out_png = workdir / "preview.png"
        # libass filter wants forward-slashes and posix-style escapes for ':'
        ass_arg = str(ass_path).replace("\\", "/").replace(":", "\\:")
        fdir_arg = str(fonts_dir()).replace("\\", "/").replace(":", "\\:")
        vf = f"subtitles=filename='{ass_arg}':fontsdir='{fdir_arg}'"

        ffmpeg = _ffmpeg_bin()
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(src_frame),
            "-vf", vf,
            "-frames:v", "1",
            str(out_png),
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, creationflags=_creationflags(),
            timeout=120,
        )
        if r.returncode != 0 or not out_png.exists():
            raise RuntimeError(
                f"ffmpeg libass render failed: {(r.stderr or '')[-500:]}"
            )
        return out_png.read_bytes()
    finally:
        # Always clean the per-call workdir (we keep the source-frame cache).
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass
