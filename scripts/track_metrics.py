"""Inregistreaza zilnic performanta postarilor, ca sa se poata compara ritmuri.

Intrebarea la care raspunde: cate postari pe zi dau cele mai multe vizualizari PE
POSTARE. Nu se poate afla dintr-o singura citire — de-aia scriptul adauga un
instantaneu pe zi in `data/metrics_history.jsonl` si abia dupa 1-2 saptamani
compararea are sens.

Ce compara: media de vizualizari pe postare, grupata dupa cate postari a avut
canalul in ziua respectiva. O zi cu 3 postari care aduce mai putine vizualizari
PE POSTARE decat una cu 2 inseamna ca ritmul isi manaca propriul public.

Vizualizarile cresc zile intregi dupa publicare, deci o postare de ieri nu se
compara cu una de acum doua saptamani. `--min-varsta` (implicit 3 zile) exclude
postarile prea proaspete din comparatie.

    python scripts/track_metrics.py            # instantaneu + raport
    python scripts/track_metrics.py --raport   # doar raportul, fara scriere
"""
import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from buffer_api import channels, default_org, gql  # noqa: E402

OUT = _ROOT / "data" / "metrics_history.jsonl"
TZ = timezone(timedelta(hours=3))
Q = ("query P($i: PostsInput!, $f: Int) { posts(input: $i, first: $f) "
     "{ edges { node { id sentAt metricsUpdatedAt metrics { name value } } } } }")


def local_date(iso):
    if not iso:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return (datetime.strptime(iso, fmt).replace(tzinfo=timezone.utc)
                    .astimezone(TZ).date())
        except ValueError:
            continue
    return None


def culege():
    org = default_org()
    acum = datetime.now(TZ)
    snap = {"la": acum.isoformat(timespec="seconds"), "canale": {}}
    for c in sorted(channels(org), key=lambda x: x["name"]):
        r = gql(Q, {"i": {"organizationId": org,
                          "filter": {"channelIds": [c["id"]], "status": ["sent"]}},
                    "f": 100})
        postari = []
        for e in r["posts"]["edges"]:
            n = e["node"]
            m = {x["name"]: x["value"] for x in (n.get("metrics") or [])}
            postari.append({"id": n["id"], "sentAt": n.get("sentAt"),
                            "views": m.get("Views"), "reach": m.get("Reach"),
                            "reactions": m.get("Reactions"),
                            "comments": m.get("Comments"), "shares": m.get("Shares")})
        snap["canale"][c["name"]] = {"service": c["service"], "postari": postari}
    return snap


def raport(snap, min_varsta=3):
    azi = datetime.now(TZ).date()
    print(f"instantaneu {snap['la'][:16]}   (postari mai noi de {min_varsta} zile "
          f"sunt excluse din medii — vizualizarile inca urca)\n")
    for nume, d in snap["canale"].items():
        pe_zi = defaultdict(list)
        fara = 0
        for p in d["postari"]:
            zi = local_date(p.get("sentAt"))
            if not zi:
                continue
            if p.get("views") is None:
                fara += 1
                continue
            pe_zi[zi].append(p["views"])
        coapte = {z: v for z, v in pe_zi.items() if (azi - z).days >= min_varsta}
        total = sum(sum(v) for v in pe_zi.values())
        print(f"{nume}  [{d['service']}]   publicate={len(d['postari'])}  "
              f"vizualizari totale={total:,}".replace(",", "."))
        if fara:
            print(f"   {fara} postari fara metrica Views (platforma nu o raporteaza aici)")
        if not coapte:
            print("   inca nu sunt destule zile coapte pentru comparatie\n")
            continue
        # MEDIANA, nu media: un singur clip viral duce media in sus si face un
        # ritm sa para bun cand de fapt restul zilei a fost plata. Pe canalul
        # romanesc, 3 postari din 65 aduceau jumatate din toate vizualizarile.
        #
        # Si se compara doar postari de varsta apropiata: vizualizarile cresc
        # saptamani intregi, deci una de acum 3 luni bate una de acum 5 zile
        # indiferent de ritm. Fara asta, comparatia masoara vechimea, nu ritmul.
        ferestre = [(3, 7), (8, 30), (31, 3650)]
        for jos, sus in ferestre:
            dupa_ritm = defaultdict(list)
            for z, v in coapte.items():
                varsta = (azi - z).days
                if jos <= varsta <= sus:
                    dupa_ritm[len(v)].extend(v)
            if not dupa_ritm:
                continue
            et = f"{jos}-{sus} zile" if sus < 3650 else f"peste {jos} zile"
            print(f"   vechime {et}:")
            print(f"      {'postari/zi':>10} {'postari':>8} {'mediana':>10} {'medie':>10}")
            for ritm in sorted(dupa_ritm):
                vals = sorted(dupa_ritm[ritm])
                med = vals[len(vals) // 2] if len(vals) % 2 else                     (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
                print(f"      {ritm:>10} {len(vals):>8} {med:>10,.0f} "
                      f"{sum(vals) / len(vals):>10,.0f}".replace(",", "."))
        print()


def main():
    doar_raport = "--raport" in sys.argv
    mv = int(sys.argv[sys.argv.index("--min-varsta") + 1]) if "--min-varsta" in sys.argv else 3
    snap = culege()
    if not doar_raport:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
        print(f"scris instantaneu in {OUT}  ({sum(len(v['postari']) for v in snap['canale'].values())} postari)\n")
    raport(snap, mv)


if __name__ == "__main__":
    main()
