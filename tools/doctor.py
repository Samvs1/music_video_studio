"""Check the install and say how to fix what's missing (python tools/studio.py doctor).

Checks: Python, the venv, the Python packages, FFmpeg + NVENC, the GPU / CUDA for the audio models, Node (optional),
a Chromium the web engine can drive (and whether WebGL runs on the GPU or in software), Blender, Claude Code (and
its login), the song-analysis model downloads, disk space. Install hints use winget on Windows and apt on Linux.
Nothing is installed or downloaded by this check.
"""
import json, os, platform, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STUDIO = os.path.dirname(HERE)
WIN = os.name == "nt"

RESULTS = []


def line(name, good, detail="", fix="", optional=False):
    tag = "ok" if good else ("--" if optional else "!!")
    RESULTS.append({"name": name, "ok": bool(good), "optional": optional, "detail": detail, "fix": fix})
    print(f"  [{tag}] {name}" + (f": {detail}" if detail else ""))
    if not good and fix:
        print(f"       -> {fix}")


def hint(win, linux):
    return win if WIN else linux


def main():
    print(f"Music Video Studio at {STUDIO}  ({platform.system()} {platform.release()}, {platform.machine()})")
    v = sys.version_info
    line("Python 3.11-3.13", (3, 11) <= v[:2] <= (3, 13), sys.version.split()[0],
         hint("winget install -e --id Python.Python.3.13", "sudo apt install python3.12-venv (or python3.13)"))
    venv = os.path.join(STUDIO, ".venv")
    in_venv = os.path.abspath(sys.prefix).startswith(os.path.abspath(venv))
    line("studio venv (.venv)", os.path.isdir(venv) and in_venv, sys.prefix if in_venv else ("exists, not in use" if os.path.isdir(venv) else "missing"),
         hint("powershell -ExecutionPolicy Bypass -File setup.ps1", "bash setup.sh"))
    for mod, pkg in (("numpy", "numpy"), ("scipy", "scipy"), ("librosa", "librosa"), ("soundfile", "soundfile"),
                     ("matplotlib", "matplotlib"), ("PIL", "pillow"), ("playwright", "playwright"), ("torch", "torch"),
                     ("torchaudio", "torchaudio"), ("demucs", "demucs"), ("beat_this", "beat-this"), ("torchcrepe", "torchcrepe"),
                     ("transformers", "transformers")):
        try:
            m = __import__(mod)
            line(f"module {mod}", True, getattr(m, "__version__", ""))
        except Exception as e:
            line(f"module {mod}", False, str(e)[:80], "pip install -r requirements.txt (inside the venv)")
    # ffmpeg
    from mv.common import ffmpeg, has_nvenc
    ff = shutil.which("ffmpeg")
    ver = ""
    if ff:
        ver = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.splitlines()[0][:60]
    line("FFmpeg", ff, ver or "missing", hint("winget install -e --id Gyan.FFmpeg", "sudo apt install ffmpeg"))
    if ff:
        enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        line("FFmpeg libx264", "libx264" in enc, "", hint("winget install -e --id Gyan.FFmpeg (the full build)", "sudo apt install ffmpeg"))
        nv = has_nvenc()
        line("NVENC (GPU video encoder)", nv, "h264_nvenc works" if nv else "not available: encodes use libx264 on the CPU (slower, same result)",
             "an NVIDIA GPU + a current driver + an FFmpeg build with NVENC (Gyan.FFmpeg has it)", optional=True)
    # GPU for the models
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            line("GPU for the audio models (CUDA)", True, f"{p.name}, {p.total_memory / 2 ** 30:.0f} GB, torch {torch.__version__}")
        else:
            line("GPU for the audio models (CUDA)", False, f"torch {torch.__version__} has no CUDA: the models run on the CPU (slower)",
                 hint("setup.ps1 installs the CUDA build when nvidia-smi works", "pip install torch --index-url https://download.pytorch.org/whl/cu126"),
                 optional=True)
    except Exception as e:
        line("torch", False, str(e)[:80])
    # node (optional)
    node = shutil.which("node")
    nv_ = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else ""
    line("Node.js (optional: the web engine runs from Python)", node, nv_ or "not installed",
         hint("winget install -e --id OpenJS.NodeJS.LTS", "sudo apt install nodejs"), optional=True)
    # browser for the web engine
    try:
        from mv.web import probe_browser
        info = probe_browser("auto")
        line("Chromium for the web engine", True, f"{info['browser']} {info['version']}")
        line("WebGL on the GPU", info["gpu"], f"WebGL renderer: {info['webgl']}" +
             ("" if info["gpu"] else " (software: three.js scenes render slowly; 2D is fine)"),
             "a GPU and Chrome/Edge (Playwright's Chromium may run in software)", optional=True)
    except Exception as e:
        line("Chromium for the web engine", False, str(e).splitlines()[0][:120],
             hint("python -m playwright install chromium   (or install Chrome; Edge also works)",
                  "python -m playwright install chromium   (or sudo apt install chromium)"))
    # blender
    sys.path.insert(0, os.path.join(HERE, "blender"))
    from run import find_blender, blender_version, has_nvidia
    b = find_blender()
    line("Blender (optional 3D renderer)", b, f"{blender_version(b)} at {b}" if b else "not found",
         hint("winget install -e --id BlenderFoundation.Blender   (or set MV_BLENDER)", "sudo apt install blender xvfb"),
         optional=True)
    if b and not WIN and not os.environ.get("DISPLAY"):
        line("Xvfb (Eevee / Workbench without a display)", shutil.which("xvfb-run"), "", "sudo apt install xvfb", optional=True)
    if b:
        line("Blender engine on this machine", True, "Eevee on the NVIDIA GPU" if has_nvidia() else "Cycles on the CPU (no NVIDIA GPU found)")
    # claude
    cl = shutil.which("claude")
    cver, logged = "", None
    if cl:
        try:
            cver = subprocess.run([cl, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
            st = subprocess.run([cl, "auth", "status"], capture_output=True, text=True, timeout=30)
            logged = json.loads(st.stdout).get("loggedIn") if st.stdout.strip().startswith("{") else None
        except Exception:
            pass
    line("Claude Code (the director)", cl, f"{cver} at {cl}" if cl else "missing",
         hint("irm https://claude.ai/install.ps1 | iex   (then run `claude` once to log in)", "curl -fsSL https://claude.ai/install.sh | bash"))
    if cl:
        line("Claude Code logged in", logged is not False, "yes" if logged else "unknown" if logged is None else "no",
             "run `claude` once in this folder and log in")
    # models
    from pathlib import Path
    home = Path.home()
    hub = Path(os.environ.get("TORCH_HOME", home / ".cache" / "torch")) / "hub" / "checkpoints"
    demucs = list(hub.glob("*.th")) if hub.exists() else []
    line("Demucs weights (stems)", bool(demucs), f"{len(demucs)} files in {hub}" if demucs else
         "not downloaded yet: the first ingest downloads them (~80-320 MB from dl.fbaipublicfiles.com)", optional=True)
    bt = list((home / ".cache" / "torch" / "hub" / "checkpoints").glob("*final0*")) + list(home.glob(".cache/beat_this/**/*final0*"))
    line("beat_this checkpoint (beats)", bool(bt), "cached" if bt else "downloads on first ingest (~80 MB from cloud.cp.jku.at)", optional=True)
    du = shutil.disk_usage(STUDIO)
    line("free disk", du.free > 15e9, f"{du.free / 1e9:.0f} GB (models ~2-4 GB once; each project ~1-3 GB of stems, renders and caches)",
         "free some space", optional=du.free > 5e9)
    bad = [r for r in RESULTS if not r["ok"] and not r["optional"]]
    print("all good" if not bad else f"{len(bad)} thing(s) to fix: the !! lines above")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
