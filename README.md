# Music Video Studio

Drop in a song (plus a few notes if you like). The studio writes the song down so an AI can read what it can't hear:
every beat and bar, the sections and big moments, the melody, the chords, each instrument, each sung word, and
per-frame curves to animate with. Then **Claude Code directs an original music video for it on its own**, for a time
budget you set, and delivers `final/music-video.mp4` with a report. Motion graphics only: everything is made in code
(2D, 3D or both), no filmed footage, photos, AI-generated images or video, or downloaded artwork. The whole song,
audio unchanged.

One director with **full reign**: the top model (at max effort) makes the whole video, from the concept to the final
render, and deploys agents only where it wants to (nothing makes it). **The critic** (a cheaper model with fresh eyes)
looks at every preview and at the final and reports back in text, ambition first, then craft, so the expensive model
doesn't spend its tokens looking at pictures. You watch it work on the Direct page (the live log, its agents, the
critic's notes, the cost per model), and you can **play its scenes live** in your browser with the song while it
works, no render (you are the only one who can hear: a note you leave there reaches the director within seconds).
Then you watch the video next to the song's lanes with the critic's notes beside it, press **N** to leave a note at a
moment, and the director makes the next version. Earlier versions are kept.

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
   **Pitch round** (optional: the step with no number; your choice, never a required step): several directors pitch
   from different angles; the critic compares; you pick which one the director builds. Skip it and the director
   starts from your notes, exactly as before. The page puts the two side by side: **Hold a pitch round** (how many,
   2-5; the minutes per pitch, 20 by default; the model and the effort, high; the critic, which is the judge; notes for
   the round; a cost cap per pitch; and "let the critic pick and go straight on with a __-minute director run") or
   **Skip it**. Each pitch gets its own angle (the story, the wild card, the sound and the structure, one medium pushed
   to its limit, against the grain; your ideas taken to the extreme first when you wrote some) and is told to be
   unmistakably different from the others and as bold as it can make well, and proves its idea on the same 12-24 s
   window around the song's biggest moment. While they work, each pitch's line shows its angle, its state, its time,
   its cost, its agents and its last lines; **Stop** makes each wrap up (the critic and the verdict still look at what
   exists). Then each round is a row of cards: per pitch its title, angle, the idea in two sentences, its style frames
   (click to enlarge), **the proof with the song** (`proof_av.mp4`), **▶ live** (its scenes, or its draft plan, played
   in your browser with the song over the proof window, no render; ✕ closes it), the critic's scores and notes on its
   proof, and the verdict above them: the critic's pick (★), the ranking, why, the ideas worth grafting from the
   others, what to push (ambition first; the safe option never wins; it's advice). **Build this one** starts the
   director on that pitch with your Direct settings (step 5: the form keeps them as you change them) and takes you
   to the Direct page, which says "starting from pitch round-1/p2: <title>": its pitch, scenes, style frames and draft
   plan are put in place, and the director, with full reign as always, is told to go further than the pitch. The
   pitches are full directors on the top model (the idea is what you're choosing); the critic's cheaper model does the
   looking. From the command line: `studio.py pitch <project>`, then `studio.py direct <project> --pitch round-K/pN`
   (`--auto`: the verdict picks and the director starts at once). To build it together instead: `/musicvideo
   <project>` in Claude Code, and say which pitch (it runs `studio.py pitch-pick <project> round-K/pN`, which puts it
   in place the same way and prints what the run's brief would say about it).
5. **Direct**: set a time budget (e.g. 90 min), the model (Opus by default), the effort (max), the critic (Sonnet by
   default; Haiku is cheaper, Opus sharper, or off), **Ultracode** (off by default: the director may also orchestrate
   multi-agent workflows; more tokens), optionally a cost cap in dollars (a hard stop for the director's session, its
   agents included) and a note for this run, and start. The settings are kept for next time. Whether to deploy agents
   is the director's call, not a setting: it builds the video itself, or hands parts to agents where that makes the
   video better or faster. The run view shows the live log (the director's work; its agents' lines indented under
   them), the **Agents** list (each agent's task, model, running or done, how long), the critic's notes on every
   preview and the final (scores, the video, the report), and the costs: per model (the director's, its agents'), the
   critic's and the total. While it runs, **▶ play the scenes live** opens the live view (step 6). **Stop** wraps it
   up cleanly. Or direct it *with* Claude: open Claude Code in this folder and type `/musicvideo <project>`: the same
   director's workflow and tools (the critic, the plan tools, agents if it wants them), and you can talk along.
6. **Watch & review**: each version with the song's lanes under the video, and the critic's notes on it (scores, the
   worst problems with their times, what works; **Ask the critic** for a fresh look). Press **N** for a note at that moment.
   **▶ live** (the default while there's no video yet) plays the project's scenes as they are now, in your browser with
   the song and its lanes, no render: the plan's segments in their slots (or the director's `work/live.json`, the
   renders, a pitch's draft plan, or one scene alone), a scene that changed reloads by itself within seconds. Pick the
   size (¼, ½, full); each layer says how it keeps up ("24 of 30 fps", "catching up 40%", its error); set a **sound
   delay** for Bluetooth headphones (kept in the browser). A note here (N) is a live note: during a run it goes to the
   director within seconds with the bar, the section and the scene; the director marks it done with its fix under it.
   Notes left while no run is going go into the next run's brief.
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
| `pitch <p> [--n 3] [--budget 20m] [--model M] [--effort high] [--critic-model sonnet] [--critic-effort E] [--proof "chorus 1"\|24-44] [--angles story,wild,...] [--seed-notes "..."] [--max-usd N] [--auto [--direct-budget 90m] [--direct-effort max] [--direct-model M] [--direct-max-usd N] [--ultracode]] [--dry-run]` | the pitch round, optional: N directors pitch at once from different angles, each proving its idea on the same window of the song (`work/pitches/round-K/`); proof_av.mp4 (the proof with the song), the critic on each proof, one verdict (ambition first) with graft ideas, the costs; `--auto`: the verdict's winner is built at once |
| `pitch-pick <p> round-K/pN [--dry-run]` | put a picked pitch in place for an interactive director (`/musicvideo`), as `direct --pitch` does first, and print what's where, the critic's notes on its proof and the verdict's graft ideas |
| `direct <p> --budget 90m [--model opus] [--effort max] [--ultracode] [--max-usd N] [--seed-notes "..."] [--revise] [--no-nudge] [--dry-run] [--critic-model sonnet\|haiku\|opus\|off] [--critic-effort medium] [--critic-max 6] [--pitch round-K/pN]` | the autonomous run: one director with full reign (it deploys agents if it wants to; `--ultracode` adds multi-agent workflows: more tokens; `--max-usd` caps the session, its agents included); the critic looks at every preview and the final; `--pitch`: it starts from that pitch (put in place first) and goes further |
| `critique <p> <video \| vN> [--model sonnet] [--effort medium] [--focus "..."] [--max-usd 2] [--pictures a.png,b.png] [--concept PATH] [--out DIR] [--no-look]` | the critic: a cheaper model with fresh eyes looks at a video, reports back in text (`work/critic/`; a version also `final/critic_vN.md`) |
| `plan validate\|status <p>` | the optional plan tools (`work/plan.json`, a scene list): check it (exit 1 with what to fix) · each scene's renders, the previews with the critic's scores, the final's estimated render time here, the render queue |
| `plan render <p> [--preview\|--final] [--only s1,s3]` · `plan assemble <p> [--preview\|--final] [--no-critic]` | each segment over its own frame range (unchanged ones from the cache) · join, layer, mux → `work/previews/preview_N.mp4` or `final/music-video.mp4`, then the eyes, the checks and the critic on the whole film |
| `clock <p>` · `stop <p>` | time left in a running budget (a director's or a pitch round's) · ask a running director or pitch round to stop cleanly |
| `note-done <p> <id> ["what changed"] [--undo]` | mark the user's note done with a one-line fix (how a director answers a note: `review/notes.json` is written safely, since the web app writes it too) |
| `check <p> [video]` | every check on the final (spec, flashes, text, sync) |
| `deliver <p> [--critic [--critic-model M]]` · `versions <p>` | archive `final/music-video.mp4` as the next version (+ checks; `--critic`: + the critic's notes) · list versions |
| `permtest <p> [--pitch] [--model haiku]` | check the director's sandbox, and an agent's, with the real Claude Code (a few cents with Haiku); `--pitch`: a pitch's (its own folder writable, another pitch's and the project's files refused) |
| `run <p> <tool.py> [args]` | run any tool inside a project (`eyes.py`, `songq.py`, `compose.py`, `web-engine/render.py`, `blender/run.py`, `checks.py`, ...) |
| `serve [--port 8765]` · `doctor` · `benchmark [--quick]` · `smoke` | web app · install check · render speeds · the end-to-end smoke test |
Every command's `--help` lists all its options. The tools are documented in **`tools/README.md`** (with
`tools/web-engine/README.md` and `tools/blender/README.md`); the studio's own side (the runner, the sessions, the pitch
round's files, the live server) in `tools/INTERNALS.md`.

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
  Cycles / Workbench with the song data; scene scripts import the video's shared code and models), **compose**
  (frame-exact joins, layers, the song muxed unchanged), the **eyes** (contact sheets with bar:beat / section / lyric
  in the margin, beat sheets, motion-vs-music plots, the sync checker) and the **checks** (delivery spec incl. audio
  identity and A/V offset, photosensitivity, text).
- **The director**: the `/musicvideo` skill (how a run works, the optional agents and plan tools, the workflow + craft
  rules + an *anti-defaults* note: no spectrum bars, bass-pulsing particles or synthwave grids as a starting point;
  the concept comes from this song's story and structure, with signature moments on its ★★★ moments).
  `studio.py direct` fills `templates/director-brief.md` and runs Claude Code headless (`-p`, stream-json in and out,
  effort max by default, `--permission-mode dontAsk` with an allow-list that includes the agent tools, the studio's
  folders write-protected, network commands and web tools denied, no MCP servers), streams its progress to
  `work/director/`, messages it as time runs down, nudges it to keep improving if it finishes early, ends the session
  at the deadline plus a grace period, then archives the cut, runs the checks and appends the run settings (the
  models and their costs, the agents, ultracode, the cap, effort, budget, wall time) to `report.md`.
- **One director with full reign; agents are its choice.** The brief's "You have full reign" section: build it all
  yourself, or deploy agents where that makes the video better or faster; nothing requires them. If it does, it gets
  the studio's guidance, not rules: run them in the background (`run_in_background: true`; it's told when one
  finishes), give each its model (the top model for creative scene code, Sonnet or Haiku for a mechanical task that
  needs some judgment; renders and the checks are plain background commands, no agent), brief them narrowly (the concept, the song notes, the shared code, the task, the files it may write; never
  "read the song folder"), leave the looking to the critic, and put the look, the characters, the rigs and the models
  in shared code it owns first (`work/scenes/lib/`, `work/kit/`) so that agents use one look rather than copy it.
  Agents work in the director's own session and sandbox; the studio sees them in the stream (`mv/session.py`): while
  any runs the session counts as working even between the director's turns (no nudge, no reminder, no closing), and
  when the last one ends the session starts a turn of its own. The live log has a line per agent (its own tool calls
  summarised under it, at most one line per 20 s), `status.json` lists them (`agents`) with the costs per model
  (`model_usage`), and the Direct page shows both. `--ultracode` (the web form's checkbox, off by default) adds the
  Workflow tool for multi-agent workflows and one line to the brief that says so. `--max-usd N` caps the session's
  spending (Claude Code's `--max-budget-usd`), its agents included.
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
  a ready final first, then the newest preview; 6 per run at most; never a cut that has a critique already or is
  being critiqued, e.g. by `plan assemble`) and sends each critique to the director as a message, with the pictures it
  looked at; the director doesn't do a full look of its own after a preview (the brief and the skill say so), only
  the few pictures it needs. The early-finish nudge carries its notes on the final (ambition first); the archived
  version keeps its critique as `final/critic_vN.md`. A call that couldn't answer (a usage limit, a login, the $2 cap)
  is no critique: nothing is written but a log line, and its cost still counts.
- **The pitch round** (`tools/pitch.py`, `templates/pitch-brief.md`, `templates/pitch-verdict.md`), optional: N
  headless directors at pitch scale, all at once, each its own session (`mv/session.py`, like the director's: agents
  allowed, its own time budget, time warnings, a reminder when something is missing, one push to go further when it
  ends early, the deadline plus a grace), its own angle, and a sandbox: the director's plus deny rules on writes
  outside its own pitch folder (the other pitches, the round's files, earlier rounds, `final/`, `report.md`,
  `review/`, `project.json`, `song/`, `work/scenes/`, `work/kit/`, `work/plan.json`, `work/concept.md`, ...). Each
  brief says the others pitch from other angles and that the user will compare: be unmistakably different, as bold
  as can be made well, the proof the idea at full strength on the window, honest about the render time (the whole
  song's final render per renderer on this machine is in it). The studio picks one proof window for all: the
  strongest ★★★ moment with music on both sides (a chorus start or a drop first, then the biggest lift), 2 bars
  before it to ~16 s after, on downbeats, 12-24 s (`--proof` sets it). After the pitches: `proof_av.mp4` (the proof's
  video copied, the song's audio cut at the window, AAC), the critic on each proof (`--concept pN/pitch.md --out
  pN/`, with its style frames; two at a time), then one verdict call on the critic's model (every pitch.md inline, the
  style frames and the proofs' strips to open, each critique, this machine's render speeds): ambition highest, then
  the fit to this song, then whether it can be built well in a director's budget here, then craft; never the safe
  option; a ranking, the winner, why, ideas worth grafting → `verdict.md` + `verdict.json`. `round.json` keeps it
  all (per pitch: angle, state, the session's latest running cost, agents, title, idea, proof, critic; the verdict;
  the costs; `picked`). `direct --pitch round-K/pN` puts the pick in place (pitch.md → `work/concept.md`, its scenes
  and `lib/` → `work/scenes/`, or `work/scenes/pitch-K-N/` when there are scenes already, its style frames, its draft
  plan with the paths moved; whatever was there is kept as a `_before_pitch_<time>` copy, `work/live.json` too) and
  the brief gets a short
  section: the user picked this pitch, build it and go further, the critic's notes on its proof, the verdict's
  graft ideas and the round's notes (written for the pitchers: context, not a rule); full reign as always. A review
  round never starts from a pitch.
- **The plan tools** (`tools/plan.py`, `studio.py plan ...`), optional: when a scene list helps its video, the
  director writes `work/plan.json` (segments that cover the song on downbeats, each scene in its own folder
  `work/scenes/<id>/`, shared code in `work/scenes/lib/` and `work/kit/`, overlays on top); the studio checks it
  (errors a model can act on: "a gap before it: s1 ends at 24.24 s (frame 727) and s2 starts at 24.50 s ... Set
  s2.from to 24.24"), renders each segment over its own frame range into `work/segments/<id>/` (web: one `render.py
  --frames a:b`; Blender: `run.py`, then `compose.py encode`; overlays as transparent passes; unchanged scenes from the
  cache), and assembles a preview or the final in one command (join, layer, mux, the eyes, the checks, the critic on
  the whole film; a segment without a scene is a labelled slate in a preview, the final needs them all). Python, no
  tokens. plan.json is also the live preview's timeline.
- **The Direct page** polls `GET /api/p/<p>/director` (the run's status: its live log, the clock, the agents, the
  costs per model with the total, the critiques; the pitch it started from, with the round's cost).
- **The Pitch round page** polls `GET /api/p/<p>/pitches` (every round.json, newest first, with the URLs of each
  pitch's files and what its card's live player plays: its draft plan as the live state's `pitch:round-K/pN` timeline,
  else the scene its proof was rendered from, per the proof's sidecar). `POST /api/p/<p>/pitch` starts `studio.py
  pitch` as the job "pitch" (with "go straight on": `--auto` and the Direct form's saved model, effort, ultracode and
  cap); `POST /api/p/<p>/pitch-pick {round, k}` starts `studio.py direct --pitch round-K/pN` with the Direct form's
  saved settings; Stop asks a pitch job (or a round started from the command line) to wrap up. With `--auto` one job
  runs the round and then the director: the job state says `chained: "direct"`, and the Direct page treats it as its
  run (its steps, its Stop).
- **The live preview** (`tools/mv/live.py`, the runtime's live mode in `web-engine/runtime/mv.js`, the Watch page's
  `livePlayer`): the web app starts a small server per project on 127.0.0.1 (its own port, GET only, `no-store`, the
  renderer's exact URL space and nothing outside it) and stacks the scenes' pages from it as iframes, each visible only
  in its slot, over the song's `<audio>`, which is the clock: every frame the page tells each scene where the song is,
  and the scene shows that frame (its own frame clock, the same pixels a render gives), dropping frames when it is
  slower than real time rather than drifting (a stateful scene then skips frames too, and is close to its render, not
  exact; pausing or playing never restarts a simulation). `GET /api/p/<p>/live` lists the timelines (work/live.json, work/plan.json,
  the renders, the pitches' draft plans) and the scenes. The live notes go into `review/notes.json` (`"version":
  "live"`, the scene, the timeline), and the director's runner forwards them every ~5 s (`director.py` `LiveNotes`):
  into the running turn, or at once as a turn of their own (while its agents work too); not once the run is stopping
  or with less than 2 minutes left; marked `forwarded_run`. The director marks one done with `studio.py note-done`.
- **The render queue** (`tools/mv/renderlock.py`): every video render (`web-engine/render.py`, `blender/run.py`; not
  stills) takes a machine-wide lock first (`cache/render.lock` in the studio folder), so the director and its agents
  queue their renders instead of sharing the CPU/GPU and all slowing down; a waiting render prints `waiting for the
  render queue: <who> since <time>`. A lock whose process is gone (a killed render) is taken over at once.

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
- **The pitch round is a choice, not a stage**: nothing runs it unless you ask (a director may suggest it in an
  interactive session; an autonomous one never starts it), and the director that builds the winner keeps full reign:
  the pitch is its starting point ("go further than the pitch: the proof is a first draft, not a ceiling"), not a
  spec. The pitches are full directors on the top model (the project's director model, effort high), because the idea
  is what's being chosen; the critic's cheaper model does the image-heavy looking at the proofs and judges the round,
  weighing ambition highest and never rewarding the safe option. All pitches prove themselves on the same window
  (chosen by the studio, around the song's biggest moment), so they can be compared, and start fresh from the song and
  your notes rather than from an earlier director's work. The angles are fixed starts (the story, the wild card, the
  sound and the structure, one medium, against the grain; your ideas first when you wrote some) so the pitches differ
  by construction, not by luck. Picking never overwrites: an existing concept, plan or style frames are kept as
  `_before_pitch_<time>` copies, and existing scenes stay where they are (the pitch's go next to them).
- **The Pitch round page is a choice, not a stage**: it sits between Story and Direct without a number and marked
  optional, opens with the choice (hold a round or skip it, side by side), and every word on it says the director
  keeps full reign. The cards stay in pitch order (p1, p2, ...) with the verdict's rank as a badge, so you watch the
  proofs before you read the ranking; the verdict sits above them as advice ("you pick"). Build this one uses the
  Direct form's saved settings rather than a second form (one place to set the director), and the page says which.
  A card plays one live view at a time; opening another, closing it, or leaving the page destroys the player.
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
- **One director with full reign; the forced builders were dropped (the user's call).** An earlier version of this
  studio ran every video through one fixed pipeline: the top model planned and drafted, a language gate, then builder
  sessions built the other segments of the song in parallel, then assemble / revise / final stages. After seeing it,
  the user decided against it: they didn't want each chorus or part of the song made by a different agent, but simple
  instructions and more freedom for the top model, with full reign: if it wants to deploy agents it does, if it
  doesn't it doesn't, and nothing forces it. So the stage machine, the builders, the modes and their settings are gone
  (git history keeps them), and one director session (the top model, effort max) makes the whole film. It may still
  deploy agents, as many or as few as the video needs, and the brief says how to do that well without requiring it.
  Cutting the work by song time also split up the kind of work the user had seen their earlier director do well (a
  rig engine for a comic-book video's characters, many Blender models, used across the whole video: three builders
  would draw three versions of one character), and one look across the segments was held only by instructions. What
  made the pipeline worth having stays, for one
  director: the critic does the looking, the plan tools render per scene with a cache and assemble in one command, the
  render queue, the cache key that follows imports (Blender's too), the live preview and its notes.
- **The brief encourages, it never forces**, and it keeps the ambition: the director sets how bold the video is, and
  agents are there to make it bolder or faster, never to average an idea into a safe consensus. The guidance where it
  does use agents: in the background, each with its model (the top model for creative scene code; Sonnet or Haiku for
  a mechanical task that needs judgment), narrow briefs (the concept, the song notes, the shared code, the task, the files it may write;
  never "read the song folder"), the looking left to the critic, one look in shared code the director owns
  (`work/scenes/lib/`, `work/kit/`), renders queued one at a time, their cost part of the run's.
- **Ultracode is opt-in** (`--ultracode`, the Direct page's checkbox, off by default): it adds Claude Code's Workflow
  tool (multi-agent workflows) and one line to the brief. Workflows burn tokens; most videos don't need them, so the
  user turns them on per run.
- **Effort max for the director by default** (the judgment lives there: the concept, the code, what to take from the
  critic); the critic runs at medium on a cheaper model.
- **Agents in a session, verified on the real CLI** (Claude Code 2.1.311-2.1.312, Haiku, stream-json): the session
  has the Agent tool (`--allowedTools Agent` is the name it needs; the init event lists it as `Task`) and the Workflow
  tool; an agent called with `run_in_background: true` runs in the background ("Async agent launched"; without it, on
  2.1.312, in the foreground), the director may end its turn while it runs, and when it finishes the session **starts
  a turn by itself** (a new `system/init`, then its events and a `result`, with no user message; within a second of
  the empty `background_tasks_changed`). The stream shows the lifecycle (`background_tasks_changed`, `task_started`,
  `task_progress`, `task_updated`, `task_notification`; an agent's own events carry `parent_tool_use_id`). So the
  session counts as working while its agents run, and the runner sends nothing that needs a finished turn (a nudge, a
  reminder) and never closes it then. A subagent's write into the studio's tools is refused by the same deny rules as
  the director's (`studio.py permtest` checks both). The test stand-in plays the same lifecycle.
- **The costs are the session's running totals**: a `result`'s `total_cost_usd` and `modelUsage` are cumulative for
  the session (three results gave 0.00385, 0.00468, 0.00510; another session 0.00735, 0.00820, 0.00863), and
  `num_turns` is per turn (2, 1, 1). The runner used to add every result's total, which over-reported every multi-turn
  run; now the session's cost is its latest result's, the cost per model the latest `modelUsage`, and the turns add
  up. The run view and report.md list the cost per model, labelled: the director's model, the models its agents asked
  for ("agents"), Claude Code's own small calls (unlabelled); then the critic's and the total.
- **A cost cap, optional** (`--max-usd N`, the Direct page's "cost cap"): Claude Code's `--max-budget-usd` on the
  director's session, its agents included. When a result says it was reached, the run ends there quietly (more
  messages would be refused or cost more), the cut that exists is archived and checked, and report.md says the cap was
  reached.
- **One render at a time per machine**: parallel agents speed up thinking and writing code, not rendering; two
  renders at once only share the same CPU or GPU and both take twice as long (or run out of GPU memory). So every
  video render, the director's and its agents', waits its turn in the render queue; the final render is the floor no
  fan-out lowers. `MV_RENDER_LOCK=0` turns it off; `MV_RENDER_WHO` names a render while it holds the queue.
- **The cut that exists is never lost**: the start of a run archives `final/music-video.mp4` as a version.
- **The critic never looks twice at one cut**: `plan assemble` runs the critic on its own cut and marks it from the
  mux on (`work/critic/pending/`), every critic.py run marks its cut while it works, and the runner's watcher skips a
  cut that has a critique already (its path, size and mtime) or is being critiqued, and an older preview once a newer
  cut has one. A critique made that way still goes into the run's record as it comes (the run view, its cost in the
  total), without a second message to the director.
- **The render cache follows imports**: a web scene's chunks are keyed by its folder plus every file it reaches by a
  relative `import`, so a change to the shared code renders every scene that uses it again, and a change in one
  scene's folder renders only that scene (that is why the plan tools want each scene in its own folder). A Blender
  scene's cache follows its own folder, `work/scenes/lib/` and `work/kit/` (its `sys.path`), so a shared model or rig
  that changes renders its scenes again.
- **The director's sandbox** is tested, not assumed: `studio.py permtest` (a relative deny rule doesn't match in
  Claude Code 2.1; the absolute `//path` form does), with an agent step: an agent the director starts shares its rules.

- **Live preview: the browser plays the scenes, the parent page owns the clock.** No render, no video stream: the
  scenes' own pages run in the user's browser on the song's audio clock. Frames drop when a scene is slower than real
  time, but each frame shown is the one for that moment of the song, so the timing the user hears against is true;
  stateful scenes catch up the way a render pre-rolls (a jump back in one without `preroll` reloads it, since its state
  must start from setup). The same runtime, page and URLs as a render, so a live frame is the render's frame (the smoke
  test compares them pixel for pixel), and a scene edited on disk plays its new version within seconds.
- **A live server per project, on its own port**: a separate origin with exactly the renderer's URL space (`/_mv/...`,
  the project at `/`), so scenes need no rewriting and can't reach the web app; the iframes are sandboxed to their
  origin. Started lazily, kept for the web app's life.
- **Live notes reach the director at once**: a message to a busy session joins its turn, and an idle one gets a turn
  of its own for them (while its agents work too: the note may matter to what they build). Not once the run is
  stopping or with less than 2 minutes left (it couldn't act on them): open ones go into the next run's brief. The
  director marks a note done through `studio.py note-done` (a lock, a re-read, an atomic replace), never by editing
  `review/notes.json`, which the web app writes too.
- **Under the live view the song's lanes give way**: they share the browser's main thread with the scenes, so while a
  scene falls behind real time the lanes are drawn less often (the line above them still every frame).

## Known limitations
- The director's sandbox refuses its Write and Edit tools (and its agents') in the studio's folders (Claude Code's
  deny rules); Bash commands aren't path-checked, so a command that copies files could still write there (the brief
  and the system prompt say not to).
- Costs are known when a turn ends: Claude Code reports them in a `result`, so during a long first turn the run view's
  costs stay at what the last turn said (at first $0.00), and an agent's cost shows once a turn ends after it. The cap
  (`--max-usd`) is Claude Code's own check on those totals.
- The cost per model is labelled by model: a model the director's agents asked for is "agents", the session's own
  "the director"; an agent on the director's own model can't be told apart from it (that line says "the director and
  its agents").
- Ultracode's workflows weren't probed on the real CLI: a Workflow call (or a task whose type says workflow) is listed
  as one agent entry, "a workflow"; how a workflow's own agents show up in the stream is unverified (their cost is in
  the session's totals either way).
- `SendMessage` isn't allowed in autonomous runs: in Claude Code 2.1.312 it can also address another Claude session
  on the same machine by its name, and the deny rules can't limit whom it reaches. The director starts and stops its
  own agents (`Agent`, `TaskStop`); it can't steer one that is already running (it stops it and starts another).
- A pitch's sandbox is the director's plus deny rules for its file tools (verified with `permtest --pitch` on Claude
  Code 2.1.312 and Haiku: its own folder writable; another pitch's folder, `work/scenes/`, `final/` and the studio's
  tools refused); its Bash commands aren't path-checked. The round notes any file in the project's own folders that
  changed while the pitches worked (round.json `warnings`), whoever changed it.
- A pitch round renders N proofs through the one render queue: with N pitches the last proof may wait behind the
  others' (each brief says to start its proof by half time). Its cost is N short director sessions on the top model
  plus N critiques and the verdict (round.json and the job log give each); `--max-usd` caps each pitch.
- The verdict sees stills (style frames, the proofs' strips and moments) and reads the pitches and the critiques: like
  the critic it can't judge motion or sound, and says so; with `--auto` it is the only judge. When its answer can't be
  read, the critic's scores on the proofs rank the pitches (round.json says so). A stop while the verdict works ends
  its call: what it had spent is never reported (it never answered), so the round's cost leaves it out.
- A pitch card's ▶ live plays only web scenes: a pitch built in Blender has its proof (with the song) but no live view.
  A pitch without a draft plan plays the one scene its proof was rendered from, over the proof window only.
- Build this one takes the Direct form's settings (the form keeps them as you change them, without starting a run):
  to build a pitch with another budget or model, change them on the Direct page first. A round started from the
  command line shows on the page (its pitches, Stop) but its job log doesn't.
- An agent's render that waits for the render queue waits inside one Bash call: a Bash timeout can cut the wait short
  and the render is started again (the skill says to run long renders in the background or with the longest timeout).
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
  autonomous runner passes its own scoped permissions and asks nothing. The studio doesn't see an interactive
  session's own cost or agents: the Direct page shows autonomous runs.
- One song per project; the whole song, always.
- A Blender scene's render cache follows its own folder, `work/scenes/lib/` and `work/kit/` (files of 20 MB or more by
  size and date): a file it loads from anywhere else isn't followed (render with `--fresh` then). A web scene's follows
  its folder, what it imports (and `new URL(...)`s), and `work/kit/` when its code names a `kit/` path: a file it
  fetches from anywhere else isn't followed either.
- The render queue is per machine (a file in the studio folder): two studio installs on one PC don't see each other's
  renders. A render that holds the queue for more than 6 h is taken over (a hung one would block it for good): a
  longer final render then shares the machine with the next one.
- QA can't hear: phrase-level musical feel needs a human ear (the director's report lists the timestamps).
- The plan tools' estimate of the final render is the previews' average cost per frame scaled by this machine's
  benchmark: a scene whose cost changes a lot over the song, or a final that shares the machine with something else,
  can run longer.
- The session plumbing waits 30 s instead of 2 s before it takes a turn as ended when a message went in less than
  10 s before that turn's result (the CLI may run it as a turn of its own); that case wasn't seen on the real CLI.
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
- A **storyboard view** of the running director's own style frames and section plan in the web app (the pitch round
  shows them per pitch before a run; a run's own aren't shown until its previews).
- **Approving a running director's style frames** mid-run (the pitch round is the checkpoint before a run; within a
  run there is none).
- **Beat-grid editing** in the song viewer (drag bar 0, fix a downbeat) written to project.json like `lyrics_fix`.
- A **choreography helper**: rigged 2D / 3D figures driven by the beat grid for "a lot of dance".
- Loudness-matched **share copies** (720p, vertical crops) next to the master.
