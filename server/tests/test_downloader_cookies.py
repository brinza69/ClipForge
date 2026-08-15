"""
yt-dlp authentication: the cookie options, and whether they are actually read.

Pure — no network, no yt-dlp execution. `YoutubeDL` is replaced with a spy that
records the options it was constructed with and then refuses to run, because
the failure worth guarding against is not a wrong option value, it is an option
that is computed and never handed to yt-dlp at all. Metadata extraction and
download build their option dicts separately, so both are checked.
"""

from __future__ import annotations

import pytest

from config import Settings
from services import downloader


# ── what the setting resolves to ─────────────────────────────────────────────


def test_no_cookies_configured_changes_nothing():
    """The default must leave a public URL behaving exactly as before."""
    assert Settings().ytdlp_cookie_opts == {}


def test_a_cookie_file_is_passed_through(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    opts = Settings(ytdlp_cookies_file=str(jar)).ytdlp_cookie_opts
    assert opts == {"cookiefile": str(jar)}


def test_a_cookie_file_that_is_not_there_is_ignored(tmp_path):
    """Handing yt-dlp a missing path turns a clear failure into a confusing one."""
    missing = tmp_path / "nope.txt"
    assert Settings(ytdlp_cookies_file=str(missing)).ytdlp_cookie_opts == {}


def test_a_browser_name_becomes_the_tuple_yt_dlp_wants():
    opts = Settings(ytdlp_cookies_from_browser="Chrome").ytdlp_cookie_opts
    assert opts == {"cookiesfrombrowser": ("chrome", None, None, None)}


def test_a_browser_profile_is_kept():
    opts = Settings(ytdlp_cookies_from_browser="chrome:Profile 1").ytdlp_cookie_opts
    assert opts == {"cookiesfrombrowser": ("chrome", "Profile 1", None, None)}


def test_an_explicit_file_wins_over_a_browser(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("x", encoding="utf-8")
    opts = Settings(ytdlp_cookies_file=str(jar),
                    ytdlp_cookies_from_browser="firefox").ytdlp_cookie_opts
    assert opts == {"cookiefile": str(jar)}


# ── whether anything reads it ────────────────────────────────────────────────


class _Spy:
    """Records the options it was built with, then declines to do anything."""

    seen: dict = {}

    def __init__(self, opts):
        type(self).seen = dict(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        raise RuntimeError("spy: no network in tests")


@pytest.fixture
def spy(monkeypatch, tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("CLIPFORGE_YTDLP_COOKIES_FILE", str(jar))

    import config
    monkeypatch.setattr(config, "settings", Settings(), raising=True)
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _Spy)
    _Spy.seen = {}
    return str(jar)


def test_metadata_extraction_sends_the_cookies(spy):
    """A source that authenticates only at download time fails validation first."""
    with pytest.raises(RuntimeError):
        downloader._extract_info_sync("https://example.com/watch?v=x")
    assert _Spy.seen.get("cookiefile") == spy


async def test_the_download_sends_the_cookies(spy, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(RuntimeError):
        await downloader.download_video("https://example.com/watch?v=x", out)
    assert _Spy.seen.get("cookiefile") == spy
