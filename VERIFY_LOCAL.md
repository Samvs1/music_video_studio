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
- **Direct** → budget **30** minutes for a first try, effort `high`, model `opus` → **Start the director**. The live
  log should start with `session started · model claude-opus-...` and show the director reading the song folder,
  writing scenes in `work\scenes`, rendering style frames and looking at them. Around the deadline you'll see
  `studio → director (5 minutes)` / `(time's up)` and at the end `delivered v1`.
- **Watch & review**: v1 plays with the song's lanes under it; the report shows what it made and the `Run settings`.
  The checks line should read spec / flash / text / sync PASS.
- If the director finishes early (`■ turn finished` with time left), the next line, `studio → director (use the time
  left...)`, should follow within seconds (or within 4 minutes when it says `… waiting for the critic's notes on the
  final`). If it comes only about 90 s after the director went quiet, every time, tell Claude: the runner's guess
  about how Claude Code answers messages sent mid-turn is wrong on your version.

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

## 9. A review round
On **Watch & review**, play, press **N** at a moment and write a note (e.g. "the chorus should hit harder") → **Direct**
→ the "Review round" box is ticked → budget 20 min → Start. Expect `v2` next to `v1` (both play), your note ticked with
the director's one-line fix under it, and a "Round 2" section at the top of the report.

## 10. By hand in your browser
- The Song page: zoom chips, scroll on the lanes, click a section row, the beat click at 0.5×.
- Watch page: the lanes follow the video, clicking a note's time seeks, N works while the video plays.
- Stop: start a director run and press **Stop it** after a minute: within ~2 minutes the run shows `stopped`
  (`stopped by the user`), and nothing is half-written in `final\`.
