"""Herstory / Victoria (French) dispatcher — its own sheet, its own folder.

Reads the French-only sheet that lives in Victoria's Drive folder:

    A=NR  B=LINK  C=TRANSCRIPT  D=DESCRIERE_FR  E=VIDEO_URL  F=STATUS

A row needs work when its video is missing from Victoria's Drive folder OR it
has no French description. Videos are named after column A (`<NR>.mp4`, or
`<NR>_p1.mp4`… when split), which is what makes the de-dup possible: a row whose
video is already on Drive is never rendered again, and its link is recovered
instead. Add a link in A/B and the rest fills itself in.

Separate from dual_dispatch.py on purpose — different sheet, different language,
different Drive folder, different account. The two never touch each other's rows.

Run:  server/.venv/Scripts/python.exe scripts/herstory_dispatch.py [--dry]
"""
import sys, json, time, os, re, urllib.request, urllib.error

sys.path.insert(0, r"D:\clipforge\server")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import targets as _targets
from services.sheets import write_cell, _service
from services.drive_upload import list_folder_files

SID = os.environ.get("CLIPFORGE_FR_SHEET") or _targets.get("fr_sheet_id")
TAB = os.environ.get("CLIPFORGE_FR_TAB") or _targets.get("fr_tab", "Victoria")
PRESET = "victoria"
DRIVE_FOLDER = _targets.get("fr_drive_folder")
BACKENDS = {"A(:8420)": "http://127.0.0.1:8420", "B(:8421)": "http://127.0.0.1:8421"}
# `--backend A` (sau B) leaga dispecerul de o singura placa, ca sa poata rula
# doua piste in paralel: una pe fiecare GPU. Fara asta, doua dispecere ar vedea
# amandoua aceeasi placa libera si i-ar trimite cate un job fiecare.
_only = None
for _i, _a in enumerate(sys.argv):
    if _a == "--backend" and _i + 1 < len(sys.argv):
        _only = sys.argv[_i + 1].strip().upper()
if _only:
    BACKENDS = {k: v for k, v in BACKENDS.items() if k.upper().startswith(_only)}
    if not BACKENDS:
        raise SystemExit(f"--backend {_only}: nu exista; alege A sau B")
BACKEND_DBS = {"A(:8420)": r"D:\clipforge\data\db\clipforge.db",
               "B(:8421)": r"D:\clipforge\data_b\db\clipforge.db"}
# Column letters in THIS sheet (not the master's layout).
NR_COL, LINK_COL, TX_COL, DESC_COL, VIDEO_COL, STATUS_COL = "A", "B", "C", "D", "E", "F"
LANG = "fr"
MAX_ENQUEUE_ATTEMPTS = 3
DRIVE_CACHE_TTL = 900
NAME_RE = re.compile(r"^(\d+)(?:_p\d+|_part\d+of\d+)?\.mp4$", re.I)

_drive_cache = {"at": 0.0, "nrs": {}}


def http(url, data=None, timeout=60):
    body = json.dumps(data).encode() if data is not None else None
    hdr = {"Content-Type": "application/json"} if data is not None else {}
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, data=body, headers=hdr), timeout=timeout))


def http_detail(e):
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        return str(json.loads(body).get("detail") or body)
    except Exception:
        return body


def backend_up(base):
    try:
        http(base + "/api/health", timeout=4)
        return True
    except Exception:
        return False


def drive_numbers(force=False):
    """{NR: [file dicts]} already in Victoria's folder. A folder we cannot list
    yields {} — i.e. we fall back to "not done", never to a wrong skip."""
    now = time.time()
    if not force and _drive_cache["nrs"] and (now - _drive_cache["at"]) < DRIVE_CACHE_TTL:
        return _drive_cache["nrs"]
    nrs = {}
    res = list_folder_files(DRIVE_FOLDER)
    if res.get("status") == "ok":
        for f in res["files"]:
            m = NAME_RE.match(f.get("name") or "")
            if m:
                nrs.setdefault(m.group(1), []).append(f)
    else:
        print(f"drive list esuat ({res.get('status')}): {str(res.get('reason'))[:90]}", flush=True)
        return _drive_cache["nrs"]
    _drive_cache.update(at=now, nrs=nrs)
    print(f"drive: {len(nrs)} videoclipuri in folderul Victoria", flush=True)
    return nrs


def files_links(files):
    urls = [(f.get("download_url") or f.get("link") or "").strip() for f in (files or [])]
    return "\n".join(u for u in urls if u)


def col_idx(letter):
    return ord(letter) - ord("A")


def read_pending():
    """(row, url, nr, has_desc, drive_files) for every row still needing work."""
    svc = _service()
    vals = (svc.spreadsheets().values()
            .get(spreadsheetId=SID, range=f"'{TAB}'!A1:F400")
            .execute().get("values", []))
    have = drive_numbers()
    out = []
    for i, r in enumerate(vals, start=1):
        def g(letter):
            k = col_idx(letter)
            return (r[k].strip() if len(r) > k and r[k] else "")
        url, nr = g(LINK_COL), g(NR_COL)
        if not url.startswith("http"):
            continue                      # header row, or a blank line
        fl = have.get(nr) or []
        if fl and g(DESC_COL):
            continue                      # video + description -> done
        out.append((i, url, nr, bool(g(DESC_COL)), fl))
    return out


def enqueue(backend, url, number):
    body = {"url": url, "variant_preset_ids": [PRESET], "from_sheets": False,
            "auto_detect_zones": True, "erase_method": "lama",
            "transcript_engine": "openai", "transcript_target_lang": LANG}
    if number:
        body["number"] = str(number)
    return http(backend + "/api/auto", body)["job_id"]


def running_job(base):
    try:
        for j in http(base + "/api/jobs/", timeout=20):
            if j.get("type") == "parallel_pipeline" and j.get("status") in ("running", "queued"):
                return j.get("id"), j.get("project_id")
    except Exception:
        pass
    return None


def project_url(name, project_id):
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{BACKEND_DBS[name]}?mode=ro", uri=True, timeout=5)
        try:
            r = con.execute("SELECT source_url FROM projects WHERE id=?", (project_id,)).fetchone()
        finally:
            con.close()
        return (r[0] or "").strip() if r else ""
    except Exception:
        return ""


def adopt_running(inflight, pend):
    """Re-attach to jobs that outlived a dispatcher restart, so the same row is
    not handed to the other GPU as well. A running job we cannot tie to a row
    still parks its backend until it finishes."""
    by_url = {u: (r, nr) for r, u, nr, _, _ in pend}
    for name, base in BACKENDS.items():
        rj = running_job(base)
        if not rj:
            continue
        jid, pid = rj
        hit = by_url.get(project_url(name, pid))
        if hit:
            row, nr = hit
            inflight[name] = (row, jid, nr)
            print(f"[{name}] adoptat {jid} = rand {row} (NR {nr})", flush=True)
        else:
            print(f"[{name}] job {jid} ruleaza dar nu il pot lega de un rand — astept", flush=True)
            inflight[name] = (-1, jid, "")


def note_bad(row, reason, bad_rows):
    bad_rows[row] = reason
    try:
        write_cell(SID, TAB, STATUS_COL, row, f"esuat — {reason}"[:200])
    except Exception:
        pass
    print(f"    randuri esuate: {sorted(bad_rows)}", flush=True)


def bump(row, attempts, done, bad_rows, reason):
    attempts[row] = attempts.get(row, 0) + 1
    if attempts[row] < MAX_ENQUEUE_ATTEMPTS:
        return False
    print(f"    rand {row}: {attempts[row]} incercari — renunt", flush=True)
    note_bad(row, reason, bad_rows)
    done.add(row)
    return True


# `--max-randuri N` opreste dispecerul dupa N randuri RANDATE (nu si cele sarite
# fiindca existau deja pe Drive). Iese cu SystemExit, care trece prin plasa de
# siguranta de mai jos — altfel s-ar reporni si ar continua la nesfarsit.
MAX_RANDURI = None
for _i, _a in enumerate(sys.argv):
    if _a == "--max-randuri" and _i + 1 < len(sys.argv):
        MAX_RANDURI = int(sys.argv[_i + 1])
_randate = {"n": 0}


def _bifeaza_rand():
    """Numara un rand terminat; opreste rularea cand s-a atins limita."""
    if MAX_RANDURI is None:
        return
    _randate["n"] += 1
    print(f"randuri terminate: {_randate['n']}/{MAX_RANDURI}", flush=True)
    if _randate["n"] >= MAX_RANDURI:
        raise SystemExit(0)


def main(dry=False):
    if dry:
        p = read_pending()
        print(f"de lucru: {len(p)} randuri")
        for row, url, nr, has_desc, fl in p:
            print(f"  rand {row:3} NR {nr:>3} | video pe Drive: {len(fl)} | "
                  f"descriere: {'da' if has_desc else 'NU'} | {url[:52]}")
        return

    inflight = {k: None for k in BACKENDS}      # name -> (row, jid, nr)
    done, bad_rows, attempts, no_desc = set(), {}, {}, []
    print(f"herstory-dispatch pornit | sheet {SID[:12]}… tab {TAB}", flush=True)
    adopt_running(inflight, read_pending())

    while True:
        # Google pica din cand in cand cu timeout SSL, iar tokenul OAuth expira
        # saptamanal. Fara asta, o pana de retea de 5 secunde omora dispecerul si
        # placile stateau pana observa cineva.
        try:
            busy = {v[0] for v in inflight.values() if v} | done
            pending = [p for p in read_pending() if p[0] not in busy]
        except Exception as e:
            print(f"citire sheet esuata ({type(e).__name__}: {str(e)[:60]}) "
                  f"— reincerc in 30s", flush=True)
            time.sleep(30)
            continue

        for name, backend in BACKENDS.items():
            if inflight[name] is not None or not pending or not backend_up(backend):
                continue
            while pending:
                row, url, nr, has_desc, fl = pending.pop(0)
                if fl:
                    # Video already on Drive — recover its link instead of
                    # spending a render, and only the description is missing.
                    try:
                        write_cell(SID, TAB, VIDEO_COL, row, files_links(fl))
                    except Exception as e:
                        print(f"[{name}] rand {row} scriere link esuata: {str(e)[:60]}", flush=True)
                    if not has_desc and row not in no_desc:
                        no_desc.append(row)
                    print(f"[{name}] rand {row} (NR {nr}) SARIT — video deja pe Drive; "
                          f"link rescris, descriere {'ok' if has_desc else 'LIPSA'} "
                          f"(fara descriere: {no_desc})", flush=True)
                    done.add(row)
                    continue
                try:
                    jid = enqueue(backend, url, nr)
                    inflight[name] = (row, jid, nr)
                    attempts.pop(row, None)
                    print(f"[{name}] rand {row} (NR {nr}) -> {jid}", flush=True)
                except urllib.error.HTTPError as e:
                    detail = http_detail(e)
                    if 400 <= e.code < 500:
                        print(f"[{name}] rand {row} SARIT — respins ({e.code}): "
                              f"{detail[:120]}", flush=True)
                        note_bad(row, f"{e.code}: {detail}", bad_rows)
                        done.add(row)
                        continue
                    print(f"[{name}] enqueue esuat rand {row}: HTTP {e.code}", flush=True)
                    if bump(row, attempts, done, bad_rows, f"HTTP {e.code}: {detail}"):
                        continue
                except Exception as e:
                    print(f"[{name}] enqueue esuat rand {row}: {str(e)[:80]}", flush=True)
                    if bump(row, attempts, done, bad_rows, str(e)):
                        continue
                break

        for name, backend in BACKENDS.items():
            if not inflight[name]:
                continue
            row, jid, nr = inflight[name]
            try:
                j = http(backend + f"/api/jobs/{jid}", timeout=20)
            except Exception:
                continue
            st = j.get("status")
            if row < 1:
                if st in ("done", "failed", "error", "cancelled"):
                    print(f"[{name}] jobul nelegat {jid} s-a terminat ({st})", flush=True)
                    inflight[name] = None
                continue
            if st == "done":
                try:
                    r = http(backend + f"/api/parallel/{jid}/result", timeout=25)
                    links = ""
                    for v in (r.get("variants") or []):
                        fl = ((v.get("drive") or {}).get("files") or [])
                        if fl:
                            links = files_links(fl)
                            if nr:
                                _drive_cache["nrs"].setdefault(str(nr).strip(), fl)
                    tx = (r.get("cleaned_text") or r.get("transcript_text") or "").strip()
                    desc = ((r.get("descriptions") or {}).get("ai_generated") or "").strip()
                    if tx:
                        write_cell(SID, TAB, TX_COL, row, tx)
                    if desc:
                        write_cell(SID, TAB, DESC_COL, row, desc)
                    if links:
                        write_cell(SID, TAB, VIDEO_COL, row, links)
                    # "ready" only when BOTH exist — the poster uses the
                    # description as the caption, so a caption-less row must
                    # never be picked up.
                    if links and desc:
                        write_cell(SID, TAB, STATUS_COL, row, "ready")
                    elif not desc and row not in no_desc:
                        no_desc.append(row)
                        print(f"[{name}] rand {row} ATENTIE: fara descriere "
                              f"(fara descriere: {no_desc})", flush=True)
                    print(f"[{name}] rand {row} GATA -> {len(links.splitlines()) if links else 0} "
                          f"link(uri), descriere {'ok' if desc else 'LIPSA'} ({len(desc)} car.)",
                          flush=True)
                except Exception as e:
                    print(f"[{name}] rand {row} writeback esuat: {str(e)[:80]}", flush=True)
                done.add(row); inflight[name] = None
                _bifeaza_rand()
            elif st in ("failed", "error", "cancelled"):
                print(f"[{name}] rand {row} {st}: {(j.get('error') or '')[:90]}", flush=True)
                done.add(row); inflight[name] = None

        time.sleep(10 if (pending or any(inflight.values())) else 300)


if __name__ == "__main__":
    if "--dry" in sys.argv:
        main(dry=True)
    else:
        # Plasa de siguranta: orice exceptie neprinsa reporneste bucla in loc sa
        # lase placile oprite. adopt_running() se reataseaza la joburile in curs.
        while True:
            try:
                main()
            except KeyboardInterrupt:
                break
            except Exception as e:  # noqa: BLE001
                print(f"dispecer picat ({type(e).__name__}: {str(e)[:80]}) "
                      f"— repornesc in 30s", flush=True)
                time.sleep(30)
