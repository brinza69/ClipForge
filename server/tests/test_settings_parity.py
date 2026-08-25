"""The frontend and the backend must agree on what a setting is called.

`_normalise_settings` keeps only keys already present in `_default_settings()`.
A key the source form posts and that dict does not know about is discarded in
SILENCE — the project is created, the run proceeds, and the feature simply does
nothing. Every unit test of that feature stays green, because the value never
reaches the code under test.

This happened twice on 2026-08-17, hours apart: `auto_export` and then
`vision_review`. Both were found by running the feature and noticing the setting
come back as `None`, which is an expensive way to learn a name is misspelled.

So the two dictionaries are compared directly. The test reads the TypeScript
source rather than a generated artifact, because a generator is one more thing
that can be stale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TS = Path(__file__).resolve().parents[2] / "src" / "types" / "clipper.ts"

# Keys the frontend keeps for itself. `fps` is sent but the backend stores it
# under the same name, so it is not here; anything that IS here needs a reason.
_FRONTEND_ONLY: set[str] = set()

# Keys the backend accepts that the form does not offer. These are real — they
# are set per project through the API or left at their config default — so the
# test asserts the direction that matters (nothing posted is dropped) and only
# reports these.
_BACKEND_ONLY = {"vision_model"}


def _typescript_defaults() -> dict[str, object]:
    """`DEFAULT_SETTINGS` from src/types/clipper.ts, keys and values."""
    text = _TS.read_text(encoding="utf-8")
    match = re.search(r"DEFAULT_SETTINGS[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert match, "DEFAULT_SETTINGS not found in src/types/clipper.ts"
    body = match.group(1)
    # Strip comments so a commented-out key does not read as a real one.
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)

    out: dict[str, object] = {}
    for key, raw in re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*:\s*([^,\n]+),", body, re.M):
        value = raw.strip()
        if value in ("true", "false"):
            out[key] = value == "true"
        elif re.fullmatch(r"-?\d+(\.\d+)?", value):
            out[key] = float(value)
        else:
            out[key] = value.strip('"').strip("'")
    return out


def _typescript_default_settings() -> set[str]:
    return set(_typescript_defaults())


def test_every_setting_the_form_posts_survives_the_api():
    """The direction that costs something. A key the form sends and the backend
    does not know is dropped without a word."""
    from routers.clipper import _default_settings

    frontend = _typescript_default_settings() - _FRONTEND_ONLY
    backend = set(_default_settings())
    dropped = sorted(frontend - backend)
    assert not dropped, (
        f"these would be discarded in silence by _normalise_settings: {dropped}. "
        f"Add them to _default_settings() in server/routers/clipper.py."
    )


def test_the_backend_offers_nothing_the_frontend_cannot_name():
    """The other direction is not a bug, but an unexplained extra key usually
    means one half was edited and the other forgotten."""
    from routers.clipper import _default_settings

    extra = sorted(set(_default_settings()) - _typescript_default_settings()
                   - _BACKEND_ONLY)
    assert not extra, (
        f"the backend accepts {extra} and ClipperSettings in "
        f"src/types/clipper.ts does not declare them. Either add them there or "
        f"list them in _BACKEND_ONLY with a reason."
    )


def test_the_two_sides_agree_on_the_VALUES_too():
    """The half that actually shipped wrong.

    The form posts `DEFAULT_SETTINGS` WHOLESALE, so a value there that disagrees
    with the backend's default silently wins — the config default never applies
    to anything created through the UI. `trim_silence` was `true` in TypeScript
    against `clipper_trim_silence = False`, so every project made in the browser
    had dead-air trimming on while three documents said it ships off.

    Matching keys is not enough. A name can line up while the value does not,
    and that failure is quieter than a dropped key: the feature runs, it just
    runs in a mode nobody chose.
    """
    from routers.clipper import _default_settings

    frontend = _typescript_defaults()
    backend = _default_settings()
    disagree = []
    for key in sorted(set(frontend) & set(backend)):
        a, b = frontend[key], backend[key]
        if isinstance(a, float) and isinstance(b, (int, float)):
            if float(a) != float(b):
                disagree.append(f"{key}: ts={a} backend={b}")
        elif str(a) != str(b):
            disagree.append(f"{key}: ts={a!r} backend={b!r}")

    assert not disagree, (
        "DEFAULT_SETTINGS is posted wholesale, so these override the backend "
        f"default for every project created in the browser: {disagree}"
    )


def test_the_parser_reads_the_real_file():
    """A regex over source is only worth having if it fails when it stops
    matching, rather than quietly returning an empty set and passing."""
    keys = _typescript_default_settings()
    assert len(keys) > 10
    for known in ("clip_count", "min_clip_s", "auto_export", "vision_review"):
        assert known in keys, f"{known} missing — the parser has drifted"


@pytest.mark.parametrize("key", ["auto_export", "vision_review"])
def test_the_two_that_were_actually_dropped(key: str):
    """Named rather than left to the set comparison, so the failure message
    says which mistake is being repeated."""
    from routers.clipper import _default_settings

    assert key in _default_settings()
    assert key in _typescript_default_settings()
