# Music Video Studio — CLAUDE.md

A studio for making original music videos as **motion graphics** (made in code, 2D / 3D; no filmed footage, photos,
AI-generated video or images, or downloaded artwork), one per project, always the whole song with its audio unchanged.
The user drops in a song in the local web app; the studio writes the song down (the song folder) so an AI can read
what it can't hear; Claude Code directs the video, interactively (`/musicvideo <project>`) or alone with a time budget
(`studio.py direct`). (A git-ignored `CLAUDE.local.md` may say more about the user.)

## Where things are
- `projects/<p>/`: `project.json` (the user's notes: `about`, `mood`, `ideas`, `avoid`, `format`, `lyrics`, `refs`;
  `versions`, `runs`), `song/` (the song file + the song folder: start at `song/README.md`), `refs/` (the user's
  reference pictures: who's in the song, so the director can draw them), `work/` (the director's workspace and log;
  optional `work/plan.json` a scene list the plan tools render segment by segment and assemble (also the live
  timeline); `work/scenes/lib/` and `work/kit/` shared code, characters, rigs and models that web and Blender scenes
  import (the render caches follow them); `work/segments/<id>/` a plan segment's renders, log and eyes; `work/plan/`
  the plan tools' render times and assemble logs; `work/previews/preview_N.mp4` the full previews the critic looks at,
  `work/critic/` its critiques + `log.jsonl` + `pending/`; `work/director/run-N/` a run's brief, events, messages and
  status.json with its agents and costs per model; `work/pitches/round-K/` an optional pitch round: `round.json`,
  `verdict.md`, and per pitch `pN/` its pitch.md, scenes, style frames, proof and the critic's notes),
  `final/` (`music-video.mp4` + `music-video_vN.mp4`, never overwritten; `critic_vN.md/.json`: the critic's notes on
  each version, rewritten by "Ask the critic"), `report.md`, `review/notes.json`.
- `tools/README.md`: every tool. `tools/installed.md`: what this machine has. `tools/speeds.json`: render speeds here.
  `tools/critic.py` (`studio.py critique`): the critic, a cheaper model with fresh eyes that does the image-heavy
  looking and reports back in text (ambition first, then craft); the director decides what to do with its notes.
  `tools/plan.py` (`studio.py plan validate|status|render|assemble`): the optional plan tools;
  `tools/mv/session.py` one stream-json Claude Code session (busy from a message to an idle session until its
  result; messages sent while busy join the running turn; working while its agents run, then a turn of its own; its
  cost is the latest result's running total, not a sum); `tools/mv/renderlock.py` the render queue (one video
  render at a time per machine, `<studio>/cache/render.lock`, `MV_RENDER_LOCK=0` off). `tools/mv/live.py`: the live
  preview (the web app plays the scenes in the browser with the song, no render, from a live server per project on
  127.0.0.1 in the renderer's URL space; timelines from `work/live.json` (optional override), `work/plan.json`, the
  renders). The user's live notes reach a running director; directors mark notes done with `studio.py note-done <p>
  <id> "<fix>"` (the web app writes `review/notes.json` too).
- `.claude/skills/musicvideo/SKILL.md`: how a run works (full reign, the critic, live notes; optional agents and plan
  tools), the director's workflow and craft rules (+ `anti-defaults.md`).
- `templates/`: the director brief, the revise brief, the story prompt, the critic's prompt, the pitch brief and the
  pitch verdict. `tools/pitch.py` (`studio.py pitch`): the optional pitch round. `app/`: the web app (with its
  optional Pitch round page). `tests/`: smoke + server tests.

## A run (autonomous or interactive)
One director (the top model, effort max by default) with full reign: it builds the video itself, or deploys agents
where it wants to (the Agent tool, in the background, each with its own model and a narrow brief; `--ultracode` adds
the Workflow tool; nothing forces agents). If agents build parts, the look, characters, rigs and models live in shared
code the director owns (`work/scenes/lib/`, `work/kit/`). Agents share the director's sandbox and its cost
(`--max-usd` caps the session). The cheap critic looks at every preview and the final and reports back in text
(ambition first); the user's live notes reach the director mid-run. `studio.py direct <p> --budget 90m [--ultracode]
[--max-usd N]`. Optional before a run, and only when the user starts it: a pitch round (`studio.py pitch <p>`):
several directors pitch from different angles with a short proof at the song's biggest moment, the critic ranks
them (ambition first), the user picks; `direct --pitch round-K/pN` starts the director from that pitch, a starting
point to go further from, with full reign. A director never starts a pitch round itself.

## Running things
Always through the studio's venv: `.venv\Scripts\python.exe tools\studio.py ...` (Windows; prefix
`PYTHONIOENCODING=utf-8`) or `.venv/bin/python tools/studio.py ...` (Linux/macOS). `studio.py run <p> <tool> ...` runs
any tool inside a project (paths relative to the project folder). Plain `python tools/studio.py` re-runs itself in the
venv. Bash heredocs mangle quotes: for non-trivial patches write a small script file.

## Rules
- Creative freedom: the user's notes are a starting point, not a spec (the less they wrote, the more is the
  director's); of their notes only `avoid` is a hard rule. A bold idea beats a safe one.
- Inside a project, stay inside it; the studio's own folders (`tools/`, `assets/`, `.claude/`, `templates/`, `app/`)
  are read-only to a director run. No network at render time: everything is local (vendored libraries, fonts).
- Fonts only from `assets/fonts` (OFL / Apache, licences there) or other open-licensed fonts with their licence kept.
- Reference pictures (`refs/`) show how people look: draw them in the video's language; the pictures (or anything made
  from their pixels) never appear in the video.
- You can't hear or watch: never claim you did. Look at frames (`eyes.py`), read the song folder, run the checks.
  Let the critic do the image-heavy looking at full previews; it pushes ambition up, never toward the safer idea.
- Craft (details in the skill): every cut and hit on a musical event within a frame; lyric text on the beat or 8th at
  or just before its sung word; text readable long enough, ≥ 3:1 contrast, never clipped; nothing revealed in a
  shot's last beat; one committed visual language; fix the film, not the frame; no rapid bright flashing; say which
  timestamps need a human ear. Scenes should play near real time at half size where they can (heavy work in setup):
  the user plays them live with the song mid-run.
- Privacy: songs, notes, reference pictures and videos stay in `projects/` (git ignores the media). The web app listens
  on 127.0.0.1 only.

## Working on the studio itself
- Python 3.11-3.13 (no 3.12-only syntax: run `python3.11 -m py_compile`); `tools/mv/song.py` and
  `tools/blender/mvblend.py` also run inside Blender's own Python. Windows is the main target: no POSIX-only calls,
  paths via `os.path`, subprocess lists (no shell strings). A Blender scene's own folder, its sibling `lib/`
  (`<scene dir>/../lib`), `work/scenes/lib/` and `work/kit/` are on its `sys.path` and in its render cache key.
- Tests: `python tests/smoke_test.py` (synthetic song → ingest → web engine + Blender → compose → every check →
  the director runner with a stand-in for Claude) and `python tests/test_server.py` (the web app's API + each page in
  headless Chromium). Both must pass. `studio.py permtest <p>` checks the director's sandbox, and an agent's, with
  the real Claude Code.
- The song kit came from aftermovie-studio; keep its behaviour (and its warnings) honest when changing it.
