"""Claseaza videoclipurile povestitor dupa cat de bine merg ca Short, din text.

Scorul vine dintr-un LLM care primeste descrierea (generata la randare din
naratiunea romaneasca), nu din lungime sau din data — un clip scurt si plat nu
trebuie sa bata unul cu miza reala. Acelasi apel produce si TITLUL de YouTube,
fiindca YouTube cere titlu separat de descriere si e pacat sa mai faci o tura.

Criteriile, in ordinea greutatii:
  1. carlig in primele secunde — o intrebare, o miza, ceva ce nu se poate ignora
  2. conflict clar si o rasturnare / rezolvare (nu doar o intamplare)
  3. emotie sau ceva de comentat — motivul pentru care cineva da mai departe
  4. se intelege fara context extern

    python scripts/rank_videos.py            # scrie data/pov_ranking.json
    python scripts/rank_videos.py --top 20   # arata primele 20
"""
import asyncio
import json
import os
import pathlib
import sys

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import httpx  # noqa: E402
from post_povestitor import load_groups  # noqa: E402
from services.transcript_cleaner import get_openai_key  # noqa: E402

PLAN = _ROOT / "data" / "pov_post_list.json"
OUT = _ROOT / "data" / "pov_ranking.json"
LOT = 15
MODEL = os.environ.get("CLIPFORGE_RANK_MODEL", "gpt-4o-mini")

SISTEM = (
    "Esti editor de continut scurt vertical (TikTok/Shorts) in limba romana. "
    "Primesti descrierile unor clipuri narate. Pentru fiecare dai un scor 0-100 "
    "pentru cat de bine ar merge ca Short si un titlu de YouTube.\n"
    "Scor mare = carlig puternic in prima propozitie, conflict clar cu miza, o "
    "rasturnare sau o rezolvare, si un motiv sa fie dat mai departe. "
    "Scor mic = intamplare plata, fara miza, sau care nu se intelege fara context.\n"
    "Titlul: maxim 70 de caractere, in romana, fara clickbait mincinos, fara "
    "ghilimele, fara emoji, sa spuna despre ce e clipul si sa starneasca curiozitate.\n"
    'Raspunzi DOAR cu JSON: [{"nr":"...","scor":0,"titlu":"...","motiv":"max 12 cuvinte"}]'
)


async def scoreaza(client, key, lot):
    intrare = "\n\n".join(f'NR {n}: {d[:420]}' for n, d in lot)
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": MODEL, "temperature": 0.2,
              "response_format": {"type": "json_object"},
              "messages": [
                  {"role": "system", "content": SISTEM},
                  {"role": "user", "content":
                   f"Clipurile:\n\n{intrare}\n\n"
                   f'Raspunde cu {{"clipuri": [...]}} — cate o intrare pentru fiecare NR.'},
              ]},
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:200]}")
    txt = r.json()["choices"][0]["message"]["content"]
    d = json.loads(txt)
    return d.get("clipuri") or d.get("videos") or []


async def main():
    key = get_openai_key()
    if not key:
        raise SystemExit("lipseste cheia OpenAI (Settings → API keys)")

    vids = []
    vazut = set()
    for g in load_groups(PLAN):
        if g["desc"] and g["key"] not in vazut:
            vazut.add(g["key"])
            vids.append((str(g["key"]), g["desc"]))
    print(f"de clasat: {len(vids)} videoclipuri, in loturi de {LOT}")

    rezultate = {}
    async with httpx.AsyncClient(timeout=180.0) as client:
        for i in range(0, len(vids), LOT):
            lot = vids[i:i + LOT]
            try:
                for x in await scoreaza(client, key, lot):
                    nr = str(x.get("nr", "")).strip()
                    if nr:
                        rezultate[nr] = {"scor": int(x.get("scor") or 0),
                                         "titlu": (x.get("titlu") or "").strip()[:100],
                                         "motiv": (x.get("motiv") or "").strip()}
                print(f"  {min(i + LOT, len(vids))}/{len(vids)}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  lotul {i // LOT + 1} a esuat: {str(e)[:90]}", flush=True)

    lipsa = [n for n, _ in vids if n not in rezultate]
    if lipsa:
        print(f"fara scor ({len(lipsa)}): {lipsa[:12]}")

    ordonat = sorted(rezultate.items(), key=lambda kv: -kv[1]["scor"])
    OUT.write_text(json.dumps({n: v for n, v in ordonat}, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 15
    print(f"\ntop {top}:")
    for n, v in ordonat[:top]:
        print(f"  {v['scor']:>3}  NR {n:<5} {v['titlu'][:58]:<58} {v['motiv'][:34]}")
    print(f"\nscris: {OUT}  ({len(ordonat)} clasate)")


if __name__ == "__main__":
    asyncio.run(main())
