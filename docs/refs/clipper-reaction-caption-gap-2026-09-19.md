# Reaction caption placement — 19–20 September 2026

The previous ordinary-path diagnostic placed `TEST CAPTION` at y=.349, over
the source's own writing. Keeping captions above the reaction band did not keep
the watched image clear. This batch changes automatic placement for the explicit
reaction layout only; it does not detect source text or choose the two regions.

The fitted foreground and reaction use the existing renderer geometry. The
foreground is added temporarily to caption keep-outs, and the existing position
resolver searches the remaining space. Its least-overlap fallback is refused
when it still intersects a reserved region. Stored layout geometry and source
bindings are not rewritten to accommodate the caption.

The caption box remains an approximation for two short lines. Supported automatic
styles have base font times scale at most 72px, scale at most 1, outline at most 5px, shadow at
most 3px and no entry animation. Larger/custom treatments require explicit
placement. These operational limits do not measure arbitrary fonts or establish
readability. Suppressed, missing and empty layers are not placed; manual caption
positions remain user-owned. Events outside the original clip window are filtered
before silence remapping, and completely removed events do not trigger placement.
The save API passes the same original window duration; silence removals are known
only at render time. This is not an assertion that the save API precomputes them.

The proof uses synthetic diagnostic text on the user's local source, not a new
editorial selection or an inferred transcription. It compares an explicitly
requested reconstruction of the old position with the new automatic position
and an uncaptioned control, in a private database and output directory. Pixel
differences in PNG previews isolate the caption; decoded MP4 frames check that
the position reaches an actual video. Input fingerprints and output hashes have
their usual narrower meanings and are not visual-quality certificates.

Independent regression tests exposed suppression applied too late, incomplete
preset-style checks, raw ASS controls bypassing the envelope, removed text still
being judged, and the comparison of remapped event times with untrimmed duration.
The last counterexample is a four-second clip with its first three seconds
removed: a caption starting at source-relative five seconds remaps to two seconds,
but the delivered clip lasts only one. It remains invisible. Comparing that two
against the original four wrongly demanded caption space for it.

The original real-media probe is retained in
`data/claude-xqc-reference/caption_gap_20260919_185925_157724/`.
At clip-relative .5s and 1.5s, placement moved from .349 to .5342. Its actual ink
occupied y=996..1058 for one line and y=957..1098 for two highlighted lines. Both
are within the existing blur gap y=904..1152. Foreground and reaction PNG pixels
were byte-identical to the uncaptioned control; MP4 decode differed from PNG by
less than one RGB level on average. This checks these cards, not arbitrary text.
The final-code rerun is in
`data/claude-xqc-reference/caption_gap_20260920_005030_790630/`.
All 28 checks in `verification.json` pass. The resulting `after.mp4` is identical
to the first probe (SHA256
`420ce4cfb8a7e76ed7471c11b502cb2e55275cf325260a30d651568d24091228`). It is a
2s, 1080×1920, 60fps video with audio; full decoding succeeds. The final run also
asserts zero pixel difference in both reserved regions, not only a thresholded
caption mask. All 390 existing export files retain their hashes. Audio was not
listened to, and browser interaction was not visually exercised in this batch.

Final verification: **2,244 passed, 2 deselected**, exit 0, 263.91s. Command from
`server/`: `python -m pytest -q --tb=short
--deselect=tests/test_tiktok_transform.py::test_list_endpoint_ok
--deselect=tests/test_tiktok_transform.py::test_create_rejects_invalid_url`.
`CLIPFORGE_DATA_DIR` was unset so tests used a disposable database. The complete
log is `data/claude-xqc-reference/caption-gap-full-suite.log`. Claude's final
bounded review found no further issues and ran the 43 new focused tests
successfully; its result is `REVIEW-REACTION-CAPTION-GAP-FINAL.md` in that directory.
The two earlier Claude runs stopped at task turn limits, not account quota;
the final review completed successfully.
