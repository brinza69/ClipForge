# Grounding coverage pe șase surse — 31 august 2026

```bash
python scripts/measure_grounding.py gateslice4h gate2d3375 pilotf81b pilotee0e pilot6b38 pilot2c8a
```

Prima măsurătoare de grounding pe mai mult de o sursă. Cifra din handover — „27 grounded strict, 11
potriviri exacte locale, 5 relaxate, 5 fără nicio potrivire" — era `gateslice4h` singur, 48 de
afirmații, și se reproduce exact aici. Astea sunt **164 de afirmații pe 6 surse**.

**NU e gate-ul Batch 3.** Acela e *context* coverage — dacă fereastra aleasă CONȚINE momentele de
context — și a trecut la 8/8 și 15/15. Ăsta e *grounding* coverage: dacă afirmația poate fi legată de
transcript. Două întrebări diferite, numai prima are prag. Nu recalibra una după cealaltă.

## Per sursă

| sursă | afirmații | contiguous | near_verbatim | subsequence | absent | livrat |
|---|---:|---:|---:|---:|---:|---:|
| `gateslice4h` | 48 | 27 | 11 | 5 | 5 | 56,2% |
| `gate2d3375` | 10 | 10 | 0 | 0 | 0 | 100% |
| `pilotf81b` | 11 | 7 | 3 | 0 | 1 | 63,6% |
| `pilotee0e` | 20 | 12 | 5 | 2 | 1 | 60,0% |
| `pilot6b38` | 11 | 7 | 1 | 2 | 1 | 63,6% |
| `pilot2c8a` | 64 | 42 | 18 | 2 | 2 | 65,6% |

```
pooled  105/164 = 64,0%          macro (media pe sursă) = 68,2%
```

**Ambele, fiindcă planul S8 cere macro-agregare tocmai ca o sursă lungă să nu domine suma** —
`pilot2c8a` duce 64 din 164, adică 39% din afirmații. `gate2d3375` e 10/10 dar are zece afirmații;
nu e o sursă „mai bună", e un eșantion prea mic ca să spună ceva.

## Ce spun cifrele, și ce nu

**23,2% sunt `near_verbatim`: citatul E acolo, verbatim, în ±120s — doar nelegat canonic de un
atom.** Plus 6,7% `subsequence`. Împreună **49 de afirmații, 30%, sunt lucruri pe care resolverul
determinist din S7 le-ar putea muta din „nelegat" în „legat"**, fără nicio schimbare de prompt și
fără vreun model.

Rămân **10 afirmații (6,1%) fără nicio potrivire locală** — și „fără potrivire locală" NU înseamnă
„inventat". Măsurătoarea nu poate separa cauzele; asta e chiar motivul pentru care resolverul e
primul pas și nu al treilea.

Ce NU s-a schimbat de la nota din handover: `matched_by` e `timestamp` pentru toate afirmațiile,
adică modelul nu numește niciun atom și `atom_ids` sunt completate de noi din fereastră. Deci cele
`near_verbatim` nu sunt „atomi numiți greșit" — sunt citate exacte, găsite local, nelegate canonic.

**Ordinea de reparare din handover a fost urmată, iar primul pas e făcut** — vezi secțiunea
următoare. Rezultatul lui schimbă pasul doi.

## Resolverul determinist — LIVRAT 31 august 2026, și ce a găsit

`services/clipper/quote_resolver.py` + `scripts/measure_quote_drift.py`. Întoarce întrebarea: în loc
de „e citatul în atomul revendicat plus un vecin de fiecare parte" (≈9,3s), întreabă **unde ÎN
TRANSCRIPT e citatul** — aceeași normalizare și aceeași regulă de cuvinte întregi ca `ground_claim`,
cu un test care asertează că cele două sunt de acord pe fiecare citat.

Trei stări, și `ambiguous` e garda care le face pe celelalte sigure: un citat găsit de mai multe ori
NU se leagă de cea mai apropiată apariție, fiindcă asta ar FABRICA o legătură pe care dovada nu o
susține.

```
POOLED  164 afirmații pe 6 surse
  bound          128   78,0%
  ambiguous       15    9,1%
  absent          21   12,8%
  |drift| median 0,9s   p75 8,6s   p90 18,1s   max 78,1s
```

| sursă | bound | ambiguous | absent | \|drift\| median | p90 |
|---|---:|---:|---:|---:|---:|
| `gateslice4h` | 31 | 7 | 10 | 0,9s | 23,3s |
| `gate2d3375` | 10 | 0 | 0 | 0,5s | 3,0s |
| `pilotf81b` | 4 | **6** | 1 | 10,8s | 20,2s |
| `pilotee0e` | 17 | 0 | 3 | 1,0s | 10,3s |
| `pilot6b38` | 7 | 1 | 3 | 0,7s | 6,2s |
| `pilot2c8a` | 59 | 1 | 4 | 3,2s | 18,1s |

### Ce spune, și mi-a contrazis așteptarea

**Așteptam ca driftul să se grupeze strâns și răspunsul să fie „lărgește fereastra cu o cifră
derivată". Nu se grupează.** Mediana e 0,9s — foarte bună — dar p90 e 18,1s și maximul 78,1s. Din
cele 128 de afirmații localizate, **99 sunt deja în raza de ~9,3s pe care regula livrată o atinge, și
29 nu sunt.** Ca să le prinzi pe alea 29 ai avea nevoie de o rază de peste 20s, adică 3+ atomi de
fiecare parte — iar aia crește direct riscul de ambiguitate, care e deja 9,1%.

**Deci fereastra NU e răspunsul întreg**, și asta e exact motivul pentru care resolverul trebuia
construit înainte de a atinge `_NEIGHBOURS`. Lărgirea de la 1 la 3 ar fi ridicat cifra și ar fi
ascuns că un sfert dintre ratări n-au nimic de-a face cu fereastra.

**Cele 21 `absent` sunt cifra reală de nepotrivire, și e mai mare decât se credea.** Măsurătoarea cu
scară raporta 10 „fără potrivire locală" plus 11 `subsequence` (în ordine, cu goluri). Resolverul cere
contiguitate, deci cele 11 sunt tot absente: **10 + 11 = 21, exact.** Un `subsequence` nu e o
potrivire slabă, e o parafrază.

**`pilotf81b` e valoarea extremă și merită privită separat: 6 din 11 afirmații sunt `ambiguous`** —
talking-head cu fraze scurte și repetitive. Pe sursele astea metoda localizează puțin, iar cifrele de
drift descriu doar afirmațiile ușoare. De aia `ambiguous` se citește ÎNAINTEA driftului.

### Ce urmează, pe baza asta și nu pe baza unei presupuneri

Ordinea din handover rămâne validă, dar pasul doi se schimbă: **nu „remăsurare după lărgirea
ferestrei", ci A/B de prompt cu `atom_ids`.** Motivul e în date — `matched_by` e `timestamp` pentru
toate cele 164 de afirmații, deci modelul nu numește niciodată un atom, iar driftul care nu se
grupează spune că timestamp-ul e un pointer prea slab. Un id de atom ar elimina și driftul, și o
parte din ambiguitate, fiindcă ar spune CARE apariție.

Nimic nu e aplicat: `applied: false`, nicio afirmație nu a fost re-legată, nicio fereastră schimbată.

## Restul stării de reasoning, verificat azi

- **`story_v2` e în continuare REFUZAT de API.** `SELECTABLE` e `('legacy', 'llm_nominate',
  'story_v1', 'story_v2_shadow')`; `story_v2` există ca mod și nu e selectabil.
- **`training_rows()` întoarce 0**, deci ranker-ul rămâne dormant. Log-ul are 131 de rânduri
  `manual`, 255 `system` și 69 fără origin (dinaintea lui 2a) — dar niciunul nu produce o pereche
  (features, label) utilizabilă. Un set cu o singură clasă e mai periculos decât niciunul.
- **S8 e blocat de randare, și blocajul s-a ÎNTĂRIT azi.** Gate-ul cere „aceeași randare neutră și
  **tehnic validă** pentru ambele board-uri". Review-ul v2 raportase probleme tehnice la 45/58; gate-ul
  uman de pe 31 august a găsit 11 din 20 stricate pe `render_v3_letterbox`, plus 116 tăieturi
  invizibile pe 23 din 58 de exporturi. Deci un review orb rulat acum ar amesteca din nou selecția cu
  randarea, exact defectul care a invalidat prima sesiune.
