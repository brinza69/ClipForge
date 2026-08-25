"""Unde am ramas — raportul de continuare al rigului.

Se ruleaza la inceputul fiecarei sesiuni (vezi CLAUDE.md). Raspunde la o singura
intrebare: **ce mai e de facut?** Nu modifica nimic, doar citeste.

    server\\.venv\\Scripts\\python.exe scripts\\stare.py

Nu inlocuieste handoff-urile din docs/ — alea explica DE CE s-a facut ceva.
Asta spune CE a ramas, acum.
"""
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "server"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("CLIPFORGE_DATA_DIR", os.path.join(_ROOT, "data"))

import targets  # noqa: E402

RO_SHEET = targets.get("pov_sheet_id")
EN_FOLDER = targets.get("povestitor_en_drive_folder")
BACKENDS = {"A": 8420, "B": 8421}


def titlu(t):
    print(f"\n{'=' * 66}\n{t}\n{'=' * 66}")


def de_facut():
    """Ce a ramas neterminat dintr-o sesiune anterioara.

    Un supraveghetor pornit in fundal moare la oprirea calculatorului. Fara
    urma scrisa pe disc, treaba lui dispare fara sa spuna nimeni nimic — asa a
    ramas reordonarea Contouse pe 25 august. Se scrie in `data/de_facut.json`
    si se sterge de acolo cand s-a rezolvat.
    """
    import pathlib
    p = pathlib.Path(_ROOT) / "data" / "de_facut.json"
    if not p.exists():
        return
    try:
        lista = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    if not lista:
        return
    titlu("DE FACUT — ramas din sesiunea anterioara")
    for x in lista:
        print(f"  * {x.get('ce')}")
        if x.get("de_ce"):
            print(f"      de ce: {x['de_ce']}")
        if x.get("cum"):
            print(f"      cum:   {x['cum']}")
    print()
    print(f"  (sterge din {p.name} ce ai rezolvat)")


def backenduri():
    titlu("BACKEND-URI SI RANDARI")
    for nume, port in BACKENDS.items():
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/jobs/?status=running,queued", timeout=20) as r:
                j = json.loads(r.read())
            if not j:
                print(f"  {nume} :{port}  UP, liber")
            for x in j:
                print(f"  {nume} :{port}  {x.get('id')} {x.get('status'):<8} "
                      f"{round((x.get('progress') or 0) * 100):>3}%  "
                      f"{(x.get('progress_message') or '')[:34]}")
        except Exception:
            print(f"  {nume} :{port}  NU RASPUNDE — porneste rigul (scripts/rig_boot.ps1)")


def credite():
    titlu("CREDITE ELEVENLABS")
    try:
        from services.elevenlabs import get_api_key
        k = get_api_key()
        if not k:
            print("  nicio cheie configurata in data/tts_config.json"); return
        r = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription",
                                   headers={"xi-api-key": k})
        d = json.loads(urllib.request.urlopen(r, timeout=30).read())
        lim, used = d.get("character_limit") or 0, d.get("character_count") or 0
        ram = lim - used
        print(f"  {ram:,} ramase din {lim:,}  (plan {d.get('tier')})")
        ts = d.get("next_character_count_reset_unix")
        if ts:
            print(f"  reset: {datetime.fromtimestamp(ts).strftime('%d %b %Y')}")
        # ~1000-2500 caractere pe varianta; un rand cu 2 roluri poate cere 5000
        print(f"  ajung pentru ~{ram // 2500} randuri cu 2 roluri, "
              f"sau ~{ram // 1200} cu unul singur")
        if ram < 3000:
            print("  ATENTIE: sub pragul unui rand cu doua roluri")
    except Exception as e:
        print(f"  nu pot citi: {type(e).__name__} {str(e)[:70]}")


def cozi():
    titlu("COZI BUFFER")
    try:
        from buffer_api import gql, default_org, channels
        Q = ("query P($i: PostsInput!, $f: Int, $a: String) { posts(input: $i, first: $f, "
             "after: $a) { pageInfo { hasNextPage endCursor } edges { node { dueAt "
             "assets { ... on VideoAsset { source } } } } } }")
        org = default_org()
        acum = datetime.now(timezone.utc)
        id_re = re.compile(r"id[=%]3?D?([A-Za-z0-9_-]{25,44})")

        def fid(n):
            a = n.get("assets") or []
            m = id_re.search((a[0].get("source") or "") if a else "")
            return m.group(1) if m else None

        for c in sorted(channels(org), key=lambda x: x["name"]):
            n_err = 0
            dues = []
            programate_ids = set()
            for stare in ("scheduled", "error"):
                out, after = [], None
                while True:
                    r = gql(Q, {"i": {"organizationId": org,
                                      "filter": {"channelIds": [c["id"]], "status": [stare]}},
                                "f": 100, "a": after})["posts"]
                    out += [e["node"] for e in r["edges"]]
                    pi = r.get("pageInfo") or {}
                    if not pi.get("hasNextPage") or not pi.get("endCursor"):
                        break
                    after = pi["endCursor"]
                if stare == "error":
                    # Numaram DOAR erorile care mai cer ceva. O postare esuata al
                    # carei fisier e deja reprogramat e istorie, nu treaba de facut —
                    # altfel aceleasi 7 erori vechi apar alarmant la fiecare sesiune.
                    n_err = sum(1 for x in out
                                if (f := fid(x)) and f not in programate_ids)
                else:
                    dues = [datetime.strptime(x["dueAt"], "%Y-%m-%dT%H:%M:%S.%fZ")
                            .replace(tzinfo=timezone.utc) for x in out if x.get("dueAt")]
                    programate_ids = {f for x in out if (f := fid(x))}
            zile = (max(dues) - acum).days if dues else 0
            steag = " ".join(k for k in ("isDisconnected", "isLocked", "isQueuePaused")
                             if c.get(k))
            alarma = "  <-- SE GOLESTE" if zile <= 2 else ""
            print(f"  {c['name']:<24} {len(dues):>3} programate, tine {zile:>2} zile"
                  f"{'  erori: ' + str(n_err) if n_err else ''}{'  [' + steag + ']' if steag else ''}"
                  f"{alarma}")
    except Exception as e:
        print(f"  nu pot citi Buffer: {type(e).__name__} {str(e)[:70]}")


def randuri_de_randat():
    titlu("RANDURI DE RANDAT")
    try:
        from services.sheets import _service
        import targets
        g = lambda r, k: (r[k].strip() if len(r) > k and r[k] else "")
        ro = _service().spreadsheets().values().get(
            spreadsheetId=RO_SHEET, range="'Sheet1'!A1:L400").execute().get("values", [])
        cu_link = [r for r in ro if g(r, 1).startswith("http")]
        fara = {"narator": 0, "comentator": 0, "povestitor": 0}
        for r in cu_link:
            if not g(r, 5):
                fara["narator"] += 1
            if not g(r, 6):
                fara["comentator"] += 1
            if not g(r, 7):
                fara["povestitor"] += 1
        print(f"  sheet ROMANESC: {len(cu_link)} randuri cu link")
        for k, v in fara.items():
            print(f"     fara {k:<12} {v}")
        # REGULA (25 aug 2026): restanta veche NU se mai randeaza. Narator si
        # comentator se fac DOAR pe linkurile noi pe care le da utilizatorul.
        # Cifrele de mai sus raman ca inventar, nu ca lista de lucru.
        print("     ^ restanta VECHE — nu se randeaza. Narator/comentator doar pe")
        print("       linkurile noi primite. Povestitorul romanesc e inchis (canalele")
        print("       povestitor au trecut pe engleza pe 27 aug).")
        fr = _service().spreadsheets().values().get(
            spreadsheetId=targets.get("fr_sheet_id"),
            range="'Victoria'!A1:F400").execute().get("values", [])
        fr_link = [r for r in fr if g(r, 1).startswith("http")]
        fr_lipsa = [g(r, 0) for r in fr_link if not g(r, 4)]
        print(f"  sheet VICTORIA (FR): {len(fr_link)} randuri, {len(fr_lipsa)} fara video"
              + (f" -> NR {', '.join(fr_lipsa[:10])}" if fr_lipsa else ""))
    except Exception as e:
        print(f"  nu pot citi sheet-urile: {type(e).__name__} {str(e)[:70]}")


def coperti():
    titlu("COPERTI TIKTOK (povestitorul.ro, engleza)")
    try:
        from buffer_api import gql, default_org, channel_by_name
        from services.drive_upload import _resolve_credentials
        from googleapiclient.discovery import build
        creds, _, _e = _resolve_credentials()
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        Q = ("query P($i: PostsInput!, $f: Int) { posts(input: $i, first: $f) "
             "{ edges { node { id assets { ... on VideoAsset { source thumbnail } } } } } }")
        org = default_org()
        ch = channel_by_name("povestitorul.ro", org)
        r = gql(Q, {"i": {"organizationId": org, "filter": {"channelIds": [ch["id"]],
                    "status": ["scheduled"]}}, "f": 100})["posts"]["edges"]
        en = 0
        for e in r:
            a = e["node"].get("assets") or []
            m = re.search(r"id[=%]3?D?([A-Za-z0-9_-]{25,44})", (a[0].get("source") or "") if a else "")
            if not m:
                continue
            try:
                meta = drive.files().get(fileId=m.group(1), fields="parents").execute()
            except Exception:
                continue
            if EN_FOLDER in (meta.get("parents") or []):
                en += 1
        print(f"  {en} postari engleze programate pe canal")
        print("  (cate au deja coperta se vede in scratchpad/aplica_cover.py — ruleaza-l "
              "cu --dry ca sa vezi ce a mai ramas)")
    except Exception as e:
        print(f"  nu pot verifica: {type(e).__name__} {str(e)[:70]}")


def procese():
    titlu("PROCESE DE FUNDAL")
    try:
        import subprocess
        out = subprocess.run(["wmic", "process", "where", "name='python.exe'",
                              "get", "commandline"], capture_output=True, text=True,
                             timeout=60).stdout
        interesante = [l.strip() for l in out.splitlines()
                       if any(k in l for k in ("dispatch", "runner", "lot_", "faza",
                                               "remux", "verifica_sunet", "aplica_cover"))]
        if not interesante:
            print("  niciun dispecer sau lot in rulare")
        for l in interesante[:8]:
            print(f"  {l[-90:]}")
    except Exception:
        print("  nu pot lista procesele")


if __name__ == "__main__":
    print(f"STARE CLIPFORGE — {datetime.now().strftime('%d %b %Y, %H:%M')}")
    de_facut()
    backenduri()
    credite()
    cozi()
    randuri_de_randat()
    coperti()
    procese()
    print("\nHandoff-urile cu context: docs/handoff-*.md")
