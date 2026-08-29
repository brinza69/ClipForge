r"""Programeaza cardurile cu text — o postare-imagine pe zi, per canal.

Postarile video au productie grea in spate; astea sunt ieftine si tin ritmul
zilnic intre clipuri. Cardurile se genereaza cu `carduri_text.py` si se urca
intr-un subfolder `CARDURI TEXT - <rol>` de langa videoclipurile rolului.

Buffer accepta imagini pe TikTok — verificat pe viu pe 29 aug 2026 cu o postare
de proba, creata si stearsa imediat: forma buna e `assets: [{image: {url}}]`,
NU `photo` si nici `video`. `post_povestitor.py` trimite doar `video`, de aia
cardurile au nevoie de scriptul asta si nu merg prin acela.

CE MANANCA SLOTURI: fiecare card ocupa un loc in coada programata a canalului,
alaturi de clipuri. Daca vezi coada plina si videoclipuri neprogramate, asta e
cauza, nu un defect.

Ora 12:00 e aleasa in afara sloturilor de video ale AMBELOR roluri: povestitorul
posteaza la 08:00, 13:00, 18:30 si 20:30 (`SLOTS_LOCAL` in `post_povestitor.py`),
naratorul la 05:00, 10:00, 15:30 si 17:30. Doua postari pe acelasi minut s-ar
bate pe slot.

    server\.venv\Scripts\python.exe scripts\posteaza_carduri.py --canal narator [--dry] [--limit 3]
"""
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from buffer_api import gql, default_org, channel_by_name  # noqa: E402
from services.drive_upload import list_folder_files  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Bucharest")
except Exception:                                  # noqa: BLE001
    TZ = timezone(timedelta(hours=3))

STARE = _ROOT / "data" / "carduri_postate.json"
CATALOG = _ROOT / "data" / "carduri_catalog.json"
ORA = (12, 0)                                      # in afara sloturilor video
PLAFON = int(os.environ.get("CLIPFORGE_QUEUE_MAX", "10"))

CANALE = {
    # `narator` e INCHIS: singurul canal narator conectat e naratorul.ro, care e
    # TikTok, iar cardurile nu se pun pe TikTok (cerut pe 29 aug 2026). Cele 20
    # de carduri raman pe Drive in `CARDURI TEXT - narator` — daca apare o pagina
    # de Facebook pentru narator, se sterge doar linia `inchis`.
    "narator": ("narator_channel", "narator_drive_folder", "CARDURI TEXT - narator",
                "cardurile nu se pun pe TikTok; naratorul n-are pagina de Facebook"),
    "povestitor": ("facebook_channel", "povestitor_drive_folder",
                   "CARDURI TEXT - Facebook", None),
}

CREATE = """
mutation P($i: CreatePostInput!) {
  createPost(input: $i) {
    ... on PostActionSuccess { post { id status dueAt } }
    ... on MutationError { message }
  }
}
"""
POSTS = ("query P($i: PostsInput!, $f: Int) { posts(input: $i, first: $f) "
         "{ edges { node { id dueAt } } } }")


def _sub_folder(parinte, nume):
    """Id-ul subfolderului de carduri. Se cauta dupa nume, ca sa nu fie nevoie
    de inca o cheie in targets.json pentru fiecare rol."""
    for f in list_folder_files(parinte).get("files", []):
        if (f.get("name") or "").strip().lower() == nume.lower():
            return f["id"]
    raise SystemExit("nu gasesc subfolderul '" + nume + "' in " + str(parinte))


def _ocupate(org, channel_id):
    r = gql(POSTS, {"i": {"organizationId": org,
                          "filter": {"channelIds": [channel_id],
                                     "status": ["scheduled"]}}, "f": 100})
    zile, n = set(), 0
    for e in r["posts"]["edges"]:
        n += 1
        due = e["node"].get("dueAt")
        if due:
            t = datetime.strptime(due, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            loc = t.astimezone(TZ)
            if (loc.hour, loc.minute) == ORA:
                zile.add(loc.date())
    return n, zile


def main():
    argv = sys.argv[1:]
    if "--canal" not in argv:
        raise SystemExit("--canal trebuie sa fie: " + ", ".join(CANALE))
    care = argv[argv.index("--canal") + 1]
    if care not in CANALE:
        raise SystemExit("--canal trebuie sa fie: " + ", ".join(CANALE))
    dry = "--dry" in argv
    limita = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 99

    cheie_canal, cheie_folder, nume_sub, inchis = CANALE[care]
    if inchis and "--si-inchise" not in argv:
        print("canalul '" + care + "' e inchis pentru carduri: " + inchis)
        print("deschise: " + ", ".join(k for k, v in CANALE.items() if not v[3])
              + ". Cu --si-inchise merge oricum.")
        return
    canal = targets.get(cheie_canal)
    org = default_org()
    ch = channel_by_name(canal, org)

    fid = _sub_folder(targets.get(cheie_folder), nume_sub)
    fisiere = {f["name"]: f for f in list_folder_files(fid).get("files", [])
               if (f.get("name") or "").lower().endswith((".jpg", ".jpeg", ".png"))}
    texte = {}
    if CATALOG.exists():
        for c in json.loads(CATALOG.read_text(encoding="utf-8")):
            texte[c["nume"]] = c.get("text", "")

    facute = json.loads(STARE.read_text(encoding="utf-8")) if STARE.exists() else {}
    ale_mele = facute.setdefault(care, {})

    n_coada, zile_ocupate = _ocupate(org, ch["id"])
    print("canal %s: %d in coada (plafon %d), %d carduri pe Drive, %d postate deja"
          % (canal, n_coada, PLAFON, len(fisiere), len(ale_mele)))

    zi = datetime.now(TZ).date()
    trimise = 0
    for nume in sorted(fisiere):
        if nume in ale_mele:
            continue
        if trimise >= limita:
            print("  stop: limita " + str(limita)); break
        if n_coada + trimise >= PLAFON:
            print("  stop: coada e la plafon (" + str(PLAFON) + ")"); break
        while zi in zile_ocupate or datetime.combine(
                zi, datetime.min.time()).replace(hour=ORA[0], minute=ORA[1],
                                                 tzinfo=TZ) <= datetime.now(TZ):
            zi += timedelta(days=1)
        cand = datetime.combine(zi, datetime.min.time()).replace(
            hour=ORA[0], minute=ORA[1], tzinfo=TZ)
        text = texte.get(nume, "")
        if dry:
            print("  %s %02d:%02d | %-12s %s" % (cand.strftime("%a %d %b"), ORA[0], ORA[1],
                                                 nume, text[:44]))
            zile_ocupate.add(zi); trimise += 1
            continue
        inp = {"channelId": ch["id"], "text": text,
               "schedulingType": "automatic", "mode": "customScheduled",
               "dueAt": cand.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "assets": [{"image": {"url": fisiere[nume]["download_url"]}}]}
        r = (gql(CREATE, {"i": inp}) or {}).get("createPost") or {}
        if r.get("message"):
            print("  " + nume + ": RESPINS — " + r["message"][:120]); break
        post = r.get("post") or {}
        ale_mele[nume] = {"id": post.get("id"), "dueAt": post.get("dueAt")}
        STARE.write_text(json.dumps(facute, ensure_ascii=False, indent=1), encoding="utf-8")
        zile_ocupate.add(zi); trimise += 1
        print("  %s %02d:%02d | %-12s programat" % (cand.strftime("%a %d %b"), ORA[0], ORA[1], nume))

    print("\nprogramate: " + str(trimise))


if __name__ == "__main__":
    main()
