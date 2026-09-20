# ClipForge — Handover Index

Acesta este punctul de intrare pentru handover-urile aplicației. ClipForge nu este doar AI Stream Clipper; fiecare zonă funcțională are propria hartă.

## Citește în această ordine

1. [`CURRENT.md`](CURRENT.md) — starea generală a aplicației
2. Handover-ul modulului la care lucrezi
3. [`TEMPLATE.md`](TEMPLATE.md) — formatul pentru următorul handover
4. Documentele stabile din `docs/` — runbook-uri, hărți și audituri

## Modulele aplicației

| Modul | Scop | Handover |
|---|---|---|
| AI Stream Clipper | VOD lung → clipuri verticale clasificate și exportate | [`areas/clipper/CURRENT.md`](areas/clipper/CURRENT.md) |
| Remix Pipeline | Un videoclip sursă → video vertical cu transcript, TTS, captions și descrieri | [`areas/remix/CURRENT.md`](areas/remix/CURRENT.md) |
| Parallel Processing | Rulează mai multe variante ale aceluiași material | [`areas/parallel/CURRENT.md`](areas/parallel/CURRENT.md) |
| Parallel from Sheets | Procesează rânduri din Google Sheets într-un flux serial | [`areas/parallel-sheets/CURRENT.md`](areas/parallel-sheets/CURRENT.md) |
| Auto Story Doodle | Storyboard → script → voiceover → imagini → video | [`areas/doodle/CURRENT.md`](areas/doodle/CURRENT.md) |
| Caption Studio | Template-uri, fonturi, preview și arderea caption-urilor | [`areas/captions/CURRENT.md`](areas/captions/CURRENT.md) |
| Transcript Studio | Transcriere, curățare și export transcript | [`areas/transcript/CURRENT.md`](areas/transcript/CURRENT.md) |
| TTS Studio | Engine-uri, voci, ElevenLabs și sinteză audio | [`areas/tts/CURRENT.md`](areas/tts/CURRENT.md) |
| Utilities | Erase, silence removal și upscale | [`areas/utilities/CURRENT.md`](areas/utilities/CURRENT.md) |
| Infrastructură comună | API, DB, job queue, storage, configurare și integrări | [`areas/infrastructure/CURRENT.md`](areas/infrastructure/CURRENT.md) |

## Istoric

Handover-urile vechi sunt păstrate în arhivă și trebuie consultate ca istoric, nu ca sursă pentru starea actuală:

- [Clipper: snapshot, R0–R3](archive/clipper/2026-09-20-current-history-1.md)
- [Clipper: R4–R6 și corecții](archive/clipper/2026-09-20-current-history-2.md)
- [Clipper: R7, review și gate vizual](archive/clipper/2026-09-20-current-history-3.md)
- [Clipper: R8, S7–S8 și încadrarea pe faze](archive/clipper/2026-09-20-current-history-4.md)
- [Clipper: puncte de reluare și limite istorice](archive/clipper/2026-09-20-current-history-5.md)
- [`handoff-clipper-session-5.md`](archive/clipper/handoff-clipper-session-5.md)
- [`handoff-clipper-session-4.md`](archive/clipper/handoff-clipper-session-4.md)
- [`handoff-clipper-session-3.md`](archive/clipper/handoff-clipper-session-3.md)
- [`handoff-clipper-session-2.md`](archive/clipper/handoff-clipper-session-2.md)
- [`handoff-dynamic-edit.md`](archive/clipper/handoff-dynamic-edit.md)
- [`HANDOVER-2026-06-25.md`](archive/legacy/HANDOVER-2026-06-25.md)
- [`SESSION-HANDOVER.md`](archive/legacy/SESSION-HANDOVER.md)
- [`docs-session-handover.md`](archive/legacy/docs-session-handover.md)

## Documente de referință

- [`docs/clipper-map.md`](../clipper-map.md) — hartă detaliată a fișierelor Clipper
- [`docs/ai-stream-clipper-runbook.md`](../ai-stream-clipper-runbook.md) — rulare și verificare Clipper
- [`reliability-audit-2026-08-18.md`](audits/reliability-audit-2026-08-18.md) — audit global de fiabilitate

## Regulă de actualizare

`CURRENT.md` descrie doar starea actuală a modulului. Istoricul sesiunii se păstrează într-un document separat și nu se copiază integral în `CURRENT.md`.
