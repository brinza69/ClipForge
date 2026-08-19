# Unde am rămas — 2026-08-20

Starea operațională e în `data/rig_state.json` (citită automat de `rig_boot.ps1`
la logon). Documentul ăsta ține contextul care NU încape într-un fișier de config.

## Ce rulează la repornire

`ClipForgeBoot.cmd` din folderul Startup → `rig_boot.ps1` → watchdog → backend-uri
+ dispecer. Configurația salvată:

| | |
|---|---|
| dispecer | `herstory_dispatch.py` — pista **franceză**, pe AMBELE plăci |
| roluri (dacă treci pe română) | narator, comentator |
| MIN_ROW | 2 |
| outro RO | „Dă like și follow dacă vrei să vezi mai multe povestiri interesante" |
| outro FR | „Aime et abonne-toi si tu veux voir plus d'histoires interessantes" |
| muzică fundal | −18 dB |

## De randat

- **franceză: 15 rânduri** (NR 51-65, din lotul de 16 linkuri adăugat pe 19 aug).
  Rândurile 53-68 în sheet-ul Victoria.
- **română: ~195 rânduri** restanță (rândurile 4-228). Necesită trecerea
  dispecerului pe `dual_dispatch.py`.
- **NR 40 (rând 43) e mort** — sursa ștearsă de pe YouTube, marcat definitiv.
- **NR 44 (rând 47)** are video pe Drive dar fără descriere: se repară cu
  `scripts/recover_rows.py 44 --write`, fără randare.

## Credite ElevenLabs — LIMITA REALĂ

**20.853 rămase** din 131.000 (plan creator). ≈1.300 caractere per randare.

- pista franceză (1 rol): ~16 rânduri
- pista română (2 roluri): ~8 rânduri

Cele 15 rânduri franceze rămase intră fix. După ele, **nu mai poți randa
aproape nimic** fără să mărești planul sau să treci pe voce locală.

## Cozile de postare (nimic nu se oprește peste noapte)

| canal | postări | până la |
|---|---|---|
| Contouse (FB francez) | 57 | 7 sep |
| Narativ (YouTube) | 42 | 2 sep |
| povestitorul.ro | 12 | 23 aug |
| Povestitorul (FB) | 11 | 23 aug |
| journal.dune.conteuse | 5 | 21 aug |

**TikTok francez rămâne gol vineri 21 aug** — de-aia randăm franceza acum.

## Vocea locală — unde am ajuns

- **Kokoro** (engleză, GPU) — funcțional, legat în pipeline ca motor `kokoro`.
  Preset `narativ_en` gata. NU are voci românești.
- **Piper** (română, CPU) — funcțional. Voce stoc, gratuită.
- **Clonare română** = Piper + OpenVoice, în venv izolat `.venv_clone` (Python
  3.11, adus cu uv). Funcțional: `scripts/clone_voice.py`, `scripts/clone_sweep.py`.
- **De ascultat și ales**: `data/clone_variante/clona_tau{0.3,0.45,0.6,0.8}.wav`
  plus `clona_piper.wav` (neclonat, referință).
- XTTS, Chatterbox, RTVC — verificate, **niciunul nu vorbește română**.
- Drumul spre „identic": fine-tuning Piper pe 30-60 min de voce înregistrată.
  Materialul ElevenLabs existent = 22 min unice (33 fișiere, multe duplicate),
  sub prag, plus problema de termeni (sunt ieșiri ElevenLabs).

## Ultima schimbare

`caption_scale` 2.0 → **2.8** pe toate presetele, ambele foldere. Subtitrarea era
mai mică decât textul ars din sursă. Randările pornite înainte de schimbare ies
cu dimensiunea veche.

## Capcane știute

- Tokenul Google expiră ~săptămânal (app OAuth în modul Testing). După fiecare
  reconectare: **copiază `data/drive_oauth_token.json` în `data_b/`**.
- Buffer: 100 cereri/15min, 250/24h. Posterul se oprește curat și spune care.
- `git add -A` e nesigur — funcționalitatea AI stream clipper e necomisă și pică
  la `tsc`. Stage pe căi explicite.
- Un singur dispecer odată, dacă nu au ambele `--backend A|B`.
