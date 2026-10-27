"""A scripted mini "director" for the smoke test: what a director run does, without the creative part.

  python tests/mini_director.py <project folder>

1. web engine: tests/scenes/field.js (Canvas 2D) for the whole song except the bars around the stop
2. Blender: tests/scenes/stop.py (Workbench) for those bars
3. web engine: tests/scenes/lyrics.html as a transparent type pass over the whole song
4. compose: concat the picture frame-exact, layer the type pass over it, mux the song unchanged -> final/music-video.mp4
5. report.md
Every step goes through tools/studio.py run, the way a director calls the tools.
"""
import json, os, subprocess, sys, time

TESTS = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(TESTS)


def step(pdir, *args, log=print):
    name = os.path.basename(pdir)
    cmd = [sys.executable, os.path.join(STUDIO, "tools", "studio.py"), "run", name] + list(args)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=STUDIO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    tail = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()][-3:]
    log(f"   [{time.time() - t0:5.1f}s] {args[0]} {' '.join(a for a in args[1:3])}: {tail[-1] if tail else ''}")
    if r.returncode:
        raise RuntimeError(f"{' '.join(args)} failed:\n" + (r.stdout + r.stderr)[-3000:])
    return r.stdout


def run(pdir, log=print, blender=True):
    pdir = os.path.abspath(pdir)
    m = json.load(open(os.path.join(pdir, "song", "song-map.json"), encoding="utf-8"))
    pj = json.load(open(os.path.join(pdir, "project.json"), encoding="utf-8"))
    fps = (pj.get("format") or {}).get("fps", 30)
    n = int(round(json.load(open(os.path.join(pdir, "song", "frames.json")))["frames"]))
    stop = next((x for x in m["moments"] if "stop" in x["kind"]), None)
    scenes = os.path.relpath(os.path.join(TESTS, "scenes"), pdir)
    os.makedirs(os.path.join(pdir, "work", "renders"), exist_ok=True)
    if blender and stop:
        bar = int(stop["pos"].split(":")[0])
        a = int(round(m["bars"][max(0, bar - 1)]["t"] * fps))
        b = int(round(m["bars"][min(len(m["bars"]) - 1, bar + 2)]["t"] * fps))
    else:
        a = b = None
    parts = []
    if a is not None:
        step(pdir, "web-engine/render.py", f"{scenes}/field.js", "--out", "work/renders/field_a.mp4", "--frames", f"0:{a}", log=log)
        step(pdir, "blender/run.py", f"{scenes}/stop.py", "--out", "work/renders/stop", "--engine", "workbench", "--frames", f"{a}:{b}", log=log)
        step(pdir, "compose.py", "encode", "work/renders/stop", "-o", "work/renders/stop.mp4", log=log)
        step(pdir, "web-engine/render.py", f"{scenes}/field.js", "--out", "work/renders/field_b.mp4", "--frames", f"{b}:{n}", log=log)
        parts = ["work/renders/field_a.mp4", "work/renders/stop.mp4", "work/renders/field_b.mp4"]
    else:
        step(pdir, "web-engine/render.py", f"{scenes}/field.js", "--out", "work/renders/field.mp4", log=log)
        parts = ["work/renders/field.mp4"]
    step(pdir, "web-engine/render.py", f"{scenes}/lyrics.html", "--out", "work/renders/type", "--transparent", log=log)
    step(pdir, "compose.py", "concat", *parts, "-o", "work/renders/picture.mp4", log=log)
    step(pdir, "compose.py", "layer", "work/renders/picture.mp4", "--over", "work/renders/type", "-o", "work/renders/layered.mp4", log=log)
    step(pdir, "compose.py", "mux", "work/renders/layered.mp4", log=log)
    open(os.path.join(pdir, "report.md"), "w", encoding="utf-8").write(
        "# Report (scripted mini director for the smoke test)\n\n"
        "A field of marks re-arranged on every downbeat (web engine, Canvas 2D); a Blender pass (Workbench) for the bars "
        f"around the stop ({stop['t'] if stop else '-'} s); the sung lines on plates as a transparent type pass, composited "
        "over the picture; the song muxed unchanged.\n\nTimestamps to check by ear: the stop and the tempo change.\n")
    return os.path.join(pdir, "final", "music-video.mp4")


if __name__ == "__main__":
    print(run(sys.argv[1]))
