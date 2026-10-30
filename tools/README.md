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
so the director's tokens go into the video. In autonomous runs the runner does this for every new
`work/previews/*.mp4` and the final by itself (a ready final first), but never twice at one cut: not at one that has
a critique already (the path, size and mtime match) or is being critiqued (a marker in `work/critic/pending/`, which
`plan assemble` and every critic.py run set for their cut), nor at an older preview once a newer cut has one. Such a
critique goes into the run's status as it comes (`used`: "the director ran it"), with no message.

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

## Agents in a run (the director's choice)
The autonomous director has full reign: it builds the video itself, or deploys agents where it wants to; nothing
requires them. What the studio does around them (`director.py`, `mv/session.py`):
- **The tools**: `--allowedTools` has `Agent` (the init event lists it as `Task`; its tool_use is `Agent`, with
  `description`, `model`, `prompt`, `run_in_background`) and `TaskStop`, and the director's own to-do list
  (`TaskCreate`, `TaskUpdate`, `TaskList`, `TaskGet`: Claude Code 2.1.312 has these, not `TodoWrite`); `SendMessage`
  and `ListAgents` aren't allowed (SendMessage can reach the user's other Claude sessions on the machine by name);
  `--ultracode` adds `Workflow` (multi-agent workflows) and one opt-in line to the brief. The briefs ask for `run_in_background: true`: without
  it Claude Code 2.1.312 ran the agent in the foreground (the director's turn waits for it).
- **The sandbox**: agents share the director's session and its permission rules (the studio's folders read-only, no
  network commands). `studio.py permtest <p>` checks it with the real Claude Code: an agent's write into the project
  is allowed, its write into `tools/` refused.
- **The session while they work**: the stream says what runs (`system/background_tasks_changed` with the current
  tasks, `task_started`, `task_progress`, `task_updated`, `task_notification`; an agent's own events carry
  `parent_tool_use_id`). While any task runs, the session is *working* even when the director's own turn has ended:
  the runner sends no nudge or "no final yet" reminder and never closes it (STOP and the deadline + grace still end
  it); critic notes, live notes and time warnings still go in. When the last task ends the session starts a turn of
  its own (a new `system/init`, no user message; seen within a second), and the runner waits up to 90 s for it. An
  agent's progress counts as a sign of life.
- **What the run records**: the live log gets a line per agent (`🤖 agent: build s3 (haiku) started` / `done in 2 min,
  14 tool calls`), its own tool calls at most once per 20 s, indented, and `↻ the director started a turn of its own
  (its agent finished)`. `status.json` has `agents` [{id, description, model, subagent_type, started, ended (seconds
  from the run's start), state running | done | failed | stopped, tools, last}], `model_usage` {model: {input_tokens,
  output_tokens, cache_read_tokens, cache_write_tokens, cost_usd}}, `background` (tasks running now), `working`,
  `budget_hit`. `studio.py clock` says how many agents are running.
- **Costs**: `total_cost_usd` and `modelUsage` in a `result` are the session's running totals (verified: 0.00735,
  0.00820, 0.00863 over three results), so the session's cost is its latest result's (agents included), the cost per
  model the latest `modelUsage`; `num_turns` is per turn and adds up. The run's total is that plus the critic's calls.
  report.md's run settings and the web app's run view list the cost per model, labelled: the director's model, the
  models its agents asked for ("agents"), Claude Code's own small calls (unlabelled), the critic, the total.
  `--max-usd N` passes `--max-budget-usd N` (a hard cap on the session, its agents included): when a result says it
  was reached the run ends there without more messages, and report.md says so.
- **Renders**: every video render waits its turn in the render queue (below), the agents' too.

## pitch.py: the pitch round, optional (`studio.py pitch <p>`)
Several directors pitch the video at the same time from different angles; the critic compares; the user picks which
one the director builds (`studio.py direct <p> --pitch round-K/pN`). A run without a pitch round is exactly as before.
```
studio.py pitch <p> [--n 3] [--budget 20m] [--model M] [--effort high] [--critic-model sonnet] [--critic-effort E]
                    [--proof "chorus 1" | --proof 24-44] [--angles story,wild,structure,medium,against,ideas]
                    [--seed-notes "..."] [--max-usd N] [--dry-run]
                    [--auto [--direct-budget 90m] [--direct-effort max] [--direct-model M] [--direct-max-usd N] [--ultracode]]
```
- **The round**: `work/pitches/round-K/` with `round.json` (the settings, the proof window, the angles, the deadline,
  `state` running | done | stopped | failed and `stage` pitching | proofs | critic | verdict | done; per pitch: k,
  angle, dir, state done | no proof | failed | stopped, cost_usd (its session's latest running total), model_usage,
  agents, turns, title, idea (from pitch.md), pitch, proof, proof_av, style, scenes, plan, critic (scores, verdict, the
  top problems, file, cost), error, the last log lines; `verdict`; `picked`; `costs` {pitches, critic, verdict,
  total}; `warnings`), `p1/ … pN/` (each pitch's folder) and `sessions/pN/` (brief.md, command.json, settings.json:
  its sandbox, events.jsonl, messages.md, stderr.log, status.json with its live log). `pitch.rounds(p)` and
  `pitch.round_active(p)` read them.
- **The angles**: the story, the wild card, the sound and the structure, one medium pushed to its limit, against the
  grain; with ideas in the brief, "your ideas taken to the extreme" comes first. `--angles` picks them.
- **The proof window** (the same for every pitch): the strongest ★★★ moment with music on both sides (a chorus start
  or a drop first, then the biggest lift, a key change, the loudest stretch), from 2 bars before it to the downbeat
  nearest ~16 s after, 12-24 s, inside the song. `--proof "chorus 1"` takes the same rule around a section's start;
  `--proof 24-44` exact seconds. The brief gives it as times, bar:beat and `--frames A:B` at the delivery fps.
- **Each pitch** is a headless director at pitch scale (`mv/session.py`; agents allowed, its choice), cwd the project
  folder, the brief `templates/pitch-brief.md` (`<!-- mv:pitch -->`), the project's director model by default at
  effort high. Its sandbox: the director's plus deny rules on writes outside its own folder (`pitch.deny_rules`: the
  other pitches, the round's files, earlier rounds, `final/`, `report.md`, `review/`, `project.json`, `song/`, `refs/`,
  `work/scenes/`, `work/kit/`, `work/plan.json`, `work/concept.md`, `work/director/`, `work/style-frames/`, ...);
  `studio.py permtest <p> --pitch` checks it with the real Claude Code. It delivers in its folder: `pitch.md` (title;
  the idea in two sentences; why this song; the visual language; 3-5 signature moments with bar:beat; a section plan;
  unlike the defaults; how it gets made, with the final render time here; risks), `style/` (2-3 style frames),
  `scenes/` (shared code in `scenes/lib/`, imported relatively; the proof scene in its own folder), `proof.mp4` (the
  window at preview size) and optionally a draft `plan.json` (the plan tools' format; its scenes named where they'll
  live once picked: `work/scenes/<id>/...`, the files in its `scenes/<id>/`). Its eyes go to its own `eyes/`.
  All N run at once; their renders wait in the render queue (`MV_RENDER_WHO` = `pitch pN (round K)`). The studio
  sends each the time warnings (half time, the last minutes, time's up), a reminder when pitch.md, the proof or the
  style frames are missing, one push to go further when it ends early with a third of its time left, and ends it at
  the deadline plus a grace (10%, 1.5-5 min).
- **After the pitches**: `proof_av.mp4` (the proof's video copied, the song's audio from the proof's own range, AAC; a
  proof without a sidecar gets the window's), the critic on each proof (`critic.py <proof> --concept pN/pitch.md
  --out pN/ --pictures <its style frames> --focus <the round>`, two at a time), then one verdict call (`claude -p`,
  the critic's model, read-only tools, `templates/pitch-verdict.md`, `<!-- mv:verdict -->`: every pitch.md, its style
  frames and the proof's strips to open, each critique, the song, the user's notes, this machine's render speeds;
  ambition highest, then the fit to this song, then whether it can be built well in a director's budget here, then
  craft; never the safe option) → `verdict.md` + `verdict.json` (winner, ranking, verdict, why, scores per pitch,
  graft [{from, idea, where}], push), read with the critic's lenient json parser. One pitch with a pitch.md wins
  without a call; an answer without readable json falls back to the critic's scores on the proofs.
- **Stop**: `studio.py stop <p>` (and the web app's Stop) writes `work/pitches/STOP`: each pitch wraps up, then the
  critic and the verdict run on what exists; a stop while they work ends them too (their processes are ended). The
  round's state is `stopped` and `--auto` doesn't start the director. A pitch whose session doesn't end within 20 s
  of its input closing at the deadline plus the grace is stopped with what it started. round.json is written every
  few seconds all along (the verdict's wait too), so the web app and the guards see the round as running until it is
  done.
- **Costs**: per pitch its session's latest running total (agents included), the critic per proof, the verdict, the
  round's total (`round.json` `costs`, the job log's last line). `--max-usd N` caps each pitch's session.
- **Picking**: `studio.py direct <p> --pitch round-K/pN [...]` (`pitch.promote`, before the run): pitch.md →
  `work/concept.md` (an existing one kept as `work/concept_before_pitch_<time>.md`); its scenes → `work/scenes/` (its
  `lib/` → `work/scenes/lib/`), or `work/scenes/pitch-K-N/` when `work/scenes/` has scenes already (they're kept as they
  are; the brief says so; the same pitch picked again after its folder there changed: that folder is kept as
  `work/scenes/pitch-K-N_before_pitch_<time>/`); its style frames → `work/style-frames/` (the ones there before moved to
  `work/style-frames_before_pitch_<time>/`); its `plan.json` → `work/plan.json` with the scene paths moved with the
  scenes (an existing one kept as `work/plan_before_pitch_<time>.json`). `round.json` records `picked` {k, by, at, run}.
  The brief gets a short section (`pitch.brief_section`): the user picked this pitch (its title, angle, proof), build
  it and go further than the pitch (the proof is a first draft, not a ceiling), the critic's notes on its proof,
  the verdict's graft ideas, and the round's notes (`--seed-notes`, written for the pitchers: context, not the
  director's own notes, the same with `--auto` and with a pick); full reign as always. A pitch that can't be built
  (no such round or pitch, no pitch.md, its round still running) is refused before the run starts. status.json and the run's entry in project.json have `pitch`
  {ref, title, angle, by, round_cost_usd, chained}; report.md's run settings name it with the round's cost. A review
  round (`--revise`) never starts from a pitch. `--auto` picks the verdict's winner and runs `direct` in the same job.
- `clock <p>` says the round's time left while one runs (the pitches use it).
- **The web app** (`app/server.py`, the **Pitch round** page between Story and Direct, marked optional): `POST
  /api/p/<p>/pitch` {n, budget, model, effort, critic_model, seed_notes, max_usd, auto, direct_budget} runs `studio.py
  pitch` as the job "pitch" (`auto`: `--auto` with the Direct form's saved model, effort, ultracode and cap); `GET
  /api/p/<p>/pitches` gives every round.json with its files' URLs and, per pitch, `live` {timeline: "pitch:round-K/pN"
  when it has a draft plan, scene: the one its proof was rendered from}; `POST /api/p/<p>/pitch-pick` {round, k} runs
  `studio.py direct --pitch round-K/pN` with the Direct form's saved settings. Stop works on a pitch job (and on a
  round started from the command line). With `--auto` the job state carries `chained: "direct"` once its director
  run starts.

## The render queue (`mv/renderlock.py`)
One video render at a time on this machine, the director's and its agents' alike: parallel agents speed up
thinking and code, not rendering. `web-engine/render.py` and `blender/run.py` take `<studio>/cache/render.lock` (O_CREAT|O_EXCL: pid, who, what, started) before a video render (not
`--still`); a waiting render prints `waiting for the render queue: <who> (<what>) since <time>` (at once, then every
~30 s) and `render queue: my turn after N s`. A lock whose process is gone (a render killed with its tree), that was
held for 6 h (a hung render), or that shows no sign of life for 10 min (the holder touches it every minute; on Windows
a dead render's pid can soon belong to another process) is taken over. `MV_RENDER_WHO` names the render
(the plan tools set `<who>: <id> <quality>`), `MV_RENDER_LOCK=0` turns the queue off, `MV_RENDER_LOCK_FILE` moves it
(the tests do).

## Live preview: the scenes in the browser, with the song (`mv/live.py`, the web app's Watch page "▶ live")
The user is the only one who can hear. The live view plays the project's scenes in their browser with the song, no
render, while the director (and any agents of its) works, and their notes from there reach a running director
within seconds. The pieces:
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
  for live notes made since the run started, not done and not forwarded, and sends them to the director as one
  message with the song context (time, bar:beat, section, the sung line, the plan's segment there, the scene seen) and
  "treat it like a review note; mark it done with a one-line fix" (`studio.py note-done <p> <id> "<fix>"`); then marks
  them `"forwarded_run": n`. A busy director gets them in its running turn, an idle one at once (a turn of their own,
  while its agents work too); not once the run is stopping or less than 2 minutes are left. Open live notes also go into the next run's
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

## The studio around it
`studio.py doctor` (install check; writes `installed.md`), `benchmark` (speeds.json), `models.py status|fetch`,
`director.py` (`studio.py story | direct | clock | stop | deliver | versions | permtest`: the autonomous run,
`Runner`, with its `CriticWatcher` and `LiveNotes`), `critic.py` (`studio.py critique`), `plan.py` (`studio.py plan`),
`pitch.py` (`studio.py pitch`, `direct --pitch`, `permtest --pitch`: the pitch round),
`studio.py serve` (web app).
Shared code: `mv/session.py` (one long stream-json Claude Code session: start, send, events into a status, turn
ends, stop with grace, the process tree killed; busy from a message to an idle session until its `result`, messages
sent while busy join the running turn; working while its agents or background commands run, and the turn it starts
by itself when they end; its cost the latest result's running total: verified on Claude Code 2.1.311-2.1.312; see
"Agents in a run"), `mv/common.py` (paths, project, fonts, ffmpeg, encoders, `scene_inputs`: what a Blender scene's
render cache follows, `file_lock`), `mv/claude.py` (the claude command and one
headless print-mode call: the story step, the critic, the pitch round's verdict; `kill_tree` stops a process with what it started,
`pid_alive` is Windows-safe), `mv/renderlock.py` (the render queue), `mv/device.py` (GPU or CPU, one model at a time),
`mv/listen.py` (the song kit's ears), `mv/songdoc.py` (its writers), `mv/curves.py` (frame curves, hits), `mv/web.py`
(the web engine's driver), `mv/live.py` (the live preview: the live server, the timelines, the notes file).
