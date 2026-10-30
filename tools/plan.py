"""The plan tools: an optional toolbox around an optional work/plan.json, a scene list for the whole song (segments
in their song ranges, overlays on top). The director decides whether a plan helps its video; when it does, these
render each segment over its own range with per-scene caching and join, layer and mux the film in one command, with
the eyes, the checks and the critic. work/plan.json is also the live preview's timeline (the web app plays it).

  studio.py plan validate <p>                     check work/plan.json; exit 1 with what to fix
  studio.py plan status <p>                       the plan, each item's renders (current or stale), the previews with
                                                  the critic's scores, the final's estimated render time, the queue
  studio.py plan render <p> [--preview|--final] [--only s1,s3]
        each segment over its own frame range with its renderer (web: render.py --frames a:b; blender: run.py, then
        compose.py encode) -> work/segments/<id>/<id>_<preview|final>.mp4; overlays as transparent passes
        (work/segments/<id>/<id>_<q>/, PNG with alpha); unchanged scenes come back from the cache
  studio.py plan assemble <p> [--preview|--final] [--no-critic] [--critic-model M] [--no-look] [--no-check]
        render what's needed, concat the segments, layer the overlays, mux the song -> work/previews/preview_N.mp4
        (half size; a segment without a scene is a labelled slate) or final/music-video.mp4 (every scene needed); then
        eyes.py look, the checks (a preview: flash and text; a final: all), then the critic on the whole film (its
        report is printed: read it first; an autonomous run's critic watcher doesn't look at that cut again; with
        --no-critic it does, and its notes come as a message)
  (also: studio.py run <p> plan.py <sub> ...)

work/plan.json:
  {"segments": [{"id": "s1", "name": "intro: the lantern wakes", "from": 0.0, "to": 24.24, "renderer": "web|blender",
                 "scene": "work/scenes/s1/scene.js", "render_args": ["--engine", "workbench"]}],
   "overlays": [{"id": "type", "name": "the sung lines", "scene": "work/scenes/type/type.html", "from": 0, "to": 65.42,
                 "transparent": true, "mode": "normal", "opacity": 1}]}
Segments in song order, following on without gaps or overlaps (boundaries become frames: round(t x fps)), the
first from 0, the last to the song's end; ids [a-z0-9_-]+, unique ("lib" is the shared code's folder); each scene in
its own folder work/scenes/<id>/ (the render cache follows a scene's folder and what it imports: work/scenes/lib/ for
web scenes; for Blender scenes work/scenes/lib/ and work/kit/). Other fields are the director's own notes and are
left alone (a plan from an earlier studio, with stage, hero, owner, status or briefs, reads as it is).
Every command prints a short summary (it comes back to the director as tool output); the per-render logs are in
work/segments/<id>/render_<q>.log, render times in work/plan/renders.json, the assemble logs in work/plan/assemble/.
"""
import argparse, glob, json, os, re, subprocess, sys, threading, time
from contextlib import nullcontext

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, ffmpeg, file_lock, h264_args, jload, jsave, project as load_pj, scene_inputs  # noqa: E402
from mv.claude import kill_tree  # noqa: E402

PLAN = os.path.join("work", "plan.json")
ID_RE = re.compile(r"^[a-z0-9_-]+$")
EXTS = {"web": (".js", ".mjs", ".html", ".htm"), "blender": (".py",)}
MODES = ("normal", "screen", "add", "multiply", "lighten", "darken", "difference")
OWNED_FLAGS = ("--out", "-o", "--frames", "--from", "--to", "--section", "--still", "--preview", "--scale", "--transparent",
               "--width", "--height", "--fps")             # the plan tools set these themselves
DONE = ("rendered", "cached")
RENDERS_LOCK = threading.RLock()                   # the render record: threads here, other processes (file_lock)

FILM_FOCUS = (
    "This is the whole film, assembled by the studio from the director's plan, segment by segment:\n{outline}\nLook at "
    "the arc across the segments, the joins ({joins}), whether one look holds the film together (and where a segment "
    "plays safer than the rest, or drifts from the language the others share), the signature moments on the ★★★ "
    "moments, and the ending.{holes}")


def rel(p, start):
    try:
        return os.path.relpath(p, start).replace("\\", "/")
    except ValueError:                             # another drive (Windows)
        return p


def mmss(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:05.2f}"


def fmt_dur(sec):
    sec = int(max(0, sec))
    if sec < 90:
        return f"{sec} s"
    h, m = divmod(round(sec / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def renders_path(pdir):
    return os.path.join(pdir, "work", "plan", "renders.json")


# ------------------------------------------------------------------ validate
def song_facts(pdir):
    """The song as the renderers see it: duration (song/frames.json), fps (the delivery), frames, downbeats."""
    fm = jload(os.path.join(pdir, "song", "frames.json"), None)
    sm = jload(os.path.join(pdir, "song", "song-map.json"), None)
    if not fm or not sm:
        return None
    d = delivery(load_pj(pdir))
    dur = float(fm.get("duration") or sm["duration"])
    return {"fps": d["fps"], "duration": dur, "n": int(round(dur * d["fps"])), "w": d["width"], "h": d["height"],
            "downbeats": [float(b["t"]) for b in sm.get("bars") or []]}


def load_plan(pdir):
    """(plan dict or None, error text)."""
    p = os.path.join(pdir, PLAN)
    if not os.path.exists(p):
        return None, "there is no work/plan.json (it's optional: write one when a scene list helps; tools/README.md, plan.py)"
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except json.JSONDecodeError as ex:
        return None, f"work/plan.json isn't valid JSON at line {ex.lineno}, column {ex.colno}: {ex.msg}"
    except OSError as ex:
        return None, f"can't read work/plan.json: {ex}"
    if not isinstance(d, dict):
        return None, "work/plan.json must be one JSON object: {\"segments\": [...], \"overlays\": [...]}"
    return d, ""


def clean_path(val):
    """A plan path as the plan should give it (relative to the project, forward slashes) or (None, why)."""
    if not isinstance(val, str) or not val.strip():
        return None, "is missing (a path relative to the project folder, like work/scenes/s1/scene.js)"
    v = val.strip()
    if os.path.isabs(v) or re.match(r"^[A-Za-z]:", v) or v.startswith(("/", "\\")):
        return None, f"is {val!r}: give it relative to the project folder (work/...)"
    n = os.path.normpath(v).replace("\\", "/")
    if n == ".." or n.startswith("../"):
        return None, f"is {val!r}, outside the project folder: everything the video uses lives in the project"
    return n, ""


def validate(pdir, plan=None):
    """Check work/plan.json (or the given plan). Returns {"ok", "errors", "warnings", "plan", "items", "fps", "n",
    "duration", "w", "h"}; items are the segments then the overlays, each with its frame range [a, b), its scene's
    absolute path and whether it is there yet (a scene that isn't is no error: a preview shows a slate, the final
    asks for it). Errors say what to change, with the numbers to set."""
    pdir = os.path.abspath(pdir)
    v = {"ok": False, "errors": [], "warnings": [], "plan": None, "items": []}

    def E(where, msg):
        v["errors"].append(f"{where}: {msg}" if where else msg)

    def W(where, msg):
        v["warnings"].append(f"{where}: {msg}" if where else msg)
    song = song_facts(pdir)
    if not song:
        E("", "no song folder yet (song/frames.json, song/song-map.json): run ingest first")
        return v
    v.update({k: song[k] for k in ("fps", "n", "duration", "w", "h")})
    fps, n, dur = song["fps"], song["n"], song["duration"]
    if plan is None:
        plan, why = load_plan(pdir)
        if plan is None:
            E("", why)
            return v
    v["plan"] = plan
    segs = plan.get("segments")
    if not isinstance(segs, list) or not segs:
        E("segments", "a list of the segments that cover the whole song in order, at least one: "
                      "[{\"id\": \"s1\", \"from\": 0, \"to\": ..., \"renderer\": \"web\", \"scene\": \"work/scenes/s1/scene.js\"}]")
        segs = []
    ids = []

    def where_of(kind, i, x):
        return f"{kind}[{i}]" + (f" ({x.get('id')})" if isinstance(x, dict) and isinstance(x.get("id"), str) else "")

    def common(kind, i, x, default_whole=False):
        """The fields segments and overlays share. Returns the item or None."""
        w = where_of(kind, i, x)
        if not isinstance(x, dict):
            E(w, "must be an object {\"id\": ..., \"from\": ..., ...}")
            return None
        it = {"kind": kind[:-1], "where": w}
        sid = x.get("id")
        if not isinstance(sid, str) or not ID_RE.match(sid):
            E(w, f"id is {sid!r}: use lowercase letters, digits, - and _ only (like s1, chorus-2, type)")
            sid = None
        elif sid == "lib":
            E(w, "the id lib is taken: work/scenes/lib/ is the shared code's folder. Pick another id")
        elif sid in ids:
            E(w, f"the id {sid} is used twice: every segment and overlay needs its own id (and its own folder)")
        if sid:
            ids.append(sid)
        it["id"] = sid or f"#{i}"
        it["name"] = str(x.get("name") or "").strip()
        a0, a1 = x.get("from", 0.0 if default_whole else None), x.get("to", dur if default_whole else None)
        if not is_num(a0) or not is_num(a1):
            E(w, f"from and to must be numbers, song seconds (from {a0!r}, to {a1!r})")
            it["bad_range"] = True
            a0, a1 = 0.0, 0.0
        a, b = int(round(a0 * fps)), int(round(a1 * fps))
        if a0 < 0:
            E(w, f"from is {a0:g}: nothing starts before the song (0)")
            it["bad_range"] = True
        if b > n:
            E(w, f"to is {a1:g}, but the song is {dur:.3f} s long ({n} frames at {fps} fps): it ends at {dur:.3f} at the latest")
            it["bad_range"] = True
        if not it.get("bad_range") and b <= a:
            E(w, f"from {a0:g} to {a1:g} is empty at {fps} fps (boundaries become frames: round(t × fps) = {a} and {b}): "
                 f"make to later than from")
            it["bad_range"] = True
        it.update({"from": a0, "to": a1, "a": max(0, a), "b": min(n, b), "t0": a / fps, "t1": b / fps})
        r = x.get("renderer", "web")
        if r not in EXTS:
            E(w, f"renderer is {r!r}: web (HTML/JS in Chromium) or blender")
            r = "web"
        it["renderer"] = r
        ra = x.get("render_args", [])
        if not isinstance(ra, list) or not all(isinstance(y, str) for y in ra):
            E(w, "render_args must be a list of strings, like [\"--engine\", \"workbench\"]")
            ra = []
        bad = [y for y in ra if y.split("=")[0] in OWNED_FLAGS]
        if bad:
            E(w, f"render_args can't set {', '.join(bad)}: the plan tools set the range, the size, the output and the "
                 f"transparency themselves")
            ra = [y for y in ra if y not in bad]
        it["render_args"] = ra
        sc, why = clean_path(x.get("scene"))
        it["scene"], it["scene_abs"], it["exists"] = sc, None, False
        if not sc:
            E(w, f"scene {why}")
        else:
            parts = sc.split("/")
            if sid and (parts[:2] != ["work", "scenes"] or len(parts) < 4 or parts[2] != sid):
                E(w, f"scene is {sc}: every scene of the plan lives in its own folder work/scenes/{sid}/ (the render cache "
                     f"follows a scene's folder and what it imports, so a change in one segment doesn't render another). "
                     f"Use work/scenes/{sid}/{parts[-1]}")
            if not sc.lower().endswith(EXTS[r]):
                E(w, f"scene {sc} isn't a {r} scene ({' / '.join(EXTS[r])})")
            it["scene_abs"] = os.path.join(pdir, *parts)
            it["exists"] = os.path.isfile(it["scene_abs"])
        return it

    for i, x in enumerate(segs):
        it = common("segments", i, x)
        if it:
            v["items"].append(it)
    seg_items = [it for it in v["items"] if not it.get("bad_range")]
    if seg_items and len(seg_items) == len(v["items"]) == len(segs):
        first, last = seg_items[0], seg_items[-1]
        if first["a"] != 0:
            E(first["where"], f"starts at {first['from']:g} s: the first segment starts at 0 (set its from to 0)")
        for p, c in zip(seg_items, seg_items[1:]):
            if c["a"] < p["a"]:
                E(c["where"], f"comes before {p['id']} in the song (from {c['from']:g} < {p['from']:g}): list the segments "
                              f"in song order")
            elif c["a"] > p["b"]:
                E(c["where"], f"a gap before it: {p['id']} ends at {p['to']:g} s (frame {p['b']}) and {c['id']} starts at "
                              f"{c['from']:g} s (frame {c['a']}): {c['a'] - p['b']} frames ({(c['a'] - p['b']) / fps:.2f} s) "
                              f"with no picture. Set {c['id']}.from to {p['to']:g} (or {p['id']}.to to {c['from']:g})")
            elif c["a"] < p["b"]:
                E(c["where"], f"overlaps {p['id']} by {p['b'] - c['a']} frames ({(p['b'] - c['a']) / fps:.2f} s): {p['id']} "
                              f"ends at {p['to']:g} s, {c['id']} starts at {c['from']:g} s. Segments follow on: set "
                              f"{c['id']}.from to {p['to']:g} (or {p['id']}.to to {c['from']:g})")
        if last["b"] < n:
            E(last["where"], f"ends at {last['to']:g} s (frame {last['b']}), but the song goes on to {dur:.3f} s (frame {n} at "
                             f"{fps} fps): the last segment ends where the song does. Set its to to {dur:.3f}")
        dbs = song["downbeats"]
        if dbs:
            from mv.song import Song
            S = Song(pdir)
            for p, c in zip(seg_items, seg_items[1:]):
                f = c["a"]
                near = min(dbs, key=lambda t: abs(t - f / fps))
                if abs(int(round(near * fps)) - f) > 1:
                    pos = S.pos(f / fps)
                    at = f" (bar {pos[0]}, beat {pos[1]:.2f})" if pos else ""
                    W(c["where"], f"the cut {p['id']} → {c['id']} at {f / fps:.2f} s{at} isn't within a frame of a downbeat "
                                  f"(the nearest: {near:.2f} s, bar {dbs.index(near)}). Every cut lands on a musical event: "
                                  f"move it to the downbeat, unless it sits on another event on purpose (a word, a hit, a stop)")
    ovs = plan.get("overlays", [])
    if not isinstance(ovs, list):
        E("overlays", "must be a list (or left out)")
        ovs = []
    for i, x in enumerate(ovs):
        it = common("overlays", i, x, default_whole=True)
        if not it:
            continue
        w = it["where"]
        tr = x.get("transparent", True)
        if not isinstance(tr, bool):
            E(w, f"transparent is {tr!r}: true or false")
            tr = True
        mode = x.get("mode", "normal")
        if mode not in MODES:
            E(w, f"mode is {mode!r}: {', '.join(MODES)}")
            mode = "normal"
        op = x.get("opacity", 1)
        if not is_num(op) or not 0 < op <= 1:
            E(w, f"opacity is {op!r}: a number above 0, at most 1")
            op = 1
        if not tr and mode == "normal" and op >= 1:
            W(w, "an opaque overlay in normal mode covers everything under it over its range: make it transparent, or "
                 "give it a blend mode (screen, add, multiply ...) or an opacity")
        it.update(transparent=tr, mode=mode, opacity=float(op))
        v["items"].append(it)
    # a segment that imports another segment's folder is no longer on its own
    folders = {it["id"]: os.path.normcase(os.path.dirname(it["scene_abs"])) + os.sep for it in v["items"] if it.get("scene_abs")}
    for it in v["items"]:
        if it["renderer"] != "web" or not it["exists"]:
            continue
        from mv import web
        for f in web.reached(it["scene_abs"], pdir):
            other = next((k for k, d in folders.items() if k != it["id"] and os.path.normcase(f).startswith(d)), None)
            if other:
                W(it["where"], f"its scene imports {rel(f, pdir)}, from {other}'s folder: share code through "
                               f"work/scenes/lib/ instead (else a change in {other} renders {it['id']} again)")
                break
    v["ok"] = not v["errors"]
    return v


def plan_text(v, pdir=None, table=True):
    """The validation as a few readable lines."""
    segs = [it for it in v["items"] if it["kind"] == "segment"]
    ovs = [it for it in v["items"] if it["kind"] == "overlay"]
    L = []
    if v["ok"]:
        L.append(f"plan.json: valid · {len(segs)} segment{'s' * (len(segs) != 1)}"
                 + (f" + {len(ovs)} overlay{'s' * (len(ovs) != 1)}" if ovs else "")
                 + f" · {mmss(v['duration'])} at {v['fps']} fps")
    else:
        L.append(f"plan.json: NOT valid: {len(v['errors'])} error{'s' * (len(v['errors']) != 1)}"
                 + (f", {len(v['warnings'])} warning{'s' * (len(v['warnings']) != 1)}" if v["warnings"] else "")
                 + ". Fix the errors in work/plan.json, then validate again.")
    if table and v["items"]:
        for it in v["items"]:
            kind = "segment" if it["kind"] == "segment" else \
                f"overlay ({'transparent' if it.get('transparent') else 'opaque'}, {it.get('mode')})"
            fr = f"frames {it['a']}–{it['b']}"
            L.append(f"  {it['id']:<9} {mmss(it['t0'])}–{mmss(it['t1'])}  {fr:<18} {it['renderer']:<7} "
                     f"{kind:<22} {it['scene'] or '?'}{'' if it['exists'] else ' (not written yet)'}"
                     + (f"  \"{it['name'][:60]}\"" if it["name"] else ""))
    if v["errors"]:
        L.append("errors:")
        L += [f"  - {e}" for e in v["errors"]]
    if v["warnings"]:
        L.append("warnings:")
        L += [f"  - {w}" for w in v["warnings"]]
    return "\n".join(L)


# ------------------------------------------------------------------ render
def tool_env(pdir, who=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore", MV_PROJECT=os.path.abspath(pdir),
               MV_TOOLS=HERE, MV_IN_VENV="1")
    if who:
        env["MV_RENDER_WHO"] = who
    return env


def run_tool(pdir, args, env=None, logfile=None, show=None, log=print, abort=None):
    """A studio tool (path relative to tools/) inside the project. Every line goes to logfile; the lines show(line)
    picks go to log as they come. Returns (exit code, the lines). Ctrl+C, or abort() turning true, stops it with what
    it started (then the exit code is None)."""
    if abort and abort():
        return None, ["(stopped before it started)"]
    cmd = [sys.executable, "-W", "ignore", os.path.join(HERE, args[0])] + [str(x) for x in args[1:]]
    p = subprocess.Popen(cmd, cwd=STUDIO, env=env or tool_env(pdir), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    lines, stopped = [], []
    fh = open(logfile, "a", encoding="utf-8") if logfile else None
    if abort:
        def guard():
            while p.poll() is None:
                if abort():
                    stopped.append(1)
                    kill_tree(p)
                    return
                time.sleep(0.5)
        threading.Thread(target=guard, daemon=True).start()
    try:
        if fh:
            fh.write(f"$ {' '.join(args)}\n")
        for line in p.stdout:
            line = line.rstrip()
            lines.append(line)
            if fh:
                fh.write(line + "\n")
                fh.flush()
            if show and show(line):
                log("   " + line)
        p.wait()
    except BaseException:                          # Ctrl+C: nothing keeps rendering behind the director's back
        kill_tree(p)
        raise
    finally:
        if fh:
            fh.close()
    return (None if stopped else p.returncode), lines


def outputs(pdir, it, q):
    """(the item's folder, its output for quality q): work/segments/<id>/<id>_<q>.mp4, or a PNG folder with alpha for
    a transparent overlay."""
    d = os.path.join(pdir, "work", "segments", it["id"])
    if it["kind"] == "overlay" and it.get("transparent"):
        return d, os.path.join(d, f"{it['id']}_{q}")
    return d, os.path.join(d, f"{it['id']}_{q}.mp4")


def arg_value(args, flag, default=None):
    for k, x in enumerate(args):
        if x == flag and k + 1 < len(args):
            return args[k + 1]
        if x.startswith(flag + "="):
            return x.split("=", 1)[1]
    return default


def current(pdir, it, q):
    """Is the item's render for quality q still the scene as it is now? (True/False, why not). Web: the cache key in
    the render's sidecar (the scene's folder, what it imports, the runtime, the song data, the settings); Blender:
    its inputs (mv.common.scene_inputs: the scene's folder, work/scenes/lib/, work/kit/) and the render settings
    against the last render."""
    d, out = outputs(pdir, it, q)
    side = jload(out.rstrip("/\\") + ".mv.json", None)
    if not side or not os.path.exists(out):
        return False, "not rendered yet"
    if side.get("range") != [it["a"], it["b"]]:
        return False, "its range changed"
    if not it.get("exists"):
        return False, "no scene"
    if it["renderer"] == "web":
        from mv import web
        args = it.get("render_args") or []
        try:
            seed = int(arg_value(args, "--seed", 1))
        except ValueError:
            seed = 1
        tr = it["kind"] == "overlay" and bool(it.get("transparent"))
        cfg = web.render_cfg(pdir, q, 0.5 if q == "preview" else 1.0, tr, "--lossless" in args, seed)[0]
        if side.get("key") != web.scene_key(it["scene_abs"], pdir, cfg):
            return False, "the scene, its lib or the render settings changed"
        if os.path.isdir(out) and len(glob.glob(os.path.join(out, "frame_*.png"))) < it["b"] - it["a"]:
            return False, "frames missing"
        return True, ""
    rec = ((jload(renders_path(pdir), {}) or {}).get(it["id"]) or {}).get(q) or {}
    if rec.get("args") != (it.get("render_args") or []) or rec.get("range") != [it["a"], it["b"]]:
        return False, "the render settings changed"
    newest = 0.0
    for f in scene_inputs(it["scene_abs"], pdir) + [os.path.join(HERE, "blender", "mvblend.py")]:
        try:
            newest = max(newest, os.path.getmtime(f))
        except OSError:
            pass
    if newest > os.path.getmtime(out.rstrip("/\\") + ".mv.json"):
        return False, "the scene or the shared code changed"
    return True, ""


QUEUE_LINE = re.compile(r"^(waiting for the render queue|render queue:)")


def render_item(pdir, it, q, log=print, abort=None):
    """Render one segment or overlay at quality q (unless it is current). Returns {"id", "kind", "quality", "out",
    "state": rendered|cached|missing|failed|stopped, "seconds", "frames", "error", "log"}."""
    d, out = outputs(pdir, it, q)
    res = {"id": it["id"], "kind": it["kind"], "quality": q, "out": out, "state": None, "seconds": 0.0,
           "frames": it["b"] - it["a"], "rendered_frames": 0, "error": "", "log": None}
    if not it.get("exists"):
        res.update(state="missing", error=f"no scene yet ({it['scene']})")
        return res
    ok, why = current(pdir, it, q)
    if ok:
        res["state"] = "cached"
        return res
    os.makedirs(d, exist_ok=True)
    logf = os.path.join(d, f"render_{q}.log")
    open(logf, "w", encoding="utf-8").write(f"# {it['id']} {q}: {why} ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
    res["log"] = logf
    who = f"{os.environ.get('MV_RENDER_WHO') or 'the plan tools'}: {it['id']} {q}"
    env = tool_env(pdir, who)
    pv = ["--preview"] if q == "preview" else []
    tr = ["--transparent"] if it["kind"] == "overlay" and it.get("transparent") else []
    args = list(it.get("render_args") or [])
    rng = f"{it['a']}:{it['b']}"
    if it["renderer"] == "web":
        cmds = [["web-engine/render.py", it["scene"], "--frames", rng, "--out", out, *pv, *tr, *args]]
    else:
        # run.py keeps the frames there while the scene's inputs and the settings are the same (its key), else starts over
        frames = out if tr else os.path.join(d, f"{it['id']}_{q}_frames")
        cmds = [["blender/run.py", it["scene"], "--frames", rng, "--out", frames, *pv, *tr, *args]]
        if not tr:
            cmds.append(["compose.py", "encode", frames, "-o", out, *pv])
    t0 = time.time()
    lines = []
    for c in cmds:
        rc, ls = run_tool(pdir, c, env, logf, show=lambda x: bool(QUEUE_LINE.match(x)), log=log, abort=abort)
        lines += ls
        if rc is None:
            res.update(state="stopped", error="stopped")
            break
        if rc:
            tail = [x for x in ls if x.strip()]
            err = next((x for x in reversed(tail) if re.search(r"error|Error|failed|missing|no scene", x)), tail[-1] if tail else f"exit {rc}")
            res.update(state="failed", error=err.strip()[:400])
            break
    el = time.time() - t0
    waited = sum(float(m.group(1)) for x in lines for m in [re.search(r"^render queue: my turn after (\d+) s", x)] if m)
    res["seconds"] = round(max(0.0, el - waited), 1)
    res["waited"] = round(waited)
    done = [int(m.group(1)) for x in lines for m in [re.search(r"rendered (\d+) frames in", x)] if m]
    res["rendered_frames"] = sum(done)
    if res["state"] is None:
        res["state"] = "rendered" if done else "cached"
        try:
            with RENDERS_LOCK, file_lock(renders_path(pdir)):     # an agent may render in a process of its own
                rec = jload(renders_path(pdir), {}) or {}
                r = rec.setdefault(it["id"], {}).setdefault(q, {})
                r.update(range=[it["a"], it["b"]], renderer=it["renderer"], args=args, at=time.strftime("%Y-%m-%d %H:%M:%S"))
                if done:                             # the render time per frame, for the final's estimate
                    w, h = web_size(pdir, q)
                    r.update(seconds=res["seconds"], frames=sum(done), s_per_frame=round(res["seconds"] / max(1, sum(done)), 4),
                             w=w, h=h)
                jsave(renders_path(pdir), rec)
        except Exception as ex:                      # bookkeeping: the render is there either way
            log(f"   (couldn't record the render in work/plan/renders.json: {ex})")
    return res


def web_size(pdir, q):
    d = delivery(load_pj(pdir))
    s = 0.5 if q == "preview" else 1.0
    return int(round(d["width"] * s / 2)) * 2, int(round(d["height"] * s / 2)) * 2


def render(pdir, quality="preview", only=None, log=print, v=None, abort=None):
    """Render the plan's segments and overlays (or `only` those ids) at quality preview|final, one after the other
    (the render queue would make them wait for each other anyway). Returns (validation, [result per item]); prints
    one line per item. abort() turning true stops it (the rest come back "stopped")."""
    pdir = os.path.abspath(pdir)
    v = v or validate(pdir)
    if not v["ok"]:
        log(plan_text(v, pdir, table=False))
        return v, []
    items = v["items"]
    if only:
        known = [it["id"] for it in items]
        bad = [x for x in only if x not in known]
        if bad:
            log(f"render: no {', '.join(bad)} in the plan (it has {', '.join(known)})")
            return v, [{"id": x, "kind": "?", "state": "failed", "error": "not in the plan", "out": None} for x in bad]
        items = [it for it in items if it["id"] in only]
    w, h = web_size(pdir, quality)
    log(f"render ({quality}, {w}x{h}): {len(items)} item{'s' * (len(items) != 1)}")
    t0, out = time.time(), []
    for it in items:
        if abort and abort():
            out.append({"id": it["id"], "kind": it["kind"], "quality": quality, "out": None, "state": "stopped",
                        "error": "stopped", "seconds": 0.0, "rendered_frames": 0})
            continue
        r = render_item(pdir, it, quality, log, abort=abort)
        out.append(r)
        span = f"{mmss(it['t0'])}–{mmss(it['t1'])}"
        what = {"rendered": f"rendered {r['rendered_frames']} frames in {fmt_dur(r['seconds'])}"
                            + (f" (waited {fmt_dur(r['waited'])} for the render queue)" if r.get("waited") else ""),
                "cached": "unchanged (from the cache)", "missing": r["error"], "stopped": "stopped",
                "failed": f"FAILED: {r['error']}" + (f" (log: {rel(r['log'], pdir)})" if r.get("log") else "")}[r["state"]]
        log(f"  {it['id']:<9} {span}  {it['renderer']:<7} {what}" + (f"  -> {rel(r['out'], pdir)}" if r["state"] in DONE else ""))
    n = {k: sum(r["state"] == k for r in out) for k in ("rendered", "cached", "missing", "failed", "stopped")}
    log(f"render: {n['rendered']} rendered, {n['cached']} unchanged" + (f", {n['missing']} without a scene yet" if n["missing"] else "")
        + (f", {n['failed']} FAILED" if n["failed"] else "") + (f", {n['stopped']} stopped" if n["stopped"] else "")
        + f" ({fmt_dur(time.time() - t0)})")
    return v, out


# ------------------------------------------------------------------ assemble
def placeholder(pdir, it, q, why, fps):
    """A labelled slate over a segment's range, so a preview can be assembled while a segment is missing."""
    from PIL import Image, ImageDraw
    from mv.common import font
    w, h = web_size(pdir, q)
    d = os.path.join(pdir, "work", "segments", it["id"])
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, f"{it['id']}_{q}_placeholder.mp4")
    im = Image.new("RGB", (w, h), (24, 24, 28))
    dr = ImageDraw.Draw(im)
    lines = [(f"{it['id']} · {it['name']}" if it["name"] else it["id"], 0.075), (why, 0.05),
             (f"{mmss(it['t0'])}–{mmss(it['t1'])}", 0.04)]
    y = h * 0.36
    for text, size in lines:
        f = font("inter", max(8, h * size))
        tw = dr.textlength(text[:80], font=f)
        dr.text(((w - tw) / 2, y), text[:80], fill=(236, 232, 222), font=f)
        y += h * size * 1.6
    png = os.path.join(d, f"_placeholder_{q}.png")
    im.save(png)
    n = it["b"] - it["a"]
    r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", png, "-frames:v", str(n),
                        *h264_args(q, chunk=True), "-r", str(fps), "-video_track_timescale", str(fps * 1000),
                        "-movflags", "+faststart", out], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"could not make the placeholder for {it['id']}: {r.stderr[-300:]}")
    jsave(out + ".mv.json", {"range": [it["a"], it["b"]], "fps": fps, "w": w, "h": h, "t0": it["a"] / fps, "t1": it["b"] / fps,
                             "texts": [], "intents": [], "placeholder": why, "made": time.strftime("%Y-%m-%d %H:%M:%S")})
    return out


def next_preview(pdir):
    d = os.path.join(pdir, "work", "previews")
    os.makedirs(d, exist_ok=True)
    nums = [int(m.group(1)) for f in os.listdir(d) for m in [re.fullmatch(r"preview_(\d+)\.mp4", f)] if m]
    return os.path.join(d, f"preview_{max(nums or [0]) + 1}.mp4")


def outline(v):
    """The plan in a few lines, for the critic."""
    out = []
    for it in v["items"]:
        if it["kind"] == "segment":
            out.append(f"- {it['id']} {mmss(it['t0'])}–{mmss(it['t1'])}{' (' + it['renderer'] + ')' if it['renderer'] != 'web' else ''}: "
                       f"{it['name'] or '-'}")
        else:
            out.append(f"- overlay {it['id']} {mmss(it['t0'])}–{mmss(it['t1'])}: {it['name'] or '-'}")
    return "\n".join(out)


def critic_settings(pdir, model=None, effort=None):
    """(model, effort) of the critic: the given ones, else the run's (MV_CRITIC_MODEL / MV_CRITIC_EFFORT: a director in
    an autonomous run uses the run's critic), else the project's setting."""
    import critic
    m0, e0 = critic.settings(load_pj(pdir))
    return (model or m0), (effort or e0)


def run_critic(pdir, video, focus, model, effort=None, max_usd=None, pictures=(), log=print, abort=None):
    """critic.py on a video (its output shown as it comes). Returns the critique (dict, with "report") or None (it
    couldn't run, or abort() stopped it)."""
    import critic
    if abort and abort():
        return None
    cmd, env = critic.command(pdir, rel(video, pdir), model if model != "off" else None, effort, max_usd, focus=focus,
                              pictures=pictures)
    p = subprocess.Popen(cmd, cwd=STUDIO, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    out, stopped = [], []
    if abort:
        def guard():
            while p.poll() is None:
                if abort():
                    stopped.append(1)
                    kill_tree(p)
                    return
                time.sleep(0.5)
        threading.Thread(target=guard, daemon=True).start()
    try:
        for line in p.stdout:
            out.append(line)
            log(line.rstrip())
        p.wait()
    except BaseException:                          # Ctrl+C: the critic doesn't outlive the one who asked for it
        kill_tree(p)
        raise
    return critic.from_output(pdir, "".join(out)) if p.returncode == 0 and not stopped else None


def assemble(pdir, quality="preview", log=print, look=True, check=True, critic_model=None, critic_effort=None, max_usd=None,
             abort=None):
    """Render what's needed, then concat the segments, layer the overlays and mux the song: a preview (half size,
    work/previews/preview_N.mp4; a segment that's missing or failed gets a labelled slate) or the final
    (final/music-video.mp4; every segment and overlay must be there). Then eyes.py look; the checks (a preview: flash
    and text; a final: all of them); with a critic model the critic on the whole film (the cut is marked pending
    from the mux on: the runner's critic watcher leaves it to this one). abort() turning true stops it. Returns
    {"ok", "video", "renders", "placeholders", "checks", "sync", "check_lines", "critique", "seconds", "error"}."""
    import critic
    pdir = os.path.abspath(pdir)
    t0 = time.time()
    res = {"ok": False, "quality": quality, "video": None, "renders": [], "placeholders": [], "checks": None,
           "sync": None, "check_lines": [], "critique": None, "error": ""}
    stopped = lambda: bool(abort and abort())      # noqa: E731

    def halt():
        res["error"] = "stopped"
        log(f"assemble ({quality}): stopped")
        return res
    v = validate(pdir)
    if not v["ok"]:
        log(plan_text(v, pdir, table=False))
        res["error"] = "plan.json isn't valid"
        return res
    fps = v["fps"]
    if quality == "final":                         # say so before rendering anything
        missing = [it["id"] for it in v["items"] if not it["exists"]]
        if missing:
            res["error"] = (f"the final needs every segment and overlay; no scene yet for {', '.join(missing)}. Write them (or "
                            f"give their time to a neighbour in plan.json), then assemble again")
            log(f"assemble (final): stopped: {res['error']}")
            return res
    v, rs = render(pdir, quality, log=log, v=v, abort=abort)
    if stopped():
        return halt()
    byid = {it["id"]: it for it in v["items"]}
    res["renders"] = rs
    segs = [r for r in rs if r["kind"] == "segment"]
    holes = [r for r in segs if r["state"] not in DONE]
    if holes and quality == "final":
        res["error"] = ("the final needs every segment: " + "; ".join(f"{r['id']}: {r['error']}" for r in holes)
                        + ". Fix them (or give their time to a neighbour in plan.json), then assemble again")
        log(f"assemble (final): stopped: {res['error']}")
        return res
    parts = []
    for r in segs:
        if r["state"] in DONE:
            parts.append(r["out"])
        else:
            why = "not written yet" if r["state"] == "missing" else "its render failed"
            parts.append(placeholder(pdir, byid[r["id"]], quality, why, fps))
            res["placeholders"].append(r["id"])
    adir = os.path.join(pdir, "work", "plan", "assemble")
    os.makedirs(adir, exist_ok=True)
    logf = os.path.join(adir, f"assemble_{quality}.log")
    open(logf, "w", encoding="utf-8").write(f"# assemble {quality} ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
    pv = ["--preview"] if quality == "preview" else []

    def step(args, what):
        rc, ls = run_tool(pdir, args, logfile=logf, log=log, abort=abort)
        if rc is None:
            res["error"] = "stopped"
            return None
        if rc:
            tail = [x for x in ls if x.strip()][-6:]
            res["error"] = f"{what} failed: " + " | ".join(tail)[-600:] + f" (log: {rel(logf, pdir)})"
            log(f"assemble ({quality}): {res['error']}")
            return None
        return [x for x in ls if x.strip()]
    picture = os.path.join(adir, f"{quality}_picture.mp4")
    said = step(["compose.py", "concat", *parts, "-o", picture, *pv], "joining the segments (compose.py concat)")
    if said is None:
        return res
    how = "stream copy" if any("stream copy" in x for x in said) else "re-encoded"
    overs, skipped = [], []
    for r in rs:
        if r["kind"] != "overlay":
            continue
        it = byid[r["id"]]
        if r["state"] in DONE:
            overs.append(f"{r['out']}:{it['mode']}:{it['opacity']:g}")
        else:
            skipped.append(r["id"])
    if skipped and quality == "final":
        res["error"] = f"overlays without a render: {', '.join(skipped)} (the final needs them, or take them out of plan.json)"
        log(f"assemble (final): stopped: {res['error']}")
        return res
    base = picture
    if overs:
        base = os.path.join(adir, f"{quality}_layered.mp4")
        if step(["compose.py", "layer", picture, *[y for o in overs for y in ("--over", o)], "-o", base, *pv],
                "layering the overlays (compose.py layer)") is None:
            return res
    out = os.path.join(pdir, "final", "music-video.mp4") if quality == "final" else next_preview(pdir)
    if step(["compose.py", "mux", base, "-o", out, *pv], "adding the song (compose.py mux)") is None:
        return res
    res.update(ok=True, video=rel(out, pdir))
    w, h = web_size(pdir, quality)
    log(f"assemble ({quality}): {len(parts)} segment{'s' * (len(parts) != 1)} ({how})"
        + (f" + {len(overs)} overlay{'s' * (len(overs) != 1)}" if overs else "") + f" + the song -> {res['video']} "
        f"({mmss(v['duration'])}, {w}x{h}, {fps} fps)")
    if res["placeholders"]:
        log(f"   NOTE: {', '.join(res['placeholders'])} {'is' if len(res['placeholders']) == 1 else 'are'} a labelled slate in this "
            f"preview (no scene yet, or its render failed)")
    if skipped:
        log(f"   NOTE: overlay{'s' * (len(skipped) != 1)} {', '.join(skipped)} left out (no render yet)")
    with_critic = bool(critic_model and critic_model != "off")
    with (critic.pending(pdir, out) if with_critic else nullcontext()):
        looked(pdir, res, out, quality, logf, look, check, log, abort)
        if stopped():
            return dict(halt(), ok=True)
        if with_critic:
            seg_items = [it for it in v["items"] if it["kind"] == "segment"]
            joins = ", ".join(f"{a['id']}→{b['id']} at {mmss(b['t0'])}" for a, b in zip(seg_items, seg_items[1:]))
            holes_txt = (f"\n{', '.join(res['placeholders'])}: not written yet (a labelled slate stands in): judge the rest, "
                         f"and say what those segments must do.") if res["placeholders"] else ""
            focus = FILM_FOCUS.format(outline=outline(v), joins=joins or "one segment: no joins", holes=holes_txt)
            log(f"\nthe critic on the whole film ({critic_model}):")
            c = run_critic(pdir, out, focus, critic_model, critic_effort, max_usd, log=log, abort=abort)
            if c:
                res["critique"] = {k: c.get(k) for k in ("file", "scores", "verdict", "cost_usd", "model")}
            elif not stopped():
                log("   the critic couldn't run (see above)")
    res["seconds"] = round(time.time() - t0)
    log(f"assemble ({quality}): done in {fmt_dur(res['seconds'])} -> {res['video']}")
    return res


def looked(pdir, res, out, quality, logf, look, check, log, abort):
    """An assembled cut's eyes (eyes.py look) and checks (a preview: the ones that apply at any size; a final: all)."""
    stopped = lambda: bool(abort and abort())      # noqa: E731
    if look and not stopped():
        import eyes
        rc, ls = run_tool(pdir, ["eyes.py", "look", out], logfile=logf, log=log, abort=abort)
        idx = os.path.join(eyes.out_dir(out), "index.md")
        sync = next((x for x in ls if x.startswith("sync: ")), "")
        res["sync"] = sync[6:].split(" -> ")[0] if sync else None
        log(f"   eyes: {rel(idx, pdir)}" + (f" ({res['sync']})" if res["sync"] else "") if rc == 0 else
            f"   eyes.py look failed (log: {rel(logf, pdir)})")
    if check and not stopped():
        names = ["all"] if quality == "final" else ["flash", "text"]
        verdicts = {}
        for name in names:
            rc, ls = run_tool(pdir, ["checks.py", name, out], logfile=logf, log=log, abort=abort)
            res["check_lines"] += [x.strip() for x in ls if "[FAIL]" in x or "[WARN]" in x]
            if name == "all":
                verdicts.update((jload(os.path.join(pdir, "work", "checks", "report.json"), {}) or {}).get("verdicts") or {})
            elif rc is not None:
                verdicts[name] = (jload(os.path.join(pdir, "work", "checks", name, "report.json"), {}) or {}).get("verdict")
        res["checks"] = verdicts or None
        where = "work/checks/report.md" if quality == "final" else "work/checks/<check>/report.md"
        log(f"   checks: {', '.join(f'{k} {x}' for k, x in (res['checks'] or {}).items()) or 'could not run'}"
            f" ({where})" + ("".join(f"\n     {x}" for x in res["check_lines"][:8])))


# ------------------------------------------------------------------ the final's estimate
PREVIEW_RATIO = 2.5              # a final frame / a preview frame, when this machine has no benchmark row for the renderer


def speed_row(speeds, it):
    """The item's renderer in tools/speeds.json: (row name, the 1080p seconds per frame, the final / preview ratio
    measured here). Web: the parallel row (the final renders with several workers). Blender: its --engine (auto counts
    as cycles: no NVIDIA GPU)."""
    if it["renderer"] == "web":
        name = "web2d"
        row = speeds.get(name) or {}
        full = (row.get("1080p_parallel") or row.get("1080p") or {}).get("s_per_frame")
    else:
        eng = arg_value(it.get("render_args") or [], "--engine", "cycles")
        name = f"blender_{eng if eng in ('workbench', 'eevee', 'cycles') else 'cycles'}"
        row = speeds.get(name) or {}
        full = (row.get("1080p") or {}).get("s_per_frame")
    pv = (row.get("540p_preview") or {}).get("s_per_frame")
    return name, full, (full / pv if full and pv else None)


def estimate_final(pdir, v=None):
    """(seconds, how) the final render of the whole plan will take here: each item's measured preview render time ×
    what a final frame costs against a preview frame on this machine for its renderer (tools/speeds.json: about ×1.3
    for the web engine here, ×3.6 for Eevee, ×6 for Cycles, whose preview also has a quarter of the samples; ×2.5
    when there's no benchmark), else its last final render, else speeds.json; plus the join, the layers, the mux
    and the checks."""
    v = v or validate(pdir)
    rec = jload(renders_path(pdir), {}) or {}
    speeds = (jload(os.path.join(HERE, "speeds.json"), {}) or {}).get("renderers") or {}
    w, h = web_size(pdir, "final")
    px = (w * h) / (1920 * 1080)
    total, how = 0.0, set()
    for it in v["items"]:
        n = it["b"] - it["a"]
        r = rec.get(it["id"]) or {}
        name, full, ratio = speed_row(speeds, it)
        if (r.get("preview") or {}).get("s_per_frame"):
            total += r["preview"]["s_per_frame"] * n * (ratio or PREVIEW_RATIO)
            how.add("the preview renders × what a final frame costs here" if ratio else f"the preview renders × {PREVIEW_RATIO:g}")
        elif (r.get("final") or {}).get("s_per_frame"):
            total += r["final"]["s_per_frame"] * n
            how.add("the last final renders")
        else:
            total += (full or (0.6 if it["renderer"] == "web" else 5.0)) * px * n
            how.add("tools/speeds.json")
    nov = sum(it["kind"] == "overlay" for it in v["items"])
    total += v.get("n", 0) * (0.03 + 0.03 * nov) * max(px, 0.25) + 60      # join, layers, mux, checks
    return total, " and ".join(sorted(how)) or "no renders yet"


# ------------------------------------------------------------------ status
def status_text(pdir):
    import critic
    pdir = os.path.abspath(pdir)
    v = validate(pdir)
    rec = jload(renders_path(pdir), {}) or {}
    L = [f"plan status: {os.path.basename(pdir)}", plan_text(v, pdir, table=False).splitlines()[0]]
    if v["errors"]:
        L.append(f"  ({len(v['errors'])} error{'s' * (len(v['errors']) != 1)}: `studio.py plan validate` lists them)")
    if v["items"]:
        L.append("segments and overlays:")
        for it in v["items"]:
            cols = []
            for q in ("preview", "final"):
                ok, why = current(pdir, it, q) if it.get("exists") else (False, "no scene")
                r = (rec.get(it["id"]) or {}).get(q) or {}
                there = os.path.exists(outputs(pdir, it, q)[1])
                cols.append(f"{q} " + ("✓" if ok else ("stale" if there else "–"))
                            + (f" ({fmt_dur(r['seconds'])})" if ok and r.get("seconds") else ""))
            label = "overlay" if it["kind"] == "overlay" else "segment"
            L.append(f"  {it['id']:<9} {mmss(it['t0'])}–{mmss(it['t1'])}  {label:<8} {cols[0]:<18} {cols[1]}"
                     + ("" if it.get("exists") else "  (no scene yet)"))
    prev = sorted(glob.glob(os.path.join(pdir, "work", "previews", "preview_*.mp4")), key=os.path.getmtime)[-3:]
    for p in prev:
        c = critic.find(pdir, rel(p, pdir), os.path.getsize(p), os.path.getmtime(p))
        L.append(f"preview {rel(p, pdir)} ({time.strftime('%H:%M', time.localtime(os.path.getmtime(p)))})"
                 + (f" · critic {critic.score_line(c.get('scores'))} ({c.get('file')})" if c else ""))
    if v["ok"]:
        sec, how = estimate_final(pdir, v)
        L.append(f"the final render: about {fmt_dur(sec)} on this machine (from {how})")
    from mv import renderlock
    d, alive = renderlock.holder()
    L.append("render queue: " + ("free" if d is None else (f"busy: {renderlock.describe(d)}" if alive else
                                                         "a stale lock (the next render takes it over)")))
    return "\n".join(L)


# ------------------------------------------------------------------ the command line
def main(argv=None, pdir=None):
    ap = argparse.ArgumentParser(prog="studio.py plan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="sub", required=True)
    sp.add_parser("validate", help="check work/plan.json")
    sp.add_parser("status", help="the plan, the renders, the previews, the final's estimate")
    for name in ("render", "assemble"):
        p = sp.add_parser(name)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--preview", action="store_true", help="half size, fast encode (the default)")
        g.add_argument("--final", action="store_true", help="the delivery size")
        if name == "render":
            p.add_argument("--only", help="ids, comma-separated: s1,s3")
        else:
            p.add_argument("--no-look", action="store_true", help="skip eyes.py look on the result")
            p.add_argument("--no-check", action="store_true", help="skip the checks")
            p.add_argument("--no-critic", action="store_true", help="skip the critic on the whole film (in an autonomous "
                           "run the run's critic then looks at the cut by itself)")
            p.add_argument("--critic-model", help="default: the run's critic, else project.json director.critic_model, else sonnet")
            p.add_argument("--critic-effort")
            p.add_argument("--max-usd", type=float)
    a = ap.parse_args(argv)
    if not pdir:
        from mv.common import need_project
        pdir = need_project()
    pdir = os.path.abspath(pdir)
    say = lambda s: print(s, flush=True)       # noqa: E731
    if a.sub == "validate":
        v = validate(pdir)
        say(plan_text(v, pdir))
        return 0 if v["ok"] else 1
    if a.sub == "status":
        say(status_text(pdir))
        return 0
    if a.sub == "render":
        only = [x.strip() for x in (a.only or "").split(",") if x.strip()] or None
        v, rs = render(pdir, "final" if a.final else "preview", only, log=say)
        return 0 if v["ok"] and not any(r["state"] == "failed" for r in rs) else 1
    model = None if a.no_critic else critic_settings(pdir, a.critic_model, a.critic_effort)[0]
    r = assemble(pdir, "final" if a.final else "preview", log=say, look=not a.no_look, check=not a.no_check,
                 critic_model=model, critic_effort=a.critic_effort, max_usd=a.max_usd)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
