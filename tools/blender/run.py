"""Render a Blender scene script headless, with the song data available (see tools/blender/README.md).

  python tools/studio.py run <project> blender/run.py <scene.py> --out work/renders/stage
        [--from 12 --to 30 | --frames 360:900 | --section "chorus 1"]   default: the whole song
        [--engine auto|eevee|cycles|workbench] [--samples N]           auto: Eevee with an NVIDIA GPU, else Cycles (CPU)
        [--preview | --scale 0.5] [--transparent] [--workers N] [--still 12.5,40] [--fresh]

--out: a folder (PNG frames frame_000123.png, numbered by song frame; the default) or a .mp4 (frames, then H.264).
Re-running continues where it stopped: Blender skips frames that already exist (--fresh starts over).
Eevee and Workbench need OpenGL: on a Linux box without a display they run under xvfb-run automatically.
Writes <out>.mv.json (frame range, engine, timings, the text the scene declared with mv.text()).
Video renders wait their turn in the machine's render queue (mv/renderlock.py: one render at a time; MV_RENDER_WHO
names it while it renders, MV_RENDER_LOCK=0 turns the queue off); --still doesn't wait.
"""
import argparse, glob, json, os, platform, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
from mv.common import ROOT, delivery, jload, jsave, need_project, ffmpeg, h264_args  # noqa: E402
from mv.renderlock import render_queue  # noqa: E402


def find_blender():
    """Path to blender (MV_BLENDER, PATH, the usual install folders) or None."""
    if os.environ.get("MV_BLENDER") and os.path.exists(os.environ["MV_BLENDER"]):
        return os.environ["MV_BLENDER"]
    p = shutil.which("blender")
    if p:
        return p
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", "")):
            cands += glob.glob(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
        cands += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Blender*", "blender.exe"))
        steam = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
        if os.path.exists(steam):
            cands.append(steam)
    elif platform.system() == "Darwin":
        cands.append("/Applications/Blender.app/Contents/MacOS/Blender")
    else:
        cands += glob.glob("/opt/blender*/blender") + glob.glob(os.path.expanduser("~/blender*/blender"))
    cands = [c for c in cands if os.path.exists(c)]
    return sorted(cands)[-1] if cands else None


def blender_version(exe):
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        return r.stdout.splitlines()[0].strip()
    except Exception:
        return None


def has_nvidia():
    return bool(shutil.which("nvidia-smi")) and subprocess.run(["nvidia-smi", "-L"], capture_output=True).returncode == 0


def needs_xvfb(engine):
    return platform.system() == "Linux" and engine in ("eevee", "workbench") and not os.environ.get("DISPLAY") \
        and shutil.which("xvfb-run")


def run_blender(exe, scene, args, engine, log=print):
    cmd = [exe, "-b", "--factory-startup", "-noaudio", "--python", os.path.join(HERE, "mvblend.py"), "--", json.dumps(args)]
    if needs_xvfb(engine):
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-40:]
        if line.startswith(("MVBLEND", "Traceback", "Error", "  File")) or "Error:" in line or line.startswith("Saved:"):
            if not line.startswith("Saved:") or args.get("verbose"):
                log("   " + line)
    p.wait()
    if p.returncode:
        raise RuntimeError("blender failed:\n" + "\n".join(tail[-25:]))


def render(scene, out, project=None, frames=None, engine="auto", samples=None, scale=1.0, preview=False, transparent=False,
           workers=1, stills=None, fresh=False, seed=1, log=print):
    project = os.path.abspath(project or ROOT)
    exe = find_blender()
    if not exe:
        raise SystemExit("Blender not found: install it (Windows: winget install BlenderFoundation.Blender; Linux: "
                         "sudo apt install blender) or set MV_BLENDER to blender(.exe)")
    d = delivery(jload(os.path.join(project, "project.json"), {}))
    fps = d["fps"]
    w, h = int(round(d["width"] * scale / 2)) * 2, int(round(d["height"] * scale / 2)) * 2
    fm = jload(os.path.join(project, "song", "frames.json"), None)
    if fm is None:
        raise SystemExit("song/frames.json is missing: run ingest first")
    n_total = int(round(fm["duration"] * fps))
    a, b = frames or (0, n_total)
    a, b = max(0, a), min(n_total, b)
    if engine == "auto":
        engine = "eevee" if has_nvidia() else "cycles"
    out = os.path.abspath(out)
    to_mp4 = out.lower().endswith(".mp4")
    out_dir = out[:-4] + ".frames" if to_mp4 else out
    if fresh:
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    base = {"tools": TOOLS, "project": project, "scene": os.path.abspath(scene), "w": w, "h": h, "fps": fps, "seed": seed,
            "engine": engine, "samples": samples, "transparent": transparent, "out_dir": out_dir, "preview": preview}
    import hashlib
    key = hashlib.sha1(open(scene, "rb").read() + json.dumps({k: v for k, v in base.items() if k != "out_dir"}, sort_keys=True).encode()
                       + open(os.path.join(HERE, "mvblend.py"), "rb").read()).hexdigest()[:16]
    kp = os.path.join(out_dir, "_scene_key.txt")
    if os.path.exists(kp) and open(kp).read().strip() != key:     # the scene or the settings changed: old frames are stale
        for f in glob.glob(os.path.join(out_dir, "frame_*.png")) + glob.glob(os.path.join(out_dir, "_meta_*.json")):
            os.remove(f)
    open(kp, "w").write(key)
    t0 = time.time()
    if stills:
        fr = [int(round(t * fps)) for t in stills]
        run_blender(exe, scene, dict(base, f0=min(fr), f1=max(fr) + 1, stills=fr), engine, log)
        log(f"   stills -> {out_dir}")
        return [os.path.join(out_dir, f"still_{f:06d}.png") for f in fr]
    missing = [f for f in range(a, b) if not os.path.exists(os.path.join(out_dir, f"frame_{f:06d}.png"))]
    log(f"blender ({engine}): {os.path.relpath(scene, project)} frames {a}-{b} at {w}x{h} {fps} fps; "
        f"{(b - a) - len(missing)} frames already there (resume)")
    for f in range(a, b):                          # a crash can leave empty placeholders behind: remake those
        p = os.path.join(out_dir, f"frame_{f:06d}.png")
        if os.path.exists(p) and os.path.getsize(p) == 0:
            os.remove(p)
    if missing:
        nw = max(1, min(workers or 1, len(missing)))
        parts = [(a + k * (b - a) // nw, a + (k + 1) * (b - a) // nw) for k in range(nw)]
        if nw == 1:
            run_blender(exe, scene, dict(base, f0=a, f1=b), engine, log)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(nw) as ex:
                list(ex.map(lambda p: run_blender(exe, scene, dict(base, f0=p[0], f1=p[1]), engine, log), parts))
    el = time.time() - t0
    left = [f for f in range(a, b) if not os.path.exists(os.path.join(out_dir, f"frame_{f:06d}.png"))]
    if left:
        raise RuntimeError(f"{len(left)} frames missing after the render (first: {left[0]})")
    if missing:
        log(f"   rendered {len(missing)} frames in {el:.1f} s ({el / len(missing):.3f} s/frame)")
    metas = [jload(m, {}) for m in glob.glob(os.path.join(out_dir, "_meta_*.json"))]
    texts = []
    for m in metas:
        for t in m.get("texts", []):
            if t not in texts:
                texts.append(t)
    if to_mp4:
        r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-framerate", str(fps), "-start_number", str(a), "-i",
                            os.path.join(out_dir, "frame_%06d.png"), "-frames:v", str(b - a), *h264_args("preview" if preview else "final"),
                            "-r", str(fps), "-movflags", "+faststart", out])
        if r.returncode:
            raise RuntimeError("could not encode the frames")
    side = {"scene": os.path.relpath(scene, project).replace("\\", "/"), "range": [a, b], "fps": fps, "w": w, "h": h,
            "t0": round(a / fps, 4), "t1": round(b / fps, 4), "transparent": transparent,
            "format": "mp4" if to_mp4 else "png", "frames_dir": out_dir, "engine": (metas[0] if metas else {}).get("engine", engine),
            "blender": blender_version(exe), "texts": [dict(t, space=[w, h], f0=int(round(t["t0"] * fps)), f1=int(round(t["t1"] * fps)) - 1,
                                                             boxes=[[int(round(t["t0"] * fps))] + list(t["box"])]) for t in texts],
            "render_seconds": round(el, 1), "rendered_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    jsave(out.rstrip("/\\") + ".mv.json", side)
    log(f"   -> {out}")
    return side


def shown(path):
    """A path as the render queue shows it: relative to the project when it can be."""
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:                             # another drive (Windows)
        return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene")
    ap.add_argument("--out")
    ap.add_argument("--from", dest="from_", type=float)
    ap.add_argument("--to", type=float)
    ap.add_argument("--frames")
    ap.add_argument("--section")
    ap.add_argument("--engine", default="auto", choices=["auto", "eevee", "cycles", "workbench"])
    ap.add_argument("--samples", type=int)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--scale", type=float)
    ap.add_argument("--transparent", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--still")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    need_project()
    scene = a.scene if os.path.isabs(a.scene) else os.path.join(ROOT, a.scene)
    fps = delivery()["fps"]
    fm = jload(os.path.join(ROOT, "song", "frames.json"), {})
    n_total = int(round(fm.get("duration", 0) * fps))
    if a.frames:
        x, y = a.frames.split(":")
        rng = (int(x or 0), int(y) if y else n_total)
    elif a.section:
        sm = jload(os.path.join(ROOT, "song", "song-map.json"), {})
        s = next((s for s in sm["sections"] if s["name"].lower() == a.section.lower()), None)
        if not s:
            sys.exit(f"no section {a.section!r}")
        rng = (int(round(s["t0"] * fps)), int(round(s["t1"] * fps)))
    elif a.from_ is not None or a.to is not None:
        rng = (int(round((a.from_ or 0) * fps)), int(round(a.to * fps)) if a.to is not None else n_total)
    else:
        rng = None
    out = a.out or os.path.join("work", "renders", os.path.splitext(os.path.basename(scene))[0])
    out = out if os.path.isabs(out) else os.path.join(ROOT, out)
    args = (scene, out, ROOT, rng, a.engine, a.samples, a.scale or (0.5 if a.preview else 1.0), a.preview, a.transparent,
            a.workers, [float(x) for x in a.still.split(",")] if a.still else None, a.fresh, a.seed)
    if a.still:                                    # stills don't wait for the render queue
        render(*args)
        return
    with render_queue(f"{os.path.basename(ROOT)}: {shown(scene)} -> {shown(out)}"):
        render(*args)


if __name__ == "__main__":
    main()
