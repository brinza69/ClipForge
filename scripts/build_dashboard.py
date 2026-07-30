"""Construieste dashboard-ul social media: data/exports/dashboard.html

Pagina e statica si autonoma (fara CDN, fara fetch) — datele se incorporeaza la
build. Reruleaza scriptul ca s-o improspatezi:

    server/.venv/Scripts/python.exe scripts/build_dashboard.py

Se vede la http://localhost:8420/exports/dashboard.html (data/exports e montat static).
"""
import html
import json
import pathlib
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

from buffer_api import channels as buffer_channels, default_org, gql  # noqa: E402

TZ = ZoneInfo("Europe/Bucharest")
OUT = _ROOT / "data" / "exports" / "dashboard.html"
POSTS_PER_DAY = 3          # sloturile 08:00 / 13:00 / 20:30
QUEUE_MAX = 10             # Buffer Free, pe canal

POSTS_Q = ("query P($i: PostsInput!, $f: Int) { posts(input: $i, first: $f) "
           "{ edges { node { id status dueAt sentAt via text externalLink "
           "error { message } } } } }")


def local(iso):
    if not iso:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            t = datetime.strptime(iso, fmt).replace(tzinfo=timezone.utc)
            return t.astimezone(TZ)
        except ValueError:
            continue
    return None


def fmt(dt):
    if not dt:
        return "—"
    zile = ["lun", "mar", "mie", "joi", "vin", "sâm", "dum"]
    luni = ["ian", "feb", "mar", "apr", "mai", "iun",
            "iul", "aug", "sep", "oct", "nov", "dec"]
    return f"{zile[dt.weekday()]} {dt.day} {luni[dt.month - 1]} {dt:%H:%M}"


# ---------------------------------------------------------------- Buffer
# UN singur apel pe canal (fara filtru de stare, grupare locala): planul Free are
# 250 cereri/24h, iar patru apeluri pe canal le ardeau degeaba. Daca API-ul tot
# refuza, pagina se face din cache si spune clar ca datele sunt vechi.
CACHE = _ROOT / "data" / "buffer_cache.json"


def fetch_buffer():
    org = default_org()
    out = []
    for c in buffer_channels(org):
        r = gql(POSTS_Q, {"i": {"organizationId": org,
                                "filter": {"channelIds": [c["id"]]}}, "f": 100})
        nodes = [e["node"] for e in r["posts"]["edges"]]
        by = lambda s: [n for n in nodes if n.get("status") == s]  # noqa: E731
        prin_buffer = [n for n in by("sent") if n.get("via") == "buffer"]
        out.append({
            "nume": c["name"], "retea": c["service"], "tip": c["type"],
            "link": c.get("externalLink"),
            "programate": sorted(
                [{"cand": fmt(local(n["dueAt"])), "text": (n.get("text") or "")[:110]}
                 for n in by("scheduled")], key=lambda x: x["cand"]),
            "n_programate": len(by("scheduled")),
            "publicat_buffer": sorted(
                [{"cand": fmt(local(n.get("sentAt"))), "text": (n.get("text") or "")[:110]}
                 for n in prin_buffer], key=lambda x: x["cand"]),
            "n_publicat_buffer": len(prin_buffer),
            "n_istoric_importat": len(by("sent")) - len(prin_buffer),
            "erori": [{"cand": fmt(local(n["dueAt"])), "text": (n.get("text") or "")[:90],
                       "motiv": ((n.get("error") or {}).get("message") or "")[:160]}
                      for n in by("error")],
        })
    out.sort(key=lambda c: (c["retea"], c["nume"]))
    return out


buffer_avert = ""
try:
    canale = fetch_buffer()
    CACHE.write_text(json.dumps({"at": fmt(datetime.now(TZ)), "canale": canale},
                                ensure_ascii=False, indent=1), encoding="utf-8")
except Exception as ex:  # noqa: BLE001
    if not CACHE.exists():
        raise
    cached = json.loads(CACHE.read_text(encoding="utf-8"))
    canale = cached["canale"]
    motiv = "rate limit Buffer (250 cereri/24h)" if "429" in str(ex) else str(ex)[:90]
    buffer_avert = (f"Buffer nu a răspuns — {motiv}. Secțiunile pe canale sunt din "
                    f"cache, citite la {cached.get('at', '?')}"
                    + (f" · {cached['nota']}" if cached.get("nota") else ""))

# ---------------------------------------------------------------- stoc
inv = json.loads((_ROOT / "data" / "pov_inventory.json").read_text(encoding="utf-8"))
fb_list = json.loads((_ROOT / "data" / "fb_post_list_povestitor.json").read_text(encoding="utf-8"))
tt_plan = json.loads((_ROOT / "data" / "pov_post_list.json").read_text(encoding="utf-8"))
tt_files = [f for v in tt_plan for f in v["files"]]

by_chan = {c["nume"]: c for c in canale}


def ramas(canal_nume, total):
    c = by_chan.get(canal_nume)
    daţi = c["n_programate"] + c["n_publicat_buffer"] if c else 0
    return max(0, total - daţi), daţi


fb_ramas, fb_dati = ramas("Povestitorul", len(fb_list))
tt_ramas, tt_dati = ramas("povestitorul.ro", len(tt_files))

stoc = [
    {"canal": "Facebook · Povestitorul", "total": len(fb_list), "date": fb_dati,
     "ramase": fb_ramas, "zile": round(fb_ramas / POSTS_PER_DAY, 1)},
    {"canal": "TikTok · povestitorul.ro", "total": len(tt_files), "date": tt_dati,
     "ramase": tt_ramas, "zile": round(tt_ramas / POSTS_PER_DAY, 1)},
]

excluse = [{"fisier": r["name"], "motiv": ", ".join(r["excluded"]) or "identitate nesigura"}
           for r in inv if r["excluded"] or not r["in_plan"]]
limbi = Counter(r["lang"] for r in inv)

# ---------------------------------------------------------------- productie
tracks = json.loads(pathlib.Path(
    r"C:\Users\mihai\AppData\Local\Temp\claude\D--clipforge"
    r"\97cd7b5b-7b5a-4edb-80e8-f54bd9b59573\scratchpad\tracks_state.json"
).read_text(encoding="utf-8")) if pathlib.Path(
    r"C:\Users\mihai\AppData\Local\Temp\claude\D--clipforge"
    r"\97cd7b5b-7b5a-4edb-80e8-f54bd9b59573\scratchpad\tracks_state.json").exists() else {"main": [], "fr": []}

main_rows = [d for d in tracks.get("main", []) if d.get("link")]
fr_rows = tracks.get("fr", [])
cozi = [
    {"prio": 1, "nume": "narator + comentator (RO)",
     "facut": sum(1 for d in main_rows if d.get("narator")),
     "total": len(main_rows),
     "nota": "utilizatorul posteaza manual — se randeaza primele"},
    {"prio": 2, "nume": "herstory / Victoria (FR)",
     "facut": sum(1 for d in fr_rows if d.get("video")),
     "total": sum(1 for d in fr_rows if d.get("link")),
     "nota": "canalul a ramas fara stoc de postat — randeaza acum"},
    {"prio": 3, "nume": "povestitorul (RO)",
     "facut": sum(1 for d in main_rows if d.get("povestitor")),
     "total": len(main_rows),
     "nota": f"are stoc ({fb_ramas} pe Facebook, {tt_ramas} pe TikTok) — ultima"},
]

# joburi in lucru acum
BACKENDS = {"A :8420 (GTX 1660S)": "http://127.0.0.1:8420",
            "B :8421 (RTX 3060)": "http://127.0.0.1:8421"}
joburi = []
for nume, base in BACKENDS.items():
    try:
        js = json.load(urllib.request.urlopen(base + "/api/jobs/?limit=5", timeout=8))
        act = [j for j in js if j.get("status") in ("running", "queued")]
        joburi.append({"placa": nume,
                       "stare": (f"{act[0]['status']} · {round((act[0].get('progress') or 0) * 100)}%"
                                 if act else "liber"),
                       "job": (act[0]["id"][:12] if act else "—")})
    except Exception as e:  # noqa: BLE001
        joburi.append({"placa": nume, "stare": f"nu raspunde ({str(e)[:40]})", "job": "—"})

DATA = {"generat": fmt(datetime.now(TZ)), "canale": canale, "stoc": stoc,
        "excluse": excluse, "limbi": dict(limbi), "cozi": cozi, "joburi": joburi}

# ---------------------------------------------------------------- HTML
e = html.escape


def tabel(caps, randuri):
    if not randuri:
        return '<p class="gol">nimic</p>'
    th = "".join(f"<th>{e(c)}</th>" for c in caps)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in randuri)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


sectiuni = []
for c in DATA["canale"]:
    icon = {"tiktok": "TikTok", "facebook": "Facebook"}.get(c["retea"], c["retea"])
    erori = ""
    if c["erori"]:
        rows = [[e(x["cand"]), e(x["text"]), f'<span class="rau">{e(x["motiv"])}</span>']
                for x in c["erori"]]
        erori = f'<h4 class="rau">Eșuate ({len(c["erori"])})</h4>' + tabel(
            ["când", "text", "motiv"], rows)
    sectiuni.append(f"""
    <section class="card">
      <h3>{e(icon)} · {e(c['nume'])}</h3>
      <p class="meta">
        <span class="pill">{c['n_programate']}/{QUEUE_MAX} în coadă</span>
        <span class="pill">{c['n_publicat_buffer']} publicate prin Buffer</span>
        <span class="pill mut">{c['n_istoric_importat']} istoric importat</span>
        {'<span class="pill rau">' + str(len(c['erori'])) + ' eșuate</span>' if c['erori'] else ''}
      </p>
      {erori}
      <h4>Urmează</h4>
      {tabel(["când", "text"], [[e(x["cand"]), e(x["text"])] for x in c["programate"]])}
      <details><summary>Publicate prin Buffer ({c['n_publicat_buffer']})</summary>
      {tabel(["când", "text"], [[e(x["cand"]), e(x["text"])] for x in c["publicat_buffer"]])}
      </details>
    </section>""")

stoc_rows = [[e(s["canal"]), s["total"], s["date"], f'<b>{s["ramase"]}</b>',
              f'{s["zile"]} zile'] for s in DATA["stoc"]]
cozi_rows = []
for q in DATA["cozi"]:
    pct = round(100 * q["facut"] / q["total"]) if q["total"] else 0
    cozi_rows.append([
        f'<b>{q["prio"]}</b>', e(q["nume"]),
        f'{q["facut"]}/{q["total"]}',
        f'<div class="bar"><span style="width:{pct}%"></span></div> {pct}%',
        f'<span class="mut">{e(q["nota"])}</span>'])
job_rows = [[e(j["placa"]), e(j["stare"]), f'<code>{e(j["job"])}</code>'] for j in DATA["joburi"]]
excl_rows = [[f'<code>{e(x["fisier"])}</code>', e(x["motiv"])] for x in DATA["excluse"]]

page = f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClipForge — social media</title>
<style>
:root {{ --bg:#f6f7f9; --fg:#14161a; --card:#fff; --line:#e2e5ea; --mut:#6b7280;
         --acc:#2563eb; --rau:#b91c1c; --ok:#15803d; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0e1116; --fg:#e6e8ec; --card:#161a21; --line:#262c36; --mut:#9aa3b2;
           --acc:#60a5fa; --rau:#f87171; --ok:#4ade80; }} }}
:root[data-theme="dark"] {{ --bg:#0e1116; --fg:#e6e8ec; --card:#161a21; --line:#262c36;
   --mut:#9aa3b2; --acc:#60a5fa; --rau:#f87171; --ok:#4ade80; }}
:root[data-theme="light"] {{ --bg:#f6f7f9; --fg:#14161a; --card:#fff; --line:#e2e5ea;
   --mut:#6b7280; --acc:#2563eb; --rau:#b91c1c; --ok:#15803d; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
.wrap {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
h3 {{ margin:0 0 10px; font-size:17px; }}
h4 {{ margin:18px 0 6px; font-size:13px; text-transform:uppercase;
      letter-spacing:.06em; color:var(--mut); }}
.sub {{ color:var(--mut); margin:0 0 24px; font-size:13px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:18px; margin:0 0 18px; }}
.grid {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); }}
.meta {{ margin:0 0 4px; display:flex; flex-wrap:wrap; gap:6px; }}
.pill {{ background:var(--bg); border:1px solid var(--line); border-radius:999px;
  padding:3px 10px; font-size:12px; }}
.pill.mut {{ color:var(--mut); }}
.pill.rau, .rau {{ color:var(--rau); }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line);
  vertical-align:top; }}
th {{ color:var(--mut); font-weight:600; font-size:12px; white-space:nowrap; }}
td code {{ font-size:12px; }}
tbody tr:last-child td {{ border-bottom:0; }}
.gol {{ color:var(--mut); font-style:italic; margin:4px 0; }}
.mut {{ color:var(--mut); }}
.bar {{ display:inline-block; width:110px; height:7px; background:var(--line);
  border-radius:99px; overflow:hidden; vertical-align:middle; }}
.bar span {{ display:block; height:100%; background:var(--acc); }}
details {{ margin-top:12px; }} summary {{ cursor:pointer; color:var(--mut); font-size:13px; }}
.warn {{ border-left:3px solid var(--rau); padding-left:12px; }}
footer {{ color:var(--mut); font-size:12px; margin-top:28px; }}
</style>
<div class="wrap">
<h1>ClipForge — social media</h1>
<p class="sub">date de la {e(DATA['generat'])} · regenerează cu
<code>server/.venv/Scripts/python.exe scripts/build_dashboard.py</code></p>
{f'<p class="card warn rau">{e(buffer_avert)}</p>' if buffer_avert else ''}

<div class="grid">{''.join(sectiuni)}</div>

<section class="card">
  <h3>Stoc și cât ajunge</h3>
  {tabel(["canal", "total pregătit", "date lui Buffer", "rămase", "la 3/zi"], stoc_rows)}
</section>

<section class="card">
  <h3>Cozi de producție video</h3>
  {tabel(["prio", "coadă", "făcut", "progres", "notă"], cozi_rows)}
  <h4>Plăci acum</h4>
  {tabel(["placă", "stare", "job"], job_rows)}
</section>

<section class="card warn">
  <h3>Excluse de la postare</h3>
  <p class="mut">Limbă verificată cu whisper pe audio, nu pe nume:
     {e(str(DATA['limbi']))} din {len(inv)} fișiere.
     <b>101 și 102 sunt în engleză</b> — au ajuns din greșeală pe TikTok, rămân acolo,
     dar nu se postează pe Facebook.</p>
  {tabel(["fișier", "motiv"], excl_rows)}
</section>

<footer>Pagina e statică: datele sunt incorporate la build, nu se actualizează singură.</footer>
</div>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page, encoding="utf-8")
print(f"scris {OUT}  ({len(page) / 1024:.0f} KB)")
print(f"  canale: {len(canale)}  ·  stoc FB ramas {fb_ramas}  ·  TikTok ramas {tt_ramas}")
print(f"  http://localhost:8420/exports/dashboard.html")
