"""Measure how fast each renderer and encoder is on this machine -> tools/speeds.json (the director budgets with it).

  python tools/studio.py benchmark [--quick] [--skip blender] [--frames 48]

Measures seconds per frame at 1080p (and at the 540p preview size) for: the web engine with a Canvas 2D load and a
three.js / WebGL load (1 worker and the default number in parallel), Blender Workbench / Eevee / Cycles (when
Blender is installed), and the encoders (libx264 final + preview, NVENC when it works). Uses an internal project
projects/_bench with a synthetic 20 s song folder (nothing to download). Takes ~3-10 minutes (--quick: ~1-2).
"""
import argparse, json, os, platform, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
BENCH = os.path.join(STUDIO, "projects", "_bench")


def make_bench_project(dur=20.0, fps=30):
    """A synthetic song folder: 120 BPM, two sections, kicks on 1 and 3, plain curves. Enough for the scenes."""
    import numpy as np
    sd = os.path.join(BENCH, "song")
    os.makedirs(sd, exist_ok=True)
    json.dump({"title": "_bench", "format": {"width": 1920, "height": 1080, "fps": fps}},
              open(os.path.join(BENCH, "project.json"), "w"))
    beats = [round(0.5 * i, 3) for i in range(int(dur / 0.5))]
    bars = [{"i": i, "t": beats[4 * i], "end": beats[4 * i] + 2.0, "words": [], "events": [], "chord": "C", "loud": -12,
             "v": 0, "d": 5, "b": 5, "o": 5, "beats": 4, "bpm": 120} for i in range(len(beats) // 4)]
    half = bars[len(bars) // 2]["t"]
    secs = [{"name": "verse", "label": "verse", "t0": 0.0, "t1": half, "bar0": 0, "bar1": len(bars) // 2, "loud": -14, "lines": []},
            {"name": "chorus", "label": "chorus", "t0": half, "t1": dur, "bar0": len(bars) // 2, "bar1": len(bars), "loud": -10, "lines": []}]
    sm = {"file": "bench.wav", "duration": dur, "beats": beats, "bars": bars, "sections": secs, "lyrics": [], "chords": [],
          "moments": [], "notes": {"vocal": [], "bass": []}, "tempo": {"bpm": 120, "beat_s": 0.5, "bar_s": 2.0},
          "music": {"start": 0, "end": dur}}
    json.dump(sm, open(os.path.join(sd, "song-map.json"), "w"))
    n = int(round(dur * fps))
    t = np.arange(n) / fps
    curves = {k: np.round(0.5 + 0.5 * np.sin(t * (1 + i)), 3).tolist() for i, k in
              enumerate(("loud", "level", "vocals", "drums", "bass", "other", "transient", "bright", "width"))}
    json.dump({"fps": fps, "frames": n, "duration": dur, "curves": curves,
               "grid": {"bar": (t // 2).astype(int).tolist(), "beat": ((t % 2) / 0.5).round(3).tolist(),
                        "beat_phase": ((t % 0.5) / 0.5).round(3).tolist(), "bar_phase": ((t % 2) / 2).round(3).tolist(),
                        "section": (t >= half).astype(int).tolist()}}, open(os.path.join(sd, "frames.json"), "w"))
    kicks = [{"t": b, "f": int(round(b * fps)), "s": 1.0} for i, b in enumerate(beats) if i % 2 == 0]
    json.dump({"fps": fps, "kick": kicks, "snare": [], "hat": [], "crash": [], "bass": [], "other": [], "vocal": [],
               "onset": kicks, "words": [], "lines": [], "beats": [], "downbeats": []}, open(os.path.join(sd, "hits.json"), "w"))
    return BENCH


def machine():
    import multiprocessing
    info = {"date": time.strftime("%Y-%m-%d %H:%M"), "host": platform.node(), "os": f"{platform.system()} {platform.release()}",
            "cpu": platform.processor() or platform.machine(), "cores": multiprocessing.cpu_count()}
    try:
        import psutil  # noqa: F401
    except Exception:
        pass
    try:
        if os.name != "nt":
            info["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
            for ln in open("/proc/cpuinfo"):
                if ln.startswith("model name"):
                    info["cpu"] = ln.split(":", 1)[1].strip()
                    break
        else:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("m", ctypes.c_ulong), ("t", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong),
                            ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            info["ram_gb"] = round(m.t / 1e9, 1)
    except Exception:
        pass
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True)
        info["gpu"] = r.stdout.strip() or None
    except Exception:
        info["gpu"] = None
    return info


def bench_web(scene, frames, scale, workers, log):
    from mv import web
    out = os.path.join(BENCH, "work", "bench", f"{os.path.basename(scene)}_{scale}_{workers}.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.rmtree(out + ".parts", ignore_errors=True)
    t0 = time.time()
    side = web.render(scene, out, BENCH, (0, frames), scale=scale, workers=workers, chunk_s=frames / 30 / max(1, workers),
                      quality="preview" if scale < 1 else "final", fresh=True, log=lambda *a: None)
    el = time.time() - t0
    log(f"   web {os.path.basename(scene)} {int(1920 * scale)}p x{workers}: {el / frames:.3f} s/frame (incl. startup)")
    return {"s_per_frame": round(el / frames, 4), "frames": frames, "workers": workers, "width": int(1920 * scale),
            "browser": (side.get("browser") or {}).get("browser"), "webgl": (side.get("browser") or {}).get("webgl"),
            "gpu": (side.get("browser") or {}).get("gpu")}


def bench_blender(engine, frames, scale, log, samples=None):
    sys.path.insert(0, os.path.join(HERE, "blender"))
    import run as brun
    scene = os.path.join(HERE, "blender", "examples", "kick_rings.py")
    out = os.path.join(BENCH, "work", "bench", f"blender_{engine}_{scale}")
    shutil.rmtree(out, ignore_errors=True)
    t0 = time.time()
    brun.render(scene, out, BENCH, (0, frames), engine=engine, samples=samples, scale=scale, preview=scale < 1, fresh=True,
                log=lambda *a: None)
    el = time.time() - t0
    log(f"   blender {engine} {int(1920 * scale)}p: {el / frames:.3f} s/frame (incl. ~startup)")
    return {"s_per_frame": round(el / frames, 4), "frames": frames, "width": int(1920 * scale), "samples": samples}


def bench_encode(args, label, log, seconds=6):
    from mv.common import ffmpeg
    t0 = time.time()
    r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={seconds}", *args,
                        "-f", "mp4", os.path.join(BENCH, "work", "bench", "enc.mp4")], capture_output=True)
    el = time.time() - t0
    if r.returncode:
        return None
    fps = seconds * 30 / el
    log(f"   encode {label}: {fps:.0f} frames/s at 1080p")
    return round(fps, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--skip", action="append", default=[], help="web | blender | encode")
    ap.add_argument("--frames", type=int)
    a = ap.parse_args()
    log = lambda s: print(s, flush=True)  # noqa: E731
    make_bench_project()
    os.environ["MV_PROJECT"] = BENCH
    from mv.common import has_nvenc, h264_args
    from mv import web
    n = a.frames or (24 if a.quick else 60)
    res = {"measured_on": machine(), "renderers": {}, "encode": {}, "notes": []}
    print(f"benchmark on {res['measured_on']['cpu']} ({res['measured_on']['cores']} cores), {n} frames per test", flush=True)
    if "web" not in a.skip:
        try:
            nw = web.default_workers()
            ex = os.path.join(HERE, "web-engine", "examples")
            for key, scene in (("web2d", "bench2d.js"), ("web3d", "bench3d.js")):
                r = {"1080p": bench_web(os.path.join(ex, scene), n, 1.0, 1, log),
                     "540p_preview": bench_web(os.path.join(ex, scene), n, 0.5, 1, log)}
                if nw > 1 and not a.quick:
                    r["1080p_parallel"] = bench_web(os.path.join(ex, scene), n, 1.0, nw, log)
                res["renderers"][key] = r
        except Exception as e:
            res["notes"].append(f"web engine failed: {e}")
            log(f"   web engine failed: {e}")
    if "blender" not in a.skip:
        sys.path.insert(0, os.path.join(HERE, "blender"))
        import run as brun
        if brun.find_blender():
            res["measured_on"]["blender"] = brun.blender_version(brun.find_blender())
            fb = max(4, n // 6)
            for eng, samples in (("workbench", None), ("eevee", 32), ("cycles", 32)):
                try:
                    r = {"1080p": bench_blender(eng, fb, 1.0, log, samples)}
                    if not a.quick:
                        r["540p_preview"] = bench_blender(eng, fb, 0.5, log, 16 if samples else None)
                    res["renderers"][f"blender_{eng}"] = r
                except Exception as e:
                    res["notes"].append(f"blender {eng} failed: {str(e).splitlines()[0][:200]}")
                    log(f"   blender {eng} failed: {str(e).splitlines()[-1][:200]}")
        else:
            res["notes"].append("Blender not installed: no 3D render numbers")
    if "encode" not in a.skip:
        os.makedirs(os.path.join(BENCH, "work", "bench"), exist_ok=True)
        res["encode"]["libx264_final_fps"] = bench_encode(["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p"], "libx264 final", log)
        res["encode"]["libx264_preview_fps"] = bench_encode(["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p"], "libx264 preview", log)
        if has_nvenc():
            res["encode"]["nvenc_final_fps"] = bench_encode(h264_args("final"), "NVENC final", log)
        res["encode"]["nvenc"] = has_nvenc()
    # what a whole song costs
    frames_3min = 180 * 30
    est = {}
    for k, v in res["renderers"].items():
        for size, m in v.items():
            if isinstance(m, dict) and m.get("s_per_frame"):
                est[f"{k} {size}"] = round(m["s_per_frame"] * frames_3min / 60, 1)
    res["budget"] = {"frames_in_3_minutes_at_30fps": frames_3min, "minutes_for_3_minutes_at_30fps": est,
                     "how_to_read": "s_per_frame includes browser / Blender start-up for a short test, so long renders "
                                    "are a little faster per frame. Parallel rows use several workers at once. Plan the final "
                                    "render + checks to take at most a third of the time budget."}
    out = os.path.join(HERE, "speeds.json")
    json.dump(res, open(out, "w"), indent=1)
    print(f"-> {out}")
    for k, v in est.items():
        print(f"   {k:<34} {v:7.1f} min for 3 minutes of video at 30 fps")


if __name__ == "__main__":
    main()
