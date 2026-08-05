"""ClipForge dual-GPU dispatcher.

Drives TWO backends concurrently — A on the RTX 3060 (:8420), B on the
GTX 1660 SUPER (:8421) — so two sheet rows process at once (one per GPU).
Reads pending rows (URL set, description empty), sends one to each free
backend via /api/auto (explicit URL), then writes the AI description back to
the sheet. Runs forever; picks up new rows automatically. Ctrl+C / kill to stop.

Never re-does work that is already on Drive: each role's folder is listed and
indexed by the NR in the filename ("<NR>.mp4" / "<NR>_p1.mp4"), so a row whose
videos are up is skipped, and a row missing only one role renders only that
role. drive_upload.upload_files() refuses same-named duplicates as a backstop.
"""
import sys, json, time, os, re, urllib.request, urllib.error
sys.path.insert(0, r"D:\clipforge\server")
from services.sheets import write_cell, _service
from services import sheets_config as _scfg
from services.drive_upload import list_folder_files

# Sheet + tab come from the saved Sheets config (no hard-coded spreadsheet id).
_cfg = _scfg.load() or {}
SID = _cfg.get("spreadsheet_id", "")
TAB = _cfg.get("tab", "Sheet1")
# Only process rows from here on (skip older, intentionally-unprocessed rows).
# 2 = tot sheet-ul. A stat pe 199 cat timp se lucra doar la lotul nou; asta ascundea
# ~180 de randuri vechi fara narator/comentator, care nu ar fi fost randate niciodata.
MIN_ROW = int(os.environ.get("CLIPFORGE_DISPATCH_MIN_ROW", "2"))
# A -> :8420 (GPU 0), B -> :8421 (GPU 1). On a single-GPU PC only A is reachable;
# B is auto-skipped at assign time (see backend_up). Labels are by index, not card
# model, so the rig is portable to any machine.
BACKENDS = {"A(:8420)": "http://127.0.0.1:8420", "B(:8421)": "http://127.0.0.1:8421"}
# Each backend's own data dir — needed to look a project's source URL up in its
# SQLite DB (there is no HTTP endpoint for a project).
BACKEND_DBS = {"A(:8420)": r"D:\clipforge\data\db\clipforge.db",
               "B(:8421)": r"D:\clipforge\data_b\db\clipforge.db"}
# Rolurile randate pentru fiecare rand. Suprascrie cu CLIPFORGE_PRESETS, separate
# prin virgula — un lot mic poate cere toate cele 3 fara sa schimbi fisierul
# pentru restanta de sute de randuri (fiecare rol in plus = inca un set de
# credite ElevenLabs pe rand).
PRESETS = [s.strip() for s in os.environ.get(
    "CLIPFORGE_PRESETS", "narator,comentator").split(",") if s.strip()]
# A row is "pending" when THIS column is empty. We key on the description column
# (D): a row with no RO description is unprocessed, so we run it and write D.
PENDING_COL = "D"       # description
WRITE_DESC = True
# When a row completes the dispatcher writes, in PRESETS order:
#   D = RO description (caption)   F/G/H = the 3 variants' fetchable video links
#   I = posting status flag ("ready" so the n8n poster picks the row up).
# Column C (transcript) is intentionally NOT written (user only wants the video
# + description + links). E = FRENCH description column (written by
# victoria_dispatch.py) — never touched here.
# Map each role to its video-link column (robust to any subset/order of PRESETS,
# so a comentator-only run still writes to G, not F).
ROLE_COLS = {"narator": "F", "comentator": "G", "povestitor": "H"}
STATUS_COL = "I"


DATA_DIR = os.environ.get("CLIPFORGE_DATA_DIR", r"D:\clipforge\data")
# Re-list the Drive folders at most this often (seconds) — the listing only
# changes when WE upload, and we update the cache in-place when that happens.
DRIVE_CACHE_TTL = 900
_drive_cache = {"at": 0.0, "by_role": {}}


def _role_drive_folder(role):
    """The Drive folder configured on a role's variant preset."""
    try:
        with open(os.path.join(DATA_DIR, "variant_presets", f"{role}.json"), encoding="utf-8") as fh:
            return (json.load(fh).get("drive_folder") or "").strip()
    except Exception:
        return ""


def drive_numbers(force=False):
    """{role: {NR: [file dicts]}} — what each role ALREADY has on Drive.

    Videos are named after the sheet's NR: "<NR>.mp4", or "<NR>_p1.mp4",
    "<NR>_p2.mp4"... when split into parts. We key on that NR so a row whose
    videos are already up is never rendered (and re-uploaded) a second time.
    A folder we cannot list yields {} for that role — i.e. we fall back to
    "not done", never to a wrong skip."""
    now = time.time()
    if not force and _drive_cache["by_role"] and (now - _drive_cache["at"]) < DRIVE_CACHE_TTL:
        return _drive_cache["by_role"]
    by_role = {}
    for role in PRESETS:
        folder = _role_drive_folder(role)
        nrs = {}
        if folder:
            res = list_folder_files(folder)
            if res.get("status") == "ok":
                for f in res["files"]:
                    m = re.match(r"^(\d+)(?:_p\d+|_part\d+of\d+)?\.mp4$", f.get("name") or "")
                    if m:
                        nrs.setdefault(m.group(1), []).append(f)
            else:
                print(f"drive list {role} failed ({res.get('status')}): "
                      f"{str(res.get('reason'))[:90]}", flush=True)
        by_role[role] = nrs
    _drive_cache.update(at=now, by_role=by_role)
    print("drive inventory: " + ", ".join(f"{r}={len(v)}" for r, v in by_role.items()), flush=True)
    return by_role


def norm_nr(nr):
    """Cifrele NR-ului, ca sa se potriveasca cu numele fisierului de pe Drive.

    81 de randuri au NR scris ca "1.", "2." — cu punct. Fisierul iese corect
    (`_safe_stem` taie punctul, deci `1.mp4`), dar inventarul de pe Drive e
    indexat pe cifre, asa ca "1." nu s-ar potrivi niciodata cu "1": randul ar fi
    randat din nou la fiecare rulare, desi videoul exista deja.
    """
    m = re.match(r"\s*(\d+)", nr or "")
    return m.group(1) if m else (nr or "").strip()


def roles_todo(nr):
    """(roles still missing on Drive, {role: existing files}) for this NR."""
    have = drive_numbers()
    nr = norm_nr(nr)
    if not nr:
        return list(PRESETS), {}          # no NR to match on → do the work
    existing = {r: have.get(r, {}).get(nr) for r in PRESETS}
    return [r for r in PRESETS if not existing.get(r)], {r: v for r, v in existing.items() if v}


def files_links(files):
    """Newline-joined fetchable URLs for a list of Drive file dicts."""
    urls = [(f.get("download_url") or f.get("link") or "").strip() for f in (files or [])]
    return "\n".join(u for u in urls if u)


def variant_links(variant):
    """Newline-joined fetchable URLs for one variant's uploaded Drive file(s).
    Split videos produce several parts -> several links, in order."""
    return files_links(((variant or {}).get("drive") or {}).get("files") or [])


def http_detail(e):
    """The FastAPI `detail` string out of an HTTPError body (or the raw body)."""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        return str(json.loads(body).get("detail") or body)
    except Exception:
        return body


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


def running_job(base):
    """(job_id, project_id) of a parallel_pipeline job still working on this
    backend, else None. Lets a restarted dispatcher re-attach to the job it was
    watching instead of handing the same row to the other GPU as well."""
    try:
        for j in http(base + "/api/jobs/", timeout=20):
            if j.get("type") == "parallel_pipeline" and j.get("status") in ("running", "queued"):
                return j.get("id"), j.get("project_id")
    except Exception:
        pass
    return None


def project_url(name, project_id):
    """The source URL a project was built from — used to match it to a row.
    Read straight from that backend's SQLite DB; there is no HTTP route for it."""
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


def adopt_running(inflight, backends, pend):
    """Re-attach to jobs that survived a dispatcher restart.

    Without this a restart double-dispatches: the sheet row still reads pending
    (its description is only written on completion), so the row goes out AGAIN
    to the other card while the original keeps rendering. Matching is by the
    project's source URL against the pending rows."""
    by_url = {u: (r, nr) for r, u, nr in pend}
    for name, base in backends.items():
        rj = running_job(base)
        if not rj:
            continue
        jid, pid = rj
        hit = by_url.get(project_url(name, pid))
        if hit:
            row, nr = hit
            todo, _ = roles_todo(nr)
            inflight[name] = (row, "", jid, todo, nr)
            print(f"[{name}] adoptat jobul {jid} = rand {row} (NR {nr}) — deja in lucru", flush=True)
        else:
            print(f"[{name}] job {jid} ruleaza dar nu l-am putut lega de un rand — "
                  f"nu trimit nimic pe acest backend pana termina", flush=True)
            inflight[name] = (-1, "", jid, list(PRESETS), "")


def read_pending():
    pcol = ord(PENDING_COL) - ord("A")   # 'D'->3, 'F'->5
    svc = _service()
    vals = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{TAB}'!A1:I400").execute().get("values", [])
    out = []
    for i, r in enumerate(vals, start=1):
        a = (r[0].strip() if len(r) > 0 and r[0] else "")   # NR (col A)
        b = (r[1].strip() if len(r) > 1 and r[1] else "")
        pend = (r[pcol].strip() if len(r) > pcol and r[pcol] else "")
        # Skip @herytstory rows — those are French content handled by
        # victoria_dispatch.py (French desc -> col E), not Romanian.
        if i < MIN_ROW or not b.startswith("http") or "herytstory" in b.lower():
            continue
        # A row still needs work when its description is missing OR when any
        # role's video is not on Drive yet. Keying on the description alone
        # used to mean that writing a description by hand (e.g. backfilling
        # one whose render died) permanently retired the row and its videos
        # were never made. Rows with no NR can't be checked against Drive, so
        # for those the description stays the only signal.
        needs = (not pend) or (bool(a) and bool(roles_todo(a)[0]))
        if needs:
            out.append((i, b, a))   # a = NR -> names the output <NR>.mp4
    return out


def enqueue(backend, url, number=None, presets=None):
    body = {"url": url, "variant_preset_ids": list(presets or PRESETS), "from_sheets": False,
            "auto_detect_zones": True, "erase_method": "lama",
            "transcript_engine": "openai", "transcript_target_lang": "ro"}
    if number:
        body["number"] = str(number)   # names the output <number>.mp4
    return http(backend + "/api/auto", body)["job_id"]


MAX_ENQUEUE_ATTEMPTS = 3


def note_bad(row, reason, bad_rows):
    """Remember a row we gave up on and write the reason into the status column.

    The status cell is safe to write: the poster only acts on "ready", so a
    reason string parks the row visibly without it ever being posted. Column D
    is left ALONE — it is the caption, not a place for error text."""
    bad_rows[row] = reason
    try:
        write_cell(SID, TAB, STATUS_COL, row, f"esuat — {reason}"[:200])
    except Exception:
        pass
    print(f"    randuri esuate pana acum: {sorted(bad_rows)}", flush=True)


def bump(row, attempts, done, bad_rows, reason):
    """Count a transient enqueue failure; give up on the row after N tries.
    Returns True when the row was abandoned (caller should take the next one)."""
    attempts[row] = attempts.get(row, 0) + 1
    if attempts[row] < MAX_ENQUEUE_ATTEMPTS:
        return False
    print(f"    row {row}: {attempts[row]} incercari esuate — renunt", flush=True)
    note_bad(row, reason, bad_rows)
    done.add(row)
    return True


def main(dry=False):
    if dry:
        p = read_pending()
        print("PENDING:", [r for r, _, _ in p])
        return
    inflight = {k: None for k in BACKENDS}      # name -> (row, url, jid, roles, nr)
    done = set()
    no_desc = []        # rows already fully on Drive but with no description yet
    bad_rows = {}       # row -> why we gave up on it
    attempts = {}       # row -> consecutive enqueue failures
    print("dual-dispatch started:", BACKENDS, flush=True)
    adopt_running(inflight, BACKENDS, read_pending())
    while True:
        # Google pica din cand in cand cu timeout de handshake SSL. Fara asta,
        # o singura pana de retea de 5 secunde omora dispecerul si placile
        # stateau pana observa cineva. Reincearca, nu muri.
        try:
            busy_rows = {v[0] for v in inflight.values() if v} | done
            pending = [p for p in read_pending() if p[0] not in busy_rows]
        except Exception as e:
            print(f"citire sheet esuata ({type(e).__name__}: {str(e)[:60]}) "
                  f"— reincerc in 30s", flush=True)
            time.sleep(30)
            continue
        # assign to free backends (skip any backend that isn't up — e.g. the
        # 2nd GPU's backend on a single-GPU PC)
        for name, backend in BACKENDS.items():
            if inflight[name] is not None or not pending or not backend_up(backend):
                continue
            # Keep taking rows until one actually needs work — a row whose
            # videos are already on Drive costs a skip, not a GPU hour.
            while pending:
                row, url, nr = pending.pop(0)
                todo, have = roles_todo(nr)
                if have:
                    # Recover the sheet links from what is already uploaded, so
                    # a row that was rendered but never written back is not lost.
                    for role, fl in have.items():
                        col = ROLE_COLS.get(role)
                        if col:
                            try:
                                write_cell(SID, TAB, col, row, files_links(fl))
                            except Exception as e:
                                print(f"[{name}] row {row} link writeback fail: {str(e)[:60]}", flush=True)
                if not todo:
                    # Nothing left to render for this row. Its description is
                    # still missing (that is WHY it reads as pending) — flag it
                    # instead of silently leaving a hole in column D. Status is
                    # deliberately NOT set to "ready": the poster uses column D
                    # as the caption, so a description-less row must not be
                    # picked up.
                    if row not in no_desc:
                        no_desc.append(row)
                    print(f"[{name}] row {row} (NR {nr}) SKIP — all {len(PRESETS)} roles already on "
                          f"Drive; links rewritten, description still empty "
                          f"(rows needing a description: {no_desc})", flush=True)
                    done.add(row)
                    continue
                if len(todo) < len(PRESETS):
                    print(f"[{name}] row {row} (NR {nr}) partial — on Drive: "
                          f"{sorted(have)}; rendering only {todo}", flush=True)
                try:
                    jid = enqueue(backend, url, nr, todo)
                    inflight[name] = (row, url, jid, todo, nr)
                    attempts.pop(row, None)
                    print(f"[{name}] row {row} -> {jid} {todo}", flush=True)
                except urllib.error.HTTPError as e:
                    detail = http_detail(e)
                    if 400 <= e.code < 500:
                        # The backend refused the request itself — an
                        # undownloadable URL (age-restricted TikTok wanting a
                        # login), a dead link, a bad preset. Retrying can NEVER
                        # help, and retrying forever starves this backend: one
                        # such row idled a whole GPU for hours. Record it, mark
                        # the sheet, take the next row.
                        print(f"[{name}] row {row} (NR {nr}) SKIP — respins de backend "
                              f"({e.code}): {detail[:130]}", flush=True)
                        note_bad(row, f"{e.code}: {detail}", bad_rows)
                        done.add(row)
                        continue
                    print(f"[{name}] enqueue fail row {row}: HTTP {e.code} {detail[:60]}", flush=True)
                    if bump(row, attempts, done, bad_rows, f"HTTP {e.code}: {detail}"):
                        continue
                except Exception as e:
                    print(f"[{name}] enqueue fail row {row}: {str(e)[:80]}", flush=True)
                    if bump(row, attempts, done, bad_rows, str(e)):
                        continue
                break
        # poll inflight
        for name, backend in BACKENDS.items():
            if not inflight[name]:
                continue
            row, url, jid, roles, nr = inflight[name]
            try:
                j = http(backend + f"/api/jobs/{jid}", timeout=20)
            except Exception:
                continue
            st = j.get("status")
            if row < 1:
                # An adopted job we could not tie to a sheet row — just wait it
                # out so we don't hand this backend a second row in parallel.
                if st in ("done", "failed", "error", "cancelled"):
                    print(f"[{name}] jobul nelegat {jid} s-a termint ({st}) — backend liber", flush=True)
                    inflight[name] = None
                continue
            if st == "done":
                try:
                    r = http(backend + f"/api/parallel/{jid}/result", timeout=20)
                    # Links first (F/G/H by variant index), then the description
                    # (D), then the "ready" flag (I) — so the poster never sees
                    # a ready row whose video links aren't written yet. Index
                    # maps into THIS row's role list (which is a subset of
                    # PRESETS when some roles were already on Drive).
                    wrote = 0
                    for v in (r.get("variants") or []):
                        # Map by the variant's OWN preset id — the positional
                        # index only means something relative to the exact role
                        # list this job was submitted with, which we lose if the
                        # dispatcher restarts mid-job. Index is the fallback.
                        role = v.get("commentator_preset_id")
                        if role not in ROLE_COLS:
                            idx = v.get("index")
                            role = roles[idx] if isinstance(idx, int) and 0 <= idx < len(roles) else None
                        col = ROLE_COLS.get(role)
                        if col:
                            links = variant_links(v)
                            if links:
                                write_cell(SID, TAB, col, row, links)
                                wrote += 1
                            # Remember what we just uploaded so a later row with
                            # the same NR is skipped without waiting for the
                            # Drive listing to expire.
                            if nr:
                                fl = ((v.get("drive") or {}).get("files") or [])
                                if fl:
                                    _drive_cache["by_role"].setdefault(role, {})[norm_nr(nr)] = fl
                    desc = ((r.get("descriptions") or {}).get("ai_generated") or "").strip()
                    if WRITE_DESC and desc:
                        write_cell(SID, TAB, "D", row, desc)
                    elif WRITE_DESC:
                        # The row will read as pending again next pass (empty D)
                        # but its videos are now on Drive, so it will be skipped
                        # rather than re-rendered. Make the gap visible.
                        if row not in no_desc:
                            no_desc.append(row)
                        print(f"[{name}] row {row} WARNING: no description generated "
                              f"(rows needing a description: {no_desc})", flush=True)
                    # "ready" means the poster can take the row as-is: it needs
                    # BOTH the video links and the caption (column D).
                    if wrote and desc:
                        write_cell(SID, TAB, STATUS_COL, row, "ready")
                    print(f"[{name}] row {row} DONE -> {wrote} links, "
                          f"desc {'OK' if desc else 'MISSING'} ({len(desc)} chars)", flush=True)
                except Exception as e:
                    print(f"[{name}] row {row} writeback fail: {str(e)[:80]}", flush=True)
                done.add(row); inflight[name] = None
            elif st in ("failed", "error", "cancelled"):
                print(f"[{name}] row {row} {st}: {(j.get('error') or '')[:90]}", flush=True)
                done.add(row); inflight[name] = None      # skip so we don't loop on it
        busy = any(inflight.values())
        time.sleep(10 if (pending or busy) else 300)


if __name__ == "__main__":
    if "--dry" in sys.argv:
        main(dry=True)
    else:
        # Plasa de siguranta: orice exceptie neprinsa reporneste bucla in loc sa
        # lase placile oprite. adopt_running() se reataseaza la joburile in curs,
        # deci o repornire nu retrimite acelasi rand.
        while True:
            try:
                main()
            except KeyboardInterrupt:
                break
            except Exception as e:  # noqa: BLE001
                print(f"dispecer picat ({type(e).__name__}: {str(e)[:80]}) "
                      f"— repornesc in 30s", flush=True)
                time.sleep(30)
