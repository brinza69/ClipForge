export const meta = {
  name: 'ref-style-extraction',
  description: 'Measure the edit style of 9 reference YouTube Shorts and synthesise one reproducible recipe',
  phases: [
    { title: 'Measure', detail: 'one agent per reference short: cuts, framings, captions, effects' },
    { title: 'Synthesise', detail: 'merge 9 profiles into a single numeric style spec' },
  ],
}

const DIR = 'C:/Users/mihai/AppData/Local/Temp/claude/D--clipforge/ba62feb1-f2d4-403b-8d7f-cc731aaae74a/scratchpad/refs'
const WORK = 'C:/Users/mihai/AppData/Local/Temp/claude/D--clipforge/ba62feb1-f2d4-403b-8d7f-cc731aaae74a/scratchpad/refanalysis'

const IDS = ['8cO8UWyjGyc','9L2Yrs6jwb4','uRU9SzlVClg','_LQ379ZhspI','8-eCvn1gWIg','Cfpc04Tpc4k','jOUyn64mSsk','-dHfHZgtXJw','q-SaGj-pDh0']

const PROFILE = {
  type: 'object',
  required: ['id','duration_s','fps','resolution','cut_times_s','avg_shot_s','cuts_per_min','shot_len_p10_p50_p90','framing_style','caption','effects','hook','audio','notes'],
  properties: {
    id: { type: 'string' },
    duration_s: { type: 'number' },
    fps: { type: 'number' },
    resolution: { type: 'string' },
    cut_times_s: { type: 'array', items: { type: 'number' }, description: 'detected shot-change times in seconds' },
    avg_shot_s: { type: 'number' },
    cuts_per_min: { type: 'number' },
    shot_len_p10_p50_p90: { type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3 },
    framing_style: {
      type: 'object',
      required: ['distinct_framings','same_scene_reframe_pct','zoom_levels_observed','punch_in_used','shake_used','pan_drift_used','speed_ramp_used','description'],
      properties: {
        distinct_framings: { type: 'integer', description: 'how many distinct camera framings recur (wide/medium/close/extreme)' },
        same_scene_reframe_pct: { type: 'number', description: '0-100: share of cuts that are a reframe of the SAME footage (punch-in/multicam) vs a jump to different footage' },
        zoom_levels_observed: { type: 'array', items: { type: 'string' } },
        punch_in_used: { type: 'boolean' },
        shake_used: { type: 'boolean' },
        pan_drift_used: { type: 'boolean' },
        speed_ramp_used: { type: 'boolean' },
        description: { type: 'string' },
      },
    },
    caption: {
      type: 'object',
      required: ['present','position','words_per_card','style_description','color','outline','animation','all_caps','highlight_active_word'],
      properties: {
        present: { type: 'boolean' },
        position: { type: 'string', description: 'e.g. centre, lower-third, upper-third, with approximate y as % of frame height' },
        words_per_card: { type: 'number' },
        style_description: { type: 'string' },
        color: { type: 'string' },
        outline: { type: 'string' },
        animation: { type: 'string', description: 'pop, scale-in, karaoke word highlight, none' },
        all_caps: { type: 'boolean' },
        highlight_active_word: { type: 'boolean' },
      },
    },
    effects: { type: 'array', items: { type: 'string' }, description: 'zoom lines, flash, chromatic aberration, emoji/sticker overlays, meme cutaways, progress bar, border, vignette, grain, etc.' },
    hook: { type: 'string', description: 'what happens in the first 2 seconds — framing, caption, audio' },
    audio: { type: 'string', description: 'music bed? sfx hits on cuts? raw stream audio only? loudness feel' },
    notes: { type: 'string' },
  },
}

phase('Measure')
const profiles = await parallel(IDS.map((id, i) => () =>
  agent(
    `You are measuring the EDIT STYLE of one short-form vertical video so it can be reproduced programmatically with ffmpeg.\n\n` +
    `FILE: ${DIR}/${id}.mp4\n` +
    `Metadata sidecar (may exist): ${DIR}/${id}.info.json — read it for title/channel/duration.\n` +
    `Scratch dir for your outputs (create it): ${WORK}/${id}\n\n` +
    `IMPORTANT ENVIRONMENT NOTES:\n` +
    `- Use the Bash tool. \`python\` is NOT on PATH — use "D:/clipforge/server/.venv/Scripts/python.exe".\n` +
    `- ffmpeg/ffprobe ARE on PATH (ffmpeg 8.1).\n` +
    `- Use forward slashes in paths.\n\n` +
    `DO ALL OF THIS:\n\n` +
    `1. ffprobe the file for duration, fps (r_frame_rate), width, height, and audio codec.\n\n` +
    `2. Detect shot changes precisely:\n` +
    `   ffmpeg -hide_banner -i FILE -vf "select='gt(scene,0.20)',metadata=print:file=-" -f null - 2>&1\n` +
    `   Parse the pts_time values. Also try threshold 0.10 and 0.35 and report which threshold gives a shot count that matches what you SEE. Compute avg shot length, cuts per minute, and the p10/p50/p90 of shot durations.\n\n` +
    `3. LOOK AT THE VIDEO. This is the most important step — do not skip it and do not guess.\n` +
    `   Build contact sheets and READ them with the Read tool (Read renders images):\n` +
    `   - a dense timeline sheet: sample ~36 frames evenly across the whole video, tile 6x6, each tile ~180px wide.\n` +
    `     ffmpeg -y -i FILE -vf "fps=36/DURATION,scale=180:-2,tile=6x6:padding=4:color=white" -frames:v 1 -update 1 OUT.png\n` +
    `   - a first-2-seconds sheet: 8 frames from t=0..2, tile 8x1 — for the hook.\n` +
    `   - at least 3 cut-pair sheets: for 3 different detected cuts, extract the frame just BEFORE and just AFTER the cut side by side, so you can judge whether the cut is a REFRAME of the same footage (punch-in / different crop of the same camera) or a jump to DIFFERENT footage (b-roll, meme insert, other camera).\n` +
    `   Read every sheet you make. Base your answers on what you actually see.\n\n` +
    `4. From what you see, characterise:\n` +
    `   - framing: how many distinct zoom levels/framings recur; is there push-in inside a shot; camera shake; slow drift; speed ramps; is the subject always centred.\n` +
    `   - captions: present? where vertically (as % of frame height)? how many words per card? ALL CAPS? colour, outline/shadow, does the active word get highlighted in a different colour? any pop/scale animation?\n` +
    `   - overlays and effects: zoom lines, white flash on hit, emoji/sticker, meme cutaway, arrow/circle highlight, borders, grain, vignette, chromatic aberration, progress bar.\n` +
    `   - hook: exactly what the first 2 seconds do.\n\n` +
    `5. Audio: check loudness with\n` +
    `   ffmpeg -hide_banner -i FILE -af ebur128=peak=true -f null - 2>&1 | tail -20\n` +
    `   and say whether there is an obvious music bed, sfx hits landing on cuts, or just raw stream audio.\n\n` +
    `Return the structured profile. Every number must come from a measurement or from frames you actually looked at. If you could not determine something, say so in notes rather than inventing it.`,
    { label: `ref:${id}`, phase: 'Measure', schema: PROFILE }
  )
))

const good = profiles.filter(Boolean)
log(`measured ${good.length}/${IDS.length} reference shorts`)

phase('Synthesise')
const SPEC = {
  type: 'object',
  required: ['consensus','shot_grammar','caption_spec','effect_spec','hook_spec','disagreements','ffmpeg_notes'],
  properties: {
    consensus: { type: 'string', description: 'the recipe in 10 lines or fewer — what makes these edits work' },
    shot_grammar: {
      type: 'object',
      required: ['target_shot_len_s','shot_len_range_s','cuts_per_min','framing_ladder','reframe_vs_cutaway_ratio','rules'],
      properties: {
        target_shot_len_s: { type: 'number' },
        shot_len_range_s: { type: 'array', items: { type: 'number' }, minItems: 2, maxItems: 2 },
        cuts_per_min: { type: 'number' },
        framing_ladder: { type: 'array', items: { type: 'string' }, description: 'the named zoom levels with their crop factor relative to the widest 9:16 window, e.g. "wide 1.00", "medium 0.78"' },
        reframe_vs_cutaway_ratio: { type: 'string' },
        rules: { type: 'array', items: { type: 'string' }, description: 'when to switch framing — driven by speech energy, laughter, motion, silence, new sentence, etc.' },
      },
    },
    caption_spec: { type: 'object', description: 'concrete values: font size as % of frame height, y position, colour, outline width, words per card, active-word highlight colour, animation', additionalProperties: true },
    effect_spec: { type: 'array', items: { type: 'string' }, description: 'each effect with its trigger and duration, e.g. "white flash 60ms on audio peak > p95"' },
    hook_spec: { type: 'string' },
    disagreements: { type: 'array', items: { type: 'string' }, description: 'where the 9 references genuinely differ — do not average these away' },
    ffmpeg_notes: { type: 'array', items: { type: 'string' }, description: 'how each element maps onto ffmpeg crop/scale/eq/subtitles primitives' },
  },
}

const spec = await agent(
  `Nine reference short-form videos were each measured independently. Here are the profiles as JSON:\n\n` +
  JSON.stringify(good, null, 1) +
  `\n\nSynthesise ONE reproducible style spec from them.\n\n` +
  `Context for how it will be used: it will drive an automatic vertical-clip renderer that takes a 1920x1080 60fps stream VOD and outputs 1080x1920. The renderer already works and can, in a SINGLE ffmpeg encode:\n` +
  `  - hard-switch the crop rectangle (w,h,x,y) at exact times via sendcmd → instant camera switch and discrete zoom levels\n` +
  `  - step the crop size every couple of frames → smooth push-in\n` +
  `  - use per-frame expressions on crop x/y (t is available) → shake, drift\n` +
  `  - use eq with eval=frame and expressions on brightness/contrast/saturation → flash hits, punch grade\n` +
  `  - burn an ASS subtitle file → captions with per-word karaoke highlight\n` +
  `It CANNOT (in one pass) do: cutaways to external footage, sticker/emoji PNG overlays without extra inputs, or speed ramps that change audio timing.\n\n` +
  `So: be concrete and numeric. Where the references use something the renderer cannot do, say so explicitly and give the closest achievable substitute. Where the nine references genuinely disagree, list that under disagreements instead of averaging it into mush.\n` +
  `The framing_ladder must be expressed as crop factors relative to the widest possible 9:16 window of a 1920x1080 frame (that widest window is 608x1080 = factor 1.00; smaller factor = tighter shot).`,
  { label: 'synthesise-recipe', phase: 'Synthesise', schema: SPEC, effort: 'high' }
)

return { profiles: good, spec }
