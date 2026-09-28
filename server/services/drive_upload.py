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


# A filename that identifies ONE sheet row: "<NR>.mp4", "<NR>_p2.mp4",
# "<NR>_part1of3.mp4". Only these are safe to de-duplicate by name — generic
# names like "video_final.mp4" or a title-based stem are reused by completely
# different videos (the Drive folders hold 13 distinct "video_final.mp4"), so
# skipping one of those would LOSE a video instead of preventing a duplicate.
_ROW_NAME_RE = re.compile(r"^\d+(?:_p\d+|_part\d+of\d+)?\.mp4$", re.IGNORECASE)


def list_folder_files(folder_link: str) -> dict:
    """List the (non-trashed) files already in a Drive folder.

        {"status": "ok", "folder_id": ..., "files": [{"id","name","link","download_url"}]}
        {"status": "failed"/"invalid_link"/"blocked_missing_credentials", "reason": ...}

    Used to avoid re-doing work that is already on Drive: the dispatcher skips
    roles whose video exists, and upload_files() refuses to create a same-named
    duplicate.
    """
    folder_id = extract_folder_id(folder_link)
    if not folder_id:
        return {"status": "invalid_link", "reason": "Could not parse a Drive folder ID."}
    creds, kind, error = _resolve_credentials()
    if not creds:
        return {"status": "blocked_missing_credentials", "folder_id": folder_id, "reason": error}
    try:
        from googleapiclient.discovery import build  # type: ignore

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        out: List[dict] = []
        page = None
        while True:
            resp = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id,name,webViewLink,size,createdTime)",
                pageSize=1000, pageToken=page,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                fid = f.get("id", "")
                out.append({
                    "id": fid,
                    "name": f.get("name", ""),
                    "size": int(f.get("size") or 0),
                    # cand a fost randat fisierul; NR-ul nu spune nimic despre asta,
                    # iar ordinea de postare ceruta pe canale e dupa data randarii
                    "created": (f.get("createdTime") or "")[:19],
                    "link": f.get("webViewLink") or (f"https://drive.google.com/file/d/{fid}/view" if fid else ""),
                    # CRITICAL: the drive.google.com/uc?export=download form serves
                    # Google's virus-scan HTML page (Content-Type: text/html) for
                    # files over ~100 MB, so anything fetching it unauthenticated —
                    # Buffer, above all — gets HTML instead of video and the post
                    # fails. This form returns real video/mp4 at every size.
                    "download_url": (f"https://drive.usercontent.google.com/download"
                                     f"?id={fid}&export=download&confirm=t") if fid else "",
                })
            page = resp.get("nextPageToken")
            if not page:
                break
        return {"status": "ok", "folder_id": folder_id, "via": kind, "files": out}
    except Exception as e:
        logger.error(f"Drive list failed: {e}")
        return {"status": "failed", "folder_id": folder_id, "reason": f"Drive API call failed: {str(e)[:300]}"}


def upload_files(folder_link: str, files: List[Path]) -> dict:
    """Upload the given files to the Drive folder. Returns a status dict:

        {"status": "uploaded",  "folder_id": ..., "via": "oauth", "uploaded": [names],
         "skipped": [names already in the folder], "files": [{"id","name","link","download_url"}, ...]}
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
        # When set (default on), each uploaded file is made readable by anyone
        # with the link so downstream posting tools (n8n + the social poster)
        # can pull the MP4 by URL. Set CLIPFORGE_DRIVE_PUBLIC=0 to keep files
        # private (n8n must then download them via the Google Drive node/OAuth).
        make_public = os.environ.get("CLIPFORGE_DRIVE_PUBLIC", "1") != "0"
        # Drive happily stores several files with the SAME name in one folder,
        # so any re-run (crash-resume, re-submitted row, restarted watchdog)
        # used to pile up duplicate videos. Skip a file whose name is already
        # there and return the EXISTING file's links instead, so the sheet
        # still gets a working URL. Set CLIPFORGE_DRIVE_DEDUPE=0 to re-upload
        # anyway (e.g. deliberately replacing a bad render — delete the old
        # file on Drive first, or you get two again).
        dedupe = os.environ.get("CLIPFORGE_DRIVE_DEDUPE", "1") != "0"
        present: dict = {}
        if dedupe and any(_ROW_NAME_RE.match(f.name) for f in existing):
            listing = list_folder_files(folder_id)
            if listing.get("status") == "ok":
                for f in listing["files"]:
                    if _ROW_NAME_RE.match(f["name"] or ""):
                        present.setdefault(f["name"], f)
            else:
                logger.warning(
                    f"dedupe check skipped ({listing.get('status')}): {str(listing.get('reason'))[:120]}"
                )
        uploaded: List[str] = []
        skipped: List[str] = []
        files: List[dict] = []
        for fp in existing:
            dup = present.get(fp.name)
            if dup:
                skipped.append(fp.name)
                files.append(dict(dup))
                logger.info(f"{fp.name} already in Drive folder {folder_id} — not re-uploaded")
                continue
            meta = {"name": fp.name, "parents": [folder_id]}
            media = MediaFileUpload(str(fp), mimetype="video/mp4", resumable=True)
            created = service.files().create(
                body=meta, media_body=media,
                fields="id,name,webViewLink", supportsAllDrives=True,
            ).execute()
            fid = created.get("id", "")
            name = created.get("name", fp.name)
            uploaded.append(name)
            if fid and make_public:
                try:
                    service.permissions().create(
                        fileId=fid, body={"role": "reader", "type": "anyone"},
                        supportsAllDrives=True,
                    ).execute()
                except Exception as pe:
                    logger.warning(f"anyone-with-link grant failed for {name}: {str(pe)[:120]}")
            files.append({
                "id": fid,
                "name": name,
                "link": created.get("webViewLink") or (f"https://drive.google.com/file/d/{fid}/view" if fid else ""),
                # Direct-download URL — what a poster pulls the bytes from.
                "download_url": f"https://drive.google.com/uc?export=download&id={fid}" if fid else "",
            })
            logger.info(f"Uploaded {name} to Drive folder {folder_id} (via {kind})")
        return {"status": "uploaded", "folder_id": folder_id, "via": kind,
                "uploaded": uploaded, "skipped": skipped, "files": files}
    except Exception as e:
        logger.error(f"Drive upload failed: {e}")
        return {"status": "failed", "folder_id": folder_id, "reason": f"Drive API call failed: {str(e)[:300]}"}
