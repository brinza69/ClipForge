"""
ClipForge — Google Drive upload helper

Shared, file-agnostic Drive upload used by both the clip exporter and the
parallel pipeline. Requires a service-account key at
data/drive_credentials.json (or GOOGLE_APPLICATION_CREDENTIALS) plus the
google-api-python-client + google-auth packages.

Every function is synchronous — call from a worker via run_in_executor.
Failures never raise; they return a structured status dict so callers can
surface the real blocker (missing creds, missing packages, API error)
without pretending the upload succeeded.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("clipforge.drive")


def extract_folder_id(link: str) -> Optional[str]:
    """Parse a Google Drive folder URL or bare ID. Returns the folder ID."""
    s = (link or "").strip()
    if not s:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{25,64}", s):
        return s
    m = re.search(r"/folders/([A-Za-z0-9_-]{25,64})", s)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]{25,64})", s)
    if m:
        return m.group(1)
    return None


def _creds_path() -> Optional[str]:
    from config import settings
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env and Path(env).exists():
        return env
    local = Path(settings.data_dir) / "drive_credentials.json"
    return str(local) if local.exists() else None


def _resolve_credentials():
    """Pick the upload identity. Prefers the user's OAuth account (files use
    the user's quota) over the service account (0 GB quota — fails on My Drive,
    only useful for Workspace Shared Drives).

    Returns (creds, kind, error). `kind` is "oauth" or "service_account";
    `error` is a reason string when no usable creds were found.
    """
    # 1) User OAuth — the path that actually works for personal accounts.
    try:
        from services.drive_oauth import get_user_credentials
        user_creds = get_user_credentials()
        if user_creds:
            return user_creds, "oauth", None
    except ImportError:
        pass

    # 2) Service account fallback (Workspace Shared Drives).
    creds_path = _creds_path()
    if not creds_path:
        return None, None, (
            "Google Drive is not connected. Open Parallel Processing and click "
            "'Connect Google Drive', or place a service-account key at "
            "data/drive_credentials.json."
        )
    try:
        from google.oauth2 import service_account  # type: ignore
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        return creds, "service_account", None
    except ImportError:
        return None, None, "google-api-python-client / google-auth not installed."
    except Exception as e:
        return None, None, f"Could not load service-account key: {str(e)[:200]}"


def upload_files(folder_link: str, files: List[Path]) -> dict:
    """Upload the given files to the Drive folder. Returns a status dict:

        {"status": "uploaded",  "folder_id": ..., "via": "oauth", "uploaded": [names]}
        {"status": "no_files",  "folder_id": ...}
        {"status": "blocked_missing_credentials", "folder_id": ..., "reason": ...}
        {"status": "failed",    "folder_id": ..., "reason": ...}
        {"status": "invalid_link", "reason": ...}
    """
    folder_id = extract_folder_id(folder_link)
    if not folder_id:
        return {"status": "invalid_link", "reason": "Could not parse a Drive folder ID."}

    existing = [f for f in files if f and Path(f).exists()]
    if not existing:
        return {"status": "no_files", "folder_id": folder_id}

    creds, kind, error = _resolve_credentials()
    if not creds:
        return {"status": "blocked_missing_credentials", "folder_id": folder_id, "reason": error}

    try:
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        uploaded: List[str] = []
        for fp in existing:
            meta = {"name": fp.name, "parents": [folder_id]}
            media = MediaFileUpload(str(fp), mimetype="video/mp4", resumable=True)
            created = service.files().create(
                body=meta, media_body=media, fields="id,name", supportsAllDrives=True
            ).execute()
            uploaded.append(created.get("name", fp.name))
            logger.info(f"Uploaded {fp.name} to Drive folder {folder_id} (via {kind})")
        return {"status": "uploaded", "folder_id": folder_id, "via": kind, "uploaded": uploaded}
    except Exception as e:
        logger.error(f"Drive upload failed: {e}")
        return {"status": "failed", "folder_id": folder_id, "reason": f"Drive API call failed: {str(e)[:300]}"}
