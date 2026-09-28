# Detecția subtitrărilor arse în sursă — ce a mers și ce nu

**Data:** 30 august 2026 · **Batch:** R6 · **Cod:** `server/services/clipper/source_captions.py`

Documentul acesta există din același motiv ca tabelul celor patru abordări eșuate de detecție a
facecam-ului: **cunoașterea care costă o sesiune să fie obținută nu trăiește nicăieri altundeva.**
Dacă îl ștergi, următorul agent rulează exact aceleași două experimente.

## Ce se cerea

Baseline-ul din auditul v2: **15 din 15 clipuri go ghost au ieșit cu două sisteme de captions**,
fiindcă sursa avea deja text ars și nimic din pipeline nu putea să spună asta. R0 a lăsat cârligul —
`captions_duplicate_declared` e `unavailable` de atunci, fiindcă un 0 acolo ar transforma „n-am
întrebat niciodată" în „am verificat și e curat".

## Adevărul cunoscut, împotriva căruia se măsoară

Din review-ul uman v2 (identitatea surselor din `docs/source-labels.md`):

| proiect | sursă | subtitrări arse |
|---|---|---|
| `pilotf81b` | go ghost, 22m, editat | **DA** |
| `pilotee0e` | Turul apartamentului, 42m, română | nu |
| `pilot6b38` | Jensen Huang, 63m, editat | nu |
| `pilot2c8a` | moistcr1tikal, Just Chatting, 3h43m, live | nu |

**Atenție la proveniență:** `docs/source-labels.md` identifică sursele, dar **nu conține nicio
etichetă despre captions**. Adevărul despre subtitrările arse vine din review-ul uman v2 („15 din 15
clipuri go ghost cu două sisteme de captions"). O versiune anterioară a acestui document atribuia
greșit eticheta lui `source-labels.md` și numea două dintre surse „Minecraft", ceea ce niciuna nu e.

E singurul loc din plan unde un detector poate fi verificat împotriva unui răspuns pe care cineva
deja îl știa.

## Care încotro merg răspunsurile

O versiune anterioară a acestui document și a modulului aveau direcția **inversată**. §R6 e explicit:

| stare | ce înseamnă | ce face |
|---|---|---|
| `present` | sursa are deja captions | **dezactivează** stratul ClipForge |
| `absent` | nu are | **păstrează** stratul ClipForge |
| `unknown` | nu s-a putut ști | nu schimbă nimic |

Deci **`present` e răspunsul scump**: unul greșit livrează un clip fără niciun fel de captions. De
aceea `present` e singura stare care cere ambele praguri trecute — și de aceea `present` se poate
obține din dovezile unei SINGURE benzi, în timp ce `absent` cere ca TOATE benzile să fie clar
negative.

---

## Abordarea 1 — densitatea de pixeli luminoși pe benzi orizontale — **EȘUATĂ**

**Ipoteza:** subtitrările sunt aproape întotdeauna albe, deci o bandă orizontală cu mulți pixeli
aproape-albi, persistentă între cadre, e o pistă de subtitrare.

**Metoda:** 60 de cadre eșantionate pe tot fișierul, mască `v >= 235`, 24 de benzi orizontale,
`lift = mediana benzii / mediana cadrului`, plus `churn` = cât de mult se schimbă semnătura de
coloane între eșantioane.

**Rezultatul:**

```
pilotf81b (POZITIV)   band 18/24  lift x2.09   churn 0.086
pilot2c8a (negativ)   band  1/24  lift x56.12  churn 0.0
                      band 11/24  lift x10.31  churn 0.072
pilotee0e (negativ)   toate benzile 0.0000 — normalizarea degenerează
pilot6b38 (negativ)   toate benzile 0.0000 — la fel
```

**De ce a eșuat:** cel mai puternic semnal din tot corpusul e o **bară luminoasă statică** în capul
cadrului lui `pilot2c8a` — ×56 față de ×2,09 al adevăratului pozitiv. Iar pe două surse mediana
benzii e 0, deci `lift` împarte la zero și normalizarea se prăbușește. `churn` nu salvează nimic:
banda 11 a lui `pilot2c8a` are churn 0,072 față de 0,086 al pozitivului.

---

## Abordarea 2 — „subtitrarea e constantă pe porțiuni, videoul nu" — **EȘUATĂ**

**Ipoteza:** o subtitrare stă nemișcată o secundă-două și apoi sare. Videoul din spate se schimbă la
fiecare cadru. Deci într-o bandă de subtitrare diferența cadru-la-cadru a măștii ar trebui să fie
aproape zero în majoritatea timpului, cu salturi ocazionale — spre deosebire de o bandă de video,
unde e moderată tot timpul.

**Metoda:** eșantionare **densă** (5 fps, fereastră de 20s la 400s) în loc de rară pe tot fișierul,
fiindcă structura de interes se vede doar dens. Per bandă: `still` = fracția de cadre cu diferență
sub 0,25, `jumps` = fracția peste 1,0, scor = `still × jumps`.

**Rezultatul:**

```
pilotf81b (POZITIV)   band  5  lit 0.0004  still 0.63  jumps 0.29  scor 0.183
pilotee0e (negativ)   band 17  lit 0.0002  still 0.48  jumps 0.52  scor 0.250
```

**De ce a eșuat:** un **negativ a ieșit peste pozitiv**. Și valorile `lit` sunt de ordinul 0,0002 —
câțiva zeci de pixeli, adică zgomot, nu structură. Ipoteza e probabil corectă despre subtitrări; masca
de luminozitate pur și simplu nu o poate vedea.

**Lecția, mai importantă decât rezultatul:** aici era momentul să mă opresc din reglat praguri. Mai
multă ajustare pe patru surse etichetate ar fi fost calibrare pe răspuns, nu detecție.

---

## Abordarea 3 — un detector de text adevărat — **FUNCȚIONEAZĂ**

**De ce e afordabilă:** greutățile CRAFT ale lui `easyocr` sunt deja în cache local
(`~/.EasyOCR/model/craft_mlt_25k.pth`), deci detecția merge offline. **5 secunde pe sursă pe GPU**,
14 cadre. Doar detecție, nu recunoaștere — întrebarea e dacă textul E acolo și într-o bandă
persistentă, nu ce scrie.

**Rezultatul, 4 din 4 împotriva etichetelor umane:**

```
pilotf81b   present   band 9/10,  9 din 14 cadre, cea mai lată linie 0.45 din cadru
pilotee0e   absent    zero cutii de text în toate cele 14 cadre
pilot6b38   absent    band 8/10, 13 din 14 cadre, cea mai lată linie 0.079
pilot2c8a   absent    band 7/10,  3 din 14 cadre, cea mai lată linie 0.346
```

**Ce separă o subtitrare de o etichetă fixă — trei proprietăți împreună, și niciuna singură nu
ajunge:**

1. **O singură bandă.** Subtitrarea are poziție fixă; textul de pe `pilot2c8a` (moistcr1tikal) e
   împrăștiat pe opt benzi.
2. **Persistentă.** E acolo în majoritatea eșantioanelor.
3. **LATĂ.** Asta e discriminatorul pe care nimic altceva nu-l dă. `pilot6b38` are text în aceeași
   bandă în 13 din 14 cadre — **mai persistent decât adevăratul pozitiv** — și e 0,079 din cadru.
   O linie de dialog nu e.

**Fiecare negativ pică pe altă axă:** `pilot6b38` pe lățime (0,079 < 0,15), `pilot2c8a` pe persistență
(3/14 < 0,30). Asta contează: înseamnă că ambii discriminatori fac muncă, nu că unul îl cară pe
celălalt.

### Ordinea benzilor, găsită la review

Prima implementare judeca **doar banda cu cele mai multe cadre**. Combinând cele două dovezi reale
care există deja — watermark-ul lui `pilot6b38` la 14/14 și lățime 0,08, plus pista de subtitrare a
lui go ghost la 9/14 și lățime 0,45 — rezultatul era `absent`: eticheta câștiga `max()` și banda de
subtitrare nu era examinată niciodată. O sursă cu ȘI watermark ȘI subtitrări ar fi fost clasificată
greșit.

Ordinea corectă: orice bandă care trece ambele praguri → `present`; altfel orice bandă în zona
ambiguă → `unknown`; `absent` doar dacă toate benzile sunt clar negative.

### Eșecul detectorului nu e o sursă curată

A doua constatare de la review, și testul meu propriu o rata: verificam că benzile ies goale și nu
verificam deloc **verdictul**. Benzi goale sunt indistinctibile de o sursă fără text, deci numitorul
trebuie să fie ce a **analizat** modelul, nu ce s-a citit de pe disc. `_bands` întoarce acum și
numărul de cadre prin care detectorul chiar a trecut, iar `sampled` / `analysed` / `failed` sunt
raportate separat.

---

## Ce NU e demonstrat

**Pragurile au fost alese cu răspunsul la vedere, pe patru surse.** Nu e o calibrare, iar
`source_captions_v1` transportă `calibrated: false` și pragurile alături de fiecare verdict, tocmai
ca să nu poată fi citite ca măsurate. O calibrare adevărată cere mai multe surse etichetate, de
preferat în mai multe limbi și cu mai multe stiluri de subtitrare (poziție centrală, fundal opac,
karaoke).

**Nimic nu e aplicat.** Detectorul nu dezactivează încă al doilea strat de captions. `present` costă
`present` e cel care **stinge** stratul ClipForge, deci un `present` greșit livrează un clip fără
niciun fel de captions — de aceea e singura stare care cere ambele praguri și se obține dintr-o
singură bandă, în timp ce `absent` cere toate benzile clar negative. `absent` **păstrează** stratul,
iar `unknown` (fără model, fișier necitibil, prea puține cadre, dovezi între praguri, detector care
aruncă) nu schimbă nimic.

**Dependența e opțională.** `easyocr` nu e în `requirements.txt`. Fără el, fiecare verdict e `unknown`
și rularea de analiză nu costă nimic.
