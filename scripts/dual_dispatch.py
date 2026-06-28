"""ClipForge dual-GPU dispatcher.

Drives TWO backends concurrently — A on the RTX 3060 (:8420), B on the
GTX 1660 SUPER (:8421) — so two sheet rows process at once (one per GPU).
Reads pending rows (URL set, description empty), sends one to each free
backend via /api/auto (explicit URL), then writes the AI description back to
the sheet. Runs forever; picks up new rows automatically. Ctrl+C / kill to stop.
"""
import sys, json, time, os, urllib.request
sys.path.insert(0, r"D:\clipforge\server")
from services.sheets import write_cell, _service
from services import sheets_config as _scfg

# Sheet + tab come from the saved Sheets config (no hard-coded spreadsheet id).
_cfg = _scfg.load() or {}
SID = _cfg.get("spreadsheet_id", "")
TAB = _cfg.get("tab", "Sheet1")
# Only process rows from here on (skip older, intentionally-unprocessed rows).
MIN_ROW = int(os.environ.get("CLIPFORGE_DISPATCH_MIN_ROW", "126"))
# A -> :8420 (GPU 0), B -> :8421 (GPU 1). On a single-GPU PC only A is reachable;
# B is auto-skipped at assign time (see backend_up). Labels are by index, not card
# model, so the rig is portable to any machine.
BACKENDS = {"A(:8420)": "http://127.0.0.1:8420", "B(:8421)": "http://127.0.0.1:8421"}
PRESETS = ["narator", "comentator", "povestitor"]
# When a row completes the dispatcher writes, in PRESETS order:
#   C = cleaned transcript   D = RO description (caption)
#   F/G/H = the 3 variants' fetchable video links   I = posting status flag
#   ("ready" so the n8n poster picks the row up). E = FRENCH description column
#   (written by victoria_dispatch.py) — never touched here.
TRANSCRIPT_COL = "C"
VARIANT_LINK_COLS = ["F", "G", "H"]   # index 0/1/2 -> narator/comentator/povestitor
STATUS_COL = "I"


def variant_links(variant):
    """Newline-joined fetchable URLs for one variant's uploaded Drive file(s).
    Split videos produce several parts -> several links, in order."""
    files = ((variant or {}).get("drive") or {}).get("files") or []
    urls = [(f.get("download_url") or f.get("link") or "").strip() for f in files]
    return "\n".join(u for u in urls if u)


def http(url, data=None, timeout=60):
    body = json.dumps(data).encode() if data is not None else None
    hdr = {"Content-Type": "application/json"} if data is not None else {}
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), timeout=timeout))


def backend_up(base):
    """True if the backend answers /api/health. Lets the rig run on a single-GPU
    PC: a missing 2nd backend (:8421) is simply skipped, no error spam."""
    try:
        http(base + "/api/health", timeout=4)
        return True
    except Exception:
        return False


def read_pending():
    svc = _service()
    vals = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{TAB}'!A1:D400").execute().get("values", [])
    out = []
    for i, r in enumerate(vals, start=1):
        b = (r[1].strip() if len(r) > 1 and r[1] else "")
        d = (r[3].strip() if len(r) > 3 and r[3] else "")
        # Skip @herytstory rows — those are French content handled by
        # victoria_dispatch.py (French desc -> col E), not Romanian.
        if i >= MIN_ROW and b.startswith("http") and not d and "herytstory" not in b.lower():
            out.append((i, b))
    return out


def enqueue(backend, url):
    body = {"url": url, "variant_preset_ids": PRESETS, "from_sheets": False,
            "auto_detect_zones": True, "erase_method": "lama",
            "transcript_engine": "openai", "transcript_target_lang": "ro"}
    return http(backend + "/api/auto", body)["job_id"]


def main(dry=False):
    if dry:
        p = read_pending()
        print("PENDING:", [r for r, _ in p])
        return
    inflight = {k: None for k in BACKENDS}      # name -> (row, url, jid)
    done = set()
    print("dual-dispatch started:", BACKENDS, flush=True)
    while True:
        busy_rows = {v[0] for v in inflight.values() if v} | done
        pending = [p for p in read_pending() if p[0] not in busy_rows]
        # assign to free backends (skip any backend that isn't up — e.g. the
        # 2nd GPU's backend on a single-GPU PC)
        for name, backend in BACKENDS.items():
            if inflight[name] is None and pending and backend_up(backend):
                row, url = pending.pop(0)
                try:
                    jid = enqueue(backend, url)
                    inflight[name] = (row, url, jid)
                    print(f"[{name}] row {row} -> {jid}", flush=True)
                except Exception as e:
                    print(f"[{name}] enqueue fail row {row}: {str(e)[:80]}", flush=True)
        # poll inflight
        for name, backend in BACKENDS.items():
            if not inflight[name]:
                continue
            row, url, jid = inflight[name]
            try:
                j = http(backend + f"/api/jobs/{jid}", timeout=20)
            except Exception:
                continue
            st = j.get("status")
            if st == "done":
                try:
                    r = http(backend + f"/api/parallel/{jid}/result", timeout=20)
                    # Links first (E/F/G by variant index), then the description
                    # (D), then the "ready" flag (H) — so the poster never sees
                    # a ready row whose video links aren't written yet.
                    wrote = 0
                    for v in (r.get("variants") or []):
                        idx = v.get("index")
                        if isinstance(idx, int) and 0 <= idx < len(VARIANT_LINK_COLS):
                            links = variant_links(v)
                            if links:
                                write_cell(SID, TAB, VARIANT_LINK_COLS[idx], row, links)
                                wrote += 1
                    tx = (r.get("cleaned_text") or r.get("transcript_text") or "").strip()
                    if tx:
                        write_cell(SID, TAB, TRANSCRIPT_COL, row, tx)
                    desc = ((r.get("descriptions") or {}).get("ai_generated") or "").strip()
                    if desc:
                        write_cell(SID, TAB, "D", row, desc)
                    if wrote:
                        write_cell(SID, TAB, STATUS_COL, row, "ready")
                    print(f"[{name}] row {row} DONE -> Drive + desc + {wrote} links", flush=True)
                except Exception as e:
                    print(f"[{name}] row {row} writeback fail: {str(e)[:80]}", flush=True)
                done.add(row); inflight[name] = None
            elif st in ("failed", "error", "cancelled"):
                print(f"[{name}] row {row} {st}: {(j.get('error') or '')[:90]}", flush=True)
                done.add(row); inflight[name] = None      # skip so we don't loop on it
        busy = any(inflight.values())
        time.sleep(10 if (pending or busy) else 300)


if __name__ == "__main__":
    main(dry=("--dry" in sys.argv))
