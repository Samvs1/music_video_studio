# Music Video Studio

Drop in a song (plus a few notes if you like). The studio writes the song down so an AI can read what it can't hear:
every beat and bar, the sections and big moments, the melody, the chords, each instrument, each sung word, and
per-frame curves to animate with. Then **Claude Code directs an original music video for it on its own**, for a time
budget you set, and delivers `final/music-video.mp4` with a report. Motion graphics only: everything is made in code
(2D, 3D or both), no filmed footage, photos, AI-generated images or video, or downloaded artwork. The whole song,
audio unchanged.

Every run follows **one pipeline**: the top model (at max effort) plans the video, locks its visual language in code
and makes the first draft; **builder agents** build the other parts of the film in parallel; **the critic** (a
cheaper model with fresh eyes) looks at the renders at every gate and reports back in text, ambition first, then
craft. You watch it work on the Direct page (the stages, each agent, the critic's reports, the costs), and you can
**play its scenes live** in your browser with the song while it works, no render (you are the only one who can hear:
a note you leave there reaches the director within seconds). Then you watch the video next to the song's lanes with
the critic's notes beside it, press **N** to leave a note at a moment, and the director makes the next version.
Earlier versions are kept. Short budgets run solo: one session does everything.

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
5. **Direct**: set a time budget (e.g. 90 min), the mode (**auto** runs the pipeline when its stages fit before the
   final render must start, never under 45 minutes, and solo otherwise: how long the final render takes on this
   machine sets the bar; the page says which one your budget gets and why), the model, the critic (Sonnet by default; Haiku is cheaper, Opus
   sharper, or off), the planner's effort (max), how many builders work at once (3), their model (the planner's) and
   effort (high), solo's effort (high), optionally a note for this run, and start. The settings are kept for next
   time. The run view shows the stages (Plan → Gate → Build → Assemble → Revise (round n) → Final) with their
   deadlines, the plan's segments over the song, a card per session (the planner and each builder: state, model,
   effort, cost, last lines, the full log on a click), the critic's report at every gate (scores, the video, the
   report itself), the previews, the costs by role and the planner's live log. A solo run shows its live log and the
   critic's notes as before. While it runs, **▶ play the scenes live** opens the live view (step 6). **Stop** wraps it
   up cleanly. Or direct it *with* Claude: open Claude Code in this folder
   and type `/musicvideo <project>`: your session is the planner, runs the same pipeline commands, and its builders
   and the critic's reports show up on the Direct page too.
6. **Watch & review**: each version with the song's lanes under the video, and the critic's notes on it (scores, the
   worst problems with their times, what works; **Ask the critic** for a fresh look). Press **N** for a note at that moment.
   **▶ live** (the default while there's no video yet) plays the project's scenes as they are now, in your browser with
   the song and its lanes, no render: the plan's segments in their slots (or the director's `work/live.json`, the
   renders, a pitch's draft plan, or one scene alone), a scene that changed reloads by itself within seconds. Pick the
   size (¼, ½, full); each layer says how it keeps up ("24 of 30 fps", "catching up 40%", its error); set a **sound
   delay** for Bluetooth headphones (kept in the browser). A note here (N) is a live note: during a run it goes to the
   director (or the planner) within seconds with the bar, the section and the scene (while the studio assembles, in
   its next report; while the final renders, in the next review round); the director marks it done with its fix under
   it. Notes left while no run is going go into the next run's brief.
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
| `direct <p> --budget 90m [--mode auto\|pipeline\|solo] [--model opus] [--effort E] [--seed-notes "..."] [--revise] [--no-nudge] [--dry-run] [--critic-model sonnet\|haiku\|opus\|off] [--critic-max 6]` | the autonomous run: the pipeline (a planner, builders, the critic at the gates) or solo (one session); auto = the pipeline when its stages fit the budget on this machine (never under 45 min), else solo |
| `direct <p> ... [--planner-model M] [--planner-effort max] [--builders 3] [--builder-model M] [--builder-effort high]` | the pipeline's roles: the planner (default `--model`, effort max), up to N builders at once (default the planner's model, effort high; `--builders 0` = solo) |
| `critique <p> <video \| vN> [--model sonnet] [--effort medium] [--focus "..."] [--max-usd 2] [--pictures a.png,b.png]` | the critic: a cheaper model with fresh eyes looks at a video, reports back in text (`work/critic/`; a version also `final/critic_vN.md`) |
| `pipeline validate\|status <p>` | the pipeline's plan (`work/plan.json`): check it (exit 1 with what to fix) · the segments' renders, stages, sessions, gates, costs, the final's estimated render time, the render queue |
| `pipeline render <p> [--preview\|--final] [--only s1,s3]` · `pipeline assemble <p> [--preview\|--final] [--no-critic]` | each segment over its own frame range (unchanged ones from the cache) · join, layer, mux → `work/previews/preview_N.mp4` or `final/music-video.mp4`, then the eyes, the checks (final) and the critic on the whole film |
| `pipeline gate <p> [--model M]` | the language gate: the critic on the hero segment's preview and the style frames, before any builder starts |
| `pipeline build <p> [--only s1,s3] [--builders 3] [--builder-model M] [--budget 30m]` | builder agents for an interactive planner: one headless session per segment (or the tasks in `work/pipeline/tasks.json`), a critic round each; blocks until they're done, then a summary |
| `clock <p>` · `stop <p>` | time left in a running budget · ask a running director to stop cleanly |
| `note-done <p> <id> ["what changed"] [--undo]` | mark the user's note done with a one-line fix (how a director answers a note: `review/notes.json` is written safely, since the web app writes it too) |
| `check <p> [video]` | every check on the final (spec, flashes, text, sync) |
| `deliver <p> [--critic]` · `versions <p>` | archive `final/music-video.mp4` as the next version (+ checks; `--critic`: + the critic's notes) · list versions |
| `permtest <p> [--builder]` | check the director's (or a pipeline builder's) sandbox with the real Claude Code (a few cents with Haiku) |
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
- **The director**: the `/musicvideo` skill (the pipeline, the workflow + craft rules + an *anti-defaults* note: no
  spectrum bars, bass-pulsing particles or synthwave grids as a starting point; the concept comes from this song's
  story and structure, with signature moments on its ★★★ moments). In solo mode `studio.py direct` fills
  `templates/director-brief.md` and
  runs Claude Code headless (`-p`, stream-json in and out, `--permission-mode dontAsk` with an allow-list, the studio's
  folders write-protected, network commands and web tools denied, no MCP servers), streams its progress to
  `work/director/`, messages it as time runs down, nudges it to keep improving if it finishes early, ends the session
  at the deadline plus a grace period, then archives the cut, runs the checks and appends the run settings (model,
  effort, budget, wall time, cost) to `report.md`.
- **The critic** (`tools/critic.py`, `templates/critic-prompt.md`): looking at frames is image-heavy, so it shouldn't
  be the expensive director doing all of it. The critic is one headless print-mode call on a cheaper model (Sonnet by
  default) with read-only tools: it runs the eyes on a video (plus strips at its strongest ★★★ moments), opens the
  pictures itself, and judges them against the director's `work/concept.md`, the song's essentials, the user's notes,
  the anti-defaults and the craft rules (inlined from the skill), and its previous critique. It never sees
  `report.md` or the director's log: fresh eyes judge the picture, not the account of it. Ambition first (is this a
  video only this song could have? where does it play safe? what would make the boldest version of *this* concept
  land?), then craft; at most 8 problems, each with a time range, what's wrong in the picture, why it matters and a
  direction to push; what works; what the pictures can't tell. It never recommends the safer idea. During an
  autonomous run the runner critiques every new `work/previews/*.mp4` and the final in the background (one at a time;
  a ready final first, then the newest preview; 6 per run at most) and sends each critique to the director as a
  message, with the pictures it looked at; the director doesn't do a full look of its own after a preview (the brief
  and the skill say so), only the few pictures it needs. The early-finish nudge carries its notes on the final
  (ambition first); the archived version keeps its critique as `final/critic_vN.md`. A call that couldn't answer (a
  usage limit, a login, the $2 cap) is no critique: nothing is written but a log line, and its cost still counts.
- **The pipeline's studio side** (`tools/pipeline.py`, `studio.py pipeline ...`): the planner writes `work/plan.json`
  (segments that cover the song on downbeats, each scene in its own folder `work/scenes/<id>/`, a shared lib in
  `work/scenes/lib/`, the hero segment, the style frames, the overlays); the studio checks it (errors a model can act
  on: "a gap before it: s1 ends at 24.24 s (frame 727) and s2 starts at 24.50 s ... Set s2.from to 24.24"), renders
  each segment over its own frame range into `work/segments/<id>/` (web: one `render.py --frames a:b`; Blender:
  `run.py`, then `compose.py encode`; overlays as transparent passes), assembles previews and the final from the plan
  (a segment that isn't built yet is a labelled slate in a preview; the final needs them all), and gates the visual
  language with the critic on the hero preview and the style frames. Python, no tokens; the same functions run
  in-process in the autonomous runner.
- **The pipeline, run by the studio** (`studio.py direct`, the default whenever its stages fit the budget): the same
  shape every run, so cost and structure stay bounded (no workflow or agent tools inside the headless sessions; the
  studio orchestrates):
  ```
  1 PLAN       the planner (top model, effort max): the song, the concept, the lib, style frames, plan.json, the hero
  GATE 1       validate plan.json; the critic on the hero + the style frames -> the planner revises once -> locked
  2 BUILD      builders (one session per segment, effort high, narrow brief, own folders) || the planner's own segments
               each builder: render -> the critic -> one revision -> built (or failed: the planner hears)
  3 ASSEMBLE   the studio: a preview of the whole film, the checks, the critic's film report -> the planner
  4 REVISE     the planner: fixes, or work/pipeline/tasks.json -> builders again -> 3 ... ; or "stage": "final"
  5 FINAL      the studio (also by itself when the time left is what the final needs): render, checks, critic ->
               the planner fixes any FAIL and writes report.md -> the version is archived
  ```
  The briefs (`templates/plan-brief.md`, `templates/build-brief.md`) carry the ambition: the planner sets how bold the
  video is (it commits to the boldest concept it can make well, locks it in code and builds the hero as the bar);
  each builder gets only its part (the concept, the lib, 2-3 style frames, its segment and its neighbours' joins, the
  song over its range as text, the craft rules) and is told to surprise inside the rules. Every way out ends with a
  final when one can be made: a plan that never validates (three tries) or doesn't exist 5 minutes after the plan
  stage → solo for the rest; a plan whose final render leaves no room for the stages (before anything was built: a
  final then would be the hero everywhere) → solo, with the estimate; a builder that crashes, hangs or runs out of
  time → failed (what it rendered stands when it is the scene as it is), the planner is told and may take it over; a
  planner that ends turns without the stage's output → up to three reminders, then the studio moves on; a planner
  whose session ends → the studio assembles the final alone; a final the planner was asked to fix, and the fix
  doesn't come in time (or its session ends) → the final with stand-ins; a broken plan.json at the final → the last
  valid one goes back; a segment still missing or broken at the final → its own last final render, else a
  neighbour's scene over its range. A cut delivered before the early-finish nudge is kept as a version before the
  studio renders the next final over it. Live state in `work/pipeline/state.json` (stages,
  sessions with their model, effort, state, cost and last lines, segments, gates, assembles, critiques, messages,
  costs by role). A review round (`--revise`) starts at stage 4 with the user's notes.
- **The pipeline, run by you** (`/musicvideo`, in ultracode or not): the skill is written around the same pipeline.
  Your Claude Code session is the planner and runs the stages as commands, for each the time it takes and what comes
  back: `pipeline validate`, `pipeline render --only <hero>`, `pipeline gate`, `pipeline build` (in the background:
  it blocks until the builders and their critic rounds are done), `pipeline assemble --preview` (prints the critic's
  report on the whole film), `pipeline assemble --final`, `deliver --critic`. Your `gate`, `assemble` and `build` make
  `work/pipeline/state.json` yours (mode interactive; the last autonomous run's goes to
  `work/pipeline/history/state_run-<n>.json`, and the Direct page keeps showing that run from there), so the page
  shows "Your pipeline" too, with a Stop for builders at work. In ultracode the pipeline *is* the fan-out: segments are built
  only by `pipeline build` (narrow briefs, own folders, the render queue, a critic round, costs counted), and the
  session's own workflows are for its thinking (concept variants, signature-moment variants, style-frame
  alternatives), never for building segments or looking at renders (that's the critic).
- **The Direct page** polls `GET /api/p/<p>/director` (the run's status: its live log, the clock, the costs) and
  `GET /api/p/<p>/pipeline` (`work/pipeline/state.json` with the URLs of what it names: each session's brief, messages
  and segment preview, each critique and video); a session's full log comes from `GET /api/p/<p>/session-log`, which
  summarises the end of its `events.jsonl` the way the live log does. Its Stop also reaches an interactive
  planner's `pipeline build` (the same STOP file as `studio.py stop`).
- **The live preview** (`tools/mv/live.py`, the runtime's live mode in `web-engine/runtime/mv.js`, the Watch page's
  `livePlayer`): the web app starts a small server per project on 127.0.0.1 (its own port, GET only, `no-store`, the
  renderer's exact URL space and nothing outside it) and stacks the scenes' pages from it as iframes, each visible only
  in its slot, over the song's `<audio>`, which is the clock: every frame the page tells each scene where the song is,
  and the scene shows that frame (its own frame clock, the same pixels a render gives), dropping frames when it is
  slower than real time rather than drifting (a stateful scene then skips frames too, and is close to its render, not
  exact; pausing or playing never restarts a simulation). `GET /api/p/<p>/live` lists the timelines (work/live.json, work/plan.json,
  the renders, the pitches' draft plans) and the scenes. The live notes go into `review/notes.json` (`"version":
  "live"`, the scene, the timeline), and the director's runner forwards them every ~5 s (`director.py` `LiveNotes`):
  into the running turn, or at once to an idle planner while it plans, revises or waits for builders; while the studio
  gates or assembles, in its next report (which lists them all again with what was marked); in the final stage they
  wait for the review round; marked `forwarded_run`. The director marks one done with `studio.py note-done`.
- **The render queue** (`tools/mv/renderlock.py`): every video render (`web-engine/render.py`, `blender/run.py`; not
  stills) takes a machine-wide lock first (`cache/render.lock` in the studio folder), so parallel agents queue their
  renders instead of sharing the CPU/GPU and all slowing down; a waiting render prints `waiting for the render queue:
  <who> since <time>`. A lock whose process is gone (a killed render) is taken over at once.

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
- **The critic pushes ambition up, never down**: its prompt says to never recommend a safer or more conventional
  idea, and when something bold isn't landing, to say how to make it land (timing, staging, room, set-up and payoff)
  rather than how to retreat; to push the director's own concept, not swap in its own; and to say what the pictures
  can't tell instead of guessing. Its notes go to the director, who decides ("take what's right, push harder where it
  says you played safe, ignore what's wrong, and log what you took").
- **The critic is Sonnet by default** (`project.json director.critic_model` / the Direct page; Haiku for pennies,
  Opus when it matters; `off`), effort `medium`, with a $2 cap per critique (`--max-budget-usd`; an older Claude Code
  without it runs uncapped and says so). One real Haiku critique of the 65 s test video took 50 s and $0.02.
- **The runner waits for the critic's notes on the final** (up to 4 minutes, when time is left) before it nudges a
  director that finished early, so the nudge carries its notes instead of a generic "look again"; it waits for a
  director's turn to end before deciding anything.
- **A session is busy or idle** (`tools/mv/session.py`, verified on the real CLI, Claude Code 2.1.311): a message
  sent while a turn runs is merged into that turn (three messages, two of them sent during a 20 s Bash command, gave
  one `result` answering all three). So a session is busy from a message to an idle session until the next result,
  the studio decides only when a turn has ended, and a message sent while it works simply joins the work in
  progress. The test stand-in answers the same way.
- **The pipeline's shape** (the user asked for one pipeline every run: the top model plans and drafts, agents build,
  a cheaper model checks and reports back; the orchestrator designed it and pushed back where the plain version
  didn't make sense):
  - **The cheap model checks; it never builds.** The scene code *is* the video, so builders default to the planner's
    model (effort high). The savings come from narrow briefs (small contexts, no song-folder reading, no images but
    2-3 style frames) and from giving all the looking at frames to the critic. `--builder-model sonnet` is an option.
  - **Lock the language before fanning out.** The planner puts the visual language in code (`work/scenes/lib/`) and
    style frames and builds the hero segment itself; gate 1 (the critic on the hero and the style frames) comes before
    any builder starts; builders can't change the lib (they write requests). Five builders guessing at a language
    give five films.
  - **Max effort where judgment lives**: the planner max, builders high, the critic medium.
  - **No workflow or agent tools inside the headless sessions**: the studio orchestrates, so cost and structure stay
    bounded and visible (each session's cost is on its card).
  - **Small budgets collapse to solo** (below).
  - **The ambition lives in the planner's briefs**: the planner sets how bold the video is (it commits to the boldest
    concept it can make well and builds the hero as the bar), each builder is told to surprise inside the rules, the
    critic only ever pushes up. Fan-out is there to make the video bolder, not to average it into a safe consensus.
- **One render at a time per machine**: parallel agents speed up thinking and writing code, not rendering; two
  renders at once only share the same CPU or GPU and both take twice as long (or run out of GPU memory). So every
  video render waits its turn in the render queue; the final render is the floor no fan-out lowers.
  `MV_RENDER_LOCK=0` turns it off; `MV_RENDER_WHO` names a render while it holds the queue.
- **When the pipeline, when solo** (`--mode auto`): the pipeline when its stages fit, by one rule
  (`pipeline.stage_times`, shared by the runner, the planner's brief, `--mode auto` and the Direct page's hint): the
  plan stage (30% of the budget), the gate (5 min), a builder round of at least 10 min, a preview assemble and 5 min,
  all before the final must start (its estimate × 1.3 + 8 min before the end). Never under 45 min; solo also with
  `--builders 0` and for a review round of a video made without a valid plan. On a slow machine the final render is
  the floor: here (4 CPUs, no GPU) a 3-minute song at 1080p needs a budget of several hours before the pipeline
  fits, so most runs on this machine are solo; a faster PC (its `tools/speeds.json`) gets the pipeline much sooner.
  `--mode pipeline` forces it (when the stages then don't fit, the run goes solo once the plan's estimate says so).
- **The studio forces the final** when the time left is the final's estimated render time × 1.3 + 8 min, stopping
  builders that are still at work (what they rendered stands when it is their scene as it is). The estimate: each
  item's measured preview render time × what a final frame costs against a preview frame on this machine for its
  renderer (from `tools/speeds.json`: here about ×1.3 for the web engine, ×3.6 for Eevee and Workbench, ×6 for
  Cycles, whose preview also has a quarter of the samples; ×2.5 without a benchmark), else its last final render,
  else `speeds.json`. Before there's a plan it is the whole song with the web engine plus one overlay pass (a plan
  nearly always layers the type).
- **The cut that exists is never lost**: the start of a run archives `final/music-video.mp4` as a version, and in
  the pipeline the studio does the same before it renders a final over one this run already delivered.
- **No critic watcher in pipeline mode**: the pipeline asks the critic at its gates (the language, each builder's
  segment, every assembled preview, the final), so a preview isn't critiqued twice; the watcher comes back when a run
  falls back to solo.
- **A builder that fails isn't redone by the studio**: the planner hears at once (what failed, what is there) and
  decides: take it over, give its time to a neighbour, or leave it to the final's stand-in (a neighbour's scene over
  its range, the last resort that keeps a final possible).
- **The render cache follows imports**: a web scene's chunks are keyed by its folder plus every file it reaches by a
  relative `import`, so a change to the shared lib renders every segment that uses it again, and a change in one
  segment's folder renders only that segment. That is why the pipeline wants each scene in its own folder.
- **The director's sandbox** is tested, not assumed: `studio.py permtest` (a relative deny rule doesn't match in
  Claude Code 2.1; the absolute `//path` form does).
- **The Direct page sends both efforts**: solo's (`--effort`, default high) and the planner's (`--planner-effort`,
  default max), since with `--mode auto` the runner only decides which mode to run when it starts. The page's hint
  about which mode a budget gets uses the same rule as the runner (`pipeline.stage_times` with the final render's
  estimate here).

- **Live preview: the browser plays the scenes, the parent page owns the clock.** No render, no video stream: the
  scenes' own pages run in the user's browser on the song's audio clock. Frames drop when a scene is slower than real
  time, but each frame shown is the one for that moment of the song, so the timing the user hears against is true;
  stateful scenes catch up the way a render pre-rolls (a jump back in one without `preroll` reloads it, since its state
  must start from setup). The same runtime, page and URLs as a render, so a live frame is the render's frame (the smoke
  test compares them pixel for pixel), and a scene edited on disk plays its new version within seconds.
- **A live server per project, on its own port**: a separate origin with exactly the renderer's URL space (`/_mv/...`,
  the project at `/`), so scenes need no rewriting and can't reach the web app; the iframes are sandboxed to their
  origin. Started lazily, kept for the web app's life.
- **Live notes reach the director without derailing the pipeline**: a message to a busy session joins its turn, and
  an idle planner gets one at once, only where a turn of its own is safe (planning, revising, waiting for builders: the
  assemble then waits for that turn); while the studio gates or assembles, they wait for the planner's next message
  (the assemble report has them), busy planner or not, since a fix then would race the scenes being rendered; in the
  final stage (the planner is told not to touch the scenes while the final renders) they wait for the review round.
  Open ones go into the next run's brief. The director marks a note done through `studio.py note-done` (a lock, a
  re-read, an atomic replace), never by editing `review/notes.json`, which the web app writes too.
- **Under the live view the song's lanes give way**: they share the browser's main thread with the scenes, so while a
  scene falls behind real time the lanes are drawn less often (the line above them still every frame).

## Known limitations
- A builder's sandbox refuses its Write and Edit tools outside its two folders (Claude Code's deny rules); its Bash
  commands aren't path-checked, so a builder that copies files with Bash could still write elsewhere in the project
  (the brief says not to). The same holds for the director and the studio's folders.
- The lib is locked by the briefs, not by permissions: the planner can still change it while builders work (the
  brief asks it to only add to it); a change re-renders every segment that imports it.
- A builder's render that waits for the render queue waits inside one Bash call (the brief says to give it the
  longest timeout, or run it in the background): with many builders rendering at once, a Bash timeout can cut a
  wait short, and the builder renders again.
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
- The render cache of a **Blender** scene is keyed by the scene file and the settings only (as before): a helper
  module it loads isn't followed. `pipeline render` starts a Blender segment over (`--fresh`) when any other file in its
  folder changed; by hand, use `--fresh`.
- The render queue is per machine (a file in the studio folder): two studio installs on one PC don't see each other's
  renders. A render that holds the queue for more than 6 h is taken over (a hung one would block it for good): a
  longer final render then shares the machine with the next one.
- QA can't hear: phrase-level musical feel needs a human ear (the director's report lists the timestamps).
- In an interactive pipeline the planner is your own Claude Code session: the studio doesn't see its cost, so the
  Direct page's costs there are the builders' and the critic's only.
- The final's estimate is the preview's average cost per frame scaled by this machine's benchmark: a scene whose
  cost changes a lot over the song, or a final whose workers share the machine with something else, can run longer;
  the 1.3 × + 8 min margin is there for that.
- `work/pipeline/state.json` is written by several processes (the runner, `pipeline build`, a planner's own `gate`
  or `assemble`): each write takes a lock file next to it (`state.json.lock`, given up after 5 s so bookkeeping never
  holds up work). A runner that was cut off (its console closed, the PC slept) leaves `current.json` saying
  "running": after 3 min without a write it counts as over (`clock` and `pipeline build` say so).
- The session plumbing waits 30 s instead of 2 s before it takes a turn as ended when a message went in less than
  10 s before that turn's result (the CLI may run it as a turn of its own); that case wasn't seen on the real CLI.
- A builder's full log on the Direct page is its `events.jsonl` summarised (the last ~600 KB of it); the studio's
  messages to a builder are in its `messages.md`.
- The critic sees stills: motion between frames, how a move feels and anything about sound are out of its reach (it
  says so). It can be wrong; the director decides. A critique stopped midway (Stop on the Watch page, a preview
  critique giving way to a new final, the end of a run) isn't counted in the run's critic cost: the call can't say
  what it spent.
- Live preview: the scenes play in iframes from the same site as the web app, which Chrome and Edge usually run on
  one main thread with the page, so several heavy scenes at once (a segment, an overlay, the lanes) share one CPU core;
  a scene that is slow live may still render fine (renders run in parallel workers). Blender scenes don't play live
  (their preview render does, or a slate), nor do transparent PNG-folder passes; blend modes and opacity are the
  browser's (`mix-blend-mode`), close to compose's but not the same pixels. The sound delay for Bluetooth headphones is
  set by hand (the browser can't measure it). Live notes made while no run is going wait for the next run's brief (a
  first run gets them as notes on the scenes as they were). A stateful scene too slow to render every frame live skips
  frames to keep time, so its picture is then only close to its render's. A player scrolled out of view stops drawing
  (the browser doesn't run a frame it doesn't show) and catches up when it's back.
- In headless browsers (the tests) `page.wait_for_function` can't watch a scene's own page: the runtime owns its
  `requestAnimationFrame` and `setTimeout`; the tests poll from Python instead.

## Ideas for next
- A **storyboard view** in the web app: the director's style frames and section plan as cards before the full render.
- **Style-frame approval** as an optional checkpoint in autonomous runs (the director pauses for one decision).
- **Beat-grid editing** in the song viewer (drag bar 0, fix a downbeat) written to project.json like `lyrics_fix`.
- A **choreography helper**: rigged 2D / 3D figures driven by the beat grid for "a lot of dance".
- Loudness-matched **share copies** (720p, vertical crops) next to the master.
