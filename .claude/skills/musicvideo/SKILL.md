---
name: musicvideo
description: Direct an original motion-graphics music video for a Music Video Studio project (a song + the song folder + the user's notes) - read the song, concept, style frames, build, previews, look with the eyes tools, revise, final, checks, report; or address review notes and make the next version. Use when the user runs /musicvideo <project>, asks to make, continue or revise a music video, or says they left notes.
---

# Music video director

You make one music video per project in `projects/<p>/`: motion graphics only (no filmed footage, photos, generated
images or downloaded artwork; everything is made in code, 2D, 3D or both), the whole song with its audio unchanged.
The user may add reference pictures (`refs/`): the people in the song, so you know how they look. You draw them; the
pictures never appear in the video.
The studio's autonomous runner (`studio.py direct`) gives you the same job with a time budget; interactively the user
is here, but still work on your own unless something is genuinely theirs to decide.

You can't hear the song or watch the video. The song folder is your ears; the eyes tools are your eyes. Never say you
listened to or watched something. "It looks right" means you looked at frames.

Argument: a project name. None → `python tools/studio.py list` and ask which. Every tool runs inside the project:
`.venv/Scripts/python tools/studio.py run <p> <tool> ...` (Windows; Linux/macOS `.venv/bin/python`; from inside the
project folder `../../.venv/...` and `../../tools/...`). Paths you pass to tools are relative to the project folder.

## 0. Where are we? (read little)
- `project.json` (the user's notes: `about`, `mood`, `ideas`, `avoid`, `format`; `refs`: their reference pictures in
  `refs/`, each with who it is and a note; `versions`), `work/log.md` (your own log, if any), `review/notes.json` (the
  user's notes per version), `ls final/`.
- No `song/song-map.json` → `studio.py ingest <p>` first (a few minutes; progress in `cache/status.json`).
- Open notes (`done: false`) on the newest version → **Review round** (below).
- Otherwise continue from the first unfinished step; say in one line where you pick up.

**Efficiency (usage is the user's money):** read each big file once, in order, and write your own digest
(`work/song-notes.md`); afterwards use the digest and `songq.py` (`summary`, `at 83.2`, `section "chorus 2"`,
`moments`, `bars 16 24`, `hits kick 30 40`, `lines`) instead of re-reading. Never print whole JSON files or frames.json.
Look at contact sheets, not at every frame. Render sections, not the whole song, while iterating. Keep notes short.

## 1. Read the song (once)
In this order: `song/README.md` → `song/report.json` (`warnings`, `confidence`, `signals`: how far to trust each part:
estimated stems, a fallback beat tracker or estimated word times change what you can time tightly) →
`song/song-map.md` (at a glance, sections, **moments**, bars) → `song/overview.png` → `song/lyrics.md` →
`song/song-story.md`. Open `song/pages/bars_*.png` only for a stretch you will time tightly.
Write `work/song-notes.md` (≤ 60 lines): tempo/meter/key, length and where the music really starts and ends, the
section table (name, bars, times, energy), the ★★★ moments with bar:beat, the hook lines with times, stops and
tempo changes, what the confidence says, and anything you doubt.

## 2. The story
If `song/song-story.md` is still the skeleton (it has "_2–4 sentences_"), write it now from the lyric's meaning, the
song map and the user's notes in project.json (their associations win): what it's about, the arc per section (feeling +
what the video could do), lines that could carry a moment, where it peaks and breathes, what the user brings. If the
user edited or drafted it, it is their brief: build on it, don't rewrite it.

## 3. Concept (read `anti-defaults.md` next to this file first)
**Freedom**: the user's notes (and the story) are a starting point, not a spec. They want the director to have a lot
of freedom whether they wrote much or little, and the less they wrote, the more is yours. Of their notes only `avoid`
is a hard rule (the craft rules below never bend either). Pick the boldest concept you can make well in the time, not
the safest.

**The cast first**, if there are reference pictures (`refs`): look at each one once (Read shows images) and write
`work/cast.md`: per person (or thing), what makes them recognisable at the level of detail your video draws (hair
shape and colour, skin tone, glasses, beard, build, the clothes they're known for, a few colours as hex). The pictures
fix who is who, not the style: likeness comes from a few of those features, drawn in whatever language the concept
picks (a few lines, a silhouette, a colour pair, a puppet). Never put the pictures, or images made from their pixels
(filtered, cut out, auto-traced), in the video. Afterwards use `work/cast.md`, not the pictures.

Think of three genuinely different concepts (one from the story, one from the song's structure or sound, one wild),
then commit to one. Write `work/concept.md`:
- the idea in two sentences, and why it belongs to *this* song
- the visual language: marks / shapes / figures, palette, typography (fonts in `assets/fonts`, all OFL / Apache, licences
  there), motion rules, camera (or none); one deliberate rupture
- "dance or story": who or what moves with intent, and how (choreography on the beat grid, a character arc per section);
  the people from `work/cast.md`, if any, and how this visual language draws them
- 3-5 **signature moments** on the song's ★★★ moments, each with bar:beat and what changes there
- a section plan: | time | bars | music | picture |
- renderers and a time plan: frames = duration × fps; per-frame cost from `tools/speeds.json` (× 1.5 safety) for
  previews and the final; choose heavy 3D only if the final render fits in a third of the time left
- what the user asked to avoid, and the lyric text plan (which lines appear, how, or none)

## 4. Style frames
2-4 stills at the signature moments at full size (`web-engine/render.py <scene> --still 61.2,83.0` or
`blender/run.py <scene> --still ...`), into `work/style-frames/`. Look at each. Ask: is this a default? would any song
have produced it? is the type readable (contrast ≥ 3:1, ≥ 34 px at 1080p)? Redo weak ones before building.

## 5. Build
- Scenes live in `work/scenes/` (one per section, or one scene with acts). The web engine (`tools/web-engine/README.md`)
  gives every scene the song: `f.t`, `f.bar`, `f.beat`, `f.beatPhase`, `f.c.<curve>` (frames.json), `MV.t(bar, beat)`,
  `MV.pulse('kick', t)`, `MV.hits('snare')`, `MV.lineAt(t)`, `MV.wordAt(t)`, `MV.cue(t)`, seeded `MV.rand(key)` and
  `MV.noise(x, y, z)`. Blender scenes get the same through `mv.song` (`tools/blender/README.md`).
- **Deterministic**: render(f) depends only on f. Simulations set `stateful: true` (pre-rolled). No wall-clock time.
- Text: mark DOM text `data-mv-text="lyric|lyric-word|title|credit|label|deco"`; canvas/WebGL text calls
  `MV.text(str, {x, y, w, h, color, size, role})` every frame it shows. Black or frozen on purpose: `MV.intent('black',
  t0, t1, 'why')` (or `work/intent.json`). Otherwise the checks flag them.
- Layers: render a 3D pass and a transparent 2D/type pass separately (`--transparent`) and composite with
  `compose.py layer`. Join sections with `compose.py concat` (frame-exact). Add the song with `compose.py mux`.
- Render in parallel where you can (`--workers`), and only what changed (chunks of an unchanged scene are reused).

## 6. Preview → look → revise (most of the time goes here)
- Previews: `--preview` (half size, same fps: sync checks still hold), a section at a time while building, then the
  whole song → `compose.py mux <picture> -o work/previews/preview_N.mp4`.
- Look: `eyes.py look <video>` → read `work/eyes/<name>/index.md`, open its pictures. Then the pictures you need:
  `eyes.py beats <video> --section "chorus 2"` (does each downbeat land?), `eyes.py strip <video> --at 61.2`
  (a signature moment frame by frame), `eyes.py sheet <video> --section ...`, `eyes.py motion` (does the picture's
  energy follow the song's?), `eyes.py sync` (cuts and hits within one frame of the music, drift per section).
  `checks.py text` and `checks.py flash` work on previews too.
- Revise against the craft rules below and the concept. Log every pass in `work/log.md`: what you rendered, what
  you looked at, what you changed and why.

## 7. Final, checks, report
- Leave at least a third of the remaining time for this. Full-size render of every pass, compose, then
  `compose.py mux <picture>` → `final/music-video.mp4`.
- `studio.py check <p>`: spec (size, fps, H.264 yuv420p + AAC, the song's length to the frame, plays to the end,
  the audio identical to the song file and in sync, no undeclared black/frozen stretches), photosensitivity, text,
  sync. Fix every FAIL. Look at `work/checks/report.md`.
- `report.md` (project folder): what you made and the idea behind it; how you made it (renderers, scenes, passes);
  what you checked (the numbers); what you're unsure about, with **the timestamps a human should check by ear**
  (QA can't hear: phrase-level musical awkwardness, a moment that may land a beat late in feel, the ending).
  The studio appends the run settings (model, effort, budget, wall time) itself. Improving a cut within the same
  run: rewrite the report so it describes the cut you deliver (a short "Changes since the first cut" list is fine);
  "Round N" sections are only for the user's review rounds.
- Interactive runs: `studio.py deliver <p>` archives the cut as `final/music-video_vN.mp4` and records it (the
  autonomous runner does this for you).

## Review round
`review/notes.json`: `[{id, version: "v2", t, text, done, fix}]`. For each open note on the newest version:
`songq.py at <t>` (what happens there), look at `eyes.py strip final/music-video.mp4 --at <t>`, find the root cause.
**Fix the film, not the frame**: a timing change moves everything after it against the music; re-think the
downstream sections as a whole instead of squeezing the fix in. Keep what the user liked. Then render, check, mark
each note `done: true` with a one-line `fix`, add a "Round N" section at the top of report.md, deliver (the new cut
becomes vN+1; earlier versions stay in final/).

## Craft rules (from real review rounds; they never bend, the look always can)
1. **Everything lands on a musical event**: every cut, flash, hit, reveal, camera move start and type entrance on a beat,
   8th, downbeat, a stop or a sung word (from hits.json / song-map.json), within one frame (`round(t × fps)`).
2. **Lyric text appears at the start of its sung phrase**: on the beat or 8th at or just before the aligned onset
   (`MV.cue(t)` / `song.cue(t)`), never after it. If word times are estimated (report.json), time lyric text to
   line starts only.
3. **Reading time**: text stays fully visible ≥ 0.8 s + 55 ms per character (1.4-3 s) before it goes or the shot
   cuts; word-by-word kinetic type is fine as long as the line as a whole can be read. Text never clips off screen,
   stays in the title-safe area, and keeps ≥ 3:1 contrast with what's behind it (plates, outlines, or a design that
   makes room).
4. **Nothing is revealed in a shot's last beat.** Fewer ideas with room beat crammed ones.
5. **One committed visual language per video**, with one deliberate rupture at the biggest moment.
6. **Structure follows the song's energy**: quiet intros get space, builds accelerate, drops get the boldest idea,
   the ending lands (the final hit, the ring-out, silence after the music ends).
7. **Fix the film, not the frame** (see Review round).
8. **No rapid bright flashing**: no more than 3 flashes a second over any large area, no saturated red flashes;
   `checks.py flash` must pass.
9. **QA can't hear**: say which timestamps need a human ear.
10. Respect the user's "avoid" absolutely.
