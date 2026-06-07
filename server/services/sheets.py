"""
ClipForge — Google Sheets API wrapper

Reuses the user OAuth credentials saved by drive_oauth.py (same token,
scope https://www.googleapis.com/auth/spreadsheets must be present).

Used by the Parallel-from-Sheets flow:
  - pull next row → read URL + number cells
  - commit       → write the AI-generated description back into the row

Cells are addressed in A1 notation, e.g. "Sheet1!B5".
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger("clipforge.sheets")


_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_COL_LETTERS_RE = re.compile(r"^[A-Za-z]{1,3}$")


class SheetsError(RuntimeError):
    """Raised for any Sheets-API or config-level failure. The message is
    user-facing; the router maps it to a 4xx/5xx with the same text."""


class SheetsScopeMissing(SheetsError):
    """Token is connected but lacks the spreadsheets scope. UI prompts a
    Drive reconnect to pick up the new scope."""


def extract_spreadsheet_id(url_or_id: str) -> str:
    """Accept a full Sheets URL OR a bare ID and return the ID."""
    s = (url_or_id or "").strip()
    if not s:
        raise SheetsError("Spreadsheet URL or ID is required.")
    m = _SPREADSHEET_ID_RE.search(s)
    if m:
        return m.group(1)
    # Bare ID — IDs are 40+ char base64url-ish, but the API is the real check.
    if "/" in s or " " in s:
        raise SheetsError(
            "Could not parse spreadsheet ID from URL. "
            "Expected a link like https://docs.google.com/spreadsheets/d/<ID>/edit "
            "or the bare ID."
        )
    return s


def validate_column(letter: str, label: str) -> str:
    """Normalize and validate a column letter ("a" → "A"). Raises SheetsError."""
    s = (letter or "").strip().upper()
    if not _COL_LETTERS_RE.match(s):
        raise SheetsError(
            f"{label}: '{letter}' is not a valid column letter (expected A–Z, AA, AB, …)."
        )
    return s


def _cell_a1(tab: str, col: str, row: int) -> str:
    """Build A1 notation. Quotes the tab name when it contains spaces or
    characters that would break the unquoted form."""
    t = tab.replace("'", "''")
    needs_quote = bool(re.search(r"[^A-Za-z0-9_]", tab))
    tab_part = f"'{t}'" if needs_quote else t
    return f"{tab_part}!{col}{int(row)}"


def _service():
    """Build a Sheets service using the same user OAuth credentials as Drive."""
    from services.drive_oauth import get_user_credentials

    creds = get_user_credentials()
    if not creds:
        raise SheetsError(
            "Google Drive is not connected. Go to /parallel and click 'Connect Google Drive'."
        )
    # Detect a token that pre-dates the Sheets scope addition.
    scopes = getattr(creds, "scopes", None) or []
    if scopes and "https://www.googleapis.com/auth/spreadsheets" not in scopes:
        raise SheetsScopeMissing(
            "Your saved Drive token doesn't include Google Sheets permission yet. "
            "Click 'Disconnect' and then 'Connect Google Drive' again to re-consent "
            "with the new scope."
        )
    try:
        from googleapiclient.discovery import build
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise SheetsError(f"Could not build Sheets client: {e}") from e


def read_cell(spreadsheet_id: str, tab: str, col: str, row: int) -> str:
    """Return the trimmed string value at <tab>!<col><row>, or '' if empty."""
    rng = _cell_a1(tab, col, int(row))
    svc = _service()
    try:
        resp = svc.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=rng
        ).execute()
    except Exception as e:
        _raise_friendly(e, rng, spreadsheet_id)
        return ""  # unreachable — _raise_friendly always raises
    vals = resp.get("values") or []
    if not vals or not vals[0]:
        return ""
    return str(vals[0][0]).strip()


def read_pair(
    spreadsheet_id: str, tab: str, col_a: str, col_b: str, row: int,
) -> Tuple[str, str]:
    """Read two cells from the same row in one API call.
    Returns (col_a_value, col_b_value), each trimmed and '' if empty."""
    rng_a = _cell_a1(tab, col_a, int(row))
    rng_b = _cell_a1(tab, col_b, int(row))
    svc = _service()
    try:
        resp = svc.spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id, ranges=[rng_a, rng_b]
        ).execute()
    except Exception as e:
        _raise_friendly(e, f"{rng_a}+{rng_b}", spreadsheet_id)
        return "", ""
    ranges = resp.get("valueRanges") or []

    def _one(idx: int) -> str:
        if idx >= len(ranges):
            return ""
        vs = ranges[idx].get("values") or []
        if not vs or not vs[0]:
            return ""
        return str(vs[0][0]).strip()

    return _one(0), _one(1)


def write_cell(
    spreadsheet_id: str, tab: str, col: str, row: int, value: str,
) -> None:
    """Overwrite a single cell. Uses USER_ENTERED so newlines are honored."""
    rng = _cell_a1(tab, col, int(row))
    svc = _service()
    body = {"values": [[value]]}
    try:
        svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=rng,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
    except Exception as e:
        _raise_friendly(e, rng, spreadsheet_id)


def probe_access(spreadsheet_id: str, tab: str) -> dict:
    """Quick reachability check for the Save Config UI. Reads A1 of the tab —
    only fails if the spreadsheet/tab is unreachable or scope missing."""
    svc = _service()
    try:
        meta = svc.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="properties.title,sheets.properties.title"
        ).execute()
    except Exception as e:
        _raise_friendly(e, f"{tab}!A1", spreadsheet_id)
        return {}  # unreachable
    title = (meta.get("properties") or {}).get("title") or ""
    tabs = [
        (s.get("properties") or {}).get("title") or ""
        for s in (meta.get("sheets") or [])
    ]
    if tab not in tabs:
        raise SheetsError(
            f"Tab '{tab}' not found in spreadsheet '{title}'. "
            f"Available tabs: {', '.join(t for t in tabs if t) or '(none)'}"
        )
    return {"spreadsheet_title": title, "tabs": tabs}


def _raise_friendly(e: Exception, rng: str, spreadsheet_id: str) -> None:
    """Translate googleapiclient.errors.HttpError into a SheetsError users
    can act on."""
    # Sniff the HTTP status without hard-importing googleapiclient.errors at
    # module load (keeps import-time deps soft).
    status = getattr(getattr(e, "resp", None), "status", None)
    body = ""
    try:
        body = (getattr(e, "content", b"") or b"").decode("utf-8", "ignore")
    except Exception:
        pass
    msg_lower = (body + " " + str(e)).lower()

    if status == 401 or "invalid_grant" in msg_lower or "unauthorized" in msg_lower:
        raise SheetsScopeMissing(
            "Drive credentials expired or lack the Sheets scope. "
            "Disconnect + Connect Google Drive again."
        ) from e
    if status == 403:
        if "insufficient" in msg_lower or "scope" in msg_lower:
            raise SheetsScopeMissing(
                "Your Drive token doesn't include Google Sheets permission. "
                "Disconnect + Connect Google Drive again to re-consent."
            ) from e
        raise SheetsError(
            f"Access denied to spreadsheet (id={spreadsheet_id[:12]}…). "
            f"Make sure the connected Google account can open this sheet."
        ) from e
    if status == 404:
        raise SheetsError(
            f"Spreadsheet not found (id={spreadsheet_id[:12]}…). "
            f"Check the URL."
        ) from e
    if status == 400:
        raise SheetsError(
            f"Sheets API rejected range '{rng}' — check the tab name and "
            f"column letters. ({str(e)[-200:]})"
        ) from e
    raise SheetsError(f"Sheets API error{f' ({status})' if status else ''}: {str(e)[-200:]}") from e
