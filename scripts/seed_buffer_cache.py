"""Scrie data/buffer_cache.json din verificarile facute azi, 2026-07-29.

De ce exista: API-ul Buffer are 250 cereri/24h pe planul Free si le-am consumat
azi. Dashboard-ul trebuie sa poata afisa ceva si cand API-ul refuza. Cache-ul e
inlocuit automat de `build_dashboard.py` la prima rulare care prinde API-ul liber.

Fiecare cifra de aici a fost citita din Buffer azi — nu e estimata. Ora la care
a fost citita e in `at`, si dashboard-ul o afiseaza ca sa se vada cat e de veche.
"""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "buffer_cache.json"

fb = [
    ("mie 29 iul 13:00", "125 · Descoperiți arta culinară cu un banchet spectaculos!"),
    ("mie 29 iul 20:30", "127 · O tânără se trezește șocată cu părul alb peste noapte"),
    ("joi 30 iul 08:00", "128 · Află de ce chirurgii cântă „La mulți ani” înainte de operație"),
    ("joi 30 iul 13:00", "129 · O situație dramatică în clasă (1/3)"),
    ("joi 30 iul 20:30", "129 · O situație dramatică în clasă (2/3)"),
    ("vin 31 iul 08:00", "129 · O situație dramatică în clasă (3/3)"),
    ("vin 31 iul 13:00", "130 · Descoperă cele mai ingenioase hack-uri pentru bucătărie"),
    ("vin 31 iul 20:30", "131 · Invenția anului 2026: un băț ingenios pentru câini"),
    ("sâm 1 aug 08:00", "132 · Cum un pilot de avion de vânătoare își salvează viața"),
    ("sâm 1 aug 13:00", "133 · În 1992, un film de Hollywood s-a transformat într-o tragedie"),
]
tt_ro = [
    ("mie 29 iul 13:00", "125 · Descoperiți arta culinară cu un banchet spectaculos!"),
    ("mie 29 iul 20:30", "127 · O tânără se trezește șocată cu părul alb peste noapte"),
    ("joi 30 iul 08:00", "128 · Află de ce chirurgii cântă „La mulți ani”"),
    ("joi 30 iul 13:00", "129 · O situație dramatică în clasă (1/3)"),
    ("joi 30 iul 20:30", "129 · O situație dramatică în clasă (2/3)"),
    ("vin 31 iul 08:00", "129 · O situație dramatică în clasă (3/3)"),
    ("vin 31 iul 13:00", "130 · Descoperă cele mai ingenioase hack-uri pentru bucătărie"),
    ("vin 31 iul 20:30", "131 · Invenția anului 2026: un băț ingenios pentru câini"),
    ("sâm 1 aug 08:00", "132 · Cum un pilot de avion de vânătoare își salvează viața"),
]
tt_fr = [
    ("mie 29 iul 13:00", "12 · Mon petit ami a garé sa nouvelle BMW dans mon allée"),
    ("mie 29 iul 19:00", "11 · Dans un moment de vie ou de mort, une décision tragique"),
]

data = {
    "at": "mie 29 iul 12:40",
    "nota": "citit din Buffer azi; API-ul a intrat apoi in rate limit (250 cereri/24h)",
    "canale": [
        {"nume": "Povestitorul", "retea": "facebook", "tip": "page",
         "link": "https://facebook.com/1166432759894488",
         "programate": [{"cand": c, "text": t} for c, t in fb],
         "n_programate": len(fb),
         "publicat_buffer": [], "n_publicat_buffer": 0,
         "n_istoric_importat": 2, "erori": []},
        {"nume": "journal.dune.conteuse", "retea": "tiktok", "tip": "account",
         "link": "https://tiktok.com/@journal.dune.conteuse",
         "programate": [{"cand": c, "text": t} for c, t in tt_fr],
         "n_programate": len(tt_fr),
         "publicat_buffer": [
             {"cand": "lun 27 iul 22:29", "text": "6.mp4"},
             {"cand": "mar 28 iul 09:04", "text": "8.mp4"},
             {"cand": "mar 28 iul 13:03", "text": "7.mp4 (115 MB — cel mai mare publicat)"},
             {"cand": "mar 28 iul 19:05", "text": "10.mp4"}],
         "n_publicat_buffer": 4, "n_istoric_importat": 5,
         "erori": [{"cand": "mie 29 iul 09:00", "text": "9.mp4 (94.7 MB)",
                    "motiv": "eroare de media la Buffer — NU e limita de marime "
                             "(7.mp4, 115 MB, a trecut). Eroare trecatoare, trebuie recreata."}]},
        {"nume": "povestitorul.ro", "retea": "tiktok", "tip": "account",
         "link": "https://tiktok.com/@povestitorul.ro",
         "programate": [{"cand": c, "text": t} for c, t in tt_ro],
         "n_programate": len(tt_ro),
         "publicat_buffer": [
             {"cand": "mar 28 iul 23:20", "text": "101 · ENGLEZĂ, postat din greșeală"},
             {"cand": "mie 29 iul 08:02", "text": "102 · ENGLEZĂ, postat din greșeală"}],
         "n_publicat_buffer": 2, "n_istoric_importat": 25, "erori": []},
    ],
}

OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"scris {OUT}")
