"""
ClipForge — Sheets Router

Endpoints for the Parallel-from-Sheets flow. The Sheets config (spreadsheet
ID, tab, column letters, next_row) is stored in data/sheets_config.json
and read/written through services.sheets_config.

  GET  /api/sheets/config           — current config + status
  POST /api/sheets/config           — save / replace config (validates access)
  POST /api/sheets/pull-next        — read next row's URL + number (does NOT
                                       advance next_row yet — only commit does)
  POST /api/sheets/commit           — write description in row, advance next_row
  POST /api/sheets/skip-row         — manually advance next_row by one
  DELETE /api/sheets/config         — wipe config (forces re-setup)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import sheets, sheets_config
from services.sheets import SheetsError, SheetsScopeMissing

logger = logging.getLogger("clipforge.routers.sheets")
router = APIRouter(prefix="/api/sheets", tags=["sheets"])


class ConfigRequest(BaseModel):
    spreadsheet_url: str            # raw URL OR bare ID
    tab: str                        # tab name, e.g. "Sheet1"
    col_url: str                    # letter, e.g. "B"
    col_number: str                 # letter, e.g. "A"
    col_description: str            # letter, e.g. "C"
    start_row: int = Field(ge=1)    # first data row; becomes next_row on save


class CommitRequest(BaseModel):
    row: int = Field(ge=1)
    description: str


def _public(cfg: Optional[dict]) -> dict:
    """Shape a config record for the UI."""
    if not cfg:
        return {"configured": False}
    return {
        "configured": True,
        "spreadsheet_id": cfg.get("spreadsheet_id"),
        "spreadsheet_url": cfg.get("spreadsheet_url"),
        "spreadsheet_title": cfg.get("spreadsheet_title"),
        "tab": cfg.get("tab"),
        "col_url": cfg.get("col_url"),
        "col_number": cfg.get("col_number"),
        "col_description": cfg.get("col_description"),
        "start_row": cfg.get("start_row"),
        "next_row": cfg.get("next_row") or cfg.get("start_row"),
    }


def _wrap(e: Exception) -> HTTPException:
    """Translate a Sheets error into an HTTPException with a stable code."""
    if isinstance(e, SheetsScopeMissing):
        return HTTPException(401, str(e))
    if isinstance(e, SheetsError):
        return HTTPException(400, str(e))
    return HTTPException(500, f"Unexpected: {e}")


@router.get("/config")
async def get_config():
    return _public(sheets_config.load())


@router.delete("/config")
async def delete_config():
    sheets_config.clear()
    return {"ok": True}


@router.post("/config")
async def save_config(req: ConfigRequest):
    try:
        spreadsheet_id = sheets.extract_spreadsheet_id(req.spreadsheet_url)
        col_url = sheets.validate_column(req.col_url, "URL column")
        col_number = sheets.validate_column(req.col_number, "Number column")
        col_description = sheets.validate_column(req.col_description, "Description column")
        if not req.tab.strip():
            raise SheetsError("Tab name is required.")
        # Validate the user can actually open the sheet + the tab exists.
        probe = sheets.probe_access(spreadsheet_id, req.tab.strip())
    except Exception as e:
        raise _wrap(e)

    saved = sheets_config.save({
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": req.spreadsheet_url.strip(),
        "spreadsheet_title": probe.get("spreadsheet_title", ""),
        "tab": req.tab.strip(),
        "col_url": col_url,
        "col_number": col_number,
        "col_description": col_description,
        "start_row": int(req.start_row),
        "next_row": int(req.start_row),
    })
    return _public(saved)


@router.post("/pull-next")
async def pull_next():
    """Read URL + number from <tab>!<col_url><next_row> and <col_number><next_row>.
    Does NOT advance next_row — only commit() does, after the job succeeds."""
    cfg = sheets_config.load()
    if not cfg:
        raise HTTPException(409, "Sheets not configured yet.")
    row = int(cfg.get("next_row") or cfg.get("start_row") or 1)
    try:
        url_val, number_val = sheets.read_pair(
            cfg["spreadsheet_id"], cfg["tab"],
            cfg["col_url"], cfg["col_number"], row,
        )
    except Exception as e:
        raise _wrap(e)

    if not url_val:
        return {
            "empty": True,
            "row": row,
            "message": (
                f"Row {row} has no URL in column {cfg['col_url']}. "
                f"Fill it in the sheet and try again, or skip this row."
            ),
        }
    return {
        "empty": False,
        "row": row,
        "url": url_val,
        "number": number_val,
        # Echo config bits the UI may want to badge
        "spreadsheet_title": cfg.get("spreadsheet_title"),
        "col_url": cfg["col_url"],
        "col_number": cfg["col_number"],
    }


@router.post("/commit")
async def commit(req: CommitRequest):
    """Write the description into <col_description><row> and advance next_row
    to row+1. Called by the pipeline after variant #0 finishes, OR directly
    by the UI if the user wants to commit manually."""
    cfg = sheets_config.load()
    if not cfg:
        raise HTTPException(409, "Sheets not configured yet.")
    try:
        sheets.write_cell(
            cfg["spreadsheet_id"], cfg["tab"], cfg["col_description"],
            req.row, req.description,
        )
    except Exception as e:
        raise _wrap(e)
    saved = sheets_config.update_next_row(int(req.row) + 1)
    return {"ok": True, "row_written": req.row, "next_row": saved.get("next_row") if saved else None}


@router.post("/skip-row")
async def skip_row():
    """Bump next_row by 1 without writing anything. UI uses this when a row
    has no URL — user fills it later, but we move on to the next."""
    cfg = sheets_config.load()
    if not cfg:
        raise HTTPException(409, "Sheets not configured yet.")
    cur = int(cfg.get("next_row") or cfg.get("start_row") or 1)
    saved = sheets_config.update_next_row(cur + 1)
    return {"ok": True, "next_row": saved.get("next_row") if saved else None}
