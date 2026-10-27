#!/usr/bin/env bash
# Music Video Studio setup for Linux (and macOS). Asks before every big download; --yes says yes to everything.
#   bash setup.sh [--yes]
set -euo pipefail
cd "$(dirname "$0")"
YES=0; [[ "${1:-}" == "--yes" ]] && YES=1
ask() { if [[ $YES == 1 ]]; then return 0; fi; read -r -p "$1 [y/N] " a; [[ "${a,,}" == y* ]]; }
step() { printf '\n== %s\n' "$1"; }

step "Python 3.11-3.13"
PY=""
for v in 3.13 3.12 3.11; do command -v "python$v" >/dev/null && { PY="python$v"; break; }; done
[[ -z "$PY" ]] && { echo "no Python 3.11-3.13: sudo apt install python3.12 python3.12-venv"; exit 1; }
echo "using $PY"

step "System tools"
for t in ffmpeg; do command -v $t >/dev/null || { echo "missing $t: sudo apt install ffmpeg"; exit 1; }; done
command -v blender >/dev/null && echo "Blender: $(blender --version 2>/dev/null | head -1)" || echo "Blender not found (optional): sudo apt install blender xvfb"
command -v xvfb-run >/dev/null || echo "xvfb-run not found (optional, for Blender Eevee/Workbench without a display): sudo apt install xvfb"

step "Python packages"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
if command -v nvidia-smi >/dev/null; then IDX="https://download.pytorch.org/whl/cu126"; else IDX="https://download.pytorch.org/whl/cpu"; fi
.venv/bin/python -m pip install -r requirements.txt --extra-index-url "$IDX"

step "A browser for the web engine"
if .venv/bin/python -c "import sys; sys.path.insert(0, 'tools'); from mv.web import probe_browser; print(probe_browser())" 2>/dev/null; then
  echo "found a Chromium the web engine can use"
elif ask "Install Playwright's own Chromium (~150 MB)?"; then
  .venv/bin/python -m playwright install --with-deps chromium
fi

step "Song-analysis models"
if ask "Download the song-analysis models now (~1.7 GB)? Otherwise the first analysis downloads them."; then
  .venv/bin/python tools/models.py fetch || echo "some downloads failed: ingest falls back to estimated stems / beats"
fi

step "Claude Code (the director)"
command -v claude >/dev/null && claude --version || echo "install: curl -fsSL https://claude.ai/install.sh | bash   (then run 'claude' once to log in)"

step "Check"
.venv/bin/python tools/studio.py doctor || true
echo; echo "Done. Start the studio: .venv/bin/python tools/studio.py serve   (then measure render speeds: .venv/bin/python tools/studio.py benchmark)"
