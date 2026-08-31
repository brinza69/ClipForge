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

**Ordinea de reparare rămâne cea din handover, și măsurătoarea asta o susține:** resolver determinist
(caută citatul în tokeni întregi, salvează `matched_t` și driftul, potrivirile multiple rămân
`ambiguous`) → remăsurare pe aceleași ancore cached → A/B de prompt cu `atom_ids` **doar dacă mai
rămâne nerezolvat** → prag lexical calibrat pe holdout.

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
