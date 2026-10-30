# VERIFY_LOCAL: check Music Video Studio on your PC (no Claude needed until step 8)

Built and tested in a Linux cloud VM without a GPU. These steps check what couldn't be tested there: Windows, the
NVIDIA GPU (CUDA, NVENC, Eevee, GPU WebGL), the model downloads, and the web app in your browser. Every command below
was run in the VM in its Linux form (`.venv/bin/python` instead of `.venv\Scripts\python.exe`). Open **PowerShell in
the studio folder** for the commands. Times are rough guesses for an RTX 4060 PC.

## 1. Setup (10-20 min the first time)
```
powershell -ExecutionPolicy Bypass -File setup.ps1
```
(or double-click `Music Video Studio.bat`, which runs it the first time). You should see:
- `using: py -3.13` (or 3.12 / 3.11), `FFmpeg found: ffmpeg version 8.1 ...`
- `NVIDIA GPU found: CUDA build of PyTorch (~2.6 GB)`, then pip installing, ending without red errors
- `Chrome found` or `Microsoft Edge found` (answer **n** to Playwright's Chromium then), a Blender question (**y** if you
  want 3D; it's optional), **Y** to "Download the song-analysis models now", which prints `== Demucs htdemucs ... ok`
  four times (htdemucs, htdemucs_ft, beat_this, MMS)
- `Claude Code found: 2.x.x`, then the install check (step 2)

If it fails: **pip errors about torch** → check you have Python 3.11-3.13 (`py -0`), delete `.venv`, run again.
**winget not found** → install "App Installer" from the Microsoft Store. **A model download fails** → run
`.venv\Scripts\python.exe tools\models.py fetch` again later (ingest also downloads them itself).

## 2. Install check (1 min)
```
.venv\Scripts\python.exe tools\studio.py doctor
```
Expect `[ok]` on every line, and in particular:
- `NVENC (GPU video encoder): h264_nvenc works`
- `GPU for the audio models (CUDA): NVIDIA GeForce RTX 4060 ..., 8 GB, torch 2.14.1+cu126`
- `Chromium for the web engine: Chrome ...` and `WebGL on the GPU: WebGL renderer: ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 ...)`
- `Blender (optional 3D renderer): Blender 4.x ...` and `Blender engine on this machine: Eevee on the NVIDIA GPU`
- `Claude Code (the director): ...` and `Claude Code logged in: yes`, `Demucs weights (stems): N files ...`
- last line `all good`

If it fails: **NVENC `--`** → update the NVIDIA driver; FFmpeg must be a build with NVENC (Gyan.FFmpeg is).
**CUDA `--`** → `nvidia-smi` must work in this PowerShell; then `.venv\Scripts\python.exe -m pip install torch==2.14.1 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu126 --force-reinstall`.
**WebGL renderer says SwiftShader** → the engine fell back to software: set `$env:MV_CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"`
and run doctor again; if it still says SwiftShader, tell Claude the exact renderer line.
**Chromium `!!`** → `.venv\Scripts\python.exe -m playwright install chromium`. **Blender not found** but installed →
`$env:MV_BLENDER="C:\Program Files\Blender Foundation\Blender 4.x\blender.exe"`. **Claude Code not logged in** → run `claude` once.

## 3. Smoke test (5-15 min; the first run may download the models)
```
.venv\Scripts\python.exe tests\smoke_test.py
```
It makes a synthetic song, analyses it, renders a tiny video with the web engine and Blender, composes it, runs every
check, and runs the director runner with a stand-in for Claude (no usage). Expect `[PASS]` lines and at the end
`== smoke test: N passed, 0 failed, 0 skipped`. On your PC section 1 should say `reachable` for Demucs, beat_this, the
MMS aligner, LRCLIB and Hugging Face, and section 3 `[PASS] stems with Demucs` and `[PASS] beats with beat_this`
(in the VM those were SKIPPED because the downloads were blocked).

If it fails: it keeps `projects\_smoke` and prints the failing step's output. `beats` / `downbeats` failing with
beat_this → send Claude the line (the tolerances were set on the fallback tracker). `check: spec FAIL` → open
`projects\_smoke\work\checks\report.md`. A Blender failure → run with `--no-blender` to see the rest, then step 2.

## 4. The director's sandbox (1 min, a few cents)
```
.venv\Scripts\python.exe tools\studio.py new sandbox-test
.venv\Scripts\python.exe tools\studio.py permtest sandbox-test
```
Expect three `[PASS]` lines: writes in the project allowed, writes into `tools\` refused, `curl` refused. This checks
the Windows form of Claude Code's path rules (`//c/...`), which couldn't be tested in the VM. If the `tools` line FAILs:
the director could still edit the studio's code by mistake; tell Claude (the fix is in `tools/director.py`, `rule_path`).
Delete the test project afterwards (`projects\sandbox-test`).

## 5. Benchmark (5-10 min)
```
.venv\Scripts\python.exe tools\studio.py benchmark
```
Writes `tools\speeds.json` (it replaces the VM's numbers; that's intended). Expect much lower `s/frame` than the VM's
(e.g. web 2D 1080p well under 0.2 s, three.js on the GPU, Eevee in seconds per frame or less) and `encode NVENC final`
in the hundreds of frames/s. If the web numbers are close to the VM's (~0.4-0.7 s/frame), WebGL is running in software:
see step 2.

## 6. The web app's server (5-10 min)
```
.venv\Scripts\python.exe tests\test_server.py
```
Starts the server on a free port and checks every endpoint with a stand-in for Claude, then loads each page in headless
Chromium. Expect `== server test: N passed, 0 failed`.

## 7. Ingest a real song (3-6 min)
Double-click `Music Video Studio.bat` → the browser opens http://127.0.0.1:8765 → **Start a music video** → drop an MP3,
fill in artist + title → **Save & analyse**. You should see the steps tick off and, at the end, `Done` with the tempo,
key, sections, `lyrics: N lines (lrclib)`, `stems: htdemucs_ft (cuda) ...`, `beats: beat_this final0`. Then
**The song**: press Space and check *by ear*: the beat click (K) sits on the beat, bar 0 is the first downbeat, the
lyric line lights up as it's sung, V / D / B / O solo the stems. Check the "warnings" line. Lines out of step (plain-text
lyrics are only placed roughly): **Fix the timing** in the Lyrics box, press V and 0.75×, Play, and press Enter as each
line starts (or first **Align with Whisper**, then fix the lines marked ?); **Save & analyse again** and check by ear
once more.
(Command-line form: `.venv\Scripts\python.exe tools\studio.py new my-song --song "C:\path\song.mp3"` then
`.venv\Scripts\python.exe tools\studio.py ingest my-song`.)

If it fails: the log on the page says which step. No lyrics → the song isn't on LRCLIB: paste them on the brief page
(plain text is fine) and analyse again. Words late or early → see the *Lyric repairs* in `tools\README.md`.

## 8. Story and a first director run (uses your Claude plan)
- **Story** → *Draft it with Claude* (about a minute) → read it, change what's wrong, **Save the story**.
- **Direct** → budget **30** minutes for a first try, mode `auto`: the hint under the mode should say *This budget runs
  solo (under 45 minutes, ...)*, and the *Pipeline* box goes dim. Change the budget to 90 and back: the hint switches
  to the pipeline, or (on a slow machine) says how long the pipeline's stages need here: the final render's estimate
  sets the bar, never under 45 minutes. Effort (the Solo box) `high`,
  model `opus` → **Start the director**. The run view looks as it always did for a solo run. The live
  log should start with `session started · model claude-opus-...` and show the director reading the song folder,
  writing scenes in `work\scenes`, rendering style frames and looking at them. Around the deadline you'll see
  `studio → director (5 minutes)` / `(time's up)` and at the end `delivered v1`.
- **Watch & review**: v1 plays with the song's lanes under it; the report shows what it made and the `Run settings`.
  The checks line should read spec / flash / text / sync PASS.
- If the director finishes early (`■ turn finished` with time left), the next line, `studio → director (use the time
  left...)`, should follow within a few seconds (or within 4 minutes when it says `… waiting for the critic's notes on
  the final`). Messages the studio sends while the director works (`studio → director (half)`, a critic's notes) join
  its running turn: no `■ turn finished` of their own follows them (Claude Code 2.1.311 merges them; that was
  verified). If on your version every such message is followed by a turn of its own, the run still works, but tell
  Claude: the session plumbing (`tools/mv/session.py`) assumes the merge.

If it fails: the run's files are in `projects\<p>\work\director\run-N\` (`brief.md`, `command.json`, `events.jsonl`,
`stderr.log`). `stopped` early with an error in `stderr.log` → send it to Claude. No v1 → the report says what was
done; `.venv\Scripts\python.exe tools\studio.py check <p>` on whatever is in `final\`.

## 8b. The critic (cents to a dollar)
```
.venv\Scripts\python.exe tools\studio.py critique <p> v1 --model haiku
```
Expect `the critic is looking at v1 (2-4 min)...`, then (as it goes) `the critic (haiku, effort medium) is looking at
final/music-video.mp4: 7 pictures (2-4 min)...` (an eyes look first if there was none), a short report (Verdict,
Scores, Ambition, Problems with times and a **Push:**, What works, Can't tell from the pictures), the pictures it
looked at, `critic: ambition N · song fit N · craft N · readability N · $0.0x · work/critic/music-video_NN.md` and
the last line `v1: critic: ... · final/critic_v1.md`. It should describe what's actually in your video (it read the pictures) and never claim
it heard anything. On **Watch & review** the Critic box shows the scores and the notes; **Ask the critic** (Sonnet)
does the same as a job. During a director run (step 8) the live log shows `🔍 critic on work/previews/preview_1.mp4: ...`
or `... final/music-video.mp4` and `studio → director (critic: ...)`, the run view lists the critiques, and the
report's Run settings end with `- Critic: sonnet ...: N critiques, $...`.

If it fails: `couldn't run: Claude didn't answer` (or Claude Code's own message, e.g. a usage limit or "Please run
/login") → run `claude -p "say hi" --model haiku` in PowerShell; a warning
that Claude Code doesn't know `--max-budget-usd` → update Claude Code (it ran without the $2 cap). Scores `?` → the
critic's json block didn't parse: the report is kept; send Claude the file in `work\critic\`.

## 8c. The render queue and the pipeline's commands (no Claude)
The smoke test (step 3) already runs `studio.py pipeline validate / render / assemble / gate` on its own project and
checks the render queue with a lock file of its own. By hand, on Windows:
- Open two PowerShell windows and start a render in each at the same time, e.g.
  `.venv\Scripts\python.exe tools\studio.py run <p> web-engine/render.py ..\..\tests\scenes\field.js --preview --fresh --out work/renders/q1.mp4`
  (and `q2.mp4` in the other). The second should print `waiting for the render queue: a render (<p>: ... -> work/renders/q1.mp4) since <time>`
  and start only when the first is done (`render queue: my turn after N s`).
- Start one again and close its window (or end its `python.exe` in Task Manager) halfway: the next render should say
  `render queue: took over a stale lock from a render (its process is gone)` and go on at once. If a render keeps
  waiting for a render that is gone, tell Claude (the Windows process check in `tools/mv/claude.py`, `pid_alive`), and
  delete `cache\render.lock` in the studio folder to free the queue.

## 8d. The pipeline (uses your Claude plan: a real run costs more than a solo one)
```
.venv\Scripts\python.exe tools\studio.py permtest sandbox-test --builder
```
first (on a project with a valid `work/plan.json` of two segments or more, e.g. the one from step 8 after a pipeline
run): expect four `[PASS]` lines: a builder can write in its own scene folder, and can't write into the lib, into
another segment's folder, or a project file on its deny list. This checks the Windows form of the deny rules for
project files. Then **Direct** with a budget the hint says runs the pipeline (it fits when the plan stage, the gate, a
builder round and a preview all come before the final render must start: on a fast PC about an hour for a short song;
`studio.py direct <p> --budget 60m --dry-run` prints which mode a budget gets and why), or
`.venv\Scripts\python.exe tools\studio.py direct <p> --budget 60m --mode pipeline` (forced: if the plan's final render
leaves no room for the stages, the live log says `switching this run to solo` with the estimate). The live log should show the
stages: `▸ stage 1. plan`, the planner writing `work/plan.json` and rendering the hero (`pipeline render ... --only
s3`), `▸ stage gate 1: the language` with `🔍 critic on work/segments/<hero>/..._preview.mp4 (gate)` and `studio →
planner (gate 1: ...)`, then `▸ stage 2. build` with `studio → planner (builders started)`, the builders' lines
(`🔨 builder s1: built`), `▸ stage 3. assemble` (a `work/previews/preview_N.mp4`), `studio → planner (assemble report
(round 1))`, `▸ stage 4. revise`, and at the end `▸ stage 5. final` (`the planner set the stage to final`, or `time is
short` when the studio starts it) and `delivered vN`. `work\pipeline\state.json` has every session with its cost;
`work\pipeline\sessions\b1-s1\` a builder's brief, events and its own `settings.json`. In Task Manager while it
builds: one `claude` per builder plus the planner's, and only one render at a time (`waiting for the render queue`
in a builder's `render_preview.log`). Stop during the build: within ~2 minutes every `claude` process is gone and the
run says `stopped`. The report's Run settings list the pipeline, the stages and the costs by role.

On the **Direct** page during that run (start it from the page: budget 60, mode `auto` or `pipeline`; the Pipeline
box shows the planner's effort `max`, 3 builders, builder model empty = the planner's, effort `high`):
- the header reads `first · pipeline · planner claude-opus-... max · 3 builders opus high · critic sonnet · budget 1 h 00 min`;
- the stage bar lights up in turn (Plan → Gate → Build → Assemble → Revise (round 1) → Final), the current stage in
  yellow with `since HH:MM` and its deadline, the finished ones green with how long they took;
- the coloured strip shows the plan's segments over the song (★ = the hero, `b` = a builder's; yellow while a builder
  works on it, green when built, red when failed), with a ▶ link to each segment's preview;
- a card per session: the planner, then each builder with its state (`working`, `rendering`, `critic`, `revising`,
  `built`), model, effort, turns, cost, its last lines, links to its brief and messages, and **log**, which opens its
  full log and keeps it open (and scrolled) while the page refreshes every 2 s;
- *The critic's reports*: gate 1 (with the style frames), one per builder, one per assembled preview (with its
  flash and text checks), the final (with every check); **read** opens the report under the row;
- the costs: planner, builders, critic and the total, matching the report's Run settings at the end.
The page should never jump while it refreshes, and the browser's console (F12) should show no errors.

## 8e. An interactive pipeline (uses your Claude plan)
In Claude Code in the studio folder: `/musicvideo <p>` on a fresh project, and ask it to make the video with the
pipeline. It should write `work/plan.json` and run `pipeline validate`, render the hero with `pipeline render ...
--only <hero>`, run `pipeline gate` and read the critic's report, then start `pipeline build` **in the background**
(it doesn't sit and wait in the foreground) and build its own segments meanwhile. The Direct page shows a *Your
pipeline* box with the builders' cards while they work (its **Stop the builders** stops them within ~2 minutes; the
build then prints `stopped by the user`). It should not start workflow agents to build segments (the
skill says segments are built only by `pipeline build`). Then `pipeline assemble <p> --preview` (the critic's report
on the whole film comes back), `pipeline assemble <p> --final` and `deliver <p> --critic`.
- Do this one right after the autonomous pipeline run of 8d, with the Direct page open: from the first `pipeline
  gate` on, the page shows the *Your pipeline* box (gate / build / assemble, `running`, then `done`) **and** still the
  8d run's own view above it (its stage bar and cards: kept in `work\pipeline\history\state_run-<n>.json`).
- The Windows file handling while the page polls every 2 s: during the whole 8d run and this one, no `⚠ the studio hit
  an error` line in the live log and no `work\director\run-N\runner_errors.log` (a `PermissionError` there means a
  status file couldn't be replaced while the page read it, even after the retries); `work\pipeline\state.json.lock`
  is gone once nothing runs.
- Close the console of a running `studio.py direct` (the hard way), wait 3 minutes, then `studio.py clock <p>`: it
  says the run was cut off, and `pipeline build <p> --only <id>` goes on (with a note) instead of refusing.

## 9. A review round
On **Watch & review**, play, press **N** at a moment and write a note (e.g. "the chorus should hit harder") → **Direct**
→ the "Review round" box is ticked → budget 20 min → Start. Expect `v2` next to `v1` (both play), your note ticked with
the director's one-line fix under it, and a "Round 2" section at the top of the report.

## 10. By hand in your browser
- The Song page: zoom chips, scroll on the lanes, click a section row, the beat click at 0.5×.
- Watch page: the lanes follow the video, clicking a note's time seeks, N works while the video plays.
- Stop: start a director run and press **Stop it** after a minute: within ~2 minutes the run shows `stopped`
  (`stopped by the user`), and nothing is half-written in `final\`.
