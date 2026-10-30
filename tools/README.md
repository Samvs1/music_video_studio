# Music Video Studio tools

Ready-made building blocks for making a music video in code. Using them is optional: you may build your own (keep
them in the project's `work/`). Every tool runs inside a project and takes paths relative to the project folder:

```
<studio>/.venv/bin/python <studio>/tools/studio.py run <project> <tool> [args]        (Windows: .venv\Scripts\python.exe)
```
From inside a project folder that is `../../.venv/bin/python ../../tools/studio.py run <project> ...`.

**What is installed on this machine:** `tools/installed.md` (written by `studio.py doctor`, refreshed at the start of
every director run). **How fast each renderer is here:** `tools/speeds.json` (`studio.py benchmark`); read
`budget.minutes_for_3_minutes_at_30fps` and scale by your song's length and fps before choosing heavy 3D.

## The song (read `song/README.md` first)
| tool | what |
|---|---|
| `songq.py summary` / `at 83.2` / `t 17 2.5` / `section "chorus 2"` / `moments --stars 3` / `bars 16 24` / `hits kick 30 40` / `lines` | small questions answered from the song folder, so you don't re-read the big files |
| `mv/song.py` (`from mv.song import Song`) | the song folder as a Python object: `t(bar, beat)`, `pos(t)`, `at(t)`, `cue(t)`, `snap`, `curve(name)`, `hits(kind)`, `pulse(kind, t)`, `since`, `section`, `moments`; standard library only (works inside Blender) |
| `song/frames.json`, `song/hits.json` | one value per video frame (curves + grid) and every hit with its frame; the renderers load them for you |

## Renderers
- **web-engine** (`web-engine/README.md`): HTML / JS scenes (Canvas 2D, SVG, DOM + CSS, three.js / WebGL, anime.js, d3)
  rendered frame by frame in headless Chromium, deterministic (the runtime owns time and chance), chunked, parallel,
  resumable; GPU when there is one, SwiftShader otherwise. Transparent passes for layering. Records every text on
  screen for the checks.
  `web-engine/render.py <scene.js|.html> --out work/renders/x.mp4 [--section NAME | --from s --to s] [--preview] [--transparent] [--still t,t]`
  (with `--still`, `--out` is a folder: one `<scene>_<t>.png` per time)
- **blender** (`blender/README.md`): a Python scene script (`build(mv)` + `frame(mv, f)`) rendered headless with the
  song data; Eevee (GPU), Cycles (GPU or CPU) or Workbench; PNG frames resume by themselves.
  `blender/run.py <scene.py> --out work/renders/stage [--engine auto|eevee|cycles|workbench] [--section NAME] [--transparent] [--still t]`

Both number frames by song frame and write `<out>.mv.json` (frame range, engine, text on screen). A frame shows
whatever happens in its time slot (`frame = round(t × fps)`): scenes get the slot's end as their time.

## compose.py (FFmpeg)
| command | what |
|---|---|
| `concat a.mp4 b/ c.mp4 -o picture.mp4` | join segments in song order, frame-exact (stream copy when the chunks match, else one encode); checks the frame count and that the sidecar ranges follow on |
| `layer BASE --over OVER[:mode[:opacity]] ... -o out.mp4` | composite passes: OVER = PNG folder with alpha / ProRes 4444 `.mov` / mp4; mode normal (alpha), screen, add, multiply, lighten, darken, difference; BASE may be `color:#101010`; each pass sits at its own song-frame range |
| `encode frames/ -o x.mp4` | a PNG folder to H.264 |
| `mux picture.mp4 [-o final/music-video.mp4]` | add the song, unchanged: AAC copied, anything else straight to AAC 320k (no filters, no offset, never cut); the video is copied when already H.264 yuv420p at the delivery size and fps |

H.264 with NVENC when the GPU encoder works, libx264 otherwise (`--preview` = fast settings). Sidecars follow the
segments into the final, so the checks know where every text was.

## eyes.py: you can't watch the video, so look at it
| command | what |
|---|---|
| `look VIDEO` | everything below in `work/eyes/<name>/` + `index.md`: start here |
| `sheet VIDEO [--every 3 \| --section NAME \| --moments 3 \| --range a b --n 24 \| --times a,b]` | contact sheet; each tile's margin: timecode, frame, bar:beat, section, chord, the lyric with the sung word in [brackets], the hits landing on that frame |
| `strip VIDEO --at 61.2 [--n 8]` / `--range a b` | consecutive frames around a moment (does the hit land on its frame?) |
| `beats VIDEO --section NAME` / `--bars 16 24` | for every downbeat: the frame before and the frame on it, side by side |
| `motion VIDEO` | the video's motion, brightness and saturation plotted against loudness and the stems; per-section numbers |
| `sync VIDEO` | hard visual changes vs the nearest musical event (beat, 8th, sung word, kick, snare, stop, 16th, onset): in sync = within one frame; drift (median offset) per section; the misses listed |

A preview works (`--preview` renders, any size); a section render works when its `.mv.json` says where it sits.
A plan segment's video keeps its eyes next to it: `work/segments/<id>/eyes/<name>/` (a pitch's proof:
`work/pitches/<round>/<pN>/eyes/<name>/`).

## critic.py: fresh eyes on a cheaper model (`studio.py critique <p> <video>`)
`critique <p> work/previews/preview_3.mp4 [--model sonnet] [--effort medium] [--focus "the chorus 2 type"] [--concept PATH] [--out DIR] [--max-usd 2] [--no-look] [--pictures a.png,b.png]`
(or `critique <p> v2`: a delivered version, also kept as `final/critic_v2.md`). It runs `eyes.py look` (unless the
eyes are newer than the video) and strips at up to three of the strongest ★★★ moments, then one headless Claude call
(read-only tools) opens those pictures and judges them against `work/concept.md` (or `--concept`), the song's
essentials (`work/song-notes.md`, else `work/critic/song-brief.md`), the user's notes, the anti-defaults and the craft
rules: ambition first, then craft. Not `report.md` or `work/log.md`: fresh eyes. 2-4 minutes. Output:
`work/critic/<video>_NN.md` (≤ 60 lines: verdict, scores, the worst problems with times and a direction to push, what
works, what the pictures can't tell) + `.json` (`verdict`, `scores` {ambition, song_fit, craft, readability} 1-10,
`problems` [{severity, t0, t1, where, what, push}], `keep`, `safe`, `anti_defaults`, `fixed_since_last`, plus the
video's size + mtime, model, cost) and a line in `work/critic/log.jsonl`; the last printed line is
`critic: ambition 7 · song fit 6 · craft 8 · readability 9 · $0.12 · work/critic/preview_3_02.md` (the line before it
lists the pictures it looked at). Only pictures made after the video count (an eyes folder is per file name). When it
can't run (no pictures of this cut, no answer, or Claude Code's own error text such as a usage limit or a login
instead of a critique) it writes no critique, logs the error and its cost, prints `critic: couldn't run: <why>
($0.0310 spent)` and exits 1. Read the report first; open only the pictures it points at: the critic does the looking,
so the director's tokens go into the video. In autonomous runs the studio's critic looks at the newest new
`work/previews/*.mp4` (up to a per-run cap: it says when that is used up) and the final by itself, never twice at one
cut (`plan assemble`'s critique counts): don't critique a cut twice.

`--pictures` adds stills to open with the video's (e.g. the style frames).

## plan.py: the plan tools, optional (`studio.py plan <sub> <p>`)
An optional toolbox around an optional **`work/plan.json`**: a scene list for the whole song. The director decides
whether a plan helps its video (several scenes in sequence, web and Blender passes, a part an agent builds); when it
does, these render each segment over its own frame range with a cache and join, layer and mux the film in one
command, with the eyes, the checks and the critic. Without a plan, render and compose by hand (below). Every command
prints a short summary (it comes back to the director as tool output).
| command | what |
|---|---|
| `validate <p>` | checks `work/plan.json`: exit 1 with errors that say what to change (the numbers to set), plus warnings |
| `status <p>` | the plan; per item whether its preview / final render is current; the last previews with the critic's scores; the final's estimated render time here; who holds the render queue |
| `render <p> [--preview\|--final] [--only s1,s3]` | each segment over its own frames: web `render.py <scene> --frames a:b --out work/segments/<id>/<id>_<q>.mp4`; Blender `run.py` (frames in `<id>_<q>_frames/`) + `compose.py encode`; overlays as transparent passes `work/segments/<id>/<id>_<q>/` (PNG with alpha). A render that is still current isn't started at all; the logs are in `work/segments/<id>/render_<q>.log`, the times in `work/plan/renders.json` (written under a lock: an agent may render from a process of its own) |
| `assemble <p> [--preview\|--final] [--no-critic] [--critic-model M] [--no-look] [--no-check]` | render what's needed, `compose.py concat` the segments, `layer` the overlays, `mux` the song → `work/previews/preview_N.mp4` (half size) or `final/music-video.mp4`; then `eyes.py look`, the checks (a preview: flash and text; a final: all), then the critic on the whole film (the arc, the joins, whether one look holds, the ending; its report is printed). A preview gets a labelled slate for a segment without a scene (or whose render failed); the final refuses and says which. The cut is marked as being critiqued (`work/critic/pending/`) from the mux on, so an autonomous run's critic doesn't look at it again (it records the critique in the run's view); with `--no-critic` it does look, and its notes come as a message. Logs in `work/plan/assemble/` |

How long they take: `validate` and `status` seconds; `render --only <id>` a segment's render (it may first wait for the
render queue); `assemble --preview` what changed plus the eyes and a critique, 3-6 min; `assemble --final` every
segment at full size (`status` prints the estimate) plus the checks and a critique: run the last two in the background.

```json
{"segments": [{"id": "s1", "name": "intro: the lantern wakes", "from": 0.0, "to": 24.24, "renderer": "web",
               "scene": "work/scenes/s1/scene.js", "render_args": ["--engine", "workbench"]}],
 "overlays": [{"id": "type", "name": "the sung lines", "scene": "work/scenes/type/type.html", "from": 0, "to": 65.42,
               "transparent": true, "mode": "normal", "opacity": 1}]}
```
The rules `validate` holds: segments in song order, following on without gaps or overlaps, the first from 0, the last
to the song's end (boundaries become frames: `round(t × fps)`; a cut that isn't within a frame of a downbeat is a
warning); ids `[a-z0-9_-]+`, unique across segments and overlays (`lib` is the shared code's); each scene in its **own
folder** `work/scenes/<id>/` (the render cache follows a scene's folder and what it imports, so a change in one segment
doesn't render another), a `web` scene `.js/.mjs/.html`, a `blender` scene `.py`; a scene not written yet is no error
(a preview shows a slate); `render_args` (e.g. `--engine workbench`, `--samples 64`, `--seed 2`) can't set the range,
size, output or transparency. Overlays (`from`/`to` default to the whole song, `transparent` true, `mode`
normal|screen|add|multiply|lighten|darken|difference, `opacity` 0-1) are layered over the joined segments in order. A
segment importing another segment's folder is a warning: share code through `work/scenes/lib/` (and `work/kit/`).
Other fields are the director's own notes and are left alone; a plan from an earlier version of the studio (with
`stage`, `hero`, `owner`, `status` or briefs) reads as it is.
**The final's estimate** (`status`): each item's measured preview render time × what a final frame costs against a
preview frame for its renderer on this machine (`tools/speeds.json`: the 1080p row over the 540p preview row; the web
engine's parallel row; ×2.5 without a benchmark), else its last final render, else speeds.json; plus the join, the
layers, the mux and the checks.

`work/plan.json` is also the live preview's timeline (below): the user plays its segments in their slots, with the
song, while the director works.

## Agents, the render queue, the pitch round (what a director needs; the studio's side: `INTERNALS.md`)
- **Agents** are the director's choice (the skill's "Agents (optional)"): the Agent tool in the background
  (`run_in_background: true`), each with its `model`; `TaskStop` stops one (`SendMessage` isn't available in an
  autonomous run); `--ultracode` adds `Workflow`. They share the director's sandbox, and their cost is part of the run's
  (report.md lists it per model).
- **The render queue** (`mv/renderlock.py`): one video render at a time on this machine, the director's and its agents'
  alike (`--still` doesn't wait). `waiting for the render queue: <who> (<what>) since <time>` means wait: run renders in
  the background (a foreground command's timeout cuts the wait short, and the render goes back to the end of the queue).
- **The pitch round** (`studio.py pitch <p>`, `tools/pitch.py`) is the user's optional step before a run; a director
  never starts one. A picked pitch reaches the director through its brief (`studio.py direct <p> --pitch round-K/pN`),
  or interactively through `studio.py pitch-pick <p> round-K/pN`: it puts the pitch in place (its pitch.md as
  `work/concept.md`, its scenes, style frames and draft plan; what was there before kept as `*_before_pitch_<time>`)
  and prints what's where, the critic's notes on its proof and the verdict's ideas worth grafting.

## Live preview: the scenes in the browser, with the song (`mv/live.py`, the web app's Watch page "▶ live")
The user is the only one who can hear. The live view plays the project's scenes in their browser with the song, no
render, while the director (and any agents of its) works; a slow scene drops frames but keeps time, and a live frame
is the renderer's frame. What plays (`live.state`): `work/live.json` when there is one (the director's override:
`web-engine/README.md` has its format), then `work/plan.json` (each segment in its slot: a web scene live, a Blender
one as its rendered preview or a labelled slate; the overlays on top), then the newest renders in `work/renders/` (from
their `.mv.json`), then each pitch's draft plan. Every scene also plays alone. The user's notes from there
(`"version": "live"` in `review/notes.json`, with the scene they saw and the timeline) reach a running director as a
message within seconds, with the song context; open ones go into the next run's brief. Mark one done with
`studio.py note-done <p> <id> "<fix>"`, never by editing `review/notes.json` (the web app writes it too).

## checks.py (`studio.py check <p>` runs `all` on final/music-video.mp4)
| check | what |
|---|---|
| `spec VIDEO` | MP4, H.264 yuv420p, AAC, the delivery size and a constant fps; decodes to the end with no errors; frame count = the song's length to the frame; both streams start at 0; the audio is the song file's (lag 0, level ±0.1 dB, same length); no undeclared black or frozen stretches (declare with `MV.intent(...)` or `work/intent.json`) |
| `flash VIDEO` | photosensitivity after ITU-R BT.1702 / Ofcom / WCAG 2.x: general and red flashes; FAIL over 3 flashes/s on > 25% of the screen, WARN on > 10% or 2-3/s sustained > 5 s (an approximation, not a Harding certificate) |
| `text VIDEO` | reading time (0.8 s + 55 ms/char, 1.4-3 s), contrast ≥ 3:1, inside title-safe, never clipped, ≥ 34 px at 1080p, lyric text on (or just before) its sung word |
| `all VIDEO` | the three + `eyes.py sync`; `work/checks/report.md`; exit 1 on any FAIL |

## By hand, without a plan (one way, not the way)
```
web-engine/render.py work/scenes/world.js --out work/renders/world.mp4 --preview            # iterate at half size
eyes.py look work/renders/world.mp4                                                          # look, fix, repeat
blender/run.py work/scenes/stage.py --out work/renders/stage --section "chorus 2"           # a 3D pass for one section
web-engine/render.py work/scenes/type.html --out work/renders/type --transparent            # the type on top
compose.py concat work/renders/world_a.mp4 work/renders/stage work/renders/world_b.mp4 -o work/renders/picture.mp4
compose.py layer work/renders/picture.mp4 --over work/renders/type -o work/renders/layered.mp4
compose.py mux work/renders/layered.mp4                                                       # -> final/music-video.mp4
../../tools/studio.py check <p>
```

## Making the song folder (ingest runs these; you rarely need them)
| step | tool | output |
|---|---|---|
| stems | `separate.py [--force] [--fallback]` (Demucs htdemucs_ft on the GPU, htdemucs on the CPU; offline: an estimate) | `song/stems/*.wav` + `model.txt` |
| synced lyrics | `fetch_lyrics.py "<artist>" "<title>"` (LRCLIB) | `cache/lyrics/search.json` |
| lyrics without LRC | `transcribe_lyrics.py [--language nl] [--force]` (Whisper on the vocal stem, once per song file) | `cache/lyrics/transcribed.json` |
| your plain-text lyrics placed | `align_lyrics.py [--in lines.json]` (your words matched to what Whisper heard; analyze_audio uses the same) | `cache/lyrics/aligned.json` |
| beats, drums, lyric times | `analyze_audio.py [--fallback-beats]` (beat_this; offline: librosa with a moving tempo) | `cache/audio.json` |
| sung word onsets | `align_words.py` (MMS forced alignment on the vocal stem) then `fix_lyrics.py` | words in `cache/audio.json` |
| the song folder | `song_kit.py [--cpu] [--no-tags] [--curves-only --fps N]` | `song/*` |
Lyrics: the user's own (project.json `lyrics` or `song/lyrics.lrc|.txt`; plain text, LRC, or LRC with `<mm:ss.xx>` word
times; a time tag after a line's last word is where the line ends: `[00:12.00] hold the light <00:14.10>`) come first,
then LRCLIB, then Whisper. Plain-text lyrics are placed where Whisper heard their words (`align_lyrics.py`). The web app's lyric timing editor (The song page) writes LRC there with
`lyrics_synced: true` (timed on this very file: no offset search) and `lyrics_set` (the lines set by hand: kept as
they are; Whisper places the others). Repairs that survive re-ingest:
project.json `lyrics_fix` (`held_lines`, `words`, `text`; see `fix_lyrics.py`).

The studio's own side (the runner, the sessions, the pitch round's files, the live server, the module map):
`tools/INTERNALS.md`, for working on the studio.
