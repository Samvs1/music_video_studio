# Music Video Studio internals

How the studio works around a director, for working on the studio itself. No director brief points here: a director
needs `tools/README.md` (the tools), the skill and its brief. Moved here from tools/README.md so every director run
and every pitch doesn't read them.

## The critic during an autonomous run
The runner's watcher (`director.CriticWatcher`) critiques every new `work/previews/*.mp4` and the final by itself (a
ready final first; of several ready previews only the newest), but never twice at one cut: not at one that has a
critique already (the path, size and mtime match) or is being critiqued (a marker in `work/critic/pending/`, which
`plan assemble` and every critic.py run set for their cut), nor at an older preview once a newer cut has one. Such a
critique goes into the run's status as it comes (`used`: "the director ran it"), with no message. At most
`--critic-max` critiques a run (default 6), the last one kept for a final; no preview critique when its notes couldn't
reach the director in time (6 min). The first preview it passes over because the critiques are used up is said to the
director once, as a message (it was told not to look at full previews itself).

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

## Claude's usage limit during a run, and Continue the last run
- **How it shows** (read from Claude Code 2.1.317's own code: a real limit can't be made on demand, so every sign
  counts; `mv/session.py` `limit_of`): a `rate_limit_event` {rate_limit_info: {status allowed | allowed_warning |
  rejected, resetsAt (epoch s), rateLimitType five_hour | seven_day | ..., isUsingOverage}} whenever the limits' state
  changes; the refused turn's assistant message is Claude Code's own (model `<synthetic>`, `error: "rate_limit"`,
  some builds `api_error: "usage_limit_reached"` with `api_error_params.rate_limit_info`), text like "You've hit
  your session limit · resets 3pm (Europe/Brussels)"; then its `result`, `is_error` true, the same text (older CLIs:
  "Claude AI usage limit reached|<epoch>"). Only Claude Code's own messages count, never the director's words; a
  temporary throttle ("Server is temporarily limiting requests (not your usage limit)") and the cost cap aren't one.
  The reset: the message's own `rate_limit_info`, else the `rejected` event of the last 15 min, else the text
  (`reset_at`: `|epoch`, "in 2h 15m", "3pm", "3:30pm (Area/City)" through zoneinfo, "15:00", "18:05Z", "Nov 4,
  10am", "Mon 10am"; the next such time to come). The session counts each refused turn once (`limit_hits`; its result
  repeats it) and keeps the latest (`limit`: {text, resets_at, kind, at}); `allowed_warning` is said once per limit
  and reset in the live log ("close to its limit").
- **The runner** (`Runner.check_limit`): with `auto_continue` (`--auto-continue`; default the Direct page's choice,
  project.json `director.auto_continue`) the run pauses: `state` "paused", `paused` {since, resets_at, until,
  left_s, text}; nothing goes to the director (no time warning, reminder, nudge, live note or critic's note: they wait),
  no new critique starts, the clock is stopped (`active_s` is the run's own time; at the end `deadline` moves by the
  wait, `paused_s` adds up, `pauses` [{t, s, resets_at, text, end}]). At the reset plus `RESET_MARGIN_S` (60 s; no
  reset given: every `RETRY_S`, 15 min) it carries on: `RESUME_MSG` to the same session (its time left, the new
  deadline, an agent the limit stopped), or, when the session ended meanwhile, `launch()`: a new process of the same
  conversation (`claude --resume <session_id>`, `append` to the run's events.jsonl and stderr.log). A limit that still
  holds pauses it again from its next refusal. A reset more than `MAX_WAIT_S` (a day) away, or refusals for more than
  a day, end the run. Without auto_continue the run ends at once (`stop_reason` "Claude's usage limit was reached (it
  resets HH:MM)", no time's-up message: it can't answer). A STOP while it waits ends it. A critique the limit refused
  gives its slot back and its cut is looked at again after the reset (`CriticWatcher.hold_until`).
- **What a run that ended early keeps** (status.json): `cut_off` {why limit | stopped | cost cap | ended, at, left_s,
  resets_at, text}, `left_s` (its budget not used), `session_id` (Claude Code's, from its first init), `conv_cost_usd`
  / `conv_usage` (the conversation's running totals as its last process counted them). `continuable(p)` reads the last
  run that started (not a dry run, not a continuing run stopped while it waited): what it had left (a run whose runner
  was cut off: what its clock said last, `active_s`), a reset still to come, whether it can be resumed;
  `GET /api/p/<p>/director` passes it on as `continuable` once no run is going on.
- **`direct --continue`** (the Direct page's Continue the last run): a new run (kind "continue", `continues` {run, why,
  kind, resumed}) with the budget the last one had left (at least `CONTINUE_MIN_S`, 15 min; `--budget` overrides).
  When the limit that stopped it resets later, the run waits first (`state` "waiting", `waiting_until`; current.json
  kept fresh; STOP ends it as "not started"). Then `claude --resume <its session_id>` with `CONTINUE_MSG` first (how
  run N ended, that anything running then was stopped, the new clock, the critic, the user's notes for this run and
  open live notes). A process that ends before its first turn couldn't resume it ("No conversation found with
  session ID": not on this machine): a fresh session instead, the full brief with `CONT_BRIEF` (where the work is,
  `work/log.md` first, don't start over), brief.md rewritten, `continues.resumed` false. command.json keeps every
  `launches` entry.
- **Costs across processes**: a resumed process's `total_cost_usd` may or may not include the conversation's earlier
  totals (Claude Code can restore them for the session it last ran in this folder). `Session(cost_base, usage_base,
  cost_before, usage_before)`: its first result says which (at least `cost_before`: they're included and taken off);
  this run's earlier processes are added. A run's cost is its own either way.

## pitch.py: the pitch round, optional (`studio.py pitch <p>`)
Several directors pitch the video at the same time from different angles; the critic compares; the user picks which
one the director builds (`studio.py direct <p> --pitch round-K/pN`). A run without a pitch round is exactly as before.
```
studio.py pitch <p> [--n 3] [--budget 20m] [--model M] [--effort high] [--critic-model sonnet] [--critic-effort E]
                    [--proof "chorus 1" | --proof 24-44 | --proof 1:02-1:20] [--angles story,wild,structure,medium,against,ideas]
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
  nearest ~16 s after, 12-24 s, inside the song. `--proof "chorus 1"` takes the same rule around a section's start
  (its name in any case); `--proof 24-44` or `--proof 1:02-1:20` exactly that (seconds or m:ss, at least 2 s). The
  window's `by` is "studio" or "user"; a user's choice reads "the section the user chose" / "the part of the song the
  user chose" in round.json, the critic and the verdict, "I chose" in the brief (the user's voice: `in_my_words`), "you
  chose" in the web app. A spec the song can't have raises `BadWindow` (a ValueError whose message has no flag: the web
  app shows it as it is; the command line says `--proof <spec>: ...` before any round folder exists). The web app's
  Pitch round page picks it (`GET /api/p/<p>/proof-window?spec=&n=&budget=` -> `pitch.window_preview`: the window, its
  render time here, the queue's note; `POST pitch {proof}` -> `--proof`, checked first; project.json `pitch.proof`
  keeps the choice). The brief gives it as times, bar:beat and `--frames A:B` at the delivery fps.
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
  scenes (an existing one kept as `work/plan_before_pitch_<time>.json`); an existing `work/live.json` (the video
  before's live timeline) set aside as `work/live_before_pitch_<time>.json`. `round.json` records `picked` {k, by, at, run}.
  After a pick, the critic doesn't compare a cut of the picked pitch with critiques of the video before
  (`critic.previous`), and a run's brief takes live notes on another pitch's scenes out (`director.for_this_run`).
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

- **The render queue in a round**: the N proofs take turns, so `Round` works out when each pitch should start its proof
  render at the latest (`pitch.queue_plan`: the deadline plus the grace minus N proof renders and a minute, never
  later than half-way, never earlier than a quarter in), says it in the brief and the job log, and messages a pitch
  without a proof at that time. When the N proofs one after another take more than 40% of the budget, round.json's
  `queue.note` (the job log and the running round's view too) says the round is tight.
- **Interactive pick**: `studio.py pitch-pick <p> round-K/pN [--dry-run]` runs the same `pitch.promote` (`picked.by`
  "the user", no run) and prints `pitch.brief_section`, for a director in `/musicvideo`. Refused while a director run or
  a pitch round is going on.

## The render queue (`mv/renderlock.py`)
One video render at a time on this machine, the director's and its agents' alike: parallel agents speed up
thinking and code, not rendering. `web-engine/render.py` and `blender/run.py` take `<studio>/cache/render.lock` (O_CREAT|O_EXCL: pid, who, what, started) before a video render (not
`--still`); a waiting render prints `waiting for the render queue: <who> (<what>) since <time>` (at once, then every
~30 s) and `render queue: my turn after N s`. A lock whose process is gone (a render killed with its tree), that was
held for 6 h (a hung render), or that shows no sign of life for 10 min (the holder touches it every minute; on Windows
a dead render's pid can soon belong to another process) is taken over. `MV_RENDER_WHO` names the render
(the plan tools set `<who>: <id> <quality>`), `MV_RENDER_LOCK=0` turns the queue off, `MV_RENDER_LOCK_FILE` moves it
(the tests do).

## Live preview (`mv/live.py`, the web app's Watch page "▶ live")
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
  were), with the timeline they were left in; a note on a pitch's scenes only in the brief of the run that builds that
  pitch. `review/notes.json` is written through `live.update_notes` (a lock file, a re-read, the change by id, an atomic
  replace): the web app, the runner and the director's `studio.py note-done` all write it.

## The live runtime's protocol (`web-engine/runtime/mv.js`, live mode)
- The page the live server sends is the renderer's own (`/_mv/live.html?scene=<path from the project>&scale=0.5`: the
  same shell, fonts, import map and URL space; an HTML scene is served at its own URL with the same head), at the
  delivery size × the size the user picks (¼, ½, full; default ½).
- The web app owns the clock (the song's `<audio>`): it posts `{type: "mv-clock", t, playing, rate, seek}` on every
  frame while it plays and on a seek, a pause or a rate change. The page shows frame `round(t × fps)` of that clock as it
  is now, on the browser's real `requestAnimationFrame`, and renders only when that frame changed and the last render
  is done: **a scene slower than real time drops frames, the timing stays true** (what you see is where the song is).
- Stateful scenes step on one frame at a time; a jump pre-rolls exactly as a render does (from 0, or `preroll` seconds
  before), in slices that let the browser breathe ("catching up 40%"), and a new seek cancels a catch-up under way. A
  jump *back* in a stateful scene without `preroll` reloads the page first (its state must start from `setup()` again).
  So a live frame is the renderer's frame, pixel for pixel (checked by the smoke test, stateful scenes included). A step
  back that isn't a seek (the clock correcting itself: a pause, a play, a new sound delay, a switch of stems) holds the
  frame until the clock passes it, so pausing never restarts a simulation.
- While it plays, a stateful scene that can't render every frame as fast as the clock runs (a catch-up that doesn't
  gain on the clock) skips to the clock's frame instead, like a stateless one: it stays on time, and its state is then
  close to the render's, not exact (`approx`, said in the live view), until a jump back starts it again. Paused, a
  catch-up is always exact.
- The page posts `{type: "mv-live", frame, fps (achieved), ms (per render), need, ready, catching_up, approx, errors}`
  to the web app ~4 times a second (errors at once); the live view shows it per layer: "24 of 30 fps · slower than real
  time here", "catching up 40%", "skipping frames to keep time", the scene's error.
- The per-frame text list is cleared every frame (no leak); texts aren't collected live (the checks read renders).

## The studio around it (the module map)
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
