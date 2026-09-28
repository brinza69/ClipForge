# Încadrarea feței — 9 septembrie 2026

## Defect și schimbare

Pe `pilotee0e/c04e7960179b`, la 3s în export, sursa are două persoane,
iar MP4-ul arată perdeaua și o parte din umăr. Plannerul găsește poziția locală
(2292, 684), apoi o respinge fiindcă noul crop nu cuprinde mediana clipului
(2024, 588). Această mediană era tratată ca autoritate pentru o cameră fixă,
deși `_stable_track` este `None`.

`dynamic_face_framing.face_framing` păstrează protecția existentă când există
ancora sursei. În lipsa ei, un conflict poate lărgi cropul pentru a cuprinde
ambele dreptunghiuri propuse. Dacă reuniunea nu încape într-un crop 9:16,
se folosește compoziția `fit` existentă. Shot-ul lărgit nu mai primește push,
snap sau shake, care ar putea anula conținerea. Decizia este înregistrată în
`framing_adjustment` și ajunge în sidecar prin exportul comun.

Nu este o verificare de subiect și nu certifică identitatea ori întregul cap.
Păstrează propuneri de încadrare concurente. Costul este reducerea scării.

## Contraexemplul care a restrâns reparația

Prima versiune lărgea inutil și `e8fa6b35ea66`, la 31.07–33.49s. Proba video
arăta webcamul mai mic, deasupra unei porțiuni mai mari de Minecraft.
Mediana locală (1640, 402) combina x-ul unei observații (1640, 172) cu y-ul
alteia (1350, 402). Nicio observație nu intra în cropul astfel inventat.

Versiunea finală păstrează încadrarea veche în acest caz. Nu s-a adăugat un prag
de clasificare: se verifică dacă dreptunghiul propus conține vreun centru dintre
observațiile locale din care a fost calculat.

Un interval fără observații locale este un caz diferit: `_centre_at` folosește
o probă apropiată, iar lărgirea conservatoare păstrează ambele presupuneri.
Motivul primește sufixul `_nearby_sample_only`. Intervalul românesc
5.48–6.895s este un asemenea caz. Îmbunătățirea văzută în export nu transformă
proba din afara intervalului într-o detecție locală.

## Probe reale, izolate de exports

Director: `data/codex-analysis-20260909/framing/`.
`before/` conține baza înaintea acestei schimbări; `after/` este prima încercare,
inclusiv lărgirea Minecraft respinsă. **`final/` este candidatul final.**
Fiecare MP4 are sidecar complet, comandă efectivă și amprentă v2.
Deciziile și observațiile intermediare sunt păstrate separat în `decision.json`.

| Clip | Shot-uri înainte/după | Ajustări finale | Cadre inspectate în probe |
|---|---:|---:|---|
| c04e7960179b | 10 / 10 | 4 | 3.0, 6.0, 7.5, 17.0s |
| aaf5f324e832 | 17 / 17 | 2 | 4.2, 6.09, 9.5s |
| e8fa6b35ea66 | 19 / 19 | 0 | 31.3, 32.2, 33.3s |

Toate limitele temporale ale shot-urilor sunt identice înainte/după, iar ASS-ul
este identic ca octeți pentru fiecare pereche. Asta nu înseamnă „ritm identic”.
Din geometria ferestrelor de randare, raportul median de scară între vecini pe
primul clip coboară de la 2.540 la 1.328; maximul de la 3.866 la 3.113.
Pe al doilea: mediană 1.510 → 1.371, maxim 5.890 → 3.879. Acestea sunt rapoarte
între ferestre, nu măsurători ale mărimii persoanei în fiecare cadru.

În cadrele primului clip apar persoanele acolo unde înainte era perdeaua.
Pe al doilea, la 4.2s fața femeii nu mai este împinsă atât de jos; bărbatul
rămâne parțial tăiat la stânga. La 6.09s compoziția fit rămâne intactă. La 9.5s
sursa este un prim-plan pe fruct: eticheta `face_tight` nu descrie conținutul;
noua încadrare îl păstrează mai bine în cadrul inspectat.

Trei controale suplimentare re-planificate au planuri publice neschimbate:
`pilotf81b/30d7c6d4eae5`, `pilot6b38/a6cf2a591a2c`,
`pilot2c8a/20c670227eda`. Pentru ele comparația este despre planuri;
nu li s-a atribuit un nou verdict de vizionare.

## Verificări și limite

`test_clipper_face_framing.py` verifică integrarea în planner, ancora fixă,
centrul inventat din două observații, cazul fără probă locală, fit-ul când
reuniunea nu încape și oprirea efectelor care ar recropa regiunea. Un test
encodează un pătrat colorat la poziția locală și verifică în pixelii decodați
că este întreg și nu este deformat.

Suita completă: **1942 passed, 2 deselected** (cele două 404 TikTok preexistente),
144.66s. Cele trei MP4 finale sunt 1080×1920 la 30fps, se decodează integral fără
erori, au amprente v2 valide și identitate reverificată după encode. Minecraft
este identic octet cu octet cu baza; cele două finale românești sunt identice
octet cu octet cu variantele îmbunătățite inspectate. Inventarul de 301 fișiere
existente din exports: 0 modificate, 0 adăugate.

Starea execuțiilor finale și inventarul exporturilor sunt în `verification-final.json`.
Acest lot nu rezolvă alegerea vorbitorului, toate mișcările în interiorul unui
shot sau subtitrările arse ale sursei. Nu există un verdict de vizionare integrală
ori o rată măsurată de clipuri postabile. Îmbunătățirea este demonstrată pe
contraexemplele de mai sus; celelalte probleme de încadrare rămân deschise.
