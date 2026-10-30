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
  `work/plan.json` the pipeline's plan, `work/scenes/lib/` the locked visual language, `work/scenes/<id>/` one
  segment's scene, `work/segments/<id>/` its renders, log and eyes, `work/pipeline/` the run's state.json, renders.json,
  tasks.json and sessions/; `work/previews/preview_N.mp4` are the full previews the critic looks at, `work/critic/`
  its critiques + `log.jsonl`),
  `final/` (`music-video.mp4` + `music-video_vN.mp4`, never overwritten; `critic_vN.md/.json`: the critic's notes on
  each version, rewritten by "Ask the critic"), `report.md`, `review/notes.json`.
- `tools/README.md`: every tool. `tools/installed.md`: what this machine has. `tools/speeds.json`: render speeds here.
  `tools/critic.py` (`studio.py critique`): the critic, a cheaper model with fresh eyes that does the image-heavy
  looking and reports back in text (ambition first, then craft); the director decides what to do with its notes.
  `tools/pipeline.py` (`studio.py pipeline validate|status|render|gate|assemble|build`): the pipeline every run
  follows; `tools/mv/session.py` one stream-json Claude Code session (busy from a message to an idle session until
  its result; messages sent while busy join the running turn); `tools/mv/renderlock.py` the render queue (one video
  render at a time per machine, `<studio>/cache/render.lock`, `MV_RENDER_LOCK=0` off). `tools/mv/live.py`: the live
  preview (the web app plays the scenes in the browser with the song, no render, from a live server per project on
  127.0.0.1 in the renderer's URL space; timelines from `work/live.json` (optional override), `work/plan.json`, the
  renders). The user's live notes reach a running director; directors mark notes done with `studio.py note-done <p>
  <id> "<fix>"` (the web app writes `review/notes.json` too).
- `.claude/skills/musicvideo/SKILL.md`: the pipeline, the director's workflow and craft rules (+ `anti-defaults.md`).
- `templates/`: the director, planner, builder and revise briefs, the story prompt, the critic's prompt. `app/`: the
  web app. `tests/`: smoke + server tests.

## The pipeline (every run, autonomous or interactive)
Plan (the top model, max effort: concept, the language in code, the hero segment as the first draft, `plan.json`) →
gate 1 (the critic on the hero; the language locks) → build (builder sessions in parallel, one per segment, narrow
briefs, one critic round each) → assemble (the studio joins a preview; the critic on the whole film) → revise (the
planner fixes or sends builders back) → final (the studio renders and checks; the critic on it). The cheap critic does
the image-heavy looking and reports back in text; it never builds. `studio.py direct <p> --budget 90m [--mode
auto|pipeline|solo]`: auto runs the pipeline when its stages fit before the final render must start (else one solo
director with the critic; `--dry-run` says which and why). In an interactive session you are the planner: run
`pipeline gate`, `pipeline build` (in the background) and `pipeline assemble` yourself.

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
  paths via `os.path`, subprocess lists (no shell strings).
- Tests: `python tests/smoke_test.py` (synthetic song → ingest → web engine + Blender → compose → every check →
  the director runner with a stand-in for Claude) and `python tests/test_server.py` (the web app's API + each page in
  headless Chromium). Both must pass. `studio.py permtest <p> [--builder]` checks the director's (or a builder's)
  sandbox with the real Claude Code.
- The song kit came from aftermovie-studio; keep its behaviour (and its warnings) honest when changing it.
