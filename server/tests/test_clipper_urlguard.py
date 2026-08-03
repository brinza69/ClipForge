"""
Tests for the AI Stream Clipper's SSRF guard.

Every case is synchronous and offline: hostname cases monkeypatch
socket.getaddrinfo so resolution is deterministic, and IP-literal cases never
touch DNS at all. The metadata-endpoint and DNS-rebinding cases are the two
that actually matter — they are the attacks this guard exists to stop.
"""

import socket

import pytest

from services.clipper.urlguard import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    MAX_REDIRECTS,
    UrlRejected,
    check_url,
    is_blocked_ip,
)


def _addrinfo(*addresses: str, port: int = 443):
    """Build a getaddrinfo-shaped answer (v6 sockaddrs carry flow/scope)."""
    out = []
    for addr in addresses:
        if ":" in addr:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, port, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)))
    return out


def _resolves_to(monkeypatch, *addresses: str):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: _addrinfo(*addresses)
    )


def _no_dns(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("getaddrinfo must not be called for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)


# ── is_blocked_ip ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ip",
    [
        "169.254.169.254",  # cloud metadata — the headline target
        "10.0.0.1",
        "127.0.0.1",
        "0.0.0.0",
        "192.168.1.1",
        "172.16.0.5",
        "100.64.0.1",       # CGNAT: is_private is False on 3.13, must still block
        "255.255.255.255",
        "224.0.0.1",
        "::1",
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "::ffff:127.0.0.1",
        "fe80::1",
        "fc00::1",
        "fec0::1",          # site-local: reports is_global True, must still block
        "2002:0a00:0001::1",  # 6to4 wrapper around 10.0.0.1
        "::",
        "not-an-ip",        # unparseable fails closed
        "",
    ],
)
def test_blocked_addresses(ip):
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize(
    "ip", ["8.8.8.8", "1.1.1.1", "142.250.185.78", "2001:4860:4860::8888"]
)
def test_public_addresses_allowed(ip):
    assert is_blocked_ip(ip) is False


# ── check_url: private / internal targets ────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/admin",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://100.64.0.1/",
    ],
)
def test_private_targets_rejected(url, monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url(url)
    assert exc.value.code == "private_address"
    assert exc.value.suggestion


def test_hostname_resolving_to_private_is_rejected(monkeypatch):
    _resolves_to(monkeypatch, "192.168.1.10")
    with pytest.raises(UrlRejected) as exc:
        check_url("https://nas.example.com/video.mp4")
    assert exc.value.code == "private_address"


def test_dns_rebinding_mixed_answer_is_rejected(monkeypatch):
    """One public + one private address must reject: the socket may pick either."""
    _resolves_to(monkeypatch, "142.250.185.78", "127.0.0.1")
    with pytest.raises(UrlRejected) as exc:
        check_url("https://evil.example.com/x.mp4")
    assert exc.value.code == "private_address"
    assert "127.0.0.1" in exc.value.message


@pytest.mark.parametrize(
    "url",
    [
        "http://printer.local/stream.mp4",
        "https://git.internal/x.mp4",
        "http://localhost/video.mp4",
        "http://api.corp.internal./x.mp4",  # trailing dot must not dodge the suffix
    ],
)
def test_internal_hostnames_rejected(url, monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url(url)
    assert exc.value.code == "internal_hostname"


# ── check_url: happy path ────────────────────────────────────────────────────

def test_public_youtube_url_passes(monkeypatch):
    _resolves_to(monkeypatch, "142.250.185.78")
    result = check_url("https://www.youtube.com/watch?v=x")
    assert result == {
        "url": "https://www.youtube.com/watch?v=x",
        "host": "www.youtube.com",
        "scheme": "https",
        "port": 443,
        "source_type": "youtube",
        "addresses": ["142.250.185.78"],
    }


def test_plain_http_defaults_to_port_80(monkeypatch):
    _resolves_to(monkeypatch, "142.250.185.78")
    result = check_url("  http://example.com/clip.mp4  ")
    assert result["port"] == 80
    assert result["url"] == "http://example.com/clip.mp4"
    assert result["source_type"] == "direct"


# ── check_url: policy rejections ─────────────────────────────────────────────

def test_credentials_in_url_rejected(monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url("https://user:pass@www.youtube.com/watch?v=x")
    assert exc.value.code == "credentials_in_url"


def test_ftp_scheme_rejected(monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url("ftp://example.com/video.mp4")
    assert exc.value.code == "bad_scheme"


@pytest.mark.parametrize(
    "url", ["https://example.com:8080/x.mp4", "http://example.com:22/x", "http://example.com:0/x"]
)
def test_non_web_ports_rejected(url, monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url(url)
    assert exc.value.code == "blocked_port"


def test_unresolvable_host(monkeypatch):
    def _fail(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(UrlRejected) as exc:
        check_url("https://no-such-host.example/x.mp4")
    assert exc.value.code == "unresolvable_host"


def test_empty_url():
    for value in ("", "   ", None):
        with pytest.raises(UrlRejected) as exc:
            check_url(value)
        assert exc.value.code == "empty_url"


def test_url_too_long(monkeypatch):
    _no_dns(monkeypatch)
    long_url = "https://example.com/?q=" + ("a" * 2100)
    with pytest.raises(UrlRejected) as exc:
        check_url(long_url)
    assert exc.value.code == "url_too_long"


def test_missing_host_rejected(monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url("https:///video.mp4")
    assert exc.value.code == "unresolvable_host"


# ── allow_private ────────────────────────────────────────────────────────────

def test_allow_private_permits_lan_address(monkeypatch):
    _no_dns(monkeypatch)
    result = check_url("http://10.0.0.1/video.mp4", allow_private=True)
    assert result["addresses"] == ["10.0.0.1"]
    assert result["host"] == "10.0.0.1"
    assert result["port"] == 80


@pytest.mark.parametrize(
    "url,code",
    [
        ("ftp://10.0.0.1/x.mp4", "bad_scheme"),
        ("http://10.0.0.1:8080/x.mp4", "blocked_port"),
        ("http://user:pw@10.0.0.1/x.mp4", "credentials_in_url"),
        ("http://nas.local/x.mp4", "internal_hostname"),
    ],
)
def test_allow_private_bypasses_only_the_address_check(url, code, monkeypatch):
    _no_dns(monkeypatch)
    with pytest.raises(UrlRejected) as exc:
        check_url(url, allow_private=True)
    assert exc.value.code == code


# ── contract surface ─────────────────────────────────────────────────────────

def test_constants_and_exception_shape():
    assert ALLOWED_SCHEMES == {"http", "https"}
    assert ALLOWED_PORTS == {80, 443}
    assert MAX_REDIRECTS == 3
    err = UrlRejected("bad_scheme", "nope", "try https")
    assert (err.code, err.message, err.suggestion) == ("bad_scheme", "nope", "try https")
    assert str(err) == "nope"
    assert UrlRejected("x", "y").suggestion == ""
