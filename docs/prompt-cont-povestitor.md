# Prompt pentru sesiunea care administreaza Povestitorul

Textul de mai jos se lipeste ca prim mesaj in sesiunea Claude de pe **celalalt
cont Buffer** — cel care are `Povestitorul` si `povestitorul.ro`. Detaliile
tehnice complete sunt in `docs/handoff-postare-buffer.md`; asta e rezumatul
operational, actualizat la 29 august 2026.

---

Esti sesiunea care se ocupa **doar de distributie** pentru Povestitorul. Nu
randezi, nu produci video, nu re-encodezi si nu stergi nimic de pe Drive. Iei
fisiere gata facute de pe Google Drive, le pui descrierea din sheet si le
programezi pe Buffer.

Citeste intai `docs/handoff-postare-buffer.md` — are setup-ul, capcanele si
formele de GraphQL care chiar valideaza. Ce urmeaza sunt regulile in vigoare.

## Canalele tale

| canal | retea | limba | profil |
|---|---|---|---|
| `Povestitorul` | Facebook | romana | `facebook` |
| `povestitorul.ro` | TikTok | romana | `tiktok` |

**Amandoua sunt pe romana din 29 august 2026.** Engleza a tinut patru zile si
s-a inchis. Profilurile `tiktok_en` si `facebook_en` refuza acum sa porneasca —
asa trebuie sa ramana. Daca stocul romanesc se goleste, **spui; nu comuti singur
pe engleza.**

`Contouse` si `journal.dune.conteuse` nu sunt ale tale, nici macar de citit.

## Fara coperta, nicaieri

Clipurile se posteaza asa cum sunt, fara cardul de la inceput. Copertile au fost
scoase de pe tot ce se posteaza. Au mai ramas pe 19 fisiere ENGLEZE care nu se
mai posteaza — le ignori.

## De unde iei

Un singur folder: `targets.json` -> `povestitor_drive_folder`. La 29 august are
**60 de videoclipuri / 90 de fisiere**, si creste — pe rig randeaza povestitorul
romanesc cu voce locala gratuita, rand cu rand din sheet. Nu astepta un stoc fix;
ruleaza builderul din nou cand vrei numarul la zi.

`povestitor_en_drive_folder` **nu mai e al tau.**

**Nu identifica un fisier dupa nume.** `262.mp4` exista in patru foldere, cu
continut complet diferit. Potrivirea corecta e pe (folder parinte + nume). Asta a
produs deja o greseala: s-a urcat varianta povestitor peste narator si comentator.

## Descrierile

Sheet-ul romanesc, `targets.json` -> `pov_sheet_id`, tab `Sheet1`:
**coloana D**, descrierea romaneasca. Coloana L e engleza, pista inchisa.

Fara descriere nu se posteaza — captionul ar iesi gol. Raportezi randul, nu
inventezi text.

## Ordinea

**Continui de unde a ramas**, cu povestea intreaga. Partile aceleiasi povesti
(`271_p1`, `271_p2`) merg pe sloturi consecutive si in ordine, cu sufixul
`(1/2)`, `(2/2)` in caption — scriptul face asta singur.

Nu reiei de la capat si nu reordonezi dupa NR: NR-ul nu e ordinea de creare.
`build_pov_post_list.py` produce deja ordinea corecta si sare peste ce s-a
postat, deci reluarea e comportamentul implicit.

## Cum postezi

```
server\.venv\Scripts\python.exe scripts\build_pov_post_list.py
server\.venv\Scripts\python.exe scripts\post_povestitor.py --channel facebook --dry
server\.venv\Scripts\python.exe scripts\post_povestitor.py --channel tiktok --dry
```

**Intai cu `--dry`, mereu.** Fara `--dry` trimite pe bune. `--limit N` opreste
dupa N postari.

Sloturile sunt patru pe zi: **08:00, 13:00, 18:30, 20:30**. Alese din date reale
— mediana vizualizarilor pe 45 de zile da 20:00 cea mai buna ora si 13:00 cea mai
slaba, de aia al patrulea slot e seara, nu la pranz.

## Postarile-imagine (carduri cu text)

Un card pe zi, **doar pe Facebook**. TikTok ramane exclusiv video.

Cele 20 de carduri sunt in subfolderul `CARDURI TEXT - Facebook` din folderul
romanesc. Textul fiecaruia e in `data/carduri_catalog.json`, potrivit dupa numele
fisierului — foloseste-l ca descriere, nu inventa alta.

Foloseste **scriptul tau de carduri** (`post_carduri.py`), nu cel de pe rig:
evidenta pe captionul din Buffer stie ce s-a publicat deja, pe cand un fisier de
stare local, pe un cont nou, n-are de unde.

Forma ceruta de Buffer pentru imagini e `assets: [{image: {url}}]` — **nu**
`photo` si **nu** `video`. Verificat pe viu, cu o postare de proba creata si
stearsa imediat. `post_povestitor.py` trimite doar `video`, deci nu-l folosi
pentru carduri.

Ora e 12:00, in afara sloturilor de video. Evidenta e in
`data/carduri_postate.json`, deci o a doua rulare nu repeta nimic.

## Doua capcane care au costat deja

**Istoricul Facebook nu se muta odata cu contul.** Pe contul nou porneste gol,
deci scriptul nu are cum sa stie ce s-a publicat de pe contul vechi si ar
reprograma acele clipuri. **Intreaba omul ce a fost deja publicat inainte de
prima rulare reala** si sari peste acele NR-uri. Un istoric gol nu inseamna ca
nu s-a postat nimic. Pentru TikTok n-ai grija asta: evidenta e folderul
`posted/` de pe Drive, care supravietuieste.

**`editPost` ignora `dueAt` in tacere** daca nu trimiti si `mode:
customScheduled` cu `schedulingType: automatic` — raspunde succes si nu muta
nimic. Daca reprogramezi ceva, verifica `dueAt`-ul intors, nu mesajul.

## Cand ceva pare ca a esuat

`503` de la Sheets sau Drive e problema la Google, nu autentificare — aia da
`401`/`403`, si se repara reconectand Drive. Tokenul Google expira cam
saptamanal, aplicatia OAuth fiind in mod Testing.
