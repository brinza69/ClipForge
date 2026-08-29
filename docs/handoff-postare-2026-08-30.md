# Handoff distributie — 30 august 2026

Starea reala a canalelor si a masinii de postare, la zi. Completeaza
`handoff-postare-buffer.md`, care descrie procedura; asta descrie *unde am ramas*.

Niciun id de sheet sau de folder aici — toate stau in `data/targets.json`,
gitignored. Documentul asta e in repo tocmai ca sa ajunga pe alt aparat.

## Directia, dupa inversarea din 29 august

Ambele canale povestitor sunt pe ROMANA. Engleza a tinut patru zile (25-29 aug)
si s-a inchis. Fisierele engleze raman pe Drive, NEPOSTATE. Daca stocul romanesc
se goleste, se RAPORTEAZA — nu se comuta singur pe engleza.

Un singur folder alimenteaza ambele canale (`povestitor_drive_folder`),
descrierile din coloana D, fara coperta pe clipuri.

Codul e sincronizat cu asta din commitul `8f0b84e`: `tiktok` si `facebook`
pornesc, `tiktok_en` si `facebook_en` refuza si listeaza profilurile deschise.

## Ce era programat la 30 august

Plafonul Buffer e 10 postari programate PE CANAL (plan Free), si e COMUN pentru
clipuri si carduri. Ambele canale erau la 10/10.

Povestitorul (Facebook): 3 carduri + 7 clipuri romanesti (125, 128, 129 p1-p3,
130, 131), 30 aug - 1 sep.
povestitorul.ro (TikTok): 10 clipuri romanesti (265, 263 p1-p2, 266, 271 p1-p2,
272 p1-p2, 273 p1-p2), 30 aug - 1 sep.

Engleza ramasa in coada: zero. Cele 8 clipuri engleze inca programate au fost
sterse pe 29 aug; alte 8 apucasera sa se publice in aceeasi zi, inainte de
schimbarea de directie.

## Cardurile cu text

- posterul e `scripts/post_carduri.py`. Pe rig exista si `posteaza_carduri.py`,
  cu ambele profiluri inchise — nu el e cel folosit.
- catalogul: `data/carduri_catalog.json` (nume, id, url, text, fel)
- ora **12:00**, in afara sloturilor video (08:00, 13:00, 18:30, 20:30)
- forma ceruta de Buffer: `assets: [{image: {url}}]`, metadata
  `{"facebook": {"type": "post"}}` — `reel` e doar video, `story` dispare in 24h
- evidenta: **captionul din Buffer**, nu un fisier local de stare. Pe un cont nou
  un fisier local n-ar sti ce s-a publicat inainte de el; captionul stie, fiindca
  Buffer importa istoricul canalului la conectare.
- ordine: alterneaza amuzant / motivational, fiecare grup in ordinea catalogului
  (card_01, card_11, card_02, card_12, ...). MOTIVUL, confirmat de utilizator pe
  29 aug: ca feedul sa nu para repetitiv. Nu o inlocui cu ordinea din catalog.

Puse pana la 30 aug: card_01, card_11, card_02, card_12. Raman 16.

## Lucruri care au costat timp, ca sa nu se redescopere

- **Fisierele de pe Drive trebuie partajate public prin link.** Altfel Buffer
  raspunde "Video could not be read from its URL", desi URL-ul e corect. S-a
  intamplat la 194.mp4: singurul nepartajat din 40.
- **Un fisier se poate descarca intreg si totusi sa nu fie video.** NR 127 dadea
  HTTP 200 si header ftyp valid, dar Drive il raporta `width=0, durationMillis=0`.
  Acum e in `STRICATE` in `build_pov_post_list.py`. Semnul e in metadata Drive,
  nu in descarcare.
- **Lipsa de `videoMediaMetadata` NU inseamna fisier stricat.** Opt fisiere n-o
  aveau si s-au postat fara probleme; doar `width=0` explicit e semn de coruptie.
- **Doua fisiere cu acelasi nume in foldere diferite deveneau "doua parti"**, cu
  caption `(1/2)` fara pereche. Reparat in builder pe 28 aug; doua postari iesite
  pe 19 aug raman asa.
- **Postarile in stare `error` nu conteaza ca facute** si se reincearca singure.
- **`--dry` intai, mereu.** Regula utilizatorului, fara exceptie.

## Deschise

- `data/pov_inventory.json` lipseste pe masina de postare; lista din cod acopera
  101/102. Clipuri non-romane adaugate ulterior NU sunt prinse.
- Stocul romanesc creste in timp ce postezi (rig-ul randeaza cu voce locala F5),
  deci cifrele planului nu sunt fixe: 103 fisiere pe 29 aug, 105 pe 30 aug.
- Tokenul Google expira cam saptamanal (OAuth in mod Testing):
  `python scripts\conecteaza_drive.py`. Un 503 e Google, nu tokenul.
