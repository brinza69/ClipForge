r"""Carduri cu text pentru postari de imagine — narator si povestitor, romana.

Postarile video au productie grea in spate. Astea sunt ieftine: un text bun pe un
fundal curat. Scopul e ritmul zilnic pe canal intre clipuri, nu spectacolul.

Doua stiluri, amandoua verticale 1080x1350 (raportul care ocupa cel mai mult
ecran in feed pe Facebook fara sa fie taiat):

  negru    alb ingrosat pe negru, ca in exemplul dat de utilizator
  gradient acelasi text peste un fundal generat, pentru varietate

Textul se aseaza singur: se cauta cea mai mare marime de font la care intra pe
latimea utila, apoi se rupe in randuri pe cuvinte. Fara asta, un text mai lung
ar iesi din cadru sau ar trebui potrivit de mana la fiecare card.

Diacriticele conteaza: Inter le are, si un card romanesc fara ele arata neingrijit.

    server\.venv\Scripts\python.exe scripts\carduri_text.py --toate
"""
import math
import pathlib
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_ROOT = pathlib.Path(__file__).resolve().parents[1]
FONTURI = _ROOT / "data" / "fonts"
IESIRE = _ROOT / "data" / "carduri"
W, H = 1080, 1350
MARGINE = 64                      # cat ramane liber pe laterale
MAX_RANDURI = 3                   # vezi _aseaza
SEMNATURA = "@naratorul.ro"


# Scrisul de mana vine din fonturile Windows. Verificat prin randare: doar
# Segoe Print Bold are TOATE diacriticele romanesti. Ink Free, Lucida
# Handwriting si Brush Script n-au ă/ș/ț, iar Segoe Script n-are ț — cu ele
# textul iese cu patratele, ceea ce se vede abia dupa ce ai postat.
FONT_MANA = pathlib.Path(r"C:\Windows\Fonts") / "segoeprb.ttf"


def _font(nume, marime):
    cale = FONT_MANA if nume == "mana" else FONTURI / nume
    return ImageFont.truetype(str(cale), marime)


def _rupe(text, font, latime_utila, draw):
    """Textul pe randuri, rupt pe cuvinte. Intoarce None daca un singur cuvant
    nu incape — asa stie apelantul sa scada marimea, nu sa taie cuvantul."""
    cuvinte, randuri, curent = text.split(), [], ""
    for c in cuvinte:
        incercare = (curent + " " + c).strip()
        if draw.textlength(incercare, font=font) <= latime_utila:
            curent = incercare
        else:
            if not curent:
                return None
            randuri.append(curent)
            curent = c
    if curent:
        randuri.append(curent)
    return randuri


def _aseaza(draw, text, nume_font, latime_utila, inaltime_utila, maxim=150):
    """Cea mai mare marime care incape SI nu rupe textul in prea multe randuri.

    Doar "cea mai mare care incape" da rezultatul gresit: la latimea utila,
    marimea maxima sparge o fraza in cinci randuri si cardul nu mai seamana cu
    referinta, unde textul sta pe doua-trei randuri mari. Cu cat fontul e mai
    mare, cu atat sunt MAI MULTE randuri — deci se coboara pana cand intra in
    MAX_RANDURI, nu pana cand intra pe inaltime.
    """
    for marime in range(maxim, 28, -2):
        f = _font(nume_font, marime)
        randuri = _rupe(text, f, latime_utila, draw)
        if not randuri or len(randuri) > MAX_RANDURI:
            continue
        pas = int(marime * 1.18)
        if pas * len(randuri) <= inaltime_utila:
            return f, randuri, pas
    f = _font(nume_font, 30)
    return f, _rupe(text, f, latime_utila, draw) or [text], 36


def _fundal_gradient(seed):
    """Fundal generat, nu fotografie: nu avem drepturi pe nicio poza de stoc, si
    un gradient blurat e destul de discret cat sa nu fure atentia de la text."""
    rnd = random.Random(seed)
    paleta = [((6, 12, 30), (10, 60, 80)), ((18, 8, 34), (72, 22, 60)),
              ((4, 20, 22), (12, 78, 66)), ((14, 10, 28), (40, 30, 96))]
    sus, jos = rnd.choice(paleta)
    im = Image.new("RGB", (W, H), sus)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(sus[i] + (jos[i] - sus[i]) * t) for i in range(3)))
    # cateva pete de lumina, ca sa nu para un degrade plat de PowerPoint
    lumina = Image.new("RGB", (W, H), (0, 0, 0))
    dl = ImageDraw.Draw(lumina)
    for _ in range(3):
        cx, cy = rnd.randint(0, W), rnd.randint(0, H)
        r = rnd.randint(260, 460)
        dl.ellipse([cx - r, cy - r, cx + r, cy + r],
                   fill=tuple(min(255, c + 40) for c in jos))
    lumina = lumina.filter(ImageFilter.GaussianBlur(180))
    return Image.blend(im, lumina, 0.35)


def fa_card(text, cale, stil="negru", semnatura=SEMNATURA):
    pe_gradient = stil.startswith("gradient")
    im = _fundal_gradient(hash(text) & 0xFFFF) if pe_gradient else Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    latime_utila = W - 2 * MARGINE
    inaltime_utila = H - 2 * MARGINE - 90          # 90 = loc pentru semnatura
    nume_font = "mana" if stil.endswith("mana") else "Inter-Black.ttf"
    font, randuri, pas = _aseaza(d, text, nume_font, latime_utila, inaltime_utila)

    total = pas * len(randuri)
    y = (H - total) // 2 - 20
    for r in randuri:
        x = (W - d.textlength(r, font=font)) / 2
        if pe_gradient:                             # umbra, altfel albul se pierde
            d.text((x + 3, y + 3), r, font=font, fill=(0, 0, 0, 180))
        d.text((x, y), r, font=font, fill=(255, 255, 255))
        y += pas

    if semnatura:
        fs = _font("Inter-Bold.ttf", 30)
        lw = d.textlength(semnatura, font=fs)
        d.text((W - MARGINE - lw, H - MARGINE - 10), semnatura, font=fs,
               fill=(235, 235, 235) if pe_gradient else (255, 255, 255, 120))
    IESIRE.mkdir(parents=True, exist_ok=True)
    im.save(cale, quality=95)
    return cale


TEXTE = [
    ("amuzant", "am zis că mă culc devreme acum patru ore"),
    ("amuzant", "eu, explicând că sunt foarte ocupat, din pat"),
    ("amuzant", "am o listă de filme pe care o completez, nu o consum"),
    ("motivational", "nu te compara cu cine a început cu zece ani înaintea ta"),
    ("motivational", "disciplina e ce faci când nu mai simți nimic"),
    ("motivational", "azi e singura zi la care ai acces"),
]

if __name__ == "__main__":
    IESIRE.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, (fel, text) in enumerate(TEXTE, 1):
        stil = "negru" if i % 2 else "gradient"
        p = IESIRE / f"proba_{i}_{fel}_{stil}.jpg"
        fa_card(text, p, stil)
        print(f"  {p.name:<38} {stil:<9} {text[:46]}")
        n += 1
    print(f"\n{n} carduri in {IESIRE}")
