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
A pipeline segment's video keeps its eyes next to it: `work/segments/<id>/eyes/<name>/` (a pitch's proof:
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
so the director's tokens go into the video. In autonomous runs the runner does this for every new
`work/previews/*.mp4` and the final by itself (a ready final first).

`--pictures` adds stills to open with the video's (the pipeline's language gate passes the style frames).

## pipeline.py: the pipeline's studio side (`studio.py pipeline <sub> <p>`)
The planner writes **`work/plan.json`**; the studio checks it, renders each segment over its own frame range,
assembles previews and the final from it, and gates the visual language with the critic. Every command prints a short
summary (it comes back to a planner as tool output).
| command | what |
|---|---|
| `validate <p>` | checks `work/plan.json`: exit 1 with errors that say what to change (the numbers to set), plus warnings |
| `status <p>` | the plan; per segment whether its preview / final render is current; stages, sessions, gates, assembles and costs from `work/pipeline/state.json`; the final's estimated render time here; who holds the render queue |
| `render <p> [--preview\|--final] [--only s1,s3]` | each segment over its own frames: web `render.py <scene> --frames a:b --out work/segments/<id>/<id>_<q>.mp4`; Blender `run.py` (frames in `<id>_<q>_frames/`) + `compose.py encode`; overlays as transparent passes `work/segments/<id>/<id>_<q>/` (PNG with alpha). A render that is still current isn't started at all; the logs are in `work/segments/<id>/render_<q>.log`, the times in `work/pipeline/renders.json` |
| `assemble <p> [--preview\|--final] [--no-critic] [--critic-model M] [--no-look] [--no-check]` | render what's needed, `compose.py concat` the segments, `layer` the overlays, `mux` the song → `work/previews/preview_N.mp4` (half size) or `final/music-video.mp4`; then `eyes.py look`, for a final the checks, then the critic on the whole film (the arc, the joins, the language held, the ending; its report is printed). A preview gets a labelled slate for a segment that isn't built or failed; the final refuses |
| `gate <p> [--model M] [--effort E] [--max-usd 2]` | the language gate: validate, the hero segment's preview (from the cache when unchanged), then the critic on it plus the style frames: "is this language strong and specific enough to hand to builders, and bold enough?" (recorded in `state.json` `gates`) |
| `build <p> [--only ids] [--builders 3] [--builder-model M] [--builder-effort high] [--budget 30m] [--critic-model M]` | builder agents (an interactive planner's; the autonomous runner does the same itself): one headless Claude Code session per segment (the builder segments that aren't built, or `--only` those, or the tasks in `work/pipeline/tasks.json`, moved aside once they go out), at most `--builders` at once, each with `templates/build-brief.md` (the concept, the rules, its segment and its neighbours' joins, the lib's files, 2-3 style frames, the song over its range as text, the craft rules; no song-folder reading), writing only in `work/scenes/<id>/` and `work/segments/<id>/` (deny rules for the rest), rendering through the render queue; then the critic on its segment preview and one revision. Blocks until they're done (or the budget, or `studio.py stop`), prints one line per segment (state, the critic's scores, requests for the lib, cost). Sessions in `work/pipeline/sessions/b<round>-<id>/` |

How long they take, for a planner deciding what to run in the background: `validate` and `status` seconds; `render
--only <id>` a segment's render (it may first wait for the render queue); `gate` the hero's render (usually from the
cache) plus a critique, 2-5 min; `build` blocks for the builders' whole time (10-40 min: always in the background);
`assemble --preview` what changed plus the eyes and a critique, 3-6 min; `assemble --final` every segment at full size
(`status` prints the estimate) plus the checks and a critique. The skill (`.claude/skills/musicvideo/SKILL.md`, "The
pipeline (every run)") has the stage-by-stage table.

```json
{"stage": "planning|planned|final", "hero": "s2", "lib": "work/scenes/lib/", "style_frames": ["work/style-frames/a.png"],
 "rules": "what every segment must keep (one paragraph)",
 "segments": [{"id": "s1", "name": "intro: the lantern wakes", "from": 0.0, "to": 24.24, "renderer": "web",
   "scene": "work/scenes/s1/scene.js", "owner": "builder", "brief": "what happens, how bold, the lyric plan",
   "enter": "state at its start", "exit": "state at its end", "moments": ["12:0 the light catches"], "status": "todo",
   "render_args": ["--engine", "workbench"]}],
 "overlays": [{"id": "type", "scene": "work/scenes/type/type.html", "from": 0, "to": 65.42, "transparent": true,
   "mode": "normal", "opacity": 1, "owner": "planner", "brief": "..."}]}
```
The rules `validate` holds: segments in song order, following on without gaps or overlaps, the first from 0, the last
to the song's end (boundaries become frames: `round(t × fps)`; a cut that isn't within a frame of a downbeat is a
warning); ids `[a-z0-9_-]+`, unique across segments and overlays (`lib` is the lib's); each scene in its **own folder**
`work/scenes/<id>/` (the render cache follows a scene's folder and its imports, and a builder writes only there), a
`web` scene `.js/.mjs/.html`, a `blender` scene `.py`; the hero (the planner's own first draft) must exist once the
plan is `planned`, every scene once it is `final` or its status is `drafted`/`built`; style frames exist; `render_args`
(e.g. `--engine workbench`, `--samples 64`, `--seed 2`) can't set the range, size, output or transparency. Overlays
(`from`/`to` default to the whole song, `transparent` true, `mode` normal|screen|add|multiply|lighten|darken|difference,
`opacity` 0-1) are layered over the joined segments in order. A segment importing another segment's folder is a
warning: share code through the lib.

`work/plan.json` is also the live preview's timeline (below): the user plays its segments in their slots, with the
song, while the pipeline works.

**work/pipeline/tasks.json** (the planner's fix tasks for builders, stage 4): `[{"segment": "s3", "what": "the note, and what you want"}]`;
each builder gets the task, its segment's last critique and its scene as it is.

**work/pipeline/state.json** (live; the web app polls it; `update_state` re-reads and writes it atomically, under a
lock file next to it, `state.json.lock`, since the runner, `pipeline build` and a planner's own commands all write it): `run`,
`mode` (pipeline | solo | interactive), `state`, `stage` (plan | gate | build | assemble | revise | final | solo | done),
`stage_started`, `round` (the assembles so far), `build_round`, `stages` [{stage, started, ended, round}], `started`,
`deadline`, `budget_s`, `plan_by`, `build_by`, `final_by` (when the studio starts the final at the latest),
`final_est`, `sessions` [{name, role (planner | builder), segment, round, model, model_used, effort, state, cost_usd,
turns, log (its events.jsonl), status, brief, critique, error, task, last (its last lines)}], `segments` {id: {kind,
owner, status, from, to, renderer, scene, exists, builder, builder_state, critique, error}}, `gates`, `assembles`
(written by `gate` and `assemble`), `critiques` (this run's: video, scores, file, cost_usd, kind gate | builder |
assemble | final, who), `messages` [{t, to, why}], `costs` {planner, builders, critic, total}, `settings`. A new
autonomous run starts a fresh one (the last goes to `work/pipeline/history/`). An interactive planner's `gate`,
`assemble` and `build` (not inside an autonomous run) make it theirs: mode `interactive`, `interactive_started`, the
stage, state running, then done | failed | stopped; an autonomous run's state goes to
`work/pipeline/history/state_run-<n>.json` first (GET pipeline returns it as `run_view`, so the page still shows that
run). Inside an autonomous run (its `current.json` written in the last 3 min) these commands leave the state alone.

**The clock** (`pipeline.stage_times`, one rule for the runner, the planner's brief, `--mode auto` and the Direct page's
hint): the plan stage ends at 30% of the budget; the gate takes 5 min; the builders end at 62% of the budget, at least
10 min after the gate, and never later than the preview assemble plus 5 min before the final; the final starts at the
latest its estimate × 1.3 + 8 min before the end. The pipeline fits a budget when a builder round of 10 min fits
(`pipeline_from(estimate)`: the smallest such budget). `estimate_final`: each item's preview seconds per frame × what a
final frame costs against a preview frame for its renderer on this machine (`tools/speeds.json`: the 1080p row over
the 540p preview row; the web engine's parallel row; ×2.5 without a benchmark), else its last final render, else
speeds.json; `estimate_rough` (no plan yet): the whole song with the web engine plus one overlay pass.
The web app's Direct page shows it (`GET /api/p/<p>/pipeline`: the state with the URLs of the files it names, the
segments from plan.json; a session's full log from `GET /api/p/<p>/session-log?log=<its events.jsonl>`): the stage
bar, a card per session, the critic's reports at the gates, the previews and the costs by role, for an autonomous
run, and in a "Your pipeline" box for an interactive planner's `gate`, `build` and `assemble` (with a Stop for
running builders).

**The render queue** (`mv/renderlock.py`): one video render at a time on this machine. `web-engine/render.py` and
`blender/run.py` take `<studio>/cache/render.lock` (O_CREAT|O_EXCL: pid, who, what, started) before a video render (not
`--still`); a waiting render prints `waiting for the render queue: <who> (<what>) since <time>` (at once, then every
~30 s) and `render queue: my turn after N s`. A lock whose process is gone (a render killed with its tree), that was
held for 6 h (a hung render), or that shows no sign of life for 10 min (the holder touches it every minute; on Windows
a dead render's pid can soon belong to another process) is taken over. `MV_RENDER_WHO` names the render
(the pipeline sets `<who>: <id> <quality>`), `MV_RENDER_LOCK=0` turns the queue off, `MV_RENDER_LOCK_FILE` moves it
(the tests do).

## Live preview: the scenes in the browser, with the song (`mv/live.py`, the web app's Watch page "▶ live")
The user is the only one who can hear. The live view plays the project's scenes in their browser with the song, no
render, while the director (or the planner and its builders) works, and their notes from there reach a running
director within seconds. The pieces:
- **The runtime in live mode** (`web-engine/runtime/mv.js`, `web-engine/README.md` "Live preview"): the renderer's own
  page on a clock the web app owns; frames drop when a scene is slower than real time, the timing stays true; stateful
  scenes catch up as a render pre-rolls (one too slow to keep time skips frames, and says its state is then only close
  to the render's); a pause or a play never restarts one; a live frame is the renderer's frame, pixel for pixel.
- **A live server per project** (`mv/live.py` `LiveServer`, started by the web app on the first `GET
  /api/p/<p>/live`, kept for its life): 127.0.0.1, its own port, GET only, `no-store` (an edited scene shows on
  reload), exactly the renderer's URL space (`Router.file_for`: `/_mv/runtime`, `/_mv/vendor`, `/_mv/fonts`,
  `/_mv/song`, the project at `/`) plus the live shell `/_mv/live.html?scene=<path from the project>&scale=0.5&transparent=0|1&seed=1`.
  Nothing outside the project, the runtime, the vendored libraries and the fonts; `/_mv/abs/` only under the studio's
  `tools/`, `tests/` and `assets/` (the examples, the tests' scenes), never another project, the studio's `.git`, cache
  or private notes; `..`, a `\\host\share` path (checked by name before anything is opened) and another Host header are
  refused. Every response carries a Content-Security-Policy that keeps the page off the network, as a render is (a scene
  that imports from the internet fails live too, not only in its render), and lets only a page on 127.0.0.1 / localhost
  frame it. A separate origin, so the renderer's URLs work as they are, with no rewriting.
- **The timelines** (`live.state`): `work/live.json` when there is one (the director's override: `web-engine/README.md`
  has its format), then `work/plan.json` (each segment in its slot: a web scene live, a Blender one as its rendered
  preview `work/segments/<id>/<id>_preview.mp4` or a labelled slate; the overlays on top with their blend mode and
  opacity), then the newest renders in `work/renders/` (from their `.mv.json`: a web pass's scene live over its range,
  any other opaque pass as its video, the newest wins where they overlap, transparent passes on top; compose's
  concat / layer / mux outputs left out), then each pitch's draft plan (`work/pitches/round-K/pN/plan.json`). Every scene
  (`work/scenes/**` but the lib, a pitch's scenes, any scene a timeline names) also plays alone.
- **Live notes**: a note made in the live view (N) is `{"version": "live", "t", "text", "scene" (what was on top),
  "timeline" (its source: live.json, plan.json, renders, pitch:round-K/pN or scene:<path>)}` in `review/notes.json`. During an autonomous run the runner (`director.py` `LiveNotes`) looks every ~5 s
  for live notes made since the run started, not done and not forwarded, and sends them to the director (solo) or the
  planner as one message with the song context (time, bar:beat, section, the sung line, the plan's segment there, the
  scene seen) and "treat it like a review note; mark it done with a one-line fix" (`studio.py note-done <p> <id>
  "<fix>"`); then marks them `"forwarded_run": n`.
  A busy planner gets them in its running turn; an idle one at once while it plans, revises or waits for builders (the
  assemble then waits for its turn), and otherwise in the next assemble report, which lists the run's live notes again
  with what was marked. While the studio gates or assembles they wait for that report (a busy planner too: a fix then
  would race the renders), and in the final stage for the review round. Open live notes also go into the next run's
  brief (a review round's with the other notes; a first run's, e.g. after a stopped one, as notes on the scenes as they
  were). `review/notes.json` is written through `live.update_notes` (a lock file, a re-read, the change by id, an atomic
  replace): the web app, the runner and the director's `studio.py note-done` all write it.

## checks.py (`studio.py check <p>` runs `all` on final/music-video.mp4)
| check | what |
|---|---|
| `spec VIDEO` | MP4, H.264 yuv420p, AAC, the delivery size and a constant fps; decodes to the end with no errors; frame count = the song's length to the frame; both streams start at 0; the audio is the song file's (lag 0, level ±0.1 dB, same length); no undeclared black or frozen stretches (declare with `MV.intent(...)` or `work/intent.json`) |
| `flash VIDEO` | photosensitivity after ITU-R BT.1702 / Ofcom / WCAG 2.x: general and red flashes; FAIL over 3 flashes/s on > 25% of the screen, WARN on > 10% or 2-3/s sustained > 5 s (an approximation, not a Harding certificate) |
| `text VIDEO` | reading time (0.8 s + 55 ms/char, 1.4-3 s), contrast ≥ 3:1, inside title-safe, never clipped, ≥ 34 px at 1080p, lyric text on (or just before) its sung word |
| `all VIDEO` | the three + `eyes.py sync`; `work/checks/report.md`; exit 1 on any FAIL |

## A typical pipeline (one way, not the way)
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

## The studio around it
`studio.py doctor` (install check; writes `installed.md`), `benchmark` (speeds.json), `models.py status|fetch`,
`director.py` (`studio.py story | direct | clock | stop | deliver | versions | permtest`: the solo runner and the
pipeline's stages, `PipelineRun`), `critic.py` (`studio.py critique`),
`pipeline.py` (`studio.py pipeline`),
`studio.py serve` (web app).
Shared code: `mv/session.py` (one long stream-json Claude Code session: start, send, events into a status, turn
ends, stop with grace, the process tree killed; busy from a message to an idle session until its `result`, messages
sent while busy join the running turn: verified on Claude Code 2.1.311), `mv/common.py` (paths, project, fonts,
ffmpeg, encoders), `mv/claude.py` (the claude command and one
headless print-mode call: the story step, the critic; `kill_tree` stops a process with what it started,
`pid_alive` is Windows-safe), `mv/renderlock.py` (the render queue), `mv/device.py` (GPU or CPU, one model at a time),
`mv/listen.py` (the song kit's ears), `mv/songdoc.py` (its writers), `mv/curves.py` (frame curves, hits), `mv/web.py`
(the web engine's driver), `mv/live.py` (the live preview: the live server, the timelines, the notes file).
