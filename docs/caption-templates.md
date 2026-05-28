# Caption Templates

Caption Studio templates live as one `.json` file per template in
`data/caption_templates/`. The folder is scanned at every API call, so dropping
a file in there makes it appear instantly in the picker.

The seven builtin templates ship with `builtin: true` and are rewritten from
`services/captioner.py:DEFAULT_PRESETS` on every server start (so if you edit
the Python presets, your changes propagate; if you edit a builtin's JSON by
hand, the next restart will overwrite it — duplicate it under a new id to
keep custom changes).

## Template JSON schema

```json
{
  "id":              "viral_gradient",
  "name":            "Viral Gradient",
  "font_family":     "Impact",
  "font_size":       76,
  "font_weight":     "Bold",
  "text_color":      "#FFFFFF",
  "highlight_color": "#FF6B35",
  "outline_color":   "#000000",
  "outline_width":   5,
  "shadow_offset":   3,
  "shadow_color":    "#FF6B3540",
  "position":        "bottom",
  "uppercase":       true,
  "animation":       "word",
  "max_words_per_line": 2,
  "borderstyle":     1
}
```

- `id` is the filename stem; lowercase letters, digits, `_`, `-` only.
- `position` is one of `top` / `center` / `bottom` (used by the
  transcript-driven auto-captioner; manual overlays use `x_pct` / `y_pct`).
- `borderstyle` is libass: `1` = outline + shadow (default), `3` = opaque box.
- All color fields are `#RRGGBB` or `#RRGGBBAA`.
- `font_family` must match a font that's installed on the system **or**
  uploaded to `data/fonts/` via the Caption Studio "Add a font" button.

## Adding a font

Drop a `.ttf` / `.otf` / `.ttc` into `data/fonts/`, or click **Add a font**
in the Caption Studio. The file is detected at every preview render — no
restart needed. The detected family name is what you use in `font_family`.

## Importing a CapCut template

CapCut templates are not plain text. They ship as either:

1. **Cloud templates** (`.zip` exports from the CapCut PC editor) containing
   `draft_content.json` + `materials/` + asset folders.
2. **Mobile templates** that are tied to the CapCut account and can't be
   exported as files at all — you can only "use" them inside the app.

For mobile templates, the only viable path is to **manually port the look**:
play the template in CapCut, screenshot a frame, and recreate the look as a
JSON file using the schema above. The fields that map cleanly are:

| CapCut UI field        | JSON field         |
|------------------------|--------------------|
| Font                   | `font_family`      |
| Size                   | `font_size`        |
| Style → Bold / Heavy   | `font_weight`      |
| Color                  | `text_color`       |
| Stroke / Outline width | `outline_width`    |
| Stroke / Outline color | `outline_color`    |
| Shadow distance        | `shadow_offset`    |
| Shadow color           | `shadow_color`     |
| Highlight color        | `highlight_color`  |
| ALL CAPS               | `uppercase: true`  |
| Box background         | `borderstyle: 3`   |

Fields that **don't** map cleanly (because libass is simpler than CapCut's
shader engine):

- Bouncy / typewriter / glitch / morph animations
- Per-character gradient fills (we only support solid colors)
- Background blur, neon glow effects
- Custom keyframes (we support `start_t` / `end_t` only, no curves)

For `.zip` PC templates, see `scripts/capcut_import.py` (placeholder — see
"Roadmap" below).

## Roadmap

- [ ] **CapCut `.zip` template import** — parser for `draft_content.json` that
      extracts text style and writes a `.json` template. Will skip unsupported
      effects (animation, gradients, blur) and log them so the user knows what
      will not render.
- [ ] **Server-side template preview thumbnails** — currently the grid uses a
      pure-CSS approximation. Pre-rendering thumbnails would match libass exactly.
- [ ] **Per-overlay color override in the UI** — right now you can only swap
      the font; to change a color you have to duplicate the template.
