# PRP: Editor Style Controls (Batch 1)

## Goal
Add per-clip style overrides for caption and hook text: font size, text color, hook background color.
These must flow through all 4 layers (DB → API → Preview → Export).

## Why
- Users want to customize caption/hook appearance beyond the 7 built-in presets
- Preview must match export exactly so users aren't surprised
- Presets are still the base; overrides only apply when user changes them

## What

### New fields (5 total)
| Field | Type | Default | Description |
|---|---|---|---|
| `caption_font_size` | Float | null | Caption font size override (px at 1080w). Preset default if null. |
| `caption_text_color` | String(9) | null | Caption text color hex `#RRGGBB`. |
| `hook_font_size` | Float | null | Hook font size override. |
| `hook_text_color` | String(9) | null | Hook text color hex. |
| `hook_bg_color` | String(9) | null | Hook background color hex (default `#0D0D0D`). |

### UI controls in editor right panel
- Caption font size: number input (range 40-120, step 2)
- Caption text color: `<input type="color">`
- Hook font size: number input (range 14-80, step 2)
- Hook text color: `<input type="color">`
- Hook background color: `<input type="color">`

All show preset default when field is null. Changes update state immediately (live preview updates).

### Preview (`_CaptionOverlay`)
- Apply `caption_font_size` and `caption_text_color` to caption span styles
- Apply `hook_font_size`, `hook_text_color`, `hook_bg_color` to hook box styles

### Export (`captioner.py`)
- `generate_captions()` gets 5 new optional params
- Each overrides the corresponding preset value when not None

## Files to Modify

```
server/models.py                   — add 5 mapped_column entries to ClipModel
server/database.py                 — add 5 entries to _clip_migrations list
server/schemas.py                  — add 5 Optional fields to ClipResponse
server/routers/clips.py            — add 5 Optional fields to ClipUpdate
server/services/captioner.py       — add 5 params to generate_captions(), apply overrides
server/workers/pipeline.py         — pass 5 new clip fields to generate_captions()
src/types/index.ts                 — add 5 optional fields to Clip interface
src/app/editor/[id]/page.tsx       — add state, UI controls, wire to overlay + buildSaveData
```

## Implementation Blueprint

### models.py additions (after hook_align line ~177)
```python
caption_font_size: Mapped[float | None] = mapped_column(Float, nullable=True)
caption_text_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
hook_font_size: Mapped[float | None] = mapped_column(Float, nullable=True)
hook_text_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
hook_bg_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
```

### database.py migration additions
```python
("caption_font_size", "REAL"),
("caption_text_color", "VARCHAR(9)"),
("hook_font_size",     "REAL"),
("hook_text_color",    "VARCHAR(9)"),
("hook_bg_color",      "VARCHAR(9)"),
```

### captioner.py generate_captions() new params
```python
caption_font_size: Optional[float] = None,
caption_text_color: Optional[str] = None,
hook_font_size: Optional[float] = None,
hook_text_color: Optional[str] = None,
hook_bg_color: Optional[str] = None,
```
Then: if caption_font_size is not None: preset["font_size"] = caption_font_size  (local copy, don't mutate)

### pipeline.py handle_export additions
```python
caption_font_size=clip.caption_font_size,
caption_text_color=clip.caption_text_color,
hook_font_size=clip.hook_font_size,
hook_text_color=clip.hook_text_color,
hook_bg_color=clip.hook_bg_color,
```

### Editor UI (page.tsx)
Add a "Style Overrides" section in the right panel with color pickers and number inputs.
Add the 5 state variables, initialize from clip in useEffect, include in buildSaveData.
Pass to `_CaptionOverlay` as new props, apply in the JSX styles.

## Gotchas
- Don't mutate the preset dict — make a shallow copy first: `effective = {**preset}`
- Hook bg color in preview: use inline `style={{ backgroundColor: hookBgColor || '#0D0D0D' }}`
- Font size in preview: use inline `style={{ fontSize: captionFontSize ? `${captionFontSize * 0.37}px` : undefined }}` (scale down from 1080px export size to preview)
- Color pickers show `#000000` when value is `null` — initialize them with preset defaults
