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
  `work/scenes/lib/` and `work/kit/` shared code, characters, rigs and models that scenes import; optional
  `work/plan.json` for the plan tools; `work/previews/preview_N.mp4` the full previews the critic looks at,
  `work/critic/` its critiques; `work/director/run-N/` a run's brief, events and status; `work/pitches/round-K/` an
  optional pitch round), `final/` (`music-video.mp4` + `music-video_vN.mp4`, never overwritten, + `critic_vN.md`),
  `report.md`, `review/notes.json`.
- `tools/README.md`: every tool a director uses. `tools/INTERNALS.md`: the studio's own side (the runner, sessions,
  the render queue, the live server, the pitch round's files). `tools/installed.md`: what this machine has.
  `tools/speeds.json`: render speeds here.
- `.claude/skills/musicvideo/SKILL.md`: how a run works, the director's workflow and craft rules (+ `anti-defaults.md`).
- `templates/`: the briefs (director, revise, pitch), the story prompt, the critic's prompt, the pitch verdict.
  `app/`: the web app. `tests/`: smoke + server tests.

## A run (autonomous or interactive)
One director with full reign (the top model, effort max by default): it builds the video itself or deploys agents
where it wants to; nothing forces agents (`--ultracode` and `--max-usd` optional). The cheap critic looks at its
previews and final and reports back in text, ambition first; the user's live notes reach it mid-run; directors mark
notes done with `studio.py note-done <p> <id> "<fix>"`. Details: the skill's "How a run works". A pitch round
(`studio.py pitch <p>`) is the user's optional step before a run; `direct --pitch round-K/pN`, or interactively
`studio.py pitch-pick <p> round-K/pN`, starts from the pick. A director never starts a pitch round itself. When the
user's Claude usage limit stops a run, `--auto-continue` (the Direct page's checkbox) waits for the reset with the
clock stopped; otherwise `direct --continue` (Continue the last run) carries it on later in the same conversation
(`claude --resume`). Details: `tools/INTERNALS.md`.

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
- You can't hear or watch: never claim you did. Directors: look at frames (`eyes.py`), read the song folder, run the
  checks; let the critic do the image-heavy looking at full previews (it pushes ambition up, never toward the safer
  idea).
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
