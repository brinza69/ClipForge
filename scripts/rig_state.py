"""Starea rigului, salvata pe disc — ca sa se poata relua dupa ce se inchide PC-ul.

De ce exista: pana acum configuratia unei rulari (ce roluri, de la ce rand, ce
outro, ce dispecer) traia doar in variabile de mediu si in capul celui care a
pornit-o. Daca PC-ul se inchidea la mijlocul unui lot, nimeni nu mai stia ce se
lucra.

Fisierul `data/rig_state.json` e sursa de adevar pentru repornire. NU tine
evidenta randurilor terminate — aia e in sheet si pe Drive, unde ii e locul:
deduplicarea decide singura ce mai are de facut, deci reluarea nu re-randeaza.

    python scripts/rig_state.py show
    python scripts/rig_state.py set --dispatcher dual_dispatch.py --presets narator,comentator --min-row 235
"""
import json
import os
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = _ROOT / "data" / "rig_state.json"

CHEI = {
    # "script.py" conduce ambele placi; "script.py:A" o leaga de una singura.
    # Doua dispecere cer AMANDOUA sufixul, altfel si-ar trimite joburi peste.
    "CLIPFORGE_DISPATCHER": "dual_dispatch.py",
    "CLIPFORGE_DISPATCHER_2": "",
    "CLIPFORGE_PRESETS": "narator,comentator",
    "CLIPFORGE_DISPATCH_MIN_ROW": "2",
    "CLIPFORGE_TTS_OUTRO": "",
    "CLIPFORGE_TTS_OUTRO_FR": "",
}


def load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def save(cfg: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def effective() -> dict:
    """Starea salvata, completata cu valorile implicite."""
    cfg = load()
    return {k: str(cfg.get(k, d)) for k, d in CHEI.items()}


def apply_to_env() -> dict:
    cfg = effective()
    for k, v in cfg.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    return cfg


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "show"

    if cmd == "show":
        cfg = effective()
        print(f"stare: {STATE}  ({'salvata' if STATE.exists() else 'implicita, nesalvata'})")
        for k, v in cfg.items():
            print(f"   {k:30} {v or '(gol)'}")
        return

    if cmd == "set":
        cfg = load()
        mapare = {"--dispatcher": "CLIPFORGE_DISPATCHER",
                  "--dispatcher2": "CLIPFORGE_DISPATCHER_2",
                  "--presets": "CLIPFORGE_PRESETS",
                  "--min-row": "CLIPFORGE_DISPATCH_MIN_ROW",
                  "--outro": "CLIPFORGE_TTS_OUTRO",
                  "--outro-fr": "CLIPFORGE_TTS_OUTRO_FR"}
        atins = False
        for flag, cheie in mapare.items():
            if flag in args:
                cfg[cheie] = args[args.index(flag) + 1]
                atins = True
        if not atins:
            raise SystemExit(f"nimic de setat; optiuni: {', '.join(mapare)}")
        save(cfg)
        print(f"salvat in {STATE}")
        for k, v in effective().items():
            print(f"   {k:30} {v or '(gol)'}")
        return

    raise SystemExit("comenzi: show | set")


if __name__ == "__main__":
    main()
