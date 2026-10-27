# Music Video Studio: plan

Drop in an MP3 (and optional notes); the studio writes the song down so an AI can "hear" it; Claude Code then works
alone for a time budget and delivers an original motion-graphics music video (no footage, photos or generated
images: everything is made in code, 2D, 3D or both).

This is a new repo. It copies what fits from `Samvs1/aftermovie-studio` (no dependency on it) and leaves out
everything photo-related.

## Architecture

```
music_video_studio/
  Music Video Studio.bat, setup.ps1, setup.sh, requirements.txt (pinned)
  app/server.py, app/static/index.html     local web app (stdlib, 127.0.0.1)
  assets/fonts/                            open-licensed fonts + licence files
  templates/director-brief.md, revise-brief.md, story-prompt.md
  .claude/skills/musicvideo/SKILL.md (+ anti-defaults.md)
  tools/
    studio.py          every command, per project (MV_PROJECT scoping, Status file, re-runs itself through .venv)
    mv/                shared Python: common, device, listen, songdoc, song (frame curves, hits, bar:beat), media (ffmpeg)
    separate.py, fetch_lyrics.py, transcribe_lyrics.py, analyze_audio.py, align_words.py, fix_lyrics.py, song_kit.py
    songq.py           small queries on the song folder (what happens at 1:23? section "chorus 2"? the ★★★ moments?)
    web-engine/        render.py (Playwright driver) + runtime/mv.js + vendor/ (three.js, anime.js, d3, simplex-noise)
    blender/           run.py (headless runner) + mvblend.py (in-Blender helper) + examples
    compose.py         concat frame-exact, layer passes, mux the original audio unchanged; NVENC or libx264
    eyes.py            contact sheets, strips, beat sheets, motion-vs-music plot, sync check, preview pack
    checks.py          spec validator, photosensitivity, text checks, audio identity
    director.py        the autonomous run: brief, headless Claude Code, live progress, budget, versions
    benchmark.py  ->   speeds.json
  tests/make_test_song.py, smoke_test.py, test_server.py, fake_claude.py, scenes/
  projects/<p>/        project.json, song/ (the song folder + the MP3 + stems), cache/, work/, final/, report.md, review/
```

### The song folder (`projects/<p>/song/`)
The heart of the project, written by ingest: `README.md` (rewritten for a music-video director), `song-map.md`,
`song-map.json`, `lyrics.md`, `song-story.md`, `overview.png`, `pages/`, `envelopes.json` (10 Hz, as before),
`report.json`, `stems/`, plus new:
- `frames.json`: per-frame curves at the project fps (loudness, the four stems, spectral bands, transient strength,
  brightness, stereo width, vocal pitch), value of frame k = mean of a 200 Hz curve over the frame's own time slot
  (k/fps ± half a frame), so an event at time t lands in frame round(t·fps). `studio.py curves <p> --fps 60`
  re-makes it for another fps from the cached 200 Hz curves.
- `hits.json`: per-stem hit lists the renderers import directly: beats / downbeats (with bar:beat), kick, snare,
  hats/cymbals, bass notes, vocal onsets + sung words, other-stem onsets, with time, frame and strength.
- `report.json` gains a `signals` block: every curve with what it measures and its confidence.
- `song-story.md`: a new **story** step drafts it headlessly with Claude from the lyrics, the song map and the
  user's notes; the user edits it in the web app; if skipped, the director writes it first.

### Renderers (`tools/`) are optional building blocks
- **web-engine**: HTML/JS scenes (three.js, Canvas 2D, SVG, anime.js) rendered frame by frame in headless Chromium
  through Playwright (Python, so Node is not required). Deterministic: the runtime owns time (`Date.now`,
  `performance.now`, `requestAnimationFrame`, CSS / Web Animations are driven by the frame index), `Math.random` is
  re-seeded per frame, stateful scenes are pre-rolled. Chunked output, parallel workers, resume after a crash,
  GPU when there is one, SwiftShader otherwise. Text drawn by a scene is recorded into a text track for the checks.
  Browser: `MV_CHROME`, Playwright's Chromium, Chrome, or Edge (always on Windows 11).
- **blender**: `blender -b` with a runner that loads the song data and the scene script (`build(mv)` +
  optional per-frame `frame(mv, f)`); Eevee on the GPU, Cycles on the CPU, Workbench as the fast fallback;
  PNG sequences resume natively (Blender skips frames that exist).
- **compose**: concat (stream copy when the chunks match, verified frame count), layer passes (alpha over),
  mux the original audio (stream copy for AAC sources, else a straight AAC encode: no filters, no offset);
  text tracks follow their segments into the final.

### Eyes and checks (the director can't hear or watch)
- eyes: contact sheets / strips with timecode, bar:beat, section and lyric in the margin; beat sheets (the frame
  on and just before every downbeat of a section); motion/energy of the video plotted against loudness and stems;
  the sync checker (frame-difference events vs beats, 8ths, kicks, snares, sung words, stops; drift per section);
  `eyes.py look` = all of it in one folder with an index.
- checks: spec (duration = audio to the frame, size, fps, H.264 yuv420p, AAC, decodes to the end, unintended
  black / frozen stretches, A/V offset and audio identity against the song file), photosensitivity (general and
  red flashes, > 3 per second over 25% of the screen fails, after ITU-R BT.1702 / WCAG 2.x; an approximation,
  not a certified Harding test), text (reading time, contrast ≥ 3:1, title-safe area, clipping, lyric timing).

### The director
- Interactive: `/musicvideo <project>` skill: song folder once, in order → `work/song-notes.md` digest → story →
  concept (from this song's story and structure; anti-defaults note) → style frames → build → low-res previews →
  eyes → revise → final → checks → `report.md`.
- Autonomous: `studio.py direct <p> --budget 90m [--effort high] [--model opus] [--seed-notes ...] [--revise]` and a
  web-app button. Fills `templates/director-brief.md`, launches `claude -p` in the project folder with
  `--output-format stream-json --input-format stream-json --verbose --permission-mode dontAsk --allowedTools ...`,
  `--add-dir` for the studio tools, no web tools, network commands denied. Streams events to `work/director/`, which
  the web app polls. Clock: the brief gives the deadline and `studio.py clock <p>`; the runner sends time warnings
  through stream-json input, nudges an early finisher to use the time left, and stops the process at the deadline
  plus a grace period. Afterwards it archives the cut as `final/music-video_vN.mp4`, runs the checks, and appends
  the run settings (model, effort, budget, wall time, cost) to `report.md`.
- Review round: notes at a moment (N) on the Watch page → `review/notes.json` → `/musicvideo <p>` or
  `direct --revise` (templates/revise-brief.md); earlier versions are never overwritten.

### Web app
Pages per project: **Song & brief** (upload, artist/title, language, what the song is about to you, mood /
direction, ideas, avoid, delivery format, optional lyrics) → **Analyse** (ingest with progress) → **The song**
(the aftermovie viewer, kept: plays along, solos stems, beat click, sections, loudness, notes, chords, lyrics) →
**Story** (draft with Claude, edit, save) → **Direct** (budget, effort, model, seed notes, live progress, stop; or
the interactive command) → **Watch** (the video above the song lanes, versions, notes with N, report, checks).

## What is copied from aftermovie-studio (and adapted)
- Song kit: `separate.py`, `fetch_lyrics.py`, `transcribe_lyrics.py`, `analyze_audio.py`, `align_words.py`,
  `fix_lyrics.py` (+ `lyrics_fix`), `song_kit.py`, `am/listen.py`, `am/songdoc.py`, `am/device.py`, the needed
  parts of `am/common.py` → `tools/mv/`. Paths change: one song per project, in `song/` with its stems.
- `studio.py` structure (per-project commands, project scoping env var, `Status`, `run`, `doctor`, `.venv` re-run).
- `app/server.py` (stdlib, 127.0.0.1, jobs, range requests) and the **The song** page; the rest of the UI is new.
- QA ideas without photos: the sync check (from `qa.py`, now EDL-free), contact sheets, reading time + contrast
  (`am/checks.py`), the audio check (now: identity with the song file, since the audio is never edited).
- `render.py`: NVENC when present, libx264 fallback, preview vs final presets, chunk caching (now in web-engine).
- `setup.ps1`, pinned `requirements.txt`, the `.bat` launcher, `.gitignore`, the OFL/Apache fonts (licence files
  added for every font).
- Craft rules from `CLAUDE.md` that still apply (sync, lyric timing, reading time, contrast, no reveal in a shot's
  last beat, one committed visual language, fix the film not the frame, say what needs a human ear).

Left out: photos, faces, cast, Polarsteps, shot logs, grading, the compositor and its EDL, `find_joins.py` (the
video always uses the whole song), song suggestions, style samples, the stylebook (replaced by an anti-defaults note).

## New
Frame curves + hit lists + extra signals; offline fallbacks for every model step (so ingest still completes, with
lower confidence written into `report.json`, when a model can't be downloaded); user-supplied lyrics (LRC or
plain text) for songs LRCLIB doesn't have; the story step; web-engine; Blender runner; compose; eyes; checks
(spec, photosensitivity, text, audio identity); benchmark + `speeds.json`; the director runner and brief; review
rounds with versions; `songq.py`; synthetic test song + smoke test + server test.

## Choices made where the request was open (more freedom, better feedback)
- Python Playwright drives the browser (no Node needed on Windows; Edge works when Chrome isn't installed).
- Text checks need to know where text is: the web runtime records it automatically (DOM elements marked
  `data-mv-text`, or `MV.text()` for canvas/WebGL text); other passes can declare text in `work/text.json`.
- Intended black / frozen stretches are declared in `work/intent.json`; anything else is flagged.
- The runner nudges a director that finishes early to keep improving the video while time is left (`--no-nudge`).
- No style menu: the anti-defaults note pushes for a concept from this song; fonts are raw material, not looks.

## Risks
- **Blocked downloads here**: pytorch.org, huggingface.co, lrclib.net, dl.fbaipublicfiles.com (Demucs, MMS
  aligner), cloud.cp.jku.at (beat_this), download.blender.org, cdn.jsdelivr.net and the Chrome-for-Testing bucket
  are refused by this VM's proxy. The ML models therefore can't run here: the fallbacks run instead, and the smoke
  test says so. The real model paths are verified on Windows (VERIFY_LOCAL.md).
- Can't test here: Windows, NVENC, CUDA, Eevee on a real GPU, the web app in a real browser. Windows code is kept
  small and boring; VERIFY_LOCAL.md is the checklist.
- Headless WebGL speed on CPU (SwiftShader) is slow: the benchmark measures it so the director can budget.
- Claude Code flags change between versions: checked against the installed `claude --help` (2.1.311 here); the
  runner logs the exact command line, and `doctor` reports the version.
- Photosensitivity check is an approximation of the broadcast guidelines, documented as such.
