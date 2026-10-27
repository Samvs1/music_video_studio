# Music Video Studio — CLAUDE.md

A studio for making original music videos as **motion graphics** (made in code, 2D / 3D; no filmed footage, photos,
AI-generated video or images, or downloaded artwork), one per project, always the whole song with its audio unchanged.
The user drops in a song in the local web app; the studio writes the song down (the song folder) so an AI can read
what it can't hear; Claude Code directs the video, interactively (`/musicvideo <project>`) or alone with a time budget
(`studio.py direct`). (A git-ignored `CLAUDE.local.md` may say more about the user.)

## Where things are
- `projects/<p>/`: `project.json` (the user's notes: `about`, `mood`, `ideas`, `avoid`, `format`, `lyrics`; `versions`,
  `runs`), `song/` (the song file + the song folder: start at `song/README.md`), `work/` (the director's workspace and
  log), `final/` (`music-video.mp4` + `music-video_vN.mp4`, never overwritten), `report.md`, `review/notes.json`.
- `tools/README.md`: every tool. `tools/installed.md`: what this machine has. `tools/speeds.json`: render speeds here.
- `.claude/skills/musicvideo/SKILL.md`: the director's workflow and craft rules (+ `anti-defaults.md`).
- `templates/`: the director brief, the revise brief, the story prompt. `app/`: the web app. `tests/`: smoke + server tests.

## Running things
Always through the studio's venv: `.venv\Scripts\python.exe tools\studio.py ...` (Windows; prefix
`PYTHONIOENCODING=utf-8`) or `.venv/bin/python tools/studio.py ...` (Linux/macOS). `studio.py run <p> <tool> ...` runs
any tool inside a project (paths relative to the project folder). Plain `python tools/studio.py` re-runs itself in the
venv. Bash heredocs mangle quotes: for non-trivial patches write a small script file.

## Rules
- Inside a project, stay inside it; the studio's own folders (`tools/`, `assets/`, `.claude/`, `templates/`, `app/`)
  are read-only to a director run. No network at render time: everything is local (vendored libraries, fonts).
- Fonts only from `assets/fonts` (OFL / Apache, licences there) or other open-licensed fonts with their licence kept.
- You can't hear or watch: never claim you did. Look at frames (`eyes.py`), read the song folder, run the checks.
- Craft (details in the skill): every cut and hit on a musical event within a frame; lyric text on the beat or 8th at
  or just before its sung word; text readable long enough, ≥ 3:1 contrast, never clipped; nothing revealed in a
  shot's last beat; one committed visual language; fix the film, not the frame; no rapid bright flashing; say which
  timestamps need a human ear.
- Privacy: songs, notes and videos stay in `projects/` (git ignores the media). The web app listens on 127.0.0.1 only.

## Working on the studio itself
- Python 3.11-3.13 (no 3.12-only syntax: run `python3.11 -m py_compile`); `tools/mv/song.py` and
  `tools/blender/mvblend.py` also run inside Blender's own Python. Windows is the main target: no POSIX-only calls,
  paths via `os.path`, subprocess lists (no shell strings).
- Tests: `python tests/smoke_test.py` (synthetic song → ingest → web engine + Blender → compose → every check →
  the director runner with a stand-in for Claude) and `python tests/test_server.py` (the web app's API + each page in
  headless Chromium). Both must pass. `studio.py permtest <p>` checks the director's sandbox with the real Claude Code.
- The song kit came from aftermovie-studio; keep its behaviour (and its warnings) honest when changing it.
