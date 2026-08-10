"""
ClipForge — SSRF / URL-policy guard for the AI Stream Clipper.

Why this module exists: the backend binds 0.0.0.0:8420 with no authentication,
and the clipper's ingest step fetches whatever URL the caller pastes. Without a
guard in front of that fetch, the server is an open proxy — anyone who can
reach the port (a flatmate on the same wifi, a malicious page doing a
cross-origin POST, a compromised browser extension) can make it dial the user's
router admin panel, a NAS, a printer, an internal git server, or the cloud
metadata endpoint at 169.254.169.254, and read the response back through the
job's error text and artifacts. The guard is the whole access-control story
here, so it fails CLOSED: anything it cannot confidently classify as a public
internet address is rejected.

Two rules that matter more than the individual CIDR list:

  * Resolve first, then judge the ADDRESSES — never the hostname. A name is
    attacker-controlled and can point anywhere; `evil.test` resolving to
    127.0.0.1 is the classic bypass. Obfuscated literals (`http://2130706433/`,
    `http://0177.0.0.1/`) also collapse to a real address only at resolution.
  * EVERY address in the answer must pass, not just the first. A record set of
    one public and one private address is a DNS-rebinding attack: the guard
    would sample the public one and the fetch would connect to the other.

The guard cannot follow the socket, so it also cannot see redirects. A caller
that follows redirects MUST re-run check_url() on every hop and stop after
MAX_REDIRECTS — a 302 to http://169.254.169.254/ is the easiest bypass of all.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("clipforge.clipper.urlguard")

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 3

MAX_URL_LENGTH = 2000

# Hostname suffixes that only ever mean "something on this machine or this LAN":
# mDNS (.local), Windows/AD split-horizon zones (.internal) and the reserved
# loopback name. Blocked before resolution because on a corporate DNS they
# resolve to real internal addresses.
INTERNAL_SUFFIXES = (".local", ".internal", ".localhost")
INTERNAL_NAMES = frozenset({"local", "internal", "localhost"})

# Explicit ranges rather than leaning on ipaddress's flags alone: those flags
# have churned across CPython releases (3.13 flipped 100.64.0.0/10 from
# is_private=True to False, and fec0::/10 reports is_global=True to this day),
# and a security check should not silently loosen on an interpreter upgrade.
_BLOCKED_V4_NETS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",          # "this network" — 0.0.0.0 hits localhost on Linux
        "10.0.0.0/8",
        "100.64.0.0/10",      # CGNAT / Tailscale
        "127.0.0.0/8",
        "169.254.0.0/16",     # link-local, incl. 169.254.169.254 cloud metadata
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",      # benchmarking
        "224.0.0.0/4",        # multicast
        "240.0.0.0/4",        # reserved, incl. 255.255.255.255
    )
)
_BLOCKED_V6_NETS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "::/128",             # unspecified
        "::1/128",            # loopback
        "fc00::/7",           # unique local
        "fe80::/10",          # link-local
        "fec0::/10",          # deprecated site-local, still routed on old LANs
        "ff00::/8",           # multicast
        "2001:db8::/32",      # documentation
    )
)


class UrlRejected(Exception):
    """A URL failed the policy check. Carries a machine-readable code."""

    def __init__(self, code: str, message: str, suggestion: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "suggestion": self.suggestion}


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[
    ipaddress.IPv4Address | ipaddress.IPv6Address
]:
    """Expand an IPv6 address into every IPv4 address it can actually reach.

    ::ffff:10.0.0.1, 2002:0a00:0001:: (6to4) and Teredo all carry an IPv4
    address inside them, and a socket opened to any of those forms lands on the
    embedded v4 host. Judging the v6 form alone would wave 10.0.0.1 straight
    through, so the embedded addresses are checked instead of (not as well as)
    the wrapper.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return [ip]
    inner: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    if ip.ipv4_mapped:
        inner.append(ip.ipv4_mapped)
    if ip.sixtofour:
        inner.append(ip.sixtofour)
    if ip.teredo:
        inner.extend(ip.teredo)  # (server, client) — both are dialable
    return inner or [ip]


def is_blocked_ip(ip: str) -> bool:
    """True if `ip` is anything other than a public, globally routable address.

    Unparseable input counts as blocked: a value that is not an address is not
    something this code should be handing to a socket.
    """
    try:
        parsed = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return True

    for addr in _unwrap(parsed):
        nets = _BLOCKED_V6_NETS if addr.version == 6 else _BLOCKED_V4_NETS
        if any(addr in net for net in nets):
            return True
        if (
            addr.is_unspecified
            or addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or getattr(addr, "is_site_local", False)
            or not addr.is_global
        ):
            return True
    return False


def _resolve(host: str, port: int) -> list[str]:
    """Every A/AAAA address `host` currently answers with, order preserved.

    An IP literal short-circuits DNS: getaddrinfo would just echo it back, and
    skipping the call keeps the check deterministic (and offline-safe) for the
    literal case.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return [str(literal)]

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise UrlRejected(
            "unresolvable_host",
            f"Could not resolve '{host}'.",
            "Check the address for typos, or that the site is reachable from this machine.",
        ) from exc

    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        if not sockaddr:
            continue
        addr = str(sockaddr[0]).split("%", 1)[0]  # drop any %eth0 zone id
        if addr and addr not in addresses:
            addresses.append(addr)

    if not addresses:
        raise UrlRejected(
            "unresolvable_host",
            f"'{host}' did not resolve to any address.",
            "Check the address for typos, or that the site is reachable from this machine.",
        )
    return addresses


def check_url(url: str, *, allow_private: bool = False) -> dict:
    """Validate a user-supplied URL before anything fetches it.

    Returns {'url', 'host', 'scheme', 'port', 'source_type', 'addresses'} for a
    URL that is safe to fetch, or raises UrlRejected with one of these codes:
    empty_url, url_too_long, bad_scheme, credentials_in_url, blocked_port,
    internal_hostname, unresolvable_host, private_address.

    `allow_private=True` skips ONLY the address-range check (for a deliberately
    configured LAN source); scheme, credential, port and hostname rules still
    apply.
    """
    raw = (url or "").strip() if isinstance(url, str) else ""
    if not raw:
        raise UrlRejected(
            "empty_url",
            "No URL was provided.",
            "Paste the video, VOD or stream link you want to clip.",
        )
    if len(raw) > MAX_URL_LENGTH:
        raise UrlRejected(
            "url_too_long",
            f"URL is {len(raw)} characters; the limit is {MAX_URL_LENGTH}.",
            "Trim tracking parameters off the link and paste it again.",
        )

    try:
        parts = urlsplit(raw)
    except ValueError as exc:  # malformed IPv6 literal, bad port syntax, …
        raise UrlRejected(
            "unresolvable_host",
            "That URL could not be parsed.",
            "Copy the link straight from your browser's address bar.",
        ) from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlRejected(
            "bad_scheme",
            f"Scheme '{scheme or '(none)'}' is not supported; only http and https are.",
            "Paste an http:// or https:// link. Local files are uploaded, not linked.",
        )

    # `@` anywhere in the authority means credentials — checked on the raw
    # netloc as well as the parsed fields, because a malformed authority can
    # leave .username empty while the socket layer still sees a userinfo part.
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise UrlRejected(
            "credentials_in_url",
            "URLs with an embedded username or password are not accepted.",
            "Remove the 'user:pass@' part before the host.",
        )

    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise UrlRejected(
            "unresolvable_host",
            "That URL has no host.",
            "Copy the link straight from your browser's address bar.",
        )

    if host in INTERNAL_NAMES or host.endswith(INTERNAL_SUFFIXES):
        raise UrlRejected(
            "internal_hostname",
            f"'{host}' is an internal-only hostname.",
            "Only public internet URLs can be fetched. Upload the file instead.",
        )

    try:
        port = parts.port
    except ValueError as exc:  # port not an int, or out of 0-65535
        raise UrlRejected(
            "blocked_port",
            "That URL has an invalid port.",
            "Use a standard http (80) or https (443) URL.",
        ) from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    if port not in ALLOWED_PORTS:
        raise UrlRejected(
            "blocked_port",
            f"Port {port} is not allowed.",
            "Only the standard web ports 80 and 443 can be fetched.",
        )

    addresses = _resolve(host, port)

    if not allow_private:
        for addr in addresses:
            if is_blocked_ip(addr):
                logger.warning("urlguard blocked %s -> %s", host, addr)
                raise UrlRejected(
                    "private_address",
                    f"'{host}' resolves to {addr}, which is not a public internet address.",
                    "Only public URLs can be fetched. Upload the file instead.",
                )

    # Imported here, not at module scope: services.downloader pulls in yt_dlp,
    # and this guard must stay importable (and therefore testable) in a minimal
    # environment. Nothing above this line depends on it.
    from services.downloader import detect_source_type

    return {
        "url": raw,
        "host": host,
        "scheme": scheme,
        "port": port,
        "source_type": detect_source_type(raw),
        "addresses": addresses,
    }
