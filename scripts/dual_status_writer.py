"""Starea ambelor plăci, scrisă pentru pagina live (același origin, fără CORS).

Scrie `data/exports/dual_status.json` la fiecare 3 secunde, cu:
  - jobul curent pe fiecare placă, cu progres și cât a trecut
  - câte rânduri așteaptă în coadă, pe placă
  - **cât mai durează**: pentru jobul curent, pentru coada plăcii, și în total

Estimarea nu e ghicită: se ia mediana duratelor reale ale randărilor terminate
pe FIECARE placă în parte, din baza de date. Conteaza separat pentru ca 1660
Super e de câteva ori mai lentă decât 3060 la ștergere — o medie comună ar minți
pe amândouă.

Mai scrie și ultimele videoclipuri terminate, cu un cadru de previzualizare
extras în `exports/thumbs/`, ca pagina să le poată arăta.
"""
import json, os, re, sqlite3, subprocess, time, urllib.request
from datetime import datetime

RADACINA = r"D:\clipforge"
OUT = os.path.join(RADACINA, r"data\exports\dual_status.json")
THUMBS = os.path.join(RADACINA, r"data\exports\thumbs")
DISPATCH_LOG = os.path.join(RADACINA, r"data\dispatch.log")
_CF = 0x08000000 if os.name == "nt" else 0
# :8420 ruleaza pe GPU 0, :8421 pe GPU 1. Perechea nume<->port de mai jos e
# corecta EXACT pentru ca `watchdog.ps1` da UUID-urile in ordinea indexului, la
# fel ca `nvidia-smi`. Pe rigul asta asta inseamna A=1660 SUPER, B=3060 —
# invers fata de ce scria in trei comentarii pana pe 26 aug 2026.
PORT_DB = {8420: os.path.join(RADACINA, r"data\db\clipforge.db"),
           8421: os.path.join(RADACINA, r"data_b\db\clipforge.db")}
PORT_MEDIA = {8420: os.path.join(RADACINA, r"data\media"),
              8421: os.path.join(RADACINA, r"data_b\media")}


def detect_backends():
    names = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=_CF).stdout
        names = [n.strip() for n in out.splitlines() if n.strip()]
    except Exception:
        pass
    if not names:
        names = ["GPU 0"]
    ports = [8420, 8421]
    return [(names[i], ports[i]) for i in range(min(len(ports), len(names)))]


BACKENDS = detect_backends()


def durata_tipica(port):
    """Mediana duratelor randărilor reușite pe placa asta, în secunde.

    Se ia din ultimele 40, ca să reflecte cum merge acum — nu cum mergea acum o
    lună, pe alt profil de encodare."""
    db = PORT_DB.get(port)
    if not db or not os.path.exists(db):
        return 2400
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        randuri = c.execute(
            "select created_at, updated_at from jobs where type='parallel_pipeline' "
            "and status='done' order by updated_at desc limit 40").fetchall()
        c.close()
    except Exception:
        return 2400
    d = []
    for a, b in randuri:
        try:
            t0 = datetime.fromisoformat(str(a)); t1 = datetime.fromisoformat(str(b))
            s = (t1 - t0).total_seconds()
            if 120 < s < 4 * 3600:          # ignoram esecurile instant si blocajele
                d.append(s)
        except Exception:
            pass
    if not d:
        return 2400
    d.sort()
    return d[len(d) // 2]


def video_terminat(port, job_id):
    """(poster, nume_fisier, url_redare) pentru videoclipul unui job terminat.

    Backend-ul monteaza deja `/media` peste directorul de media, deci clipul se
    poate reda direct din pagina — nu se copiaza nimic in exports. Placa B are
    alt port, deci url-ul ei e absolut."""
    db, media = PORT_DB.get(port), PORT_MEDIA.get(port)
    if not db or not os.path.exists(db):
        return None, None, None
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        r = c.execute("select project_id from jobs where id=?", (job_id,)).fetchone()
        c.close()
        if not r:
            return None, None, None
        pdir = os.path.join(media, r[0])
        cand = []
        for rad, _d, fis in os.walk(pdir):
            for f in fis:
                if f.endswith(".mp4") and not f.startswith("video") and not f.startswith("_"):
                    cand.append(os.path.join(rad, f))
        if not cand:
            return None, None, None
        src = max(cand, key=os.path.getmtime)
        rel = os.path.relpath(src, media).replace("\\", "/")
        url = f"/media/{rel}" if port == 8420 else f"http://127.0.0.1:{port}/media/{rel}"
        dst = os.path.join(THUMBS, f"{job_id}.jpg")
        if not os.path.exists(dst):
            os.makedirs(THUMBS, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2", "-i", src,
                            "-frames:v", "1", "-vf", "scale=240:-1", dst],
                           capture_output=True, timeout=120, creationflags=_CF)
        return (f"thumbs/{job_id}.jpg" if os.path.exists(dst) else None,
                os.path.basename(src), url)
    except Exception:
        return None, None, None


def terminate_recent(port, n=6):
    db = PORT_DB.get(port)
    if not db or not os.path.exists(db):
        return []
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        r = c.execute("select id, updated_at, created_at from jobs where "
                      "type='parallel_pipeline' and status='done' "
                      "order by updated_at desc limit ?", (n,)).fetchall()
        c.close()
    except Exception:
        return []
    out = []
    for jid, upd, cre in r:
        durata = None
        try:
            durata = (datetime.fromisoformat(str(upd))
                      - datetime.fromisoformat(str(cre))).total_seconds()
        except Exception:
            pass
        p, nume, url = video_terminat(port, jid)
        out.append({"job_id": jid, "terminat": str(upd)[11:16],
                    "durata_s": round(durata) if durata else None,
                    "poster": p, "fisier": nume, "url": url})
    return out


def harta_randuri():
    m = {}
    try:
        with open(DISPATCH_LOG, encoding="utf-8", errors="ignore") as f:
            for line in f:
                mo = re.search(r"r[ao]nd (\d+) \(NR (\d+)\) -> (\w+)", line) or \
                     re.search(r"row (\d+) -> (\w+)", line)
                if mo:
                    g = mo.groups()
                    m[g[-1]] = int(g[0])
    except Exception:
        pass
    return m


def ceas(s):
    if s is None:
        return None
    s = int(max(0, s))
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m"


while True:
    rowmap = harta_randuri()
    out = {"updated": time.strftime("%H:%M:%S"), "backends": [], "total": {}}
    total_ramas = 0
    for gpu, port in BACKENDS:
        url = f"http://127.0.0.1:{port}"
        tip = durata_tipica(port)
        info = {"gpu": gpu, "port": port, "running": False, "in_coada": 0,
                "durata_tipica_s": round(tip), "durata_tipica": ceas(tip)}
        try:
            arr = json.load(urllib.request.urlopen(
                url + "/api/jobs/?status=running,queued", timeout=8))
            rul = [x for x in arr if x.get("status") == "running"
                   and x.get("type") == "parallel_pipeline"]
            coada = [x for x in arr if x.get("status") == "queued"]
            info["in_coada"] = len(coada)
            if rul:
                j = rul[0]
                pr = j.get("progress") or 0
                info.update(running=True, job_id=j["id"], progress=pr,
                            message=j.get("progress_message"), row=rowmap.get(j["id"]))
                # cat mai are jobul curent: din progres, marginit de durata tipica
                ramas_job = tip * (1 - pr) if pr > 0.02 else tip
                info["ramas_job_s"] = round(ramas_job)
                info["ramas_job"] = ceas(ramas_job)
            else:
                info["message"] = "liber"
                ramas_job = 0
            ramas_placa = ramas_job + len(coada) * tip
            info["ramas_placa_s"] = round(ramas_placa)
            info["ramas_placa"] = ceas(ramas_placa)
            total_ramas = max(total_ramas, ramas_placa)   # plăcile merg în paralel
            info["terminate"] = terminate_recent(port)
        except Exception as e:
            info["message"] = "backend inaccesibil"
            info["error"] = str(e)[:40]
        out["backends"].append(info)
    # totalul e cat dureaza placa cea mai incarcata, nu suma — merg simultan
    out["total"] = {"ramas_s": round(total_ramas), "ramas": ceas(total_ramas),
                    "in_coada": sum(b.get("in_coada", 0) for b in out["backends"]),
                    "randeaza": sum(1 for b in out["backends"] if b.get("running"))}
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    time.sleep(3)
