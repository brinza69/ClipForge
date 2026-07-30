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
SLOTS_LOCAL = [(8, 0), (13, 0), (20, 30)]
TZ = ZoneInfo("Europe/Bucharest")   # zona reala: trecerea la ora de iarna nu muta sloturile
QUEUE_MAX = 10                      # Buffer Free: postari tinute in coada UNUI canal
LEAD_MINUTES = 15

PROFILES = {
    "tiktok": {
        "channel": targets.get("tiktok_channel_ro"),
        "plan": _ROOT / "data" / "pov_post_list.json",
        "record": "drive",
        "metadata": None,
    },
    "facebook": {
        "channel": targets.get("facebook_channel"),
        "plan": _ROOT / "data" / "fb_post_list_povestitor.json",
        "record": "buffer",
        # vertical 1080x1920 -> Reel; `post` ar aparea ca video obisnuit in feed
        "metadata": {"facebook": {"type": "reel"}},
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
         "{ edges { node { id status dueAt assets { ... on VideoAsset "
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


def done_from_buffer(org, channel_id):
    """Id-urile de Drive deja date lui Buffer pe canalul asta. `error` nu intra —
    o postare picata trebuie sa poata fi reincercata."""
    ids = set()
    for st in ("scheduled", "sending", "sent", "draft"):
        r = gql(POSTS, {"i": {"organizationId": org,
                              "filter": {"channelIds": [channel_id], "status": [st]}},
                        "f": 100})
        for e in r["posts"]["edges"]:
            for a in (e["node"].get("assets") or []):
                blob = f"{a.get('source') or ''} {a.get('thumbnail') or ''}"
                ids.update(DRIVE_ID_RE.findall(blob))
    return ids


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


def posted_folder(drive, dry):
    q = (f"'{DRIVE_ROOT}' in parents and name = '{POSTED}' "
         f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    found = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    if found:
        return found[0]["id"]
    if dry:
        return None
    fid = drive.files().create(
        body={"name": POSTED, "mimeType": "application/vnd.google-apps.folder",
              "parents": [DRIVE_ROOT]}, fields="id").execute()["id"]
    print(f"creat folderul {POSTED}/")
    return fid


def main():
    argv = sys.argv
    dry = "--dry" in argv
    which = argv[argv.index("--channel") + 1] if "--channel" in argv else "tiktok"
    if which not in PROFILES:
        raise SystemExit(f"--channel trebuie sa fie: {', '.join(PROFILES)}")
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
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

    drive = drive_service()
    if prof["record"] == "drive":
        folder_id = posted_folder(drive, dry)
        done_names, done_ids = set(), set()
        if folder_id:
            done_names = {f["name"] for f in drive.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="files(name)", pageSize=1000).execute().get("files", [])}
        print(f"deja in {POSTED}/: {len(done_names)}")
    else:
        folder_id, done_names = None, set()
        done_ids = done_from_buffer(org, ch["id"])
        print(f"deja date lui Buffer pe canalul asta: {len(done_ids)}")

    def is_done(f):
        return f["name"] in done_names or f.get("id") in done_ids

    slots = free_slots(datetime.now(timezone.utc), taken)
    sent = 0
    for g in load_groups(prof["plan"]):
        if sent >= n_max:
            break
        if not g["desc"]:
            print(f"  sarit {g['key']}: fara descriere (captionul ar fi gol)")
            continue
        todo = [f for f in g["files"] if not is_done(f)]
        if not todo:
            continue
        if len(todo) > n_max - sent:
            print(f"  stop: {g['key']} are {len(todo)} parti, mai am {n_max - sent} slot(uri) "
                  f"— il las intreg pentru rularea urmatoare")
            break
        for f in todo:
            when = next(slots)
            total = f.get("parts", 1)
            text = g["desc"] if total == 1 else f"{g['desc']} ({f['part']}/{total})"
            label = f"{g['key']} {f['name']} ({f.get('mb', '?')}MB)"
            if dry:
                print(f"  {when.strftime('%a %d %b %H:%M')} | {label}"
                      + (f"  ({f['part']}/{total})" if total > 1 else ""))
                sent += 1
                continue
            inp = {"channelId": ch["id"], "text": text,
                   "schedulingType": "automatic", "mode": "customScheduled",
                   "dueAt": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "assets": [{"video": {"url": f["url"]}}]}
            if prof["metadata"]:
                inp["metadata"] = prof["metadata"]
            res = (gql(CREATE, {"i": inp}) or {}).get("createPost") or {}
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
                                         removeParents=DRIVE_ROOT).execute()
                except Exception as e:  # noqa: BLE001
                    print(f"     ATENTIE: nu am putut muta in {POSTED}/ ({str(e)[:70]}) "
                          f"— rularea urmatoare l-ar reprograma; muta-l manual")
            sent += 1

    print(f"\nprogramate: {sent}" + ("  (--dry, nimic trimis)" if dry else ""))


if __name__ == "__main__":
    main()
