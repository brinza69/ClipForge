"""
ClipForge — Light obfuscation for API keys at rest.

This is NOT real cryptography. It prevents casual disclosure if the user
accidentally syncs data/ somewhere visible (Dropbox, a screen-share, a
git slip). The XOR key is derived from a machine-specific identifier, so
the obfuscated blobs don't trivially survive a move to another machine.

Backward compatible: decrypt() returns plaintext (un-prefixed) values
unchanged, so existing config files keep working. On next save they get
the "enc:" prefix.
"""

from __future__ import annotations

import base64
import hashlib
import platform
from typing import Optional

_PREFIX = "enc:"


def _machine_secret() -> bytes:
    # Stable per-machine: hostname + a fixed app salt. Not a secret per se —
    # this is obfuscation, not encryption.
    parts = (platform.node() or "anon") + "::clipforge::v1"
    return hashlib.sha256(parts.encode()).digest()


def encrypt(s: str) -> str:
    if not s:
        return s
    data = s.encode("utf-8")
    key = _machine_secret()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return _PREFIX + base64.urlsafe_b64encode(out).decode("ascii")


def decrypt(s: Optional[str]) -> Optional[str]:
    """Return the plaintext. Un-prefixed (legacy plaintext) values pass
    through unchanged. Returns None if a prefixed blob can't be decoded."""
    if not s or not s.startswith(_PREFIX):
        return s  # plaintext / unset — backward compatible
    try:
        data = base64.urlsafe_b64decode(s[len(_PREFIX):].encode("ascii"))
        key = _machine_secret()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode("utf-8")
    except Exception:
        return None


def is_encrypted(s: Optional[str]) -> bool:
    return bool(s and s.startswith(_PREFIX))


def migrate_config_file(path, key_names) -> int:
    """One-shot: re-save any plaintext values at `key_names` in the JSON file
    at `path` as encrypted. Returns how many were upgraded. Safe to call on
    every startup — already-encrypted values are skipped."""
    import json
    import logging
    from pathlib import Path

    logger = logging.getLogger("clipforge.secret_storage")
    p = Path(path)
    if not p.exists():
        return 0
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return 0
    upgraded = 0
    for k in key_names:
        v = cfg.get(k)
        if isinstance(v, str) and v.strip() and not is_encrypted(v):
            cfg[k] = encrypt(v.strip())
            upgraded += 1
    if upgraded:
        try:
            p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            logger.info(f"encrypted {upgraded} plaintext key(s) in {p.name}")
        except Exception:
            logger.exception(f"could not write migrated config {p.name}")
            return 0
    return upgraded
