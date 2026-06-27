"""Victoria (French) watcher + description writeback.

Polls Sheet1 for @herytstory rows (col B), submits any new one to the **Victoria**
variant on BOTH backends (3060=:8420, 1660=:8421), and — when each job finishes —
writes its French AI description back to **column I (descriere_fr)** of that row,
plus the French video link to **J (victoria_url)** and **K (status_fr)='ready'**.
Romanian descriptions stay in column D (written by dual_dispatch) — never mixed.
No video is re-generated for rows already submitted.

State (persisted, survives restart):
  data/victoria_submitted.json  — urls already submitted (never re-submit)
  data/victoria_inflight.json   — url -> {row,nr,jid,port} awaiting D writeback

Runs forever; new herystory rows picked up within ~20s. Stop with Ctrl+C / kill.
"""
import sys, json, time, urllib.request
sys.path.insert(0, r"D:\clipforge\server")
from services import sheets_config as _scfg
from services.sheets import _service, write_cell

_cfg = _scfg.load() or {}
SID = _cfg.get("spreadsheet_id", "")
TAB = _cfg.get("tab", "Sheet1")
BACKENDS = ["http://127.0.0.1:8420", "http://127.0.0.1:8421"]   # 3060, 1660
SUBMITTED = r"D:\clipforge\data\victoria_submitted.json"
INFLIGHT = r"D:\clipforge\data\victoria_inflight.json"
MATCH = "herytstory"
# French goes in its OWN columns so it never collides with the Romanian column D.
FR_DESC_COL, FR_VIDEO_COL, FR_STATUS_COL = "E", "J", "K"


def victoria_links(result):
    """Newline-joined fetchable URLs for the victoria variant's Drive file(s)."""
    out = []
    for v in (result.get("variants") or []):
        for f in ((v.get("drive") or {}).get("files") or []):
            u = (f.get("download_url") or f.get("link") or "").strip()
            if u:
                out.append(u)
    return "\n".join(out)


def http(url, data=None, timeout=140):
    body = json.dumps(data).encode() if data is not None else None
    hdr = {"Content-Type": "application/json"} if data is not None else {}
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), timeout=timeout))


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def jsave(p, obj):
    try:
        json.dump(obj, open(p, "w", encoding="utf-8"))
    except Exception as e:
        print(f"save fail {p}: {e}", flush=True)


def read_hery():
    svc = _service()
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{TAB}'!A1:B400").execute().get("values", [])
    out = []
    for i, r in enumerate(vals, start=1):
        a = (r[0].strip() if len(r) > 0 and r[0] else "")
        b = (r[1].strip() if len(r) > 1 and r[1] else "")
        if MATCH in b and b.startswith("http"):
            out.append((i, a, b))
    return out


def submit(be, num, url):
    body = {"url": url, "number": str(num), "variant_preset_ids": ["victoria"],
            "from_sheets": False, "auto_detect_zones": True, "erase_method": "lama",
            "transcript_engine": "openai", "transcript_target_lang": "fr"}
    return http(be + "/api/auto", body)["job_id"]


def main():
    submitted = set(jload(SUBMITTED, []))
    inflight = jload(INFLIGHT, {})        # url -> {row,nr,jid,port}
    rr = 0
    print(f"victoria-watch v2 online; {len(submitted)} submitted, "
          f"{len(inflight)} awaiting D-writeback", flush=True)
    while True:
        # 1) submit new herystory rows
        try:
            rows = read_hery()
        except Exception as e:
            print(f"sheet read fail: {str(e)[:90]}", flush=True)
            time.sleep(25); continue
        for (row, num, url) in rows:
            if url in submitted:
                continue
            be = BACKENDS[rr % 2]; rr += 1
            port = 8420 if "8420" in be else 8421
            gpu = "3060" if port == 8420 else "1660"
            try:
                jid = submit(be, num, url)
                submitted.add(url); jsave(SUBMITTED, sorted(submitted))
                inflight[url] = {"row": row, "nr": num, "jid": jid, "port": port}
                jsave(INFLIGHT, inflight)
                print(f"submit row {row} (nr {num}) -> {gpu} job={jid}", flush=True)
            except Exception as e:
                print(f"submit fail row {row}: {str(e)[:90]}", flush=True)

        # 2) write French description back to col D for finished jobs
        for url in list(inflight.keys()):
            info = inflight[url]
            try:
                j = http(f"http://127.0.0.1:{info['port']}/api/jobs/{info['jid']}", timeout=15)
            except Exception:
                continue
            st = j.get("status")
            if st == "done":
                try:
                    r = http(f"http://127.0.0.1:{info['port']}/api/parallel/{info['jid']}/result", timeout=25)
                    desc = ((r.get("descriptions") or {}).get("ai_generated") or "").strip()
                    links = victoria_links(r)
                    tx = (r.get("cleaned_text") or r.get("transcript_text") or "").strip()
                    if tx:
                        write_cell(SID, TAB, "C", info["row"], tx)
                    if desc:
                        write_cell(SID, TAB, FR_DESC_COL, info["row"], desc)
                    if links:
                        write_cell(SID, TAB, FR_VIDEO_COL, info["row"], links)
                        write_cell(SID, TAB, FR_STATUS_COL, info["row"], "ready")
                    print(f"FR row {info['row']} (nr {info['nr']}) -> desc + {len(links.splitlines()) if links else 0} link(s)", flush=True)
                except Exception as e:
                    print(f"writeback fail row {info['row']}: {str(e)[:80]}", flush=True)
                del inflight[url]; jsave(INFLIGHT, inflight)
            elif st in ("failed", "error", "cancelled"):
                print(f"job row {info['row']} {st} — dropped from writeback", flush=True)
                del inflight[url]; jsave(INFLIGHT, inflight)

        time.sleep(20)


if __name__ == "__main__":
    main()
