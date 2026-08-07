"""Build a review page for the dynamic clips of one clipper project.

Renders `<project>/dynamic/index.html`: every clip that has been rendered, its
player, and the shot timeline that produced it — which camera was live when,
where the flash hits landed, and what was being said. That last part is the
point: judging this style from the video alone tells you something is wrong but
not which rule did it, and the timeline does.

    python scripts/build_dynamic_review.py bbdf781b1064

Serve it with the "ClipForge Dynamic Review" configuration in .claude/launch.json.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

DATA = Path(r"D:\clipforge\data\clipper")

CAMERA_COLORS = {
    "face": "#f2a65a",
    "face_tight": "#e4572e",
    "game": "#4c9f9c",
    "game_tight": "#2d6a6b",
}
CAMERA_LABELS = {
    "face": "facecam", "face_tight": "facecam strâns",
    "game": "gameplay", "game_tight": "gameplay strâns",
}

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>ClipForge — montaj dinamic</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0e1013; color:#e7e9ee;
         font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }}
  header {{ padding:28px 32px 18px; border-bottom:1px solid #23262d; }}
  h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:-.01em; }}
  .sub {{ color:#8b919e; font-size:14px; }}
  .recipe {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }}
  .chip {{ background:#171a20; border:1px solid #262a33; border-radius:999px;
          padding:5px 13px; font-size:13px; color:#b9bfcc; }}
  .chip b {{ color:#e7e9ee; font-weight:600; }}
  main {{ padding:26px 32px 60px; display:flex; flex-direction:column; gap:34px; }}
  .clip {{ display:grid; grid-template-columns:250px 1fr; gap:26px;
          background:#12151a; border:1px solid #21252d; border-radius:14px; padding:20px; }}
  video {{ width:250px; border-radius:10px; background:#000; display:block; }}
  .meta h2 {{ margin:0 0 4px; font-size:17px; }}
  .meta .where {{ color:#8b919e; font-size:13px; margin-bottom:14px; }}
  .quote {{ color:#aab1bf; font-size:13.5px; font-style:italic;
           border-left:2px solid #2c313b; padding-left:12px; margin:0 0 18px; }}
  .lane {{ position:relative; height:44px; border-radius:8px; overflow:hidden;
          background:#0b0d10; display:flex; }}
  .shot {{ position:relative; border-right:1px solid #0b0d10; cursor:default; }}
  .shot span {{ position:absolute; inset:0; display:flex; align-items:center;
               justify-content:center; font-size:10px; color:#0b0d10;
               font-weight:700; letter-spacing:.04em; }}
  .hits {{ position:relative; height:16px; margin-top:5px; }}
  .hit {{ position:absolute; top:0; width:2px; height:9px; background:#ffd23f; }}
  .axis {{ position:relative; height:16px; color:#6d7482; font-size:11px; }}
  .axis span {{ position:absolute; transform:translateX(-50%); }}
  .legend {{ display:flex; gap:16px; margin-top:14px; font-size:12px; color:#8b919e;
            flex-wrap:wrap; }}
  .legend i {{ display:inline-block; width:11px; height:11px; border-radius:3px;
              margin-right:6px; vertical-align:-1px; }}
  .stats {{ margin-top:14px; font-size:13px; color:#8b919e; }}
  .stats b {{ color:#e7e9ee; }}
  .warn {{ margin-top:10px; font-size:12.5px; color:#e8a33d; }}
</style>
<header>
  <h1>Montaj dinamic multi-cam — {project}</h1>
  <div class="sub">{source}</div>
  <div class="recipe">{chips}</div>
</header>
<main>{clips}</main>
"""

CLIP = """
<section class="clip">
  <video src="{file}" controls preload="metadata" playsinline poster=""></video>
  <div class="meta">
    <h2>{title}</h2>
    <div class="where">{where}</div>
    <p class="quote">{text}</p>
    <div class="lane">{shots}</div>
    <div class="hits">{hits}</div>
    <div class="axis">{axis}</div>
    <div class="legend">{legend}</div>
    <div class="stats">{stats}</div>
    {warnings}
  </div>
</section>
"""


def _lane(plan: dict) -> tuple[str, str, str, str]:
    duration = float(plan.get("duration") or 1.0)
    bars, used = [], []
    for shot in plan.get("shots") or []:
        width = 100.0 * (shot["t1"] - shot["t0"]) / duration
        camera = shot["camera"]
        used.append(camera)
        tip = (f'{shot["t0"]:.2f}-{shot["t1"]:.2f}s · {CAMERA_LABELS.get(camera, camera)}'
               f' · mișcare {shot["move"]}'
               f' · energie {shot["energy"]:.2f} · acțiune {shot["action"]:.2f}'
               f'{" · " + shot["text"] if shot.get("text") else ""}')
        bars.append(
            f'<div class="shot" style="width:{width:.3f}%;background:'
            f'{CAMERA_COLORS.get(camera, "#555")}" title="{html.escape(tip)}">'
            f'<span>{camera[0].upper() if "tight" in camera else camera[0]}</span></div>')

    hits = "".join(
        f'<div class="hit" style="left:{100.0 * t / duration:.3f}%" '
        f'title="hit {t:.2f}s"></div>' for t in plan.get("hits") or [])

    ticks = "".join(
        f'<span style="left:{100.0 * i / 5:.1f}%">{duration * i / 5:.0f}s</span>'
        for i in range(6))

    legend = "".join(
        f'<span><i style="background:{CAMERA_COLORS[c]}"></i>{CAMERA_LABELS[c]}</span>'
        for c in CAMERA_COLORS if c in used)
    legend += '<span><i style="background:#ffd23f"></i>flash pe vârf audio</span>'
    return "".join(bars), hits, ticks, legend


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    args = ap.parse_args()

    root = DATA / args.project / "dynamic"
    if not root.exists():
        raise SystemExit(f"nothing rendered yet: {root}")

    meta_path = DATA / args.project / "analysis" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    source = (meta.get("source") or {}) if isinstance(meta, dict) else {}
    hours, rest = divmod(int(source.get("duration") or 0), 3600)
    # meta.json carries no title — the VOD's name lives in the SQLite row, and
    # this page deliberately reads only the analysis artefacts.
    subtitle = (f"sursă {hours}h{rest // 60:02d}m · "
                f"{source.get('width')}x{source.get('height')} @ {source.get('fps')}fps · "
                f"{(source.get('filesize') or 0) / 1e9:.1f} GB")
    detected = (meta.get("content_type") or {}).get("content_type")
    if detected:
        subtitle += (f" · analiza a clasificat-o „{detected}"
                     f"” — de fapt e gameplay cu facecam, de aici cele două camere")

    sections, totals = [], []
    for plan_path in sorted(root.glob("*.plan.json")):
        stem = plan_path.name[:-len(".plan.json")]
        video = root / f"{stem}.mp4"
        if not video.exists():
            continue
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        shots = plan.get("shots") or []
        duration = float(plan.get("duration") or 0.0)
        totals.append((len(shots), duration))

        bars, hits, axis, legend = _lane(plan)
        face = plan.get("subject", {}).get("face", {})
        cuts_min = 60.0 * max(0, len(shots) - 1) / duration if duration else 0.0
        warnings = "".join(f'<div class="warn">! {html.escape(w)}</div>'
                           for w in plan.get("warnings") or [])
        sections.append(CLIP.format(
            file=html.escape(video.name),
            title=html.escape(stem),
            where=f"start {stem.split('_')[-1]} · {duration:.1f}s · 1080x1920",
            text=html.escape((shots[0].get("text") if shots else "") or ""),
            shots=bars, hits=hits, axis=axis, legend=legend,
            stats=(f"<b>{len(shots)}</b> cadre · <b>{cuts_min:.0f}</b> tăieturi/min · "
                   f"cadru mediu <b>{duration / max(1, len(shots)):.2f}s</b> · "
                   f"<b>{len(plan.get('hits') or [])}</b> flash-uri · "
                   f"facecam detectat la x={face.get('cx', 0):.0f} y={face.get('cy', 0):.0f}"),
            warnings=warnings))

    avg = (sum(d / max(1, s) for s, d in totals) / len(totals)) if totals else 0.0
    chips = "".join(f'<div class="chip">{c}</div>' for c in [
        f"referință: <b>8cO8UWyjGyc</b> (31 tăieturi/min)",
        f"cadru mediu aici: <b>{avg:.2f}s</b>",
        "subtitrări <b>1-2 cuvinte</b> la <b>43%</b> înălțime",
        "cuvânt activ <b>galben</b>",
        "audio <b>-14 LUFS</b>, comprimat",
        "<b>30 fps</b>, un singur encode",
    ])

    out = root / "index.html"
    out.write_text(PAGE.format(project=html.escape(args.project),
                               source=html.escape(subtitle),
                               chips=chips, clips="".join(sections)),
                   encoding="utf-8")
    print(f"-> {out}  ({len(sections)} clipuri)")


if __name__ == "__main__":
    main()
