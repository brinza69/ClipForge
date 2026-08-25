"""Programeaza videoclipurile povestitor pe TikTok SAU pe Facebook, prin Buffer.

    python scripts/post_povestitor.py --dry                  # TikTok (implicit)
    python scripts/post_povestitor.py --channel facebook --dry
    python scripts/post_povestitor.py --channel facebook --limit 3

Ordinea e cea de creare pe Drive, cea mai veche intai (numarul NR nu e ordinea
de creare). Captionul e descrierea romana; ce n-are descriere nu se posteaza.

Doua reguli pe care scriptul le impune:

1. **Nu refolosi un slot deja ocupat.** Coada Free tine 10 postari pe canal, deci
   o coada aproape plina are slotul liber la SFARSIT. Un generator care merge
   inainte de la "acum" ar da un slot pe care alta postare il ocupa deja.
2. **Nu imparti un video cu parti intre doua rulari.** Partile primesc `(1/3)`,
   `(2/3)`… ca sa nu iasa postari consecutive cu text identic (= spam). Daca
   grupul nu incape in sloturile ramase, asteapta rularea urmatoare.

Evidenta a ce s-a postat difera pe canal, si asta conteaza:
  - TikTok   : folderul `posted/` de pe Drive (fisierul e mutat acolo dupa ce
               Buffer accepta). Drive pastreaza ID-ul la mutare, deci URL-ul pe
               care Buffer il descarca la publicare ramane valid.
  - Facebook : se citeste din Buffer (id-ul de Drive se extrage din URL-ul
               videoclipului postarii). Folderul `posted/` NU poate fi folosit —
               el inseamna "predat pentru TikTok", deci Facebook ar sari peste
               tot ce a primit TikTok. Postarile in stare `error` NU conteaza ca
               facute, ca sa se poata reincerca.
"""
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

import targets  # noqa: E402
from buffer_api import channel_by_name, default_org, gql  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from services.drive_upload import _resolve_credentials  # noqa: E402

DRIVE_ROOT = targets.get("povestitor_drive_folder")
POSTED = "posted"
# 4 postari/zi pe toate canalele (cerut 24 aug 2026). Al patrulea slot e
# seara, nu la pranz: mediana vizualizarilor pe ultimele 45 de zile arata
# 20:00 cea mai buna ora pe toate cele patru canale si 13:00 cea mai slaba.
SLOTS_LOCAL = [(8, 0), (13, 0), (18, 30), (20, 30)]
TZ = ZoneInfo("Europe/Bucharest")   # zona reala: trecerea la ora de iarna nu muta sloturile
# Cate postari poate tine coada UNUI canal. 10 era plafonul planului Free;
# pe plan platit nu mai exista, deci se ridica din CLIPFORGE_QUEUE_MAX.
QUEUE_MAX = int(os.environ.get("CLIPFORGE_QUEUE_MAX", "10"))
LEAD_MINUTES = 15

def _sufix_ro(desc, part, total):
    """`... (1/3)` la coada. Asa au fost publicate deja postarile romanesti —
    schimbarea formei acum ar face seria inconsecventa la mijloc."""
    return desc if total == 1 else f"{desc} ({part}/{total})"


def _prefix_fr(desc, part, total):
    """`Partie 1 : ...` in fata. Ceruta explicit pentru pista franceza, ca
    spectatorul sa vada din prima ca e o serie, nu la sfarsitul captionului."""
    return desc if total == 1 else f"Partie {part} : {desc}"


# Clasamentul (scor + titlu de YouTube) produs de scripts/rank_videos.py.
_RANKING = {}
_rank_file = _ROOT / "data" / "pov_ranking.json"
if _rank_file.exists():
    _RANKING = json.loads(_rank_file.read_text(encoding="utf-8"))


def _meta_youtube(g, f, part, total):
    """YouTube cere TITLU separat de descriere — fara el, Shorts-ul ramane fara
    nume sau il ia din primele cuvinte ale descrierii. Titlul vine din clasament;
    daca lipseste, se taie prima propozitie a descrierii.
    `madeForKids` e o declaratie ceruta de YouTube: continutul asta nu e pentru
    copii. `isAiGenerated` NU se seteaza — decizia utilizatorului."""
    r = _RANKING.get(str(g["key"])) or {}
    titlu = r.get("titlu") or (g["desc"].split(".")[0] or g["desc"])[:95]
    if total > 1:
        titlu = f"{titlu[:88]} ({part}/{total})"
    # Categoria e OBLIGATORIE la YouTube ("YouTube posts require a category").
    # 24 = Entertainment, potrivit pentru povestiri narate; 22 = People & Blogs
    # e alternativa. Se schimba din CLIPFORGE_YT_CATEGORY.
    return {"youtube": {"title": titlu[:100], "privacy": "public",
                        "madeForKids": False,
                        "categoryId": os.environ.get("CLIPFORGE_YT_CATEGORY", "24")}}


PROFILES = {
    "tiktok": {
        "channel": targets.get("tiktok_channel_ro"),
        "plan": _ROOT / "data" / "pov_post_list.json",
        "record": "drive",
        "metadata": None,
        "caption": _sufix_ro,
    },
    "facebook": {
        "channel": targets.get("facebook_channel"),
        # acelasi plan ca TikTok: fisierele sunt aceleasi, difera doar evidenta
        # a ce s-a postat (folderul posted/ vs. istoricul din Buffer)
        "plan": _ROOT / "data" / "pov_post_list.json",
        "record": "buffer",
        # vertical 1080x1920 -> Reel; `post` ar aparea ca video obisnuit in feed
        "metadata": {"facebook": {"type": "reel"}},
        "caption": _sufix_ro,
    },
    # Pista ENGLEZA: alt folder de Drive, descrieri din coloana L. Ambele
    # canale povestitor au trecut pe engleza (TikTok 27 aug, Facebook 25 aug).
    "tiktok_en": {
        "channel": targets.get("tiktok_channel_ro"),
        "plan": _ROOT / "data" / "pov_en_post_list.json",
        "drive_root": targets.get("povestitor_en_drive_folder"),
        "record": "drive",
        "metadata": None,
        "caption": _sufix_ro,
    },
    "facebook_en": {
        "channel": targets.get("facebook_channel"),
        "plan": _ROOT / "data" / "pov_en_post_list.json",
        "drive_root": targets.get("povestitor_en_drive_folder"),
        "record": "buffer",
        "metadata": {"facebook": {"type": "reel"}},
        "caption": _sufix_ro,
    },
    "narativ": {
        "channel": "Narativ",          # canal YouTube, nu e in targets.json
        "plan": _ROOT / "data" / "pov_post_list.json",
        "record": "buffer",
        "metadata": _meta_youtube,
        "caption": _sufix_ro,
    },
    "facebook_fr": {
        "channel": targets.get("facebook_channel_fr"),
        "plan": _ROOT / "data" / "fr_post_list.json",
        "record": "buffer",
        "metadata": {"facebook": {"type": "reel"}},
        "caption": _prefix_fr,
    },
    "franceza": {
        "channel": targets.get("tiktok_channel_fr"),
        "plan": _ROOT / "data" / "fr_post_list.json",
        "record": "buffer",
        "metadata": None,
        "caption": _prefix_fr,
    },
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
         "{ edges { node { id status dueAt text assets { ... on VideoAsset "
         "{ source thumbnail } } } } } }")
DRIVE_ID_RE = re.compile(r"id[=%]3?D?([A-Za-z0-9_-]{25,44})")


def load_groups(path):
    """Amandoua formatele de plan -> aceeasi forma: [{key, desc, files:[...]}]."""
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    groups, order = {}, []
    if raw and isinstance(raw[0], dict) and "files" in raw[0]:      # planul TikTok
        for v in raw:
            files = [dict(f, parts=len(v["files"])) for f in v["files"]]
            groups[v["nr"]] = {"key": v["nr"], "desc": v.get("desc", ""), "files": files}
            order.append(v["nr"])
    else:                                                            # lista plata (Facebook)
        for r in raw:
            k = r.get("nr") or r["name"]
            if k not in groups:
                groups[k] = {"key": k, "desc": r.get("desc", ""), "files": []}
                order.append(k)
            groups[k]["files"].append(r)
    return [groups[k] for k in order]


def queue_state(org, channel_id):
    """(cate programate, sloturile ocupate). Plafonul Free de 10 e PE CANAL, deci
    numaratoarea pe organizatie ar amesteca cozile si ar raporta gresit."""
    r = gql(POSTS, {"i": {"organizationId": org,
                          "filter": {"channelIds": [channel_id],
                                     "status": ["scheduled"]}}, "f": 100})
    taken = set()
    for e in r["posts"]["edges"]:
        due = e["node"].get("dueAt")
        if not due:
            continue
        t = datetime.strptime(due, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        loc = t.astimezone(TZ)
        taken.add((loc.date(), loc.hour, loc.minute))
    return len(r["posts"]["edges"]), taken


def _norm(s):
    return "".join((s or "").split()).lower()


def done_from_buffer(org, channel_id):
    """(id-uri de Drive, captions) deja pe canalul asta.

    Id-ul de Drive prinde doar ce a trecut prin scriptul asta. Ce a fost postat
    MANUAL a ajuns pe platforma direct, fara URL de Drive — dar Buffer importa
    istoricul canalului la conectare, deci captionul lui exista. Fara potrivirea
    pe text, patru videoclipuri franceze publicate de mana ar fi fost repostate.

    `error` nu intra in niciuna: o postare picata trebuie sa poata fi reincercata.
    """
    ids, texts = set(), set()
    for st in ("scheduled", "sending", "sent", "draft"):
        r = gql(POSTS, {"i": {"organizationId": org,
                              "filter": {"channelIds": [channel_id], "status": [st]}},
                        "f": 100})
        for e in r["posts"]["edges"]:
            t = _norm(e["node"].get("text"))
            if t:
                texts.add(t)
            for a in (e["node"].get("assets") or []):
                blob = f"{a.get('source') or ''} {a.get('thumbnail') or ''}"
                ids.update(DRIVE_ID_RE.findall(blob))
    return ids, texts


def free_slots(now, taken):
    earliest = now.astimezone(TZ) + timedelta(minutes=LEAD_MINUTES)
    day = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
    while True:
        for h, mi in SLOTS_LOCAL:
            t = day.replace(hour=h, minute=mi)
            if t > earliest and (t.date(), h, mi) not in taken:
                yield t
        day += timedelta(days=1)


def drive_service():
    creds, _, err = _resolve_credentials()
    if not creds:
        raise SystemExit(f"Google Drive: {err}")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def posted_folder(drive, dry, root=None):
    """`posted/` din folderul profilului. Pista engleza are alt folder de Drive,
    deci si alta evidenta — altfel un clip englez ar parea postat pentru ca
    exista un fisier cu acelasi NR in `posted/`-ul romanesc."""
    root = root or DRIVE_ROOT
    q = (f"'{root}' in parents and name = '{POSTED}' "
         f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    found = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    if found:
        return found[0]["id"]
    if dry:
        return None
    fid = drive.files().create(
        body={"name": POSTED, "mimeType": "application/vnd.google-apps.folder",
              "parents": [root]}, fields="id").execute()["id"]
    print(f"creat folderul {POSTED}/")
    return fid


def main():
    argv = sys.argv
    dry = "--dry" in argv
    which = argv[argv.index("--channel") + 1] if "--channel" in argv else "tiktok"
    if which not in PROFILES:
        raise SystemExit(f"--channel trebuie sa fie: {', '.join(PROFILES)}")
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    # --first 226,227 le urca in fata cozii fara sa schimbe planul; restul
    # ramane in ordinea de creare. Util cand vrei sa vezi repede un lot nou.
    doar = [s.strip() for s in argv[argv.index("--first") + 1].split(",")
            if s.strip()] if "--first" in argv else []
    prof = PROFILES[which]

    org = default_org()
    ch = channel_by_name(prof["channel"], org)
    used, taken = queue_state(org, ch["id"])
    free = QUEUE_MAX - used
    n_max = max(0, min(limit, free) if limit is not None else free)
    print(f"canal: {ch['name']} ({which})   coada: {used}/{QUEUE_MAX} -> pot programa {n_max}")
    if n_max <= 0:
        print("coada e plina — reia dupa ce publica." if free <= 0 else "--limit 0, nimic de facut.")
        return

    # Doar evidenta pe folder are nevoie de Drive. Canalele care isi citesc
    # istoricul din Buffer nu-l ating deloc, deci o expirare a tokenului Google
    # (se intampla saptamanal) nu trebuie sa le blocheze si pe ele.
    drive = drive_service() if prof["record"] == "drive" else None
    radacina = prof.get("drive_root") or DRIVE_ROOT
    if prof["record"] == "drive":
        folder_id = posted_folder(drive, dry, radacina)
        done_names, done_ids = set(), set()
        if folder_id:
            done_names = {f["name"] for f in drive.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="files(name)", pageSize=1000).execute().get("files", [])}
        done_texts = set()
        print(f"deja in {POSTED}/: {len(done_names)}")
    else:
        folder_id, done_names = None, set()
        done_ids, done_texts = done_from_buffer(org, ch["id"])
        print(f"deja pe canal: {len(done_ids)} prin Drive, {len(done_texts)} captions "
              f"(inclusiv istoricul importat, adica ce a fost postat manual)")

    def is_done(f, desc=""):
        if f["name"] in done_names or f.get("id") in done_ids:
            return True
        # Postarile manuale nu au URL de Drive — se recunosc dupa caption.
        # Descrierile au 180-300 de caractere, deci un prefix de 40 e destul de
        # distinctiv incat sa nu dea fals pozitiv.
        key = _norm(desc)[:40]
        return bool(key) and any(x.startswith(key) for x in done_texts)

    slots = free_slots(datetime.now(timezone.utc), taken)
    sent = 0
    oprit = False          # rate limit: opreste TOT, nu doar grupul curent
    grupuri = load_groups(prof["plan"])
    if "--invers" in argv:
        # Ordinea de pe Drive, dar de la cel mai nou spre cel mai vechi.
        grupuri = list(reversed(grupuri))
    if doar:
        # ordinea din --first conteaza (e un clasament), nu ordinea din plan
        rang = {n: i for i, n in enumerate(doar)}
        fata = sorted((g for g in grupuri if str(g["key"]) in doar),
                      key=lambda g: rang[str(g["key"])])
        lipsa = [d for d in doar if d not in {str(g["key"]) for g in fata}]
        if lipsa:
            print(f"  --first: nu am gasit in plan {lipsa}")
        grupuri = fata + [g for g in grupuri if str(g["key"]) not in doar]
    for g in grupuri:
        if sent >= n_max or oprit:
            break
        if not g["desc"]:
            print(f"  sarit {g['key']}: fara descriere (captionul ar fi gol)")
            continue
        todo = [f for f in g["files"] if not is_done(f, g["desc"])]
        if not todo:
            continue
        if len(todo) > n_max - sent:
            print(f"  stop: {g['key']} are {len(todo)} parti, mai am {n_max - sent} slot(uri) "
                  f"— il las intreg pentru rularea urmatoare")
            break
        for f in todo:
            when = next(slots)
            total = f.get("parts", 1)
            text = prof["caption"](g["desc"], f.get("part", 1), total)
            label = f"{g['key']} {f['name']} ({f.get('mb', '?')}MB)"
            if dry:
                marcaj = text[:34] + "…" if total > 1 else ""
                print(f"  {when.strftime('%a %d %b %H:%M')} | {label}"
                      + (f"   caption: {marcaj}" if marcaj else ""))
                sent += 1
                continue
            inp = {"channelId": ch["id"], "text": text,
                   "schedulingType": "automatic", "mode": "customScheduled",
                   "dueAt": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "assets": [{"video": {"url": f["url"]}}]}
            md = prof["metadata"]
            if callable(md):
                md = md(g, f, f.get("part", 1), total)
            if md:
                inp["metadata"] = md
            try:
                res = (gql(CREATE, {"i": inp}) or {}).get("createPost") or {}
                # Reels cere fix 9:16. Cateva randari vechi au iesit 1080x1980
                # (raport 0.5455) si sunt respinse. Merg totusi ca postare
                # obisnuita in feed — mai bine acolo decat deloc, si coada nu se
                # blocheaza pe ele.
                # Reels are conditii pe care un videoclip vechi nu le respecta:
                # raport ~9:16 si cel mult 1m30s. Postarea obisnuita in feed nu
                # le are, deci acolo merge — mai bine in feed decat deloc, si
                # mai ales coada nu se blocheaza pe un singur fisier.
                e_reel = isinstance(prof["metadata"], dict) and                     prof["metadata"].get("facebook", {}).get("type") == "reel"
                msg = (res.get("message") or "").lower()
                if e_reel and ("aspect ratio" in msg or "no longer than" in msg):
                    motiv = "prea lung" if "no longer than" in msg else "raport gresit"
                    print(f"  {label}: {motiv} pentru Reels — pun ca postare in feed")
                    alt = dict(inp, metadata={"facebook": {"type": "post"}})
                    res = (gql(CREATE, {"i": alt}) or {}).get("createPost") or {}
            except RuntimeError as e:
                # Buffer are DOUA plafoane: 100 cereri/15min si 250/24h. Pe cel
                # de 15 minute se asteapta si se reia; ce s-a programat deja
                # ramane programat, iar reluarea sare peste el.
                if "429" in str(e):
                    fereastra = "15 minute" if '"15m"' in str(e) else "24 de ore"
                    print(f"  rate limit Buffer ({fereastra}) dupa {sent} postari "
                          f"— reia dupa ce trece")
                    oprit = True
                    break
                raise
            if res.get("message"):
                # oprire, nu continuare: un grup pe jumatate programat ar lasa
                # partea 2 fara partea 1 si ar strica ordinea sloturilor
                print(f"  ESUAT {label}: {res['message'][:150]}")
                print("  ma opresc — verifica si reia")
                return
            print(f"  {when.strftime('%a %d %b %H:%M')} | {label} -> "
                  f"{(res.get('post') or {}).get('id')}")
            if prof["record"] == "drive" and folder_id:
                try:
                    drive.files().update(fileId=f["id"], addParents=folder_id,
                                         removeParents=radacina).execute()
                except Exception as e:  # noqa: BLE001
                    print(f"     ATENTIE: nu am putut muta in {POSTED}/ ({str(e)[:70]}) "
                          f"— rularea urmatoare l-ar reprograma; muta-l manual")
            sent += 1

    print(f"\nprogramate: {sent}" + ("  (--dry, nimic trimis)" if dry else ""))


if __name__ == "__main__":
    main()
