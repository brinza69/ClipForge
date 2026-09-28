"""The delivered clock shared by both renderers and the editor."""
from services.clipper.ffmpeg_tools import escape_filter_path


def append_captions(graph: str, label: str, ass_path: str | None) -> tuple[str, str]:
    """After select/setpts, still on the composed canvas including letterbox bars.

    `_write_ass` remaps events before writing them. Burning that ASS before
    removing pauses reads output times against input frames: the 2026-09-10
    encoded regression lost SECOND at output 1.7s in BOTH renderers.
    """
    if not ass_path:
        return graph, label
    from services.font_manager import fonts_dir

    burn = (f"subtitles=filename='{escape_filter_path(ass_path)}'"
            f":fontsdir='{escape_filter_path(fonts_dir())}'")
    return f"{graph};{label}{burn}[vcaptions]", "[vcaptions]"


def output_frames(graph: str, label: str, fps: int) -> tuple[str, str]:
    """One explicit frame grid before the encoder or editor selects a frame.

    Output -r and the fps filter do not select the same source frames when
    converting 60 to 30fps after a fractional source seek. Leaving the choice
    to each encoder made a PNG labelled 0.8s show a different source frame
    from MP4 frame 24. Apply this AFTER camera commands and caption burn.
    """
    return f"{graph};{label}fps={int(fps)}:start_time=0[vframes]", "[vframes]"
