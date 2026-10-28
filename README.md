# Music Video Studio

Drop in a song (plus a few notes if you like). The studio writes the song down so an AI can read what it can't hear:
every beat and bar, the sections and big moments, the melody, the chords, each instrument, each sung word, and
per-frame curves to animate with. Then **Claude Code directs an original music video for it on its own**, for a time
budget you set, and delivers `final/music-video.mp4` with a report. Motion graphics only: everything is made in code
(2D, 3D or both), no filmed footage, photos, AI-generated images or video, or downloaded artwork. The whole song,
audio unchanged.

You watch it next to the song's lanes, press **N** to leave a note at a moment, and the director makes the next
version. Earlier versions are kept.

## Install

**Windows 10/11** (the main target; an NVIDIA GPU makes everything faster but isn't required)
1. `git clone <this repo> "Music Video Studio"` (or download the ZIP), somewhere with ~10 GB free.
2. Double-click **`Music Video Studio.bat`**. The first start runs `setup.ps1`: Python 3.11-3.13 and FFmpeg via winget
   if missing, a `.venv` with the pinned packages (the CUDA build of PyTorch when `nvidia-smi` works), and it *asks*
   before each big download: Playwright's Chromium (skipped when Chrome or Edge is there), Blender (optional), the
   song-analysis models (~1.7 GB). Then it runs the install check.
3. Install **Claude Code** (the director; needs a Claude plan): `irm https://claude.ai/install.ps1 | iex`, then run
   `claude` once in this folder to log in.
4. Measure this PC once: `.venv\Scripts\python.exe tools\studio.py benchmark` (writes `tools\speeds.json`, which the
   director uses to budget its renders).

`VERIFY_LOCAL.md` is a step-by-step checklist for a new machine, with what you should see at each step.

**Linux / macOS:** `bash setup.sh` (the same, with apt hints), then `.venv/bin/python tools/studio.py serve`.

## Make a video
Start the studio (the `.bat`, or `studio.py serve`) → http://127.0.0.1:8765 → **Start a music video**, then the steps:
1. **Song & brief**: drop the MP3. Optional: artist + title (for the synced-lyrics lookup), the language, what the
   song is about to you, mood or direction, ideas, things to avoid, **people & references** (photos of the people in
   the song, each with who it is, so Claude knows what everyone looks like: it draws them in the video's own style,
   the photos never appear in the video), the delivery format (1080p 30/60/24, 4K, vertical, square, or custom), and
   the lyrics if they aren't online (plain text, or LRC with line or word times).
2. **Analyse** (a few minutes): stems, beats and bars, lyrics, melody, chords, sections, moments → the song folder.
3. **The song**: the song the way Claude reads it, playing along: sections, loudness and the ★★★ moments, melody and
   bass notes, chords, beats, the four stems (hear any of them alone: M V D B O), a beat click (K), every sung word.
   Lyrics out of step? **Fix the timing** → **Align with Whisper** places every line where Whisper hears its words
   (lines it didn't hear are marked ?, a chorus you wrote once comes back as a repeat, and lines it heard that aren't
   in your lyrics are listed with **add** / **add all**: an added line is a lyric line like any other). The song page
   says so too after analysing. Then fix what's left by hand: play the song (Voice only, 0.75×)
   and press Enter as each line starts, or hold Enter while the line is sung to set where it ends too; drag a line's
   edges in the lyrics lane to stretch it; nudge starts and ends by a beat or a tenth of a second, fix words, add
   repeated lines. Saving writes the lines as LRC to the brief (an end is a time tag after the last word:
   `[00:12.00] hold the light <00:14.10>`) and analyses the song again, which aligns every word inside its line.
4. **Story**: Claude drafts what the song is about and how its arc could carry a video, from the lyrics, the song map
   and your notes; edit it, it's yours. (Skip it and the director writes it first.)
5. **Direct**: set a time budget (e.g. 90 min), effort and model, optionally a note for this run, and start. The
   director works alone; the page shows its live log, the clock and the cost. **Stop** wraps it up cleanly. Or direct
   it *with* Claude: open Claude Code in this folder and type `/musicvideo <project>`.
6. **Watch & review**: each version with the song's lanes under the video. Press **N** for a note at that moment.
   Then **Direct → review round** (or `/musicvideo <project>`): the notes go to the director with the bar, section and
   lyric at each moment; the next version appears as `v2`, `v3`, ... (`final/music-video_vN.mp4`).

## Commands (`.venv\Scripts\python.exe tools\studio.py ...`, or `.venv/bin/python` on Linux/macOS)
| command | what |
|---|---|
| `new <name> [--song file.mp3]` · `list` | create / list projects |
| `ingest <p> [--force] [--cpu] [--fallback]` | the song → the song folder (progress in `cache/status.json`) |
| `song <p>` · `curves <p> [--fps N]` | remake the song folder · remake the per-frame files for another fps |
| `story <p> [--model M]` | Claude drafts `song/song-story.md` (headless, read-only) |
| `align-lyrics <p> [--in lines.json]` | the lyric lines placed where Whisper hears them (`cache/lyrics/aligned.json`; the timing editor's button) |
| `direct <p> --budget 90m [--effort high] [--model opus] [--seed-notes "..."] [--revise] [--no-nudge] [--dry-run]` | the autonomous director |
| `clock <p>` · `stop <p>` | time left in a running budget · ask a running director to stop cleanly |
| `check <p> [video]` | every check on the final (spec, flashes, text, sync) |
| `deliver <p>` · `versions <p>` | archive `final/music-video.mp4` as the next version (+ checks) · list versions |
| `permtest <p>` | check the director's sandbox with the real Claude Code (a few cents with Haiku) |
| `run <p> <tool.py> [args]` | run any tool inside a project (`eyes.py`, `songq.py`, `compose.py`, `web-engine/render.py`, `blender/run.py`, `checks.py`, ...) |
| `serve [--port 8765]` · `doctor` · `benchmark [--quick]` · `smoke` | web app · install check · render speeds · the end-to-end smoke test |
The tools are documented in **`tools/README.md`** (with `tools/web-engine/README.md` and `tools/blender/README.md`).

## How it works
- **The song folder** (`projects/<p>/song/`): `README.md` (how to read it), `song-map.md` (at a glance, sections,
  moments, every bar), `lyrics.md`, `song-story.md`, `overview.png` + `pages/`, `song-map.json`, `envelopes.json`
  (10 Hz), `report.json` (models, warnings, confidence, and a `signals` list), `stems/`, and new here:
  `frames.json` (one value per video frame: loudness, fast level, the four stems, five spectral bands, transient
  strength, brightness, stereo width, the sung note and bass note; plus bar / beat / phases / section per frame) and
  `hits.json` (beats, downbeats, kick, snare, hats, crash, bass, other-stem and vocal onsets, words, lines, chords,
  sections, moments: each with its time, frame and strength). One timing rule everywhere: an event at time t is in
  frame `round(t × fps)`; a frame's curve value is the mean over its own time slot.
- **The toolkit** (`tools/`): the **web engine** (HTML / Canvas / SVG / three.js / anime.js scenes rendered frame by
  frame in headless Chromium, deterministic, parallel, resumable, GPU or software WebGL), a **Blender** runner (Eevee /
  Cycles / Workbench with the song data), **compose** (frame-exact joins, layers, the song muxed unchanged), the
  **eyes** (contact sheets with bar:beat / section / lyric in the margin, beat sheets, motion-vs-music plots, the sync
  checker) and the **checks** (delivery spec incl. audio identity and A/V offset, photosensitivity, text).
- **The director**: the `/musicvideo` skill (workflow + craft rules + an *anti-defaults* note: no spectrum bars,
  bass-pulsing particles or synthwave grids as a starting point; the concept comes from this song's story and
  structure, with signature moments on its ★★★ moments). `studio.py direct` fills `templates/director-brief.md` and
  runs Claude Code headless (`-p`, stream-json in and out, `--permission-mode dontAsk` with an allow-list, the studio's
  folders write-protected, network commands and web tools denied, no MCP servers), streams its progress to
  `work/director/`, messages it as time runs down, nudges it to keep improving if it finishes early, ends the session
  at the deadline plus a grace period, then archives the cut, runs the checks and appends the run settings (model,
  effort, budget, wall time, cost) to `report.md`.

## What's in git
The studio (app, tools, skill, templates, fonts with licences, tests), `tools/speeds.json`, and each project's small
text files (`project.json`, `report.md`, review notes, the song folder's markdown and `song-map.json`, the director's
markdown notes and scene code). Songs, stems, reference pictures, renders and videos stay local.

## What came from aftermovie-studio, and what changed
| from aftermovie-studio | here |
|---|---|
| the song kit: `separate.py`, `fetch_lyrics.py`, `transcribe_lyrics.py`, `analyze_audio.py`, `align_words.py`, `fix_lyrics.py` + `lyrics_fix`, `song_kit.py`, `am/listen.py`, `am/songdoc.py`, `am/device.py`, parts of `am/common.py` | `tools/` + `tools/mv/`; one song per project in `song/` (stems in `song/stems/`); the README and story skeleton rewritten for a music-video director; **new**: frames.json, hits.json, extra signals with confidence, user lyrics (plain / LRC / word-timed LRC), offline fallbacks for the stems and the beats, a ★★★ *tempo change* moment and tempo changes as section boundaries, a bridge after the last chorus, an honest "not force-aligned" in the song map, a librosa/ffmpeg decoder-agreement check; **fixed**: the constant-grid test let a section at another tempo hide from it (now the tracked beats must agree with the grid) |
| `studio.py` (per-project commands, `AM_PROJECT` → `MV_PROJECT`, `Status`, `run`, `doctor`, the `.venv` hand-over) | the same structure; new commands for the story, the director, delivery and versions; `doctor` moved to `tools/doctor.py` and covers Chromium / WebGL / Blender / Claude Code |
| `app/server.py` and the **The song** page | the server structure (stdlib, 127.0.0.1, one job per project, range requests) and the song viewer kept; the other pages are new (the viewer's lanes also run under the video on the Watch page) |
| QA ideas from `qa.py` / `am/checks.py` | the sync check works on any video without an EDL (drift per section); contact sheets with the song in the margin; reading time + contrast (now on text recorded by the renderers); the audio check became an identity check against the song file |
| `render.py` | NVENC when it works, libx264 otherwise, preview vs final, chunk caching with frame-exact joins (now inside the web engine, keyed by scene version) |
| `setup.ps1`, `requirements.txt`, the `.bat`, `.gitignore`, the fonts | adapted; setup asks before big downloads; licence files added for every font, plus open-licensed fonts with more range |
| the craft rules in `CLAUDE.md` | in the skill and `CLAUDE.md`, minus everything about photos |
Left out: photos, faces, cast, Polarsteps, shot logs, grading, the photo compositor and EDL, `find_joins.py`, song
suggestions, style samples, the stylebook. (Pictures of the people came back in another role: `refs/` shows the
director how they look, so it can draw them; the pictures never appear in the video.)

## Choices made (where the request left room)
- **Python Playwright** drives the browser: no Node needed; Chrome or Edge (always on Windows 11) can be used directly.
- **Your notes are inspiration, not a spec**: the brief tells the director that whatever you wrote is a starting
  point to go further from (the less you wrote, the more is up to it), that a bold idea beats a safe one, and that of
  your notes only "things to avoid" is a hard rule (the delivery spec and the craft rules still hold). Reference
  pictures fix who is who, never the style.
- **Reference pictures are tidied on upload**: turned upright (the phone's EXIF rotation applied), at most 1600 px on
  the long side (Claude sees images at ~1568 px anyway), metadata such as GPS dropped; JPEG, or PNG when transparent.
  HEIC isn't read (save it as JPEG); 24 pictures at most. The director writes `work/cast.md` from them once.
- **Frames are evaluated at the end of their time slot**, so a downbeat that lands anywhere in a frame's slot shows in
  that frame (it was one frame late half of the time otherwise; found with the sync check on the test song).
- **JPEG q98 capture** for opaque web passes: Chrome's PNG encoder took ~0.5 s per busy 1080p frame, JPEG ~0.08 s; the
  difference vanishes in the H.264 encode. Transparent passes and `--lossless` use PNG.
- **Text checks need to know where text is**: the web runtime records DOM text marked `data-mv-text` (its glyphs' box)
  and canvas/WebGL text passed to `MV.text()`; roles (`lyric`, `lyric-word`, `title`, `label`, `deco`, ...) decide
  which rules apply. Deliberate black or frozen stretches are declared (`MV.intent`).
- **Offline fallbacks** keep ingest working without the model downloads, and say so (report.json, the song map, the
  web app): estimated stems (HPSS + bands + stereo centre) and a librosa beat tracker with a moving tempo. Analysing
  again tries the real stem model again while the stems are estimates.
- **The voice follows the lyric lines**: inside a line, the vocal stem's level counts as the voice even where it has no
  pitch (rap, spoken words); outside the lines only pitched singing does, since the level alone could be other
  instruments bleeding into the vocal stem. So lyric lines in the wrong place also hide the voice: fix the timing first.
- **Your own lyrics are placed with Whisper**: when they have no times (plain text), analysing runs Whisper on the
  vocal stem (once per song, cached) and matches your words to the words it heard, in order and forgiving
  mishearings (`tools/align_lyrics.py`): each line starts where its first words were heard. Before, the lines were
  only spread over the singing by syllable count, and the word aligner could correct them by about a second at most.
  Whisper's word times are good to ~0.2 s; the forced aligner then refines each word inside its line. Without Whisper
  (no download) it falls back to that estimate and says so.
- **Lyric lines you set by hand in the web app are used as they are** (project.json `lyrics_synced` + `lyrics_set`):
  no offset search, unlike a pasted or downloaded LRC. Lines the editor saved but you never set (their time was a
  guess) are placed with Whisper again on every analysis. Changing the lyrics on the brief page drops both marks.
- **Whisper getting stuck** (on music it can repeat a phrase many times, stamped at one moment) is filtered out: a word
  heard again at the same moment is dropped, and nothing Whisper adds (a repeat, a line to add) may sit on top of a
  line that is already placed.
- **Extra signals** only where they are measurements (spectral bands, transients, stereo width, brightness), each
  with its confidence in report.json. No mood / genre tags (aftermovie-studio found them too often wrong).
- **The runner nudges** a director that delivers early to use the time left (`--no-nudge` turns it off), and reminds
  one that hasn't delivered.
- **The director's sandbox** is tested, not assumed: `studio.py permtest` (a relative deny rule doesn't match in
  Claude Code 2.1; the absolute `//path` form does).

## Known limitations
- Tested here on Linux without a GPU: Windows, NVENC, CUDA, Eevee on a real GPU and GPU WebGL are written carefully
  but verified only by `VERIFY_LOCAL.md` on your PC. On Windows the permission rules use the `//c/...` path form from
  Claude Code's documentation: run `permtest`.
- Without the model downloads (Demucs, beat_this, the MMS aligner) the song folder is rough; the fallbacks are honest
  about it but are no substitute.
- Software WebGL (no GPU) is slow for heavy three.js; the benchmark says how slow so the director can plan.
- The photosensitivity check approximates the broadcast guidelines; it is not a certified Harding test.
- The text checks only see text the renderers recorded or that is declared in `work/text.json`.
- Interactive `/musicvideo` sessions use your own Claude Code permission settings, so expect the usual prompts for
  Bash and edits (there is no project `.claude/settings.json`; this build session wasn't allowed to write one). The
  autonomous runner passes its own scoped permissions and asks nothing.
- One song per project; the whole song, always.
- QA can't hear: phrase-level musical feel needs a human ear (the director's report lists the timestamps).

## Ideas for next
- A **storyboard view** in the web app: the director's style frames and section plan as cards before the full render.
- **Style-frame approval** as an optional checkpoint in autonomous runs (the director pauses for one decision).
- **Beat-grid editing** in the song viewer (drag bar 0, fix a downbeat) written to project.json like `lyrics_fix`.
- A **choreography helper**: rigged 2D / 3D figures driven by the beat grid for "a lot of dance".
- **Section re-render** from a note: render only the bars a note touches and splice them in frame-exact.
- Loudness-matched **share copies** (720p, vertical crops) next to the master.
