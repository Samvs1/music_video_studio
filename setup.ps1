# Music Video Studio setup for Windows 10/11.
# Double-click "Music Video Studio.bat" (it runs this on first start), or run:
#   powershell -ExecutionPolicy Bypass -File setup.ps1          (asks before every big download)
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Yes     (yes to everything)
param([switch]$Yes)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Yellow }
function Ask($q, $default) {
    if ($Yes) { return $true }
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $a = Read-Host "$q $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim().ToLower().StartsWith("y")
}
function RefreshPath { $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User") }

Step "Python 3.11-3.13"
$py = $null
foreach ($v in @("3.13", "3.12", "3.11")) {
    if (-not $py -and (Have "py")) { & py "-$v" --version *> $null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v") } }
}
if (-not $py -and (Have "python")) { $ver = (& python --version 2>&1) -join ""; if ($ver -match "3\.1[1-3]") { $py = @("python") } }
if (-not $py) {
    Write-Host "No Python 3.11-3.13 found. Installing Python 3.13 with winget..."
    winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
    RefreshPath
    $py = @("py", "-3.13")
}
Write-Host ("using: " + ($py -join " "))

Step "FFmpeg"
if (Have "ffmpeg") { Write-Host "FFmpeg found: $((& ffmpeg -version | Select-Object -First 1))" } else {
    Write-Host "FFmpeg is needed (encoding, the checks)."
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    RefreshPath
}

Step "Python packages (first time: ~3-5 GB, 5-15 minutes)"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $rest = @($py | Select-Object -Skip 1)
    & $py[0] @rest -m venv .venv
}
& .venv\Scripts\python.exe -m pip install --upgrade pip
# PyTorch: the CUDA 12.6 build when there is an NVIDIA card (stems, beats, pitch, lyrics run on the GPU), else CPU
if (Have "nvidia-smi") { $torchIndex = "https://download.pytorch.org/whl/cu126"; Write-Host "NVIDIA GPU found: CUDA build of PyTorch (~2.6 GB)" }
else { $torchIndex = "https://download.pytorch.org/whl/cpu"; Write-Host "no NVIDIA GPU: CPU build of PyTorch" }
& .venv\Scripts\python.exe -m pip install -r requirements.txt --extra-index-url $torchIndex
if ($LASTEXITCODE -ne 0) { throw "pip install failed (see above)" }

Step "A browser for the web engine"
$edge = Test-Path "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$chrome = (Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe") -or (Test-Path "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
if ($chrome) { Write-Host "Chrome found: the web engine uses it (with the GPU)." }
elseif ($edge) { Write-Host "Microsoft Edge found: the web engine can use it." }
$wantPw = if ($chrome -or $edge) { $false } else { $true }
if (Ask "Install Playwright's own Chromium for the web engine (~150 MB)? Chrome or Edge also work." $wantPw) {
    & .venv\Scripts\python.exe -m playwright install chromium
}

Step "Blender (optional 3D renderer)"
$blender = (Have "blender") -or (Test-Path "$env:ProgramFiles\Blender Foundation")
if ($blender) { Write-Host "Blender found." }
elseif (Ask "Install Blender with winget (~400 MB)? The director can make 3D with it; three.js works without it." $false) {
    winget install -e --id BlenderFoundation.Blender --accept-package-agreements --accept-source-agreements
}

Step "Song-analysis models"
if (Ask "Download the song-analysis models now (Demucs, beat_this, the lyric aligner: ~1.7 GB)? Otherwise the first analysis downloads them." $true) {
    & .venv\Scripts\python.exe tools\models.py fetch
}

Step "Claude Code (the director)"
if (Have "claude") { Write-Host "Claude Code found: $((& claude --version))" } else {
    Write-Host "Claude Code is not installed. It directs the videos (you need a Claude plan)." -ForegroundColor Cyan
    Write-Host "Install it with:   irm https://claude.ai/install.ps1 | iex"
    Write-Host "then run 'claude' once in this folder to log in."
}

Step "Check"
& .venv\Scripts\python.exe tools\studio.py doctor
Write-Host ""
Write-Host "Done. Start the studio with 'Music Video Studio.bat'. Measure this PC's render speed once: .venv\Scripts\python.exe tools\studio.py benchmark" -ForegroundColor Green
