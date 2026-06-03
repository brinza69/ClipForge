"""
ClipForge — Google Drive OAuth Router

Connect a personal Google account so uploads use the user's own 15 GB quota
(service accounts have 0 GB and fail on personal My Drive).

  GET  /api/drive-auth/status      — connected? which email? client configured?
  POST /api/drive-auth/connect     — run the one-time browser consent flow
  POST /api/drive-auth/disconnect  — forget the saved token
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

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
