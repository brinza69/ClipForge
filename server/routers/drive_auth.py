"""
ClipForge — Google Drive OAuth Router

Connect a personal Google account so uploads use the user's own 15 GB quota
(service accounts have 0 GB and fail on personal My Drive).

  GET    /api/drive-auth/status      — connected? which email? client configured?
  POST   /api/drive-auth/connect     — run the one-time browser consent flow
  POST   /api/drive-auth/disconnect  — forget the saved token
  POST   /api/drive-auth/client      — upload OAuth client JSON (Desktop type)
  DELETE /api/drive-auth/client      — remove the client + token (forces re-setup)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from services import drive_oauth

logger = logging.getLogger("clipforge.routers.drive_auth")
router = APIRouter(prefix="/api/drive-auth", tags=["drive-auth"])


@router.get("/status")
async def status():
    return drive_oauth.status()


@router.post("/connect")
async def connect():
    """Begin the OAuth consent flow. Returns an `auth_url` the UI opens in a
    new tab; a background loopback server captures the redirect and saves the
    token. The UI then polls /status until connected."""
    try:
        auth_url = drive_oauth.start_consent()
        return {"auth_url": auth_url}
    except Exception as e:
        logger.error(f"Drive OAuth connect failed: {e}")
        raise HTTPException(400, str(e))


@router.post("/disconnect")
async def disconnect():
    drive_oauth.disconnect()
    return {"connected": False}


# ── OAuth client JSON management ──────────────────────────────────────────────

def _client_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "drive_oauth_client.json"


def _validate_oauth_client(doc: dict) -> str:
    """Make sure the JSON looks like a Desktop OAuth client. Returns the type
    label ("installed" or "web") for the UI. Raises ValueError otherwise."""
    if not isinstance(doc, dict):
        raise ValueError("Not a JSON object.")
    for kind in ("installed", "web"):
        block = doc.get(kind)
        if isinstance(block, dict) and block.get("client_id") and block.get("client_secret"):
            if kind == "web":
                # The OAuth loopback flow used by ClipForge needs a Desktop
                # client; the "web" client requires a registered redirect URI.
                raise ValueError(
                    "This looks like a 'Web application' OAuth client. "
                    "ClipForge needs a 'Desktop app' OAuth Client ID — "
                    "create one of those in Google Cloud Console."
                )
            return kind
    raise ValueError(
        "Doesn't look like a Google OAuth client JSON. "
        "Expected an object with an 'installed' block containing 'client_id' "
        "and 'client_secret'."
    )


class ClientJsonRequest(BaseModel):
    """Inline-JSON alternative to multipart upload (for pasted client config)."""
    content: str


# Real Google OAuth client JSONs are ~600B–1.5KB. Anything significantly
# larger is either the wrong file or an attempt to waste memory by parsing
# a multi-MB blob as JSON. Capped at 50KB to leave generous headroom
# without making the parser do work it doesn't need to.
_MAX_CLIENT_JSON_BYTES = 50 * 1024


@router.post("/client")
async def upload_client(
    file: UploadFile | None = File(default=None),
    body: ClientJsonRequest | None = None,
):
    """Accept the OAuth client JSON as either a multipart file or a JSON
    body with `{content: "<raw json>"}`. Validates that it's a Desktop client,
    then writes to data/drive_oauth_client.json. A stale saved token is
    cleared so the next Connect re-consents with the new client."""
    raw: str | None = None
    if file is not None:
        # Probe the content length header first if the client sent one — saves
        # a wasted .read() on obviously oversized payloads.
        try:
            advertised = int(file.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            advertised = 0
        if advertised and advertised > _MAX_CLIENT_JSON_BYTES:
            raise HTTPException(
                413,
                f"Uploaded file is {advertised} bytes; OAuth client JSON should be under "
                f"{_MAX_CLIENT_JSON_BYTES // 1024}KB. Picked the wrong file?",
            )
        try:
            raw_bytes = await file.read(_MAX_CLIENT_JSON_BYTES + 1)
        except Exception as e:
            raise HTTPException(400, f"Could not read uploaded file: {e}")
        if len(raw_bytes) > _MAX_CLIENT_JSON_BYTES:
            raise HTTPException(
                413,
                f"Uploaded file exceeds {_MAX_CLIENT_JSON_BYTES // 1024}KB; "
                f"OAuth client JSON is normally under 2KB.",
            )
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise HTTPException(400, f"File is not valid UTF-8 text: {e}")
    elif body and body.content:
        if len(body.content) > _MAX_CLIENT_JSON_BYTES:
            raise HTTPException(
                413,
                f"Pasted content exceeds {_MAX_CLIENT_JSON_BYTES // 1024}KB.",
            )
        raw = body.content
    if not raw or not raw.strip():
        raise HTTPException(400, "Provide either an uploaded file or a JSON body with `content`.")

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON: {e}")

    try:
        _validate_oauth_client(doc)
    except ValueError as e:
        raise HTTPException(400, str(e))

    cp = _client_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    # Any token saved against the previous client is now stale.
    drive_oauth.disconnect()

    logger.info(f"Drive OAuth client JSON saved → {cp.name}")
    return {"ok": True, "client_configured": True, "connected": False}


@router.delete("/client")
async def delete_client():
    cp = _client_path()
    existed = cp.exists()
    if existed:
        try:
            cp.unlink()
        except Exception as e:
            raise HTTPException(500, f"Could not delete client JSON: {e}")
    drive_oauth.disconnect()  # also forget any stored token
    return {"ok": True, "removed_client": existed, "connected": False}
