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

## 4. The director's sandbox (1-2 min, a few cents)
```
.venv\Scripts\python.exe tools\studio.py new sandbox-test
.venv\Scripts\python.exe tools\studio.py permtest sandbox-test
```
Expect six `[PASS]` lines: the director's writes in the project allowed, its writes into `tools\` refused, `curl`
refused, the director can start an agent, that agent's writes in the project allowed and its writes into `tools\`
refused (agents share the director's sandbox). This checks the Windows form of Claude Code's path rules (`//c/...`),
which couldn't be tested in the VM. If a `tools` line FAILs: the director (or its agents) could still edit the
studio's code by mistake; tell Claude (the fix is in `tools/director.py`, `rule_path`). If only the agent lines FAIL,
send Claude the agent's lines from the output (and `work\director\permtest\events.jsonl`).
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
- **Direct** → the form has one director: budget **30** minutes for a first try, model `opus`, effort `max` (the
  default), critic `sonnet`, **Ultracode** unticked (the default; its note says *more tokens*), cost cap empty →
  **Start the director**. The live log should start with `session started · model claude-opus-...` and show the
  director reading the song folder, writing scenes in `work\scenes`, rendering style frames and looking at them. Under
  the log: *Agents: none so far (whether to deploy any is the director's call)*, or the agents it started (see 8d),
  the critic's notes as they come, and the costs (they update at the end of each of the director's turns, so a long
  first turn shows $0.00 for a while). Around the deadline you'll see `studio → director (5 minutes)` / `(time's
  up)` and at the end `delivered v1`.
- **Watch & review**: v1 plays with the song's lanes under it; the report shows what it made and the `Run settings`
  (the model and its cost, `- Agents: ...`, `ultracode off`). The checks line should read spec / flash / text / sync
  PASS.
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

## 8c. The render queue and the plan tools (no Claude)
The smoke test (step 3) already runs `studio.py plan validate / render / assemble` on its own project and checks the
render queue with a lock file of its own. By hand, on Windows:
- Open two PowerShell windows and start a render in each at the same time, e.g.
  `.venv\Scripts\python.exe tools\studio.py run <p> web-engine/render.py ..\..\tests\scenes\field.js --preview --fresh --out work/renders/q1.mp4`
  (and `q2.mp4` in the other). The second should print `waiting for the render queue: a render (<p>: ... -> work/renders/q1.mp4) since <time>`
  and start only when the first is done (`render queue: my turn after N s`).
- Start one again and close its window (or end its `python.exe` in Task Manager) halfway: the next render should say
  `render queue: took over a stale lock from a render (its process is gone)` and go on at once. If a render keeps
  waiting for a render that is gone, tell Claude (the Windows process check in `tools/mv/claude.py`, `pid_alive`), and
  delete `cache\render.lock` in the studio folder to free the queue.

## 8d. A run where the director deploys an agent, with ultracode on (uses your Claude plan)
Agents are the director's own choice, so to see one for sure, ask for it in the run's notes. **Direct** → budget
**60**, model `opus`, effort `max`, critic `sonnet`, tick **Ultracode**, cost cap empty, and in *Notes for this run*:
`Deploy at least one agent (the Agent tool, model haiku, in the background) for a part you can hand off, e.g. one
scene built from your shared code, or the re-renders.` → **Start the director**. While it runs:
- the header reads `first · claude-opus-... · effort max · ultracode · budget 1 h 00 min · critic sonnet`, and
  `work\director\run-N\command.json` has `Agent`, `TaskStop` and `Workflow` after `--allowedTools` (and no `SendMessage`)
  (`brief.md` ends with the line *Ultracode is on for this run...*);
- the live log shows `⚙ Agent: <what it's for> (haiku)`, then `🤖 agent: <what it's for> (haiku) started`, now and
  then an indented `↳ <what it's for>: ⚙ Bash: ...` line (at most one per agent every 20 s), and `🤖 agent: ... done in
  N min, M tool calls`. If the director had ended its turn meanwhile (`■ turn finished`), the next line is `↻ the
  director started a turn of its own (its agent finished)`, with no `studio → director (no final yet)` or `(use the
  time left)` in between: the studio waits while an agent works;
- the **Agents** list under the log: one row per agent, `running` (in yellow, with how long and the tool it's on)
  and then `done` with its duration and tool calls;
- **Costs**: one entry per model, e.g. `opus-4-x $1.20 the director`, `haiku-4-5 $0.04 agents` (maybe a small
  unlabelled one: Claude Code's own quick model), `the critic sonnet $0.30`, and `total` = their sum. The costs update
  when one of the director's turns ends; the total in the header is the same. In Task Manager only one render runs at
  a time (an agent's render prints `waiting for the render queue` while the director's runs).
- At the end the report's Run settings list `- Costs per model: ... (the director), ... (agents) · the critic sonnet
  ... · total ...`, `- Agents: N (haiku N): ...` and `ultracode on`; the total should match the run view's.
- If the director uses the **Workflow** tool, the live log shows `⚙ Workflow: ...` and the Agents list has a row
  `a workflow`: tell Claude what the log and the list showed (a workflow's own agents weren't seen on the real CLI
  yet). Whether it uses one is its call.
- **Stop** while an agent works: within ~2 minutes the run says `stopped`, and no `claude` process is left in Task
  Manager.
- The cost cap, cheaply: a second run with model `haiku`, budget 20, cost cap `0.5`: it should end early with
  `the cost cap ($0.5) was reached` (the header says `cap $0.5 (reached)`), and the report's Run settings say
  `cost cap $0.5 (reached: the run ended there)`.
- The Windows file handling while the page polls every 2 s: during these runs no `⚠ the studio hit an error` line in
  the live log and no `work\director\run-N\runner_errors.log` (a `PermissionError` there means a status file couldn't
  be replaced while the page read it, even after the retries).
- Close the console of a running `studio.py direct` (the hard way), wait 3 minutes, then `studio.py clock <p>`: it
  says the run was cut off.
The page should never jump while it refreshes, and the browser's console (F12) should show no errors.

## 8e. An interactive run (uses your Claude plan)
In Claude Code in the studio folder: `/musicvideo <p>` on a fresh project, and ask it to make the video. It should
work from the skill on its own: read the song once and write `work/song-notes.md`, the concept, style frames, then
build; run long renders, `plan assemble` and `studio.py critique` **in the background** (it doesn't sit and wait in
the foreground, nor poll with sleep loops), read the critic's report before opening any pictures itself, and finish
with `studio.py check` and `deliver <p> --critic`. If it deploys agents (its call; in ultracode it may run workflows),
each should get its own `model` and a narrow brief that names `work/concept.md` and `work/song-notes.md` (not "read
the song folder"), and the looking should go to the critic, not to reviewer agents. Tell Claude if it does otherwise.

## 8f. Live preview: the scenes in your browser with the song (Edge and Chrome on Windows)
The live view could only be tested in a headless browser in the VM, without sound. On your PC, with a project that has
scenes (after step 8 or 8d):
- **Watch & review → ▶ live** (the default while a project has no video yet). Press **Space**: the song plays and the
  picture moves with it. Under the picture there is a line per layer, e.g. `s2 · work/scenes/s2/scene.js ·
  0:24.2–0:48.5 · 30 of 30 fps · 6 ms a frame`. Check **by ear** (K for the beat click): a hit you see lands on the beat
  you hear, the lyric type comes in on its word. Click the lanes to jump, ← → for a bar: the picture follows at once (a
  stateful scene first says `catching up N%`). Pause holds the frame (a stateful scene doesn't start again: no `starting
  again from its setup`), and so do N and a switch of stems (V / D / B / O / M); 0.75× still matches. Do this in
  **Edge** and in **Chrome**. If the picture is ahead of or behind the speakers' sound, tell Claude by how much.
- **Bluetooth headphones** play the sound late, so the picture runs ahead: raise **sound delay** (try 200 ms) until a
  kick and its flash meet. Reload the page: the value is kept (it is per browser).
- **Size** ¼ / ½ / full: the fps rises at ¼. A heavy scene at full size may say `slower than real time here: frames
  drop, the timing holds`: frames are skipped, but what you see is still where the song is.
- **During a run** (step 8 or 8d): Direct → **▶ play the scenes live**. While the director writes scenes the picker lists
  its plan or its renders; a scene that changes reloads itself within ~5 s (a toast says so). Press **N**, write a note,
  save: the toast says the director gets it within seconds. Within ~5 s the Direct page's live log shows `🎧 the user's
  live note to the director: ...` and `✉ studio → director (the user's live note (1))` (also while one of its agents
  works); the note then shows `sent to the director (run N)` and, once the director has dealt with it, `fixed: ...`
  under it.
- F12 → Console: no errors (a scene's own error shows in red on its layer's line instead). When paused, the live view
  uses little CPU (Task Manager: the browser well under one core).

## 9. A review round
On **Watch & review**, play, press **N** at a moment and write a note (e.g. "the chorus should hit harder") → **Direct**
→ the "Review round" box is ticked → budget 20 min → Start. Expect `v2` next to `v1` (both play), your note ticked with
the director's one-line fix under it, and a "Round 2" section at the top of the report.

## 10. By hand in your browser
- The Song page: zoom chips, scroll on the lanes, click a section row, the beat click at 0.5×.
- Watch page: the lanes follow the video, clicking a note's time seeks, N works while the video plays.
- Stop: start a director run and press **Stop it** after a minute: within ~2 minutes the run shows `stopped`
  (`stopped by the user`), and nothing is half-written in `final\`.
