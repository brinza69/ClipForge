"""
ClipForge — Google Drive OAuth (user account)

Uploads to Drive AS THE USER (3-legged OAuth) instead of a service account.
Files end up owned by the user, so they use the user's 15 GB free quota —
unlike service accounts, which have 0 GB and hit storageQuotaExceeded.

Files:
  data/drive_oauth_client.json   ← OAuth Client ID (Desktop) downloaded from
                                    Google Cloud Console. Provided by the user.
  data/drive_oauth_token.json    ← saved user credentials (incl. refresh
                                    token) after the one-time consent flow.

The consent flow runs a loopback local server on a fixed port; Desktop OAuth
clients allow http://localhost loopback redirects on any port without
pre-registration.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clipforge.drive_oauth")

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    # Sheets read+write — needed for the "Parallel from Sheets" feature
    # (read source URL + number, write description). Older tokens that lack
    # this scope keep working for Drive uploads; Sheets calls will surface a
    # 403 with a "reconnect" hint until the user re-consents.
    "https://www.googleapis.com/auth/spreadsheets",
]
# NOTE: must NOT collide with a backend port. In the dual-GPU rig backend B
# binds 8421, so the OAuth loopback (which runs inside a backend process) failed
# to bind 8421 with WinError 10013. Use a dedicated free port instead.
_LOOPBACK_PORT = 8765


def _client_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "drive_oauth_client.json"


def _token_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / "drive_oauth_token.json"


def client_configured() -> bool:
    return _client_path().exists()


def get_user_credentials():
    """Load saved user credentials, refreshing if expired. Returns a
    Credentials object or None if not connected / unrecoverable."""
    tp = _token_path()
    if not tp.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tp.write_text(creds.to_json(), encoding="utf-8")
        if creds and creds.valid:
            return creds
    except Exception as e:
        logger.warning(f"could not load/refresh Drive OAuth token: {e}")
    return None


def _account_email(creds) -> Optional[str]:
    """Best-effort: fetch the connected account's email via Drive `about`."""
    try:
        from googleapiclient.discovery import build
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = service.about().get(fields="user(emailAddress)").execute()
        return (about.get("user") or {}).get("emailAddress")
    except Exception:
        return None


def status() -> dict:
    """UI status: is a client configured, and are we connected (+ which email)."""
    if not client_configured():
        return {"connected": False, "client_configured": False, "email": None}
    creds = get_user_credentials()
    if not creds:
        return {"connected": False, "client_configured": True, "email": None, "error": _last_error}
    return {"connected": True, "client_configured": True, "email": _account_email(creds)}


_REDIRECT_URI = f"http://localhost:{_LOOPBACK_PORT}/"
# Last consent-flow error captured by the background thread (for /status).
_last_error: Optional[str] = None
# The single live loopback server, so repeated "Connect" clicks don't leave a
# stale server (with a stale PKCE verifier) listening on the port — that
# caused "Invalid code verifier" when the browser used a newer auth URL.
_active_server = None


def last_error() -> Optional[str]:
    return _last_error


def start_consent() -> str:
    """Begin the OAuth consent flow. Returns the authorization URL for the
    user to open in their browser. A background loopback server catches the
    redirect, exchanges the code, and saves the token. Non-blocking.

    Desktop OAuth clients permit http://localhost loopback on any port, so no
    redirect URI needs to be pre-registered. Each call tears down any previous
    pending server so the newest attempt always owns the port.
    """
    global _last_error, _active_server
    if not client_configured():
        raise RuntimeError(
            f"OAuth client not configured. Place the Desktop OAuth Client ID JSON "
            f"at {_client_path()}."
        )

    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs
    from google_auth_oauthlib.flow import Flow

    # Tear down any previous pending server so its stale flow/verifier can't
    # intercept this attempt's redirect.
    if _active_server is not None:
        try:
            _active_server.server_close()
        except Exception:
            pass
        _active_server = None

    _last_error = None
    flow = Flow.from_client_secrets_file(
        str(_client_path()), scopes=SCOPES, redirect_uri=_REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            global _last_error
            qs = parse_qs(urlparse(self.path).query)
            code = (qs.get("code") or [None])[0]
            err = (qs.get("error") or [None])[0]
            body = b"<h2>ClipForge is connected. You can close this tab.</h2>"
            try:
                if err:
                    _last_error = f"Authorization denied: {err}"
                    body = b"<h2>ClipForge: authorization denied.</h2>"
                elif code:
                    flow.fetch_token(code=code)
                    _token_path().write_text(flow.credentials.to_json(), encoding="utf-8")
                    logger.info("Drive OAuth token saved")
                else:
                    _last_error = "No authorization code received."
                    body = b"<h2>ClipForge: authorization failed.</h2>"
            except Exception as e:
                _last_error = f"Token exchange failed: {str(e)[:200]}"
                body = b"<h2>ClipForge: authorization failed. Retry from the app.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence the default stderr logging
            return

    class _Server(HTTPServer):
        allow_reuse_address = True

    srv = _Server(("localhost", _LOOPBACK_PORT), _Handler)
    _active_server = srv

    def _serve():
        global _active_server
        try:
            srv.handle_request()  # serve exactly one request, then exit
        except Exception as e:
            global _last_error
            if _active_server is srv:  # ignore errors from a server we replaced
                _last_error = f"Local auth server error: {str(e)[:200]}"
        finally:
            try:
                srv.server_close()
            except Exception:
                pass
            if _active_server is srv:
                _active_server = None

    threading.Thread(target=_serve, daemon=True).start()
    return auth_url


def disconnect() -> None:
    tp = _token_path()
    if tp.exists():
        tp.unlink()
    logger.info("Drive OAuth disconnected (token removed)")
