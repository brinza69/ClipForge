"""Rearanjeaza o coada franceza: ultimele randate primele.

Ordinea ceruta pe 26 august: **clipul cel mai nou randat iese primul**. NR-ul nu
spune nimic despre cand a fost facut un clip — NR 1 e din iunie, NR 91 de ieri —
deci se ordoneaza dupa `created`, data fisierului de pe Drive, adusa in plan de
`build_fr_post_list.py`.

Aici nu se sterge si nu se recreeaza nimic — se muta doar `dueAt`, deci nu se
pierde nicio postare si nu se creeaza duplicate.

Doua lucruri care trebuie respectate:
  - `editPost` NU face actualizare partiala: fara text si assets raspunde
    "Post must have either text or media". Se trimite postarea intreaga.
  - partile aceluiasi clip raman consecutive si in ordine. Numarul partii se
    citeste din caption (`Partie N :`), nu se ghiceste din pozitie. Ordonarea e
    pe GRUP, nu pe fisier: partea 2 e randata dupa partea 1, deci o sortare
    plata dupa data ar pune partea 2 prima.

    python reordoneaza_franceza.py --canal Contouse --dry
"""
import json
import pathlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
from buffer_api import gql, default_org, channels  # noqa: E402

DRY = "--dry" in sys.argv
CANAL = sys.argv[sys.argv.index("--canal") + 1] if "--canal" in sys.argv else "Contouse"
TZ = ZoneInfo("Europe/Bucharest")
SLOTURI = [(8, 0), (13, 0), (18, 30), (20, 30)]
ID_RE = re.compile(r"id[=%]3?D?([A-Za-z0-9_-]{25,44})")
PARTE_RE = re.compile(r"Partie\s+(\d+)\s*:", re.I)
EDIT = ("mutation E($i: EditPostInput!) { editPost(input: $i) { "
        "... on PostActionSuccess { post { id dueAt } } "
        "... on MutationError { message } } }")
Q = ("query P($i: PostsInput!, $f: Int, $a: String) { posts(input: $i, first: $f, after: $a) "
     "{ pageInfo { hasNextPage endCursor } edges { node { id dueAt text "
     "assets { ... on VideoAsset { source } } } } } }")

surse = json.loads((_ROOT / "data" / "surse_franceza.json").read_text(encoding="utf-8"))
plan = json.loads((_ROOT / "data" / "fr_post_list.json").read_text(encoding="utf-8"))
nr_dupa_id = {p["id"]: p["nr"] for p in plan}
creat_dupa_id = {p["id"]: (p.get("created") or "") for p in plan}


def cu_rabdare(fn, *a, **k):
    for incercare in range(6):
        try:
            return fn(*a, **k)
        except RuntimeError as e:
            if "429" not in str(e):
                raise
            asteapta = 90 * (incercare + 1)
            print(f"   rate limit — astept {asteapta}s", flush=True)
            time.sleep(asteapta)
    raise RuntimeError("rate limit persistent")


org = cu_rabdare(default_org)
ch = next((c for c in cu_rabdare(channels, org) if c["name"] == CANAL), None)
if not ch:
    raise SystemExit(f"canalul {CANAL} nu e conectat")

out, after = [], None
while True:
    r = cu_rabdare(gql, Q, {"i": {"organizationId": org, "filter":
                   {"channelIds": [ch["id"]], "status": ["scheduled"]}}, "f": 100, "a": after})["posts"]
    out += [e["node"] for e in r["edges"]]
    pi = r.get("pageInfo") or {}
    if not pi.get("hasNextPage") or not pi.get("endCursor"):
        break
    after = pi["endCursor"]

for n in out:
    a = n.get("assets") or []
    m = ID_RE.search((a[0].get("source") or "") if a else "")
    n["_drive"] = m.group(1) if m else None
    n["_nr"] = nr_dupa_id.get(n["_drive"])
    n["_sursa"] = surse.get(n["_nr"] or "", "?")
    n["_creat"] = creat_dupa_id.get(n["_drive"], "")
    pm = PARTE_RE.search(n.get("text") or "")
    n["_parte"] = int(pm.group(1)) if pm else 1

fara_nr = [n for n in out if not n["_nr"]]
lucru = [n for n in out if n["_nr"]]
# Ordonam GRUPURI (un NR = un grup), nu fisiere: data unui grup e cea mai
# recenta dintre partile lui. Altfel partea 2, randata dupa partea 1, ar urca
# peste ea si captionurile ar iesi (2/2) inaintea lui (1/2).
_grup = {}
for n in lucru:
    _grup[n["_nr"]] = max(_grup.get(n["_nr"], ""), n["_creat"])
_rang = {nr: i for i, nr in enumerate(sorted(_grup, key=lambda k: _grup[k], reverse=True))}
lucru.sort(key=lambda n: (_rang[n["_nr"]], n["_parte"]))


def sloturi():
    acum = datetime.now(timezone.utc).astimezone(TZ) + timedelta(minutes=20)
    zi = acum.replace(hour=0, minute=0, second=0, microsecond=0)
    while True:
        for h, mi in SLOTURI:
            t = zi.replace(hour=h, minute=mi)
            if t > acum:
                yield t
        zi += timedelta(days=1)


g = sloturi()
print(f"=== {CANAL}: {len(out)} programate ({len(lucru)} recunoscute, {len(fara_nr)} fara NR) ===")
mutate, sarite, esecuri = 0, 0, 0
for n in lucru:
    nou = next(g)
    vechi = datetime.strptime(n["dueAt"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc).astimezone(TZ)
    eticheta = f"NR {n['_nr']:<4} {n['_sursa']:<9} p{n['_parte']}"
    if abs((nou - vechi).total_seconds()) < 60:
        sarite += 1
        continue
    if DRY:
        mutate += 1
        if mutate <= 12:
            print(f"   {vechi.strftime('%d %b %H:%M')} -> {nou.strftime('%a %d %b %H:%M')}  {eticheta}")
        continue
    # `mode` si `schedulingType` NU sunt optionale la editare, desi schema le
    # marcheaza asa: fara ele Buffer raspunde PostActionSuccess si lasa postarea
    # exact unde era. Prima rulare a raportat "76 mutate" fara sa miste nimic.
    inp = {"id": n["id"], "text": n.get("text") or "",
           "dueAt": nou.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "schedulingType": "automatic", "mode": "customScheduled",
           "assets": [{"video": {"url": "https://drive.usercontent.google.com/download"
                                        f"?id={n['_drive']}&export=download&confirm=t"}}]}
    if ch["service"] == "facebook":
        inp["metadata"] = {"facebook": {"type": "reel"}}
    res = (cu_rabdare(gql, EDIT, {"i": inp}) or {}).get("editPost") or {}
    msg = res.get("message") or ""
    # Reels cere si raport ~9:16, SI cel mult 1m30s. Prima varianta prindea doar
    # raportul, si opt clipuri HerStory mai lungi au ramas pe loc.
    if msg and ch["service"] == "facebook" and (
            "aspect ratio" in msg.lower() or "no longer than" in msg.lower()):
        inp["metadata"] = {"facebook": {"type": "post"}}
        res = (cu_rabdare(gql, EDIT, {"i": inp}) or {}).get("editPost") or {}
        msg = res.get("message") or ""
    if msg:
        esecuri += 1
        print(f"   ESUAT {eticheta}: {msg[:80]}")
    else:
        # Nu ne luam dupa lipsa erorii — verificam ora chiar intoarsa de API.
        intors = ((res.get("post") or {}).get("dueAt")) or ""
        bun = False
        try:
            t = datetime.strptime(intors, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            bun = abs((t - nou.astimezone(timezone.utc)).total_seconds()) < 60
        except ValueError:
            pass
        if bun:
            mutate += 1
        else:
            esecuri += 1
            print(f"   IGNORAT {eticheta}: am cerut {nou.strftime('%d %b %H:%M')}, "
                  f"a ramas la {intors or '?'}")
    time.sleep(1.1)

if fara_nr:
    print(f"   {len(fara_nr)} postari fara NR recunoscut — LASATE PE LOC, nu le mut orbeste")
print(f"{'de mutat' if DRY else 'mutate'}: {mutate}   deja pe loc: {sarite}   esuate: {esecuri}")
