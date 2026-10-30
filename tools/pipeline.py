"""The pipeline: the same shape every run. The planner (the top model) writes work/plan.json, the visual language in
code (work/scenes/lib/) and the hero segment; the studio checks the plan, renders every segment over its own frame
range, gates the language with the critic before any builder starts, and assembles previews and the final from the
plan. These commands are the studio's side of it (Python, no tokens): the autonomous runner calls the same
functions in-process, an interactive planner runs them as commands.

  studio.py pipeline validate <p>                     check work/plan.json; exit 1 with what to fix
  studio.py pipeline status <p>                       the plan, each segment's renders, stages, sessions, gates, costs
  studio.py pipeline render <p> [--preview|--final] [--only s1,s3]
        each segment over its own frame range with its renderer (web: render.py --frames a:b; blender: run.py, then
        compose.py encode) -> work/segments/<id>/<id>_<preview|final>.mp4; overlays as transparent passes
        (work/segments/<id>/<id>_<q>/, PNG with alpha); unchanged scenes come back from the cache
  studio.py pipeline assemble <p> [--preview|--final] [--no-critic] [--critic-model M] [--no-look] [--no-check]
        render what's needed, concat the segments, layer the overlays, mux the song -> work/previews/preview_N.mp4
        (half size) or final/music-video.mp4; then eyes.py look, for a final the checks, then the critic on the
        whole film (its report is printed: read it first)
  studio.py pipeline gate <p> [--model M] [--effort E] [--max-usd 2]
        the language gate: validate, the hero segment's preview, then the critic on it and the style frames ("is this
        language strong and specific enough to hand to builders, and bold enough?")
  studio.py pipeline build <p> [--only s1,s3] [--builders 3] [--builder-model M] [--builder-effort high] [--budget 30m]
                              [--critic-model M]
        builder agents (one headless Claude Code session per segment, a narrow brief, writes only in its own folders,
        renders through the render queue, one critic round and one revision) for the builder segments that aren't
        built, or --only those, or the tasks in work/pipeline/tasks.json; blocks until they're done, then a summary
  (also: studio.py run <p> pipeline.py <sub> ...)
The autonomous runner (`studio.py direct`, director.py) runs the whole pipeline with these same functions: the plan,
gate 1 (the language), the builders, the assembled previews with the critic's film report, the planner's revisions
(work/pipeline/tasks.json: [{"segment": "s3", "what": "..."}] dispatches builders again), and the final.

work/plan.json (the planner writes it; `validate` checks it; also the live preview's timeline):
  {"stage": "planning|planned|final", "hero": "s2", "lib": "work/scenes/lib/", "style_frames": ["work/style-frames/a.png"],
   "rules": "what every segment must keep (one paragraph)",
   "segments": [{"id": "s1", "name": "intro: the lantern wakes", "from": 0.0, "to": 24.24, "renderer": "web|blender",
                 "scene": "work/scenes/s1/scene.js", "owner": "planner|builder", "brief": "...", "enter": "...",
                 "exit": "...", "moments": ["12:0 the light catches"], "status": "todo|drafted|built|failed",
                 "render_args": ["--engine", "workbench"]}],
   "overlays": [{"id": "type", "scene": "work/scenes/type/type.html", "from": 0, "to": 65.42, "transparent": true,
                 "mode": "normal", "opacity": 1, "owner": "planner", "brief": "..."}]}
Segments in song order, following on without gaps or overlaps (boundaries become frames: round(t x fps)), the
first from 0, the last to the song's end; ids [a-z0-9_-]+, unique ("lib" is the lib's); each scene in its own folder
work/scenes/<id>/ (the render cache follows a scene's folder and its imports; a builder writes only there).
Every command prints a short summary (it comes back to a planner as tool output); the per-render logs are in
work/segments/<id>/render_<q>.log; render times in work/pipeline/renders.json; the stages, sessions, gates and costs
in work/pipeline/state.json.
"""
import argparse, glob, json, os, re, subprocess, sys, threading, time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, ffmpeg, h264_args, jload, jsave, project as load_pj  # noqa: E402
from mv.claude import kill_tree  # noqa: E402

PLAN = os.path.join("work", "plan.json")
ID_RE = re.compile(r"^[a-z0-9_-]+$")
STAGES = ("planning", "planned", "final")
OWNERS = ("planner", "builder")
STATUSES = ("todo", "drafted", "built", "failed")
EXTS = {"web": (".js", ".mjs", ".html", ".htm"), "blender": (".py",)}
MODES = ("normal", "screen", "add", "multiply", "lighten", "darken", "difference")
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
OWNED_FLAGS = ("--out", "-o", "--frames", "--from", "--to", "--section", "--still", "--preview", "--scale", "--transparent",
               "--width", "--height", "--fps")             # the pipeline sets these itself
DONE = ("rendered", "cached")

GATE_FOCUS = (
    "This clip is the hero segment {id} ({name}, {span}): the planner's first draft, and the visual language every "
    "other segment will be built from. Builder agents will copy it, the shared code in {lib} and the style frames "
    "(listed with the pictures) into the rest of the film, so this is the last cheap moment to change the language. "
    "Is this language strong and specific enough to hand to builders, and bold enough? Say what a builder could take "
    "from it (the rules, the motifs, how it moves on the music) and what is still vague, generic or timid; where it "
    "plays safe, say how to push it, never how to retreat. The rules every segment must keep: {rules}\nThe whole "
    "plan, for the arc this language has to carry:\n{outline}\n(The overlays, e.g. the type, aren't in this clip.)")
FILM_FOCUS = (
    "This is the whole film, assembled by the studio from the plan's segments; different agents built them from one "
    "visual language:\n{outline}\nLook at the arc across the segments, the joins ({joins}), whether the language holds "
    "from segment to segment (and where one segment plays safer than the rest), the signature moments on the ★★★ "
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


# ------------------------------------------------------------------ state (work/pipeline/state.json)
def state_path(pdir):
    return os.path.join(pdir, "work", "pipeline", "state.json")


def load_state(pdir):
    return jload(state_path(pdir), {}) or {}


STATE_LOCK = threading.RLock()                     # the runner's threads (the gate, an assemble, the builders) share it


@contextmanager
def file_lock(path, wait=5.0, stale_s=30.0):
    """A lock between processes for one bookkeeping file (path + ".lock", os.open O_CREAT | O_EXCL): the runner, a
    planner's own `pipeline assemble` or `gate` and `pipeline build` all write state.json. A lock file older than
    stale_s is left over from a writer that was killed. After `wait` s it goes on without the lock: a bookkeeping
    write never holds up the work."""
    lk = path + ".lock"
    os.makedirs(os.path.dirname(lk), exist_ok=True)
    t0, fd = time.time(), None
    while fd is None and time.time() - t0 < wait:
        try:
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):       # Windows says PermissionError while it is being removed
            try:
                if time.time() - os.path.getmtime(lk) > stale_s:
                    os.remove(lk)
                    continue
            except OSError:
                pass
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            for _ in range(20):
                try:
                    os.remove(lk)
                    break
                except FileNotFoundError:
                    break
                except OSError:                          # Windows: someone is checking its age right now
                    time.sleep(0.05)


def read_state(pdir, tries=20):
    """state.json for a change: {} only when there is none (or it is not JSON at all); a file that can't be opened
    this instant (Windows, while it is being replaced) is read again, never taken for empty."""
    p = state_path(pdir)
    for k in range(tries):
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except ValueError:
            if k == tries - 1:
                return {}
        except OSError:
            if k == tries - 1:
                raise
        time.sleep(0.05)
    return {}


def update_state(pdir, fn):
    """Change work/pipeline/state.json in place: re-read, fn(state), write atomically, under a lock between threads
    and one between processes. Everyone who writes it (the runner, a planner's gate or assemble, `pipeline build`)
    goes through here, so nobody's write is lost to an old copy."""
    with STATE_LOCK, file_lock(state_path(pdir)):
        d = read_state(pdir)
        fn(d)
        d["updated"] = time.time()
        jsave(state_path(pdir), d)
        return d


def renders_path(pdir):
    return os.path.join(pdir, "work", "pipeline", "renders.json")


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
        return None, ("there is no work/plan.json yet: write it (the format is in tools/README.md under "
                      "\"The pipeline\")")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except json.JSONDecodeError as ex:
        return None, f"work/plan.json isn't valid JSON at line {ex.lineno}, column {ex.colno}: {ex.msg}"
    except OSError as ex:
        return None, f"can't read work/plan.json: {ex}"
    if not isinstance(d, dict):
        return None, "work/plan.json must be one JSON object: {\"stage\": ..., \"segments\": [...], ...}"
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


def validate(pdir, plan=None, ready=False):
    """Check work/plan.json (or the given plan). Returns {"ok", "errors", "warnings", "plan", "stage", "hero", "items",
    "fps", "n", "duration", "w", "h"}; items are the segments then the overlays, each with its frame range [a, b)
    and its scene's absolute path. Errors say what to change, with the numbers to set. A plan that still says
    "planning" gets the lighter rules (no hero or briefs needed yet), unless ready: the gate and the builders need
    what a "planned" plan has."""
    pdir = os.path.abspath(pdir)
    v = {"ok": False, "errors": [], "warnings": [], "plan": None, "stage": None, "hero": None, "items": []}

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
    stage = plan.get("stage", "planning")
    if stage not in STAGES:
        E("stage", f"is {stage!r}: use planning (still writing it), planned (ready for the gate and the builders) or final")
        stage = "planned"
    v["stage"] = stage
    strict = ready or stage != "planning"
    hero = plan.get("hero")
    v["hero"] = hero if isinstance(hero, str) else None
    segs = plan.get("segments")
    if not isinstance(segs, list) or not segs:
        E("segments", "a list of the segments that cover the whole song in order, at least one: "
                      "[{\"id\": \"s1\", \"from\": 0, \"to\": ..., \"renderer\": \"web\", \"scene\": \"work/scenes/s1/scene.js\", ...}]")
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
            E(w, "the id lib is taken: work/scenes/lib/ is the shared lib's folder. Pick another id")
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
        owner = x.get("owner", "planner" if kind == "overlays" else None)
        if owner not in OWNERS:
            E(w, f"owner is {owner!r}: planner (you build it) or builder (a builder agent does)")
            owner = "planner"
        it["owner"] = owner
        status = x.get("status", "todo")
        if status not in STATUSES:
            E(w, f"status is {status!r}: todo, drafted, built or failed")
            status = "todo"
        it["status"] = status
        ra = x.get("render_args", [])
        if not isinstance(ra, list) or not all(isinstance(y, str) for y in ra):
            E(w, "render_args must be a list of strings, like [\"--engine\", \"workbench\"]")
            ra = []
        bad = [y for y in ra if y.split("=")[0] in OWNED_FLAGS]
        if bad:
            E(w, f"render_args can't set {', '.join(bad)}: the pipeline sets the range, the size, the output and the "
                 f"transparency itself")
            ra = [y for y in ra if y not in bad]
        it["render_args"] = ra
        sc, why = clean_path(x.get("scene"))
        it["scene"], it["scene_abs"], it["exists"] = sc, None, False
        if not sc:
            E(w, f"scene {why}")
        else:
            parts = sc.split("/")
            if sid and (parts[:2] != ["work", "scenes"] or len(parts) < 4 or parts[2] != sid):
                E(w, f"scene is {sc}: every scene lives in its own folder work/scenes/{sid}/ (the render cache follows a "
                     f"scene's folder and its imports; a builder writes only in its own folder). Use work/scenes/{sid}/"
                     f"{parts[-1]}")
            if not sc.lower().endswith(EXTS[r]):
                E(w, f"scene {sc} isn't a {r} scene ({' / '.join(EXTS[r])})")
            it["scene_abs"] = os.path.join(pdir, *parts)
            it["exists"] = os.path.isfile(it["scene_abs"])
            if not it["exists"] and (stage == "final" or status in ("drafted", "built") or (strict and sid == hero)):
                why = "the plan is final" if stage == "final" else f"its status is {status}" if status in ("drafted", "built") \
                    else "it is the hero (the gate looks at it)"
                E(w, f"scene {sc} doesn't exist, and {why}: write it, or set its status back to todo")
        return it

    for i, x in enumerate(segs):
        it = common("segments", i, x)
        if not it:
            continue
        w = it["where"]
        if strict and it["owner"] == "builder" and not str(x.get("brief") or "").strip():
            E(w, "brief is empty: a builder starts from it (what happens, how bold, the lyric plan)")
        if it["owner"] == "builder" and strict:
            for k in ("enter", "exit"):
                if not str(x.get(k) or "").strip():
                    W(w, f"no {k}: the builder needs the picture's state at its {'start' if k == 'enter' else 'end'} to make "
                         f"the join with its neighbour")
        ms = x.get("moments", [])
        if not isinstance(ms, list) or not all(isinstance(y, str) for y in ms):
            E(w, "moments must be a list of strings, like [\"12:0 the light catches\"]")
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
    # the hero, the lib, the style frames, the rules
    seg_ids = [it["id"] for it in v["items"] if it["kind"] == "segment"]
    if strict and not hero:
        E("hero", "name the hero segment: the planner's own first draft of the language (the gate looks at it)")
    elif hero is not None and hero not in seg_ids:
        E("hero", f"is {hero!r}, which isn't one of the segments ({', '.join(seg_ids) or 'none'})")
    else:
        h = next((it for it in v["items"] if it["id"] == hero and it["kind"] == "segment"), None)
        if h and h["owner"] != "planner":
            W("hero", f"{hero} is owned by a builder: the hero is the planner's own first draft (owner planner)")
    lib, why = clean_path(plan.get("lib", "work/scenes/lib/"))
    if not lib:
        E("lib", why)
    else:
        lp = lib.rstrip("/").split("/")
        if lp[:2] != ["work", "scenes"] or len(lp) != 3:
            E("lib", f"is {lib}: the shared lib is one folder in work/scenes/ (work/scenes/lib/)")
        elif lp[2] in ids:
            E("lib", f"is {lib}, the folder of {lp[2]}: the lib needs a folder of its own")
        v["lib"] = "/".join(lp) + "/"
        lib_abs = os.path.join(pdir, *lp)
        if strict and not (os.path.isdir(lib_abs) and os.listdir(lib_abs)):
            W("lib", f"{v['lib']} is empty or missing: put the visual language in code there (palette, motifs, how things "
                     f"move on the music) so the builders build from the same code")
    sf = plan.get("style_frames", [])
    if not isinstance(sf, list):
        E("style_frames", "must be a list of image paths, like [\"work/style-frames/a.png\"]")
        sf = []
    v["style_frames"] = []
    for k, x in enumerate(sf):
        p, why = clean_path(x)
        if not p:
            E(f"style_frames[{k}]", why)
            continue
        if not p.lower().endswith(IMAGES):
            E(f"style_frames[{k}]", f"{p} isn't a picture ({' / '.join(IMAGES)})")
        elif not os.path.isfile(os.path.join(pdir, p)):
            (E if strict else W)(f"style_frames[{k}]", f"{p} doesn't exist")
        else:
            v["style_frames"].append(p)
    if strict and not sf:
        W("style_frames", "none: the gate shows the critic the hero preview and the style frames (2-3 stills of the "
                          "target look, e.g. work/style-frames/), and builders get them")
    if strict and not str(plan.get("rules") or "").strip():
        W("rules", "empty: one paragraph every segment must keep (the builders get it word for word)")
    # a segment that imports another segment's folder is no longer on its own
    folders = {it["id"]: os.path.normcase(os.path.dirname(it["scene_abs"])) + os.sep for it in v["items"] if it.get("scene_abs")}
    for it in v["items"]:
        if it["renderer"] != "web" or not it["exists"]:
            continue
        from mv import web
        for f in web.reached(it["scene_abs"], pdir):
            other = next((k for k, d in folders.items() if k != it["id"] and os.path.normcase(f).startswith(d)), None)
            if other:
                W(it["where"], f"its scene imports {rel(f, pdir)}, from {other}'s folder: share code through the lib "
                               f"instead (else a change in {other} renders {it['id']} again, and {other}'s builder can "
                               f"change {it['id']}'s look)")
                break
    v["ok"] = not v["errors"]
    return v


def plan_text(v, pdir=None, table=True):
    """The validation as a few readable lines."""
    segs = [it for it in v["items"] if it["kind"] == "segment"]
    ovs = [it for it in v["items"] if it["kind"] == "overlay"]
    L = []
    if v["ok"]:
        L.append(f"plan.json: valid (stage {v['stage']}) · {len(segs)} segment{'s' * (len(segs) != 1)}"
                 + (f" + {len(ovs)} overlay{'s' * (len(ovs) != 1)}" if ovs else "")
                 + f" · {mmss(v['duration'])} at {v['fps']} fps · hero {v['hero'] or '-'}")
    else:
        L.append(f"plan.json: NOT valid: {len(v['errors'])} error{'s' * (len(v['errors']) != 1)}"
                 + (f", {len(v['warnings'])} warning{'s' * (len(v['warnings']) != 1)}" if v["warnings"] else "")
                 + ". Fix the errors in work/plan.json, then validate again.")
    if table and v["items"]:
        for it in v["items"]:
            kind = it["owner"] + (" " + it["status"] if it["kind"] == "segment" else " overlay")
            if it["kind"] == "overlay":
                kind += f" ({'transparent' if it.get('transparent') else 'opaque'}, {it.get('mode')})"
            fr = f"frames {it['a']}–{it['b']}"
            L.append(f"  {it['id']:<9} {mmss(it['t0'])}–{mmss(it['t1'])}  {fr:<18} {it['renderer']:<7} "
                     f"{kind:<15} {it['scene'] or '?'}{'' if it['exists'] else ' (not written yet)'}"
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


class Aborted(RuntimeError):
    """A long step was stopped (the user's Stop, the studio starting the final, the run's end)."""


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
    except BaseException:                          # Ctrl+C: nothing keeps rendering behind the planner's back
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


def folder_newest(d, skip=()):
    """The newest mtime among a scene folder's own files (not renders, eyes, caches)."""
    t = 0.0
    for root, dirs, fs in os.walk(d):
        dirs[:] = [x for x in dirs if not x.endswith((".parts", ".frames")) and x not in ("__pycache__", "renders", "eyes")]
        for f in fs:
            p = os.path.join(root, f)
            if p not in skip and not f.endswith((".mp4", ".mov", ".mv.json", ".part", ".tmp")):
                try:
                    t = max(t, os.path.getmtime(p))
                except OSError:
                    pass
    return t


def current(pdir, it, q):
    """Is the item's render for quality q still the scene as it is now? (True/False, why not). Web: the cache key in
    the render's sidecar (the scene's folder, what it imports, the runtime, the song data, the settings); Blender:
    the scene's folder and the render settings against the last render."""
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
    newest = max(folder_newest(os.path.dirname(it["scene_abs"])),
                 os.path.getmtime(os.path.join(HERE, "blender", "mvblend.py")))
    if newest > os.path.getmtime(out.rstrip("/\\") + ".mv.json"):
        return False, "the scene changed"
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
    who = f"{os.environ.get('MV_RENDER_WHO') or 'pipeline'}: {it['id']} {q}"
    env = tool_env(pdir, who)
    pv = ["--preview"] if q == "preview" else []
    tr = ["--transparent"] if it["kind"] == "overlay" and it.get("transparent") else []
    args = list(it.get("render_args") or [])
    rng = f"{it['a']}:{it['b']}"
    if it["renderer"] == "web":
        cmds = [["web-engine/render.py", it["scene"], "--frames", rng, "--out", out, *pv, *tr, *args]]
    else:
        frames = out if tr else os.path.join(d, f"{it['id']}_{q}_frames")
        # run.py keeps frames while the scene file is unchanged: a change elsewhere in its folder starts over
        side = out.rstrip("/\\") + ".mv.json"
        fresh = ["--fresh"] if os.path.exists(side) and \
            folder_newest(os.path.dirname(it["scene_abs"]), skip=(it["scene_abs"],)) > os.path.getmtime(side) else []
        cmds = [["blender/run.py", it["scene"], "--frames", rng, "--out", frames, *pv, *tr, *fresh, *args]]
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
            with STATE_LOCK, file_lock(renders_path(pdir)):     # builders render in their own processes too
                rec = jload(renders_path(pdir), {}) or {}
                r = rec.setdefault(it["id"], {}).setdefault(q, {})
                r.update(range=[it["a"], it["b"]], renderer=it["renderer"], args=args, at=time.strftime("%Y-%m-%d %H:%M:%S"))
                if done:                             # the render time per frame, for the final's estimate
                    w, h = web_size(pdir, q)
                    r.update(seconds=res["seconds"], frames=sum(done), s_per_frame=round(res["seconds"] / max(1, sum(done)), 4),
                             w=w, h=h)
                jsave(renders_path(pdir), rec)
        except Exception as ex:                      # bookkeeping: the render is there either way
            log(f"   (couldn't record the render in work/pipeline/renders.json: {ex})")
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
            out.append(f"- {it['id']} {mmss(it['t0'])}–{mmss(it['t1'])} ({it['owner']}{', the hero' if it['id'] == v['hero'] else ''}"
                       f"{', ' + it['renderer'] if it['renderer'] != 'web' else ''}): {it['name'] or '-'}")
        else:
            out.append(f"- overlay {it['id']} {mmss(it['t0'])}–{mmss(it['t1'])}: {it['name'] or '-'}")
    return "\n".join(out)


def critic_settings(pdir, model=None, effort=None):
    """(model, effort) of the critic: the given ones, else the run's (MV_CRITIC_MODEL / MV_CRITIC_EFFORT: a planner in an
    autonomous run uses the run's critic), else the project's setting."""
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


def stand_in_text(x):
    """One stand-in of a final, in words."""
    if x.get("last"):
        return f"{x['id']} shows its own last final render, from before its scene broke ({x['why']})"
    return f"{x['id']} shows {x['scene_of']}'s scene ({x['why']})"


def last_render(pdir, it, q):
    """The item's own earlier render at quality q, over its range and from its own scene (not a stand-in's), or None:
    a broken scene leaves its last good render in place (a render only replaces its output when it succeeds)."""
    out = outputs(pdir, it, q)[1]
    side = jload(out.rstrip("/\\") + ".mv.json", None)
    if os.path.isfile(out) and isinstance(side, dict) and side.get("range") == [it["a"], it["b"]] \
            and not side.get("placeholder") and side.get("scene", it["scene"]) == it["scene"]:
        return out
    return None


def stand_in(v, it, bad=()):
    """A last resort for the final: the segment's nearest neighbour with a scene (the one before it first) rendered
    over its range instead. Every segment speaks the same language, so the film holds where a part is missing.
    Returns the item to render (its own output paths, the neighbour's scene) or None."""
    segs = [x for x in v["items"] if x["kind"] == "segment"]
    i = next((k for k, x in enumerate(segs) if x["id"] == it["id"]), None)
    if i is None:
        return None
    for j in sorted(range(len(segs)), key=lambda j: (abs(j - i), j > i)):
        x = segs[j]
        if j != i and x.get("exists") and x["id"] not in bad:
            return dict(it, scene=x["scene"], scene_abs=x["scene_abs"], exists=True, renderer=x["renderer"],
                        render_args=x["render_args"], stand_in=x["id"])
    return None


def assemble(pdir, quality="preview", log=print, look=True, check=True, critic_model=None, critic_effort=None, max_usd=None,
             abort=None, stand_ins=False):
    """Render what's needed, then concat the segments, layer the overlays and mux the song: a preview (half size,
    work/previews/preview_N.mp4; a segment that's missing or failed gets a labelled slate) or the final
    (final/music-video.mp4; every segment must be there, unless stand_ins: then the last resort, a neighbour's scene
    over a missing or failed segment's range (its own last final render first, when its scene broke after one), and
    an overlay without a render left out). Then eyes.py look; the
    checks (a preview: flash and text; a final: all of them); with a critic model the critic on the whole film.
    abort() turning true stops it. Returns {"ok", "video", "renders", "placeholders", "stand_ins", "checks", "sync",
    "check_lines", "critique", "seconds", "error"}."""
    pdir = os.path.abspath(pdir)
    t0 = time.time()
    res = {"ok": False, "quality": quality, "video": None, "renders": [], "placeholders": [], "stand_ins": [], "checks": None,
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
    if quality == "final" and not stand_ins:       # say so before rendering anything
        missing = [it["id"] for it in v["items"] if not it["exists"]]
        if missing:
            res["error"] = (f"the final needs every segment and overlay; no scene yet for {', '.join(missing)}. Build them (or "
                            f"give their time to a neighbour in plan.json), then assemble again")
            log(f"assemble (final): stopped: {res['error']}")
            return res
    v, rs = render(pdir, quality, log=log, v=v, abort=abort)
    if stopped():
        return halt()
    byid = {it["id"]: it for it in v["items"]}
    if quality == "final" and stand_ins:           # the last resort where a segment is missing or broken: its own last
        bad = {r["id"] for r in rs if r["kind"] == "segment" and r["state"] not in DONE}     # final render, else a neighbour's scene
        for k, r in enumerate(rs):
            if r["kind"] != "segment" or r["state"] in DONE:
                continue
            own = last_render(pdir, byid[r["id"]], quality)
            if own:
                log(f"   {r['id']}: {r['error'] or r['state']}: the studio uses its last final render (from before its scene broke)")
                rs[k] = dict(r, state="cached", out=own, stand_in=r["id"])
                res["stand_ins"].append({"id": r["id"], "scene_of": r["id"], "why": r["error"] or r["state"], "last": True})
                continue
            sub = stand_in(v, byid[r["id"]], bad)
            if not sub:
                continue
            log(f"   {r['id']}: {r['error'] or r['state']}: the studio stands in {sub['stand_in']}'s scene over its range")
            r2 = render_item(pdir, sub, quality, log, abort=abort)
            if r2["state"] in DONE:
                rs[k] = dict(r2, stand_in=sub["stand_in"])
                res["stand_ins"].append({"id": r["id"], "scene_of": sub["stand_in"], "why": r["error"] or r["state"]})
        if stopped():
            return halt()
    res["renders"] = rs
    segs = [r for r in rs if r["kind"] == "segment"]
    holes = [r for r in segs if r["state"] not in DONE]
    if holes and quality == "final":
        res["error"] = ("the final needs every segment: " + "; ".join(f"{r['id']}: {r['error']}" for r in holes)
                        + ". Build them (or give their time to a neighbour in plan.json), then assemble again")
        log(f"assemble (final): stopped: {res['error']}")
        return res
    parts = []
    for r in segs:
        if r["state"] in DONE:
            parts.append(r["out"])
        else:
            why = "not built yet" if r["state"] == "missing" else "its render failed"
            parts.append(placeholder(pdir, byid[r["id"]], quality, why, fps))
            res["placeholders"].append(r["id"])
    adir = os.path.join(pdir, "work", "pipeline", "assemble")
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
    if skipped and quality == "final" and not stand_ins:
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
            f"preview (not built yet or its render failed)")
    if res["stand_ins"]:
        log("   NOTE: stand-ins in this final: " + "; ".join(stand_in_text(x) for x in res["stand_ins"]))
    if skipped:
        log(f"   NOTE: overlay{'s' * (len(skipped) != 1)} {', '.join(skipped)} left out (no render yet)")
    if look and not stopped():
        import eyes
        rc, ls = run_tool(pdir, ["eyes.py", "look", out], logfile=logf, log=log, abort=abort)
        idx = os.path.join(eyes.out_dir(out), "index.md")
        sync = next((x for x in ls if x.startswith("sync: ")), "")
        res["sync"] = sync[6:].split(" -> ")[0] if sync else None
        log(f"   eyes: {rel(idx, pdir)}" + (f" ({res['sync']})" if res["sync"] else "") if rc == 0 else
            f"   eyes.py look failed (log: {rel(logf, pdir)})")
    if check and not stopped():
        names = ["all"] if quality == "final" else ["flash", "text"]      # a preview: the checks that apply at any size
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
    if stopped():
        return dict(halt(), ok=True)
    if critic_model and critic_model != "off":
        seg_items = [it for it in v["items"] if it["kind"] == "segment"]
        joins = ", ".join(f"{a['id']}→{b['id']} at {mmss(b['t0'])}" for a, b in zip(seg_items, seg_items[1:]))
        holes_txt = (f"\n{', '.join(res['placeholders'])}: not built yet (a labelled slate stands in): judge the rest, and "
                     f"say what those segments must do.") if res["placeholders"] else ""
        if res["stand_ins"]:
            holes_txt += "\n" + "; ".join(stand_in_text(x) for x in res["stand_ins"]) + " (stand-ins: the segment's own scene "\
                "couldn't be rendered)."
        focus = FILM_FOCUS.format(outline=outline(v), joins=joins or "one segment: no joins", holes=holes_txt)
        log(f"\nthe critic on the whole film ({critic_model}):")
        c = run_critic(pdir, out, focus, critic_model, critic_effort, max_usd, log=log, abort=abort)
        if c:
            res["critique"] = {k: c.get(k) for k in ("file", "scores", "verdict", "cost_usd", "model")}
        elif not stopped():
            log("   the critic couldn't run (see above)")
    res["seconds"] = round(time.time() - t0)

    def rec(st):
        st.setdefault("assembles", []).append({k: res.get(k) for k in ("quality", "video", "placeholders", "stand_ins", "checks",
                                                                       "sync", "critique", "seconds")} | {"at": time.time()})
        del st["assembles"][:-30]
    try:
        update_state(pdir, rec)
    except Exception as ex:                        # bookkeeping: the film is made either way
        log(f"   (couldn't record it in work/pipeline/state.json: {ex})")
    log(f"assemble ({quality}): done in {fmt_dur(res['seconds'])} -> {res['video']}")
    return res


# ------------------------------------------------------------------ the language gate
def gate(pdir, model=None, effort=None, max_usd=None, log=print, abort=None):
    """Gate 1, the language: validate, render the hero segment's preview (from the cache when unchanged), then the
    critic on it and the style frames. Returns {"ok", "errors", "critique", "video", "error"}; the gate is recorded in
    work/pipeline/state.json ("gates")."""
    pdir = os.path.abspath(pdir)
    res = {"ok": False, "errors": [], "critique": None, "video": None, "error": "", "text": ""}
    v = validate(pdir, ready=True)                 # what builders start from: the hero, the briefs (even if it says planning)
    res["text"] = plan_text(v, pdir, table=False)
    log(res["text"])
    hero = next((it for it in v["items"] if it["id"] == v["hero"] and it["kind"] == "segment"), None)
    if not v["ok"] or not hero:
        res.update(errors=v["errors"] or ["hero: name the hero segment (the planner's own first draft: the gate looks at it)"],
                   error="plan.json isn't ready for the gate (the hero, the builders' briefs: what a planned plan has)")
        return res
    v, rs = render(pdir, "preview", only=[hero["id"]], log=log, v=v, abort=abort)
    if abort and abort():
        res["error"] = "stopped"
        return res
    if not rs or rs[0]["state"] not in DONE:
        res["error"] = f"the hero's preview couldn't be made: {rs[0]['error'] if rs else 'no render'}"
        log(f"gate: {res['error']}")
        return res
    video = rs[0]["out"]
    res["video"] = rel(video, pdir)
    model, effort = critic_settings(pdir, model, effort)
    if model == "off":
        log("gate: the plan is valid and the hero preview is there; the critic is off for this project "
            "(project.json director.critic_model), so nobody else looks at the language")
        res["ok"] = True
        return res
    focus = GATE_FOCUS.format(id=hero["id"], name=hero["name"] or "no name", span=f"{mmss(hero['t0'])}–{mmss(hero['t1'])}",
                              lib=v.get("lib") or "work/scenes/lib/", rules=str(v["plan"].get("rules") or "(none written)").strip(),
                              outline=outline(v))
    log(f"\nthe critic on the language: {res['video']}" + (f" + {len(v['style_frames'])} style frame"
                                                            f"{'s' * (len(v['style_frames']) != 1)}" if v["style_frames"] else ""))
    c = run_critic(pdir, video, focus, model, effort, max_usd, pictures=v["style_frames"], log=log, abort=abort)
    if not c:
        res["error"] = "the critic couldn't run (see above)"
        log(f"gate: {res['error']}")
        return res
    import critic
    res["critique"] = {k: c.get(k) for k in ("file", "scores", "verdict", "cost_usd", "model")}
    res["ok"] = True

    def rec(st):
        st.setdefault("gates", []).append({"gate": "language", "at": time.time(), "video": res["video"],
                                           "style_frames": v["style_frames"], **res["critique"]})
    try:
        update_state(pdir, rec)
    except Exception as ex:                        # bookkeeping: the critic's report is in work/critic/ either way
        log(f"   (couldn't record the gate in work/pipeline/state.json: {ex})")
    log(f"gate (language): {critic.result_line(c)}\nThis is the last cheap moment to change the language: revise the lib, "
        f"the style frames and the hero if the critic is right (push harder where it says you played safe), then lock it.")
    return res


# ------------------------------------------------------------------ plan.json edits and tasks (the studio's side)
PLAN_LOCK = threading.Lock()


def edit_plan(pdir, fn):
    """Change work/plan.json in place (re-read, fn(plan) -> True when it changed something, write atomically): the
    planner may be editing the file, so only the fields fn touches change. False when plan.json can't be read now."""
    with PLAN_LOCK:
        plan, _ = load_plan(pdir)
        if plan is None:
            return False
        if fn(plan):
            jsave(os.path.join(pdir, PLAN), plan)
        return True


def set_status(pdir, changes):
    """Segments' status in work/plan.json ({id: built|failed|...}), nothing else touched."""
    def fn(plan):
        hit = False
        for x in plan.get("segments") or []:
            if isinstance(x, dict) and x.get("id") in changes and x.get("status") != changes[x["id"]]:
                x["status"] = changes[x["id"]]
                hit = True
        return hit
    return edit_plan(pdir, fn)


def set_stage(pdir, stage):
    return edit_plan(pdir, lambda plan: plan.get("stage") != stage and (plan.update(stage=stage) or True))


def tasks_path(pdir):
    return os.path.join(pdir, "work", "pipeline", "tasks.json")


def read_tasks(pdir, v=None):
    """work/pipeline/tasks.json, the planner's fix tasks for builders: [{"segment": "s3", "what": "..."}] ->
    ({id: what}, [problems]). Two tasks for one segment become one."""
    p = tasks_path(pdir)
    if not os.path.exists(p):
        return {}, []
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except json.JSONDecodeError as ex:
        return {}, [f"work/pipeline/tasks.json isn't valid JSON at line {ex.lineno}, column {ex.colno}: {ex.msg}"]
    except OSError as ex:
        return {}, [f"can't read work/pipeline/tasks.json: {ex}"]
    if isinstance(d, dict):
        d = d.get("tasks") if isinstance(d.get("tasks"), list) else [d]
    if not isinstance(d, list):
        return {}, ["work/pipeline/tasks.json must be a list: [{\"segment\": \"s3\", \"what\": \"...\"}]"]
    v = v or validate(pdir)
    segs = [it["id"] for it in v["items"] if it["kind"] == "segment"]
    tasks, problems = {}, []
    for k, x in enumerate(d):
        if not isinstance(x, dict):
            problems.append(f"tasks[{k}] must be an object {{\"segment\": \"s3\", \"what\": \"...\"}}")
            continue
        sid, what = x.get("segment"), str(x.get("what") or "").strip()
        if sid not in segs:
            problems.append(f"tasks[{k}]: segment is {sid!r}, which isn't one of the segments ({', '.join(segs)})")
        elif not what:
            problems.append(f"tasks[{k}] ({sid}): what is empty: say what the builder must change, and why")
        else:
            tasks[sid] = (tasks[sid] + "\n" + what) if sid in tasks else what
    return tasks, problems


def consume_tasks(pdir, round_no):
    """Move tasks.json aside once its builders are dispatched (work/pipeline/tasks_done/), so it runs once."""
    p = tasks_path(pdir)
    if os.path.exists(p):
        d = os.path.join(pdir, "work", "pipeline", "tasks_done")
        os.makedirs(d, exist_ok=True)
        os.replace(p, os.path.join(d, f"tasks_r{round_no}_{time.strftime('%H%M%S')}.json"))


# ------------------------------------------------------------------ the builders
BUILDER_HUNG_S = 15 * 60         # busy with no events for this long: the session is hung (a render waits in a Bash call)
BUILDER_GRACE_S = 180            # after its deadline, before the studio stops it
MIN_BUILD_S = 10 * 60            # a builder isn't started with less time left than this
BUILDER_SYSTEM = ("You are running unattended inside Music Video Studio as a builder: you build one segment of one music "
                  "video, inside the visual language its planner locked. Nobody answers questions: decide and keep going. "
                  "Write only in your own two folders (the brief names them); the studio tools may be read and run, not "
                  "edited. No network: everything you need is installed. Watch the clock.")
BUILD_FOCUS = (
    "This clip is segment {id} ({name}, {span}), built by a builder agent from the planner's brief, inside the visual "
    "language the planner locked (the shared code in {lib}, the style frames listed with the pictures, the rules every "
    "segment keeps: {rules}).\nIts brief: {brief}\nIt enters as: {enter}. It exits as: {exit}.{neighbours}{task}\n"
    "Judge it against its brief, the joins with its neighbours (where it picks up and where it hands over), and the "
    "language. Ambition first: where it plays safe inside the language, takes the brief's easiest reading, or repeats "
    "the hero segment's moves instead of building on them, say how to push it, never how to retreat.")
BUILDER_CRITIC = (
    "The studio's critic ({model}, fresh eyes: it saw only your segment's frames, the style frames, the concept and the "
    "song, not your reasoning) looked at {video}. One revision now: take what's right, push harder where it says you "
    "played safe (never retreat to something safer), ignore what's wrong. Then render again (`{render}`), note in "
    "work/segments/{id}/log.md what you took and why, and end your turn by {by}. That is your last turn.\n\n{report}")


def hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def song_range_text(pdir, t0, t1, context=2):
    """The song over a segment's range, written down for a builder (it doesn't read the song folder): the tempo and
    key, the sections it touches, every bar (and `context` bars on each side, for the joins), the ★ moments and the
    sung lines inside it."""
    from mv.song import Song
    S = Song(pdir)
    m, t = S.m, S.m["tempo"]
    L = [f"The song: {mmss(S.duration)} long, {t['bpm']} BPM {t.get('meter') or ''}, a bar is {t['bar_s']:.2f} s ({t.get('grid')}); "
         f"key {m['key']['name']}." + ("".join(f" {g['section']}: {g['bpm']} BPM." for g in t.get("other_grid") or []))]
    L.append("\nThe sections your range touches:")
    for x in S.sections:
        if x["t1"] > t0 and x["t0"] < t1:
            L.append(f"- {x['name']}: bars {x['bar0']}–{x['bar1'] - 1}, {mmss(x['t0'])}–{mmss(x['t1'])}, {x['loud']} LUFS, "
                     f"voice {x['voice']} · drums {x['drums']} · bass {x['bass']} · other {x['other']} (0-9)"
                     + (f"; it starts with “{x['first_line']}”" if x.get("first_line") else ""))
    inside = [b for b in S.bars if b["end"] > t0 + 0.01 and b["t"] < t1 - 0.01]
    if inside:
        i0, i1 = max(0, inside[0]["i"] - context), min(len(S.bars) - 1, inside[-1]["i"] + context)
        L.append("\nBar by bar (· = outside your range, for the joins): bar, time, section, chord, loudness (LUFS), "
                 "voice / drums / bass / other (0-9), the sung words @ the beat they land on, what happens:")
        for b in S.bars[i0:i1 + 1]:
            mark = " " if b["end"] > t0 + 0.01 and b["t"] < t1 - 0.01 else "·"
            ws = " ".join(f"{w[0]}@{w[1]:g}" for w in b.get("words") or [])
            L.append(f"{mark}{b['i']:4d} {mmss(b['t'])} {str(b.get('section') or ''):<12} {str(b.get('chord') or ''):<6} "
                     f"{b['loud']:6.1f} V{b['v']} D{b['d']} B{b['b']} O{b['o']}  {ws}"
                     + (f"  [{', '.join(b['events'])}]" if b.get("events") else ""))
    ms = [x for x in m["moments"] if t0 - 0.05 <= x["t"] < t1]
    L.append("\nThe moments inside your range (★★★ = where the video's rules should change):")
    L += [f"- {mmss(x['t'])} (bar:beat {x['pos']}) {'★' * x['stars']} {x['text']}" for x in ms] or ["- none"]
    lines = [x for x in S.lines if x["end"] > t0 and x["t"] < t1]
    L.append("\nThe sung lines over your range (the time each word is sung):")
    for x in lines:
        p = S.pos(x["t"])
        L.append(f"- {mmss(x['t'])}–{mmss(x['end'])}" + (f" (bar {p[0]}:{p[1]:g})" if p else "") + f" “{x['text']}”: "
                 + " ".join(f"{w['w']}@{w['t']:.2f}" for w in x.get("words") or []))
    if not lines:
        L.append("- none (no singing here)")
    return "\n".join(L)


def builder_deny(pdir, v, sid):
    """The builder's write deny rules (Claude Code's absolute //path form, as director.claude_args): everything of the
    project that isn't its own two folders and that matters (the plan, the concept, the lib, the other segments, the
    final, the report, the review notes, the user's settings, the song)."""
    from director import rule_path
    others = sorted({it["id"] for it in v["items"]} - {sid})
    files = ["work/plan.json", "work/concept.md", "work/song-notes.md", "work/cast.md", "work/log.md", "report.md", "project.json",
             "work/live.json"]
    dirs = (["work/scenes/lib", "work/style-frames", "work/pipeline", "work/director", "work/previews", "final", "review", "song",
             "refs"] + [f"work/scenes/{x}" for x in others] + [f"work/segments/{x}" for x in others])
    rules = []
    for f in files:
        rp = rule_path(os.path.join(pdir, *f.split("/")))
        rules += [f"Edit({rp})", f"Write({rp})"]
    for d in dirs:
        rp = rule_path(os.path.join(pdir, *d.split("/")))
        rules += [f"Edit({rp}/**)", f"Write({rp}/**)"]
    return rules


def builder_brief(pdir, v, it, deadline, draft_by, critic_model=None, task=None, last=None, python=None, tools=None):
    """templates/build-brief.md filled for one segment: a narrow context (its part of the plan, the language, the song
    over its range as text, the craft rules) so the builder doesn't re-read the song folder."""
    import critic
    from director import fill, fmt_dur as dur, venv_python
    plan = v["plan"]
    raw = {x.get("id"): x for x in plan.get("segments") or [] if isinstance(x, dict)}
    segs = [x for x in v["items"] if x["kind"] == "segment"]
    i = next(k for k, x in enumerate(segs) if x["id"] == it["id"])
    me = raw.get(it["id"]) or {}
    name = os.path.basename(pdir)
    tools = tools or rel(os.path.join(STUDIO, "tools"), pdir)
    python = python or venv_python(pdir)
    nb = []
    if i > 0:
        p = segs[i - 1]
        nb.append(f"  - Before you: {p['id']} ({p['name'] or '-'}, {mmss(p['t0'])}–{mmss(p['t1'])}, {p['owner']}); it ends as: "
                  f"{str((raw.get(p['id']) or {}).get('exit') or '(not written)').strip()}")
    else:
        nb.append("  - Before you: nothing: you open the film (the first frame the viewer sees).")
    if i + 1 < len(segs):
        x = segs[i + 1]
        nb.append(f"  - After you: {x['id']} ({x['name'] or '-'}, {mmss(x['t0'])}–{mmss(x['t1'])}, {x['owner']}); it starts as: "
                  f"{str((raw.get(x['id']) or {}).get('enter') or '(not written)').strip()}")
    else:
        nb.append("  - After you: nothing: you end the film (the last frame, the ending lands).")
    raw_ov = {x.get("id"): x for x in plan.get("overlays") or [] if isinstance(x, dict)}
    ovs = [x for x in v["items"] if x["kind"] == "overlay" and x["t1"] > it["t0"] and x["t0"] < it["t1"]]
    over = "\n".join(f"  - {x['id']} ({x['name'] or x['scene']}, {'transparent' if x.get('transparent') else 'opaque'}, "
                     f"{x.get('mode')}, the planner's): {str((raw_ov.get(x['id']) or {}).get('brief') or '-').strip()}"
                     for x in ovs) or "  - none"
    lib = v.get("lib") or "work/scenes/lib/"
    lib_abs = os.path.join(pdir, *lib.rstrip("/").split("/"))
    lib_files = []
    for root, _, fs in os.walk(lib_abs):
        for f in sorted(fs):
            fp = os.path.join(root, f)
            lib_files.append(f"  - `{rel(fp, pdir)}` ({os.path.getsize(fp)} bytes)")
    hero = v.get("hero")
    h = next((x for x in segs if x["id"] == hero), None)
    if h and h["id"] != it["id"]:
        hero_txt = (f"{h['id']} ({h['name'] or '-'}, {mmss(h['t0'])}–{mmss(h['t1'])}): its scene `{h['scene']}`, its preview "
                    f"`work/segments/{h['id']}/{h['id']}_preview.mp4`. Read its code to see how the language is used: match its "
                    f"craft and its rules, not its moves (a copy of the hero is the safe option; the brief wants your segment).")
    elif h:
        hero_txt = "your segment is the hero: the first draft that set the bar. Raise it."
    else:
        hero_txt = "(none named)"
    cp = os.path.join(pdir, "work", "concept.md")
    concept = open(cp, encoding="utf-8").read().strip() if os.path.exists(cp) else "(the planner hasn't written work/concept.md)"
    if len(concept) > 9000:
        concept = concept[:9000].rsplit("\n", 1)[0] + "\n[... the rest is in work/concept.md]"
    render = f"{python} {tools}/studio.py pipeline render {name} --preview --only {it['id']}"
    d = delivery(load_pj(pdir))
    pw, ph = web_size(pdir, "preview")
    if task:
        tk = (f"\n## Your task this round (from the planner)\n{task.strip()}\n\nYour segment exists already "
              f"(work/scenes/{it['id']}/): change what the task asks, keep what works, and don't let the fix make it safer.")
        if last and (last.get("report") or "").strip():
            rep = last["report"].strip()
            rep = rep[:5000].rsplit("\n", 1)[0] + "\n[...]" if len(rep) > 5000 else rep
            tk += f"\nThe critic's last notes on your segment ({last.get('file')}):\n\n{rep}\n"
    else:
        tk = ""
    crit = (f"The studio's critic ({critic_model}, a cheaper model with fresh eyes) then looks at your preview and sends you "
            f"its notes as a message (2-4 min): one revision, a new render, and you end your turn again. You're done then."
            if critic_model and critic_model != "off" else
            "No critic in this run: when your preview is rendered and you've checked what you need, end your turn: you're done.")
    strip = f"`{python} {tools}/studio.py run {name} eyes.py strip work/segments/{it['id']}/{it['id']}_preview.mp4 --at <t>"
    if critic_model and critic_model != "off":   # the looking is the critic's: images are what a builder's context saves
        look = ("Don't look at your render's frames yourself: the looking is the critic's job (a cheaper model with fresh "
                "eyes; it sends you what it saw). The one exception: the render failed or came out visibly broken (black, "
                f"nothing moving) and you need one frame to see why: then one {strip} --n 1`.")
    else:
        look = (f"No critic looks at it in this run, so check what you need yourself, one question at a time: {strip}` at your "
                f"strongest moment, or `eyes.py beats work/segments/{it['id']}/{it['id']}_preview.mp4 --bars <a> <b>` (do the "
                f"downbeats land?).")
    vals = {"id": it["id"], "name": it["name"] or it["id"], "span": f"{mmss(it['t0'])}–{mmss(it['t1'])}",
            "frames": f"{it['a']}:{it['b']}", "nframes": it["b"] - it["a"], "fps": v["fps"], "renderer": it["renderer"],
            "scene": it["scene"], "readme": f"{tools}/{'web-engine' if it['renderer'] == 'web' else 'blender'}/README.md",
            "brief": str(me.get("brief") or "(no brief written: take it from the concept and the song)").strip(),
            "enter": str(me.get("enter") or "(not written)").strip(), "exit": str(me.get("exit") or "(not written)").strip(),
            "moments": "\n".join(f"  - {x}" for x in me.get("moments") or []) or "  - (none named: the ★ moments below)",
            "neighbours": "\n".join(nb), "overlays": over, "rules": str(plan.get("rules") or "(none written)").strip(),
            "concept": concept, "lib": lib, "lib_files": "\n".join(lib_files) or "  - (empty)",
            "style_frames": "\n".join(f"  - `{x}`" for x in (v.get("style_frames") or [])[:3]) or "  - (none)", "hero": hero_txt,
            "song": song_range_text(pdir, it["t0"], it["t1"]), "craft": critic.craft_rules(), "anti": critic.anti_defaults(),
            "render": render, "deadline": hm(deadline), "draft_by": hm(draft_by), "minutes": dur(max(0, deadline - time.time())),
            "critic": crit, "look": look, "task": tk, "python": python, "tools": tools, "project": name,
            "size": f"{d['width']}×{d['height']}", "preview_size": f"{pw}×{ph}"}
    return fill(open(os.path.join(STUDIO, "templates", "build-brief.md"), encoding="utf-8").read(), vals)


class Builder:
    """One builder: a Claude Code session (mv/session.py) on one segment: its brief, its segment preview, the
    critic's round, one revision. States: queued -> working -> (rendering) -> critic -> revising -> (rendering) ->
    built | failed | stopped | skipped (never started)."""
    ACTIVE = ("working", "rendering", "critic", "revising", "stopping")
    END = ("built", "failed", "stopped", "skipped")

    def __init__(self, rnd, it, task=None):
        self.r, self.it, self.id, self.task = rnd, it, it["id"], task
        self.name = f"builder {self.id}"
        self.state, self.phase = "queued", "draft"
        self.sdir = os.path.join(rnd.pdir, "work", "pipeline", "sessions", f"b{rnd.round}-{self.id}")
        self.status = {"name": self.name, "role": "builder", "segment": self.id, "round": rnd.round, "model": rnd.model,
                       "effort": rnd.effort, "state": "queued", "task": task}
        self.s, self.job = None, None
        self.warned = set()
        self.critique, self.error = None, ""
        self.fixes = self.reminded = 0
        self.started = self.ended = None

    def log(self, line):
        try:
            os.makedirs(self.sdir, exist_ok=True)
            with open(os.path.join(self.sdir, "studio.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass

    def set(self, state):
        self.state = state
        self.status["state"] = state

    def save(self):
        if self.s:
            jsave(os.path.join(self.sdir, "status.json"), dict(self.status, updated=time.time()))

    def render_cmd(self):
        return f"{self.r.python} {self.r.tools}/studio.py pipeline render {os.path.basename(self.r.pdir)} --preview --only {self.id}"

    def start(self, now):
        import director
        from mv.session import Session
        r = self.r
        os.makedirs(self.sdir, exist_ok=True)
        try:
            text = builder_brief(r.pdir, r.v, self.it, r.deadline, self.draft_by(), r.critic_model if r.critic_on else None,
                                 self.task, r.last.get(self.id), r.python, r.tools)
            open(os.path.join(self.sdir, "brief.md"), "w", encoding="utf-8").write(text)
            cmd = director.claude_args(r.pdir, r.model, r.effort, deny=builder_deny(r.pdir, r.v, self.id),
                                       settings=os.path.join(self.sdir, "settings.json"), system=BUILDER_SYSTEM)
        except (Exception, SystemExit) as ex:
            return self.finish("failed", f"its session couldn't start: {ex}")
        jsave(os.path.join(self.sdir, "command.json"), {"cmd": cmd, "cwd": r.pdir})
        env = dict(os.environ, MV_PROJECT=r.pdir, PYTHONIOENCODING="utf-8", MV_RENDER_WHO=self.name,
                   MV_DEADLINE=str(int(r.deadline)), MV_DEADLINE_WHAT=f"{self.name}'s build time",
                   MV_CRITIC_MODEL=r.critic_model if r.critic_on else "off", MV_CRITIC_EFFORT=r.critic_effort or "")
        self.s = Session(cmd, r.pdir, self.sdir, name=self.name, status=self.status, env=env, started=r.t0)
        try:
            self.s.start()
        except OSError as ex:
            return self.finish("failed", f"its session couldn't start: {ex}")
        self.started = now
        self.status.update(started=now, log=rel(os.path.join(self.sdir, "events.jsonl"), r.pdir),
                           brief=rel(os.path.join(self.sdir, "brief.md"), r.pdir))
        self.s.send(text, "the brief")
        self.set("working")
        self.log(f"started: {self.id} until {hm(r.deadline)}" + (f" (task: {self.task[:120]})" if self.task else ""))
        self.save()

    def draft_by(self):
        """When its first render should be done: room left for the critic's round and one revision."""
        room = max(60.0, self.r.deadline - time.time())
        return self.r.deadline - min(8 * 60 if self.r.critic_on else 2 * 60, 0.4 * room)

    def spawn(self, kind, fn):
        job = {"kind": kind, "done": False, "result": None}

        def run():
            try:
                job["result"] = fn()
            except Exception as ex:
                self.log(f"{kind} failed: {ex}")
                job["result"] = None if kind == "critic" else {"state": "failed", "error": str(ex)[:300], "log": None}
            job["done"] = True
        self.job = job
        threading.Thread(target=run, daemon=True).start()

    def fresh_item(self):
        """Its plan entry as it is now (the plan may have changed), else the one it started with."""
        try:
            v = validate(self.r.pdir)
            it = next((x for x in v["items"] if x["id"] == self.id and x["kind"] == "segment"), None)
            if it and not it.get("bad_range"):
                self.r.v = v if v["ok"] else self.r.v
                return it
        except Exception as ex:
            self.log(f"validate failed: {ex}")
        return dict(self.it, exists=bool(self.it.get("scene_abs")) and os.path.isfile(self.it["scene_abs"]))

    def poll(self, now):
        if self.state in self.END or self.state == "queued":
            return
        j = self.job
        if j and j["done"]:
            self.job = None
            return getattr(self, "on_" + j["kind"])(j["result"], now)
        if self.state not in ("working", "revising"):
            return                                   # a render or the critic in a thread, or it is being stopped
        s, r = self.s, self.r
        if not s.alive():
            return self.stop(f"its session ended (exit {s.returncode})", kind="crashed")
        if s.hung(now, BUILDER_HUNG_S):
            return self.stop(f"no sign of life from it for {fmt_dur(BUILDER_HUNG_S)}", kind="hung")
        if now > r.deadline + BUILDER_GRACE_S:
            return self.stop("its build time ran out", kind="late")
        if now > r.deadline and "up" not in self.warned:
            self.warned.add("up")
            s.send(f"Your build time is up ({hm(r.deadline)}). If your preview isn't rendered, render it now (`{self.render_cmd()}`) "
                   f"and end your turn: the studio stops this session in {BUILDER_GRACE_S // 60} minutes.", "time's up")
        elif 0 < r.deadline - now <= 300 and "five" not in self.warned:
            self.warned.add("five")
            s.send(f"5 minutes of your build time left (until {hm(r.deadline)}): render what you have (`{self.render_cmd()}`) "
                   f"and end your turn. Nothing new.", "5 minutes")
        if s.turn_over(now):
            s.ack()
            self.after_turn(now)

    def after_turn(self, now):
        it = self.it = self.fresh_item()
        if not it.get("exists"):
            if self.reminded < 2 and now < self.r.deadline:
                self.reminded += 1
                self.s.send(f"There is no {it['scene']} yet: write your scene there, render it (`{self.render_cmd()}`) and end "
                            f"your turn.", "no scene yet")
                return
            return self.finish("failed", f"it wrote no scene ({it['scene']})")
        if current(self.r.pdir, it, "preview")[0]:
            return self.rendered(now)
        self.log("its preview isn't the scene as it is now: the studio renders it")
        self.set("rendering")
        self.spawn("render", lambda: render_item(self.r.pdir, it, "preview", log=self.log, abort=self.r.abort.is_set))

    def on_render(self, res, now):
        if res and res.get("state") in DONE:
            return self.rendered(now)
        err = (res or {}).get("error") or "no render"
        if self.fixes < 1 and self.r.deadline - now > 4 * 60 and self.s.alive() and not self.r.stopping:
            self.fixes += 1
            self.set("revising" if self.phase == "revision" else "working")
            lg = (res or {}).get("log")
            self.s.send(f"Your segment's render failed: {err}" + (f" (the log: {rel(lg, self.r.pdir)})" if lg else "")
                        + f". Fix the scene, render it again (`{self.render_cmd()}`) and end your turn.", "the render failed")
            return
        self.finish("failed", f"its render failed: {err}")

    def rendered(self, now):
        r = self.r
        if self.phase == "draft" and r.critic_on and r.deadline - now > 4 * 60 and not r.stopping:
            self.set("critic")
            self.spawn("critic", lambda: r.critic_for(self))
        else:
            self.finish("built", "" if self.phase == "revision" or not r.critic_on else "no time was left for the critic's round")

    def on_critic(self, c, now):
        r = self.r
        if not c:
            return self.finish("built", "" if r.stopping else "the critic couldn't run: no revision round")
        self.critique = {k: c.get(k) for k in ("file", "scores", "verdict", "cost_usd", "model", "video")}
        self.status["critique"] = self.critique
        if r.stopping or not self.s.alive() or now > r.deadline - 60:
            return self.finish("built", "no time was left for its revision")
        rep = (c.get("report") or "").strip()
        if len(rep) > 6000:
            rep = rep[:6000].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
        self.phase = "revision"
        self.set("revising")
        self.s.ack()
        self.s.send(BUILDER_CRITIC.format(model=c.get("model") or "the critic", video=c.get("video"), render=self.render_cmd(),
                                          id=self.id, by=hm(r.deadline), report=rep), f"critic: {c.get('video')}")

    def finish(self, state, why=""):
        if self.state in self.END:
            return
        self.set(state)
        self.error = why
        self.ended = time.time()
        self.status.update(ended=self.ended, error=why)
        s = self.s
        if s and s.alive():                          # its work is done: let it exit (and stop it if it doesn't)
            threading.Thread(target=lambda: (s.stop(why or state), s.finish(), self.save()), daemon=True).start()
        elif s:
            s.finish()
        if state in ("built", "failed"):
            set_status(self.r.pdir, {self.id: state})
        self.log(f"{state}" + (f": {why}" if why else ""))
        self.save()
        self.r.on_event(state, self, why)

    def stop(self, why, kind="late"):
        """Stop it (kind: late, hung, crashed, forced = the studio starts the final, user = the user's Stop). What it
        rendered stands when its preview is the scene as it is (built); otherwise it failed. A user's Stop leaves the
        plan as it was (stopped)."""
        if self.state in self.END or self.state == "stopping":
            return
        if self.state == "queued" or self.s is None:
            return self.finish("skipped", why)
        self.set("stopping")

        def run():
            s = self.s
            if s.alive() and kind in ("forced", "user") and s.busy:
                s.send(f"The studio is stopping the builders now ({why}): stop where you are and end your turn.", "stop")
                t0 = time.time()
                while time.time() - t0 < 60 and s.alive() and not s.turn_over():
                    time.sleep(1)
            s.stop(why, wait=20)
            t0 = time.time()
            while self.job and not self.job["done"] and time.time() - t0 < 60:
                time.sleep(0.5)                      # a render or a critique it was waiting for (abort stops those)
            self.job = None
            if kind == "user":
                return self.finish("stopped", why)
            it = self.fresh_item()
            ok = bool(it.get("exists")) and current(self.r.pdir, it, "preview")[0]
            self.finish("built" if ok else "failed", why + ("; its last preview stands" if ok else ""))
        threading.Thread(target=run, daemon=True).start()

    def summary(self):
        st = dict(self.status)
        st.pop("events", None)
        st.pop("messages_sent", None)
        st["last"] = self.s.tail(6) if self.s else []
        st["cost_usd"] = round(float(self.status.get("cost_usd") or 0), 4)
        return st


class BuildRound:
    """Builders for some segments, at most `most` at once (the rest wait their turn), each with its brief, one critic
    round and one revision. The autonomous runner polls it every second (stage 2, and the fix rounds of stage 4);
    `studio.py pipeline build` runs one for an interactive planner. on_event(state, builder, why) hears each end."""

    def __init__(self, pdir, v, ids, tasks=None, most=3, model=None, effort="high", critic_model=None, critic_effort=None,
                 max_usd=None, deadline=None, round_no=1, started=None, on_event=None, last=None, python=None, tools=None):
        from director import venv_python
        self.pdir, self.v = os.path.abspath(pdir), v
        self.most, self.model, self.effort = max(1, int(most)), model, effort or "high"
        self.critic_model, self.critic_effort, self.max_usd = critic_model, critic_effort, max_usd
        self.critic_on = bool(critic_model and critic_model != "off")
        self.deadline = deadline or time.time() + 30 * 60
        self.round, self.t0 = round_no, started or time.time()
        # a queued builder needs this long to be worth starting (less when the whole round is shorter: --budget 6m)
        self.min_start = min(MIN_BUILD_S, 0.8 * max(0.0, self.deadline - time.time()))
        self.on_event = on_event or (lambda state, b, why: None)
        self.last = last or {}                       # {id: the segment's last critique (with "report")}: fix rounds
        self.python = python or venv_python(self.pdir)
        self.tools = tools or rel(os.path.join(STUDIO, "tools"), self.pdir)
        self.sem = threading.Semaphore(2)            # critiques at once (each runs the eyes, then one call)
        self.abort = threading.Event()
        self.stopping = None
        self.critiques = []
        items = {it["id"]: it for it in v["items"] if it["kind"] == "segment"}
        self.builders = [Builder(self, items[i], (tasks or {}).get(i)) for i in ids if i in items]

    def poll(self, now=None):
        now = now or time.time()
        running = sum(b.state in Builder.ACTIVE for b in self.builders)
        for b in self.builders:
            if b.state == "queued" and not self.stopping and running < self.most:
                if self.deadline - now < self.min_start:
                    b.finish("skipped", f"no time was left to start its builder (it needs {fmt_dur(self.min_start)})")
                else:
                    b.start(now)
                    running += b.state in Builder.ACTIVE
            b.poll(now)

    @property
    def done(self):
        return all(b.state in Builder.END for b in self.builders)

    def stop(self, why, kind="forced"):
        """Stop every builder (the studio starts the final, the build time is up, the user's Stop)."""
        if not self.stopping:
            self.stopping = why
        if kind in ("forced", "user"):
            self.abort.set()
        for b in self.builders:
            b.stop(why, kind)

    def retire(self):
        """The round is over: a render or a critique still running for a builder that was stopped (a "late" stop waits
        a minute for them, then lets the builder go) stops now, so nothing keeps the render queue or keeps spending."""
        self.abort.set()

    def critic_for(self, b):
        it = b.it
        video = outputs(self.pdir, it, "preview")[1]
        plan = self.v["plan"]
        raw = next((x for x in plan.get("segments") or [] if isinstance(x, dict) and x.get("id") == b.id), {})
        segs = [x for x in self.v["items"] if x["kind"] == "segment"]
        i = next((k for k, x in enumerate(segs) if x["id"] == b.id), 0)
        nb = ""
        if i > 0:
            nb += f"\nBefore it: {segs[i - 1]['id']} ({segs[i - 1]['name'] or '-'}), up to {mmss(it['t0'])}."
        if i + 1 < len(segs):
            nb += f"\nAfter it: {segs[i + 1]['id']} ({segs[i + 1]['name'] or '-'}), from {mmss(it['t1'])}."
        focus = BUILD_FOCUS.format(id=b.id, name=it["name"] or b.id, span=f"{mmss(it['t0'])}–{mmss(it['t1'])}",
                                   lib=self.v.get("lib") or "work/scenes/lib/", rules=str(plan.get("rules") or "(none written)").strip(),
                                   brief=str(raw.get("brief") or "-").strip(), enter=str(raw.get("enter") or "-").strip(),
                                   exit=str(raw.get("exit") or "-").strip(), neighbours=nb,
                                   task=f"\nThis round's task from the planner: {b.task}" if b.task else "")
        with self.sem:
            if self.abort.is_set():
                return None
            b.log(f"the critic ({self.critic_model}) looks at {rel(video, self.pdir)}")
            c = run_critic(self.pdir, video, focus, self.critic_model, self.critic_effort, self.max_usd,
                           pictures=self.v.get("style_frames") or (), log=b.log, abort=self.abort.is_set)
        if c:
            self.critiques.append({"t": round(time.time() - self.t0), "video": c.get("video"), "scores": c.get("scores"),
                                   "verdict": c.get("verdict"), "file": c.get("file"), "cost_usd": c.get("cost_usd"),
                                   "model": c.get("model"), "kind": "builder", "who": b.name, "used": f"sent to {b.name}"})
        return c

    def sessions(self):
        return [b.summary() for b in self.builders]

    def cost(self):
        return round(sum(float(b.status.get("cost_usd") or 0) for b in self.builders), 4)

    def lines(self):
        """One line per builder, for the planner (the assemble report) and the command line."""
        import critic
        out = []
        for b in self.builders:
            c = b.critique or {}
            rq = os.path.join(self.pdir, "work", "segments", b.id, "requests.md")
            has_rq = os.path.exists(rq) and b.started and os.path.getmtime(rq) >= b.started - 1
            out.append(f"- {b.id}: {b.state.upper() if b.state in ('failed', 'skipped', 'stopped') else b.state}"
                       + (f" ({b.error})" if b.error else "")
                       + (f" · critic {critic.score_line(c.get('scores'))} ({c.get('file')})" if c.get("file") else "")
                       + (f" · requests: work/segments/{b.id}/requests.md" if has_rq else "")
                       + f" · ${float(b.status.get('cost_usd') or 0):.2f}")
        return out

    def requests(self, most=1500):
        """The builders' requests for the lib (work/segments/<id>/requests.md written in this round), trimmed."""
        out = []
        for b in self.builders:
            rq = os.path.join(self.pdir, "work", "segments", b.id, "requests.md")
            if os.path.exists(rq) and b.started and os.path.getmtime(rq) >= b.started - 1:
                t = open(rq, encoding="utf-8").read().strip()
                out.append(f"{b.id} (work/segments/{b.id}/requests.md):\n" + (t[:most].rsplit("\n", 1)[0] + "\n[...]" if len(t) > most else t))
        return out


def build(pdir, only=None, most=3, model=None, effort="high", budget="30m", critic_model=None, critic_effort=None,
          max_usd=None, log=print):
    """`studio.py pipeline build`: builders for an interactive planner. The segments: --only, else the tasks in
    work/pipeline/tasks.json (moved aside once dispatched), else every builder segment that isn't built. Blocks until
    the builders and their critic rounds are done (or the budget, or `studio.py stop`), then prints a summary.
    Returns the exit code (0: every one built)."""
    import director
    pdir = os.path.abspath(pdir)
    cur = director.current(pdir) or {}
    if director.run_active(pdir):
        log(f"pipeline build: autonomous run {cur.get('run')} is going on in this project (its runner dispatches the "
            f"builders); wait for it to end, or stop it (`studio.py stop {os.path.basename(pdir)}`: it wraps up within a "
            f"minute or two)")
        return 1
    if cur.get("state") in ("running", "finishing"):
        log(f"pipeline build: (run {cur.get('run')} never finished: its runner stopped writing at "
            f"{time.strftime('%H:%M', time.localtime(float(cur.get('updated') or 0)))}, so it was cut off; going on)")
    v = validate(pdir, ready=True)
    if not v["ok"]:
        log(plan_text(v, pdir, table=False))
        return 1
    tasks, problems = read_tasks(pdir, v) if not only else ({}, [])
    if problems:
        log("pipeline build: work/pipeline/tasks.json has problems:\n" + "\n".join(f"  - {p}" for p in problems))
        return 1
    segs = [it for it in v["items"] if it["kind"] == "segment"]
    if only:
        bad = [x for x in only if x not in [it["id"] for it in segs]]
        if bad:
            log(f"pipeline build: no segment {', '.join(bad)} in the plan ({', '.join(it['id'] for it in segs)})")
            return 1
        ids = list(only)
    elif tasks:
        ids = list(tasks)
    else:
        ids = [it["id"] for it in segs if it["owner"] == "builder" and it["status"] != "built"]
    if not ids:
        log("pipeline build: nothing to build (no builder segment that isn't built, no tasks in work/pipeline/tasks.json; "
            "--only s1,s3 builds those)")
        return 0
    try:
        budget_s = director.parse_budget(budget)
    except ValueError as ex:
        log(f"pipeline build: {ex}")
        return 1
    pj = load_pj(pdir)
    model = model or (pj.get("director") or {}).get("model") or "opus"
    cm, ce = critic_settings(pdir, critic_model, critic_effort)
    t0 = time.time()
    stop_f = os.path.join(pdir, "work", "director", "STOP")
    if os.path.exists(stop_f):
        os.remove(stop_f)
    prev_round = max([int(s.get("round") or 0) for s in load_state(pdir).get("sessions") or [] if isinstance(s, dict)] or [0])
    last = {}
    import critic
    for sid in ids:                                  # a fix round: the builder gets the segment's last critique
        c = critic.find(pdir, f"work/segments/{sid}/{sid}_preview.mp4") if tasks else None
        if c:
            last[sid] = critic.load(pdir, c["file"])
    events = []
    rnd = BuildRound(pdir, v, ids, tasks, most, model, effort, cm, ce, max_usd, t0 + budget_s, prev_round + 1, t0,
                     on_event=lambda state, b, why: events.append((state, b.id, why)), last=last)
    if tasks:
        consume_tasks(pdir, rnd.round)
    interactive(pdir, "build")
    log(f"pipeline build: {len(ids)} builder{'s' * (len(ids) != 1)} for {', '.join(ids)} ({model}, effort {effort}; at most "
        f"{rnd.most} at once; critic {cm if rnd.critic_on else 'off'}) until {hm(rnd.deadline)}"
        + (" (the tasks in work/pipeline/tasks.json)" if tasks else ""))

    def publish(state="running"):
        def fn(st):
            st.update(mode="interactive", stage="build", state=state, build_round=rnd.round, build_by=rnd.deadline)
            if st.get("build_round_started") != rnd.round:         # this round's start (status: "since ...")
                st.update(stage_started=t0, build_round_started=rnd.round)
            ss = [x for x in st.get("sessions") or [] if isinstance(x, dict) and not (x.get("role") == "builder"
                                                                                        and x.get("round") == rnd.round)]
            st["sessions"] = ss + rnd.sessions()
            segs_st = st.setdefault("segments", {})
            for b in rnd.builders:
                segs_st[b.id] = dict(segs_st.get(b.id) or {}, status=b.state, builder=b.name, error=b.error,
                                     critique=b.critique, round=rnd.round)
            co = st.setdefault("costs", {})
            co["builders"] = round(sum(float(x.get("cost_usd") or 0) for x in st["sessions"] if x.get("role") == "builder"), 4)
            st.setdefault("critiques", [])
            known = {x.get("file") for x in st["critiques"]}
            st["critiques"] += [c for c in rnd.critiques if c.get("file") not in known]
            co["critic"] = round(max(float(co.get("critic") or 0), sum(float(c.get("cost_usd") or 0) for c in st["critiques"])), 4)
        try:
            update_state(pdir, fn)
        except Exception as ex:                      # bookkeeping: the builders work on either way
            log(f"   (couldn't write work/pipeline/state.json: {ex})")
    said, last_pub = 0, 0.0
    try:
        while not rnd.done:
            time.sleep(1)
            now = time.time()
            if os.path.exists(stop_f) and not rnd.stopping:
                log("pipeline build: stopped by the user (studio.py stop): the builders wrap up")
                rnd.stop("stopped by the user", "user")
            rnd.poll(now)
            for state, sid, why in events[said:]:
                log(f"  {sid}: {state}" + (f" ({why})" if why else ""))
            said = len(events)
            if now - last_pub > 2:
                publish()
                last_pub = now
    except BaseException:                            # Ctrl+C: no builder keeps spending behind the planner's back
        rnd.stop("interrupted", "user")
        for b in rnd.builders:
            if b.s and b.s.alive():
                kill_tree(b.s.proc)
        publish("stopped")
        raise
    for state, sid, why in events[said:]:
        log(f"  {sid}: {state}" + (f" ({why})" if why else ""))
    rnd.retire()
    time.sleep(1)
    publish("stopped" if rnd.stopping and "user" in str(rnd.stopping) else "done")
    log(f"\npipeline build: done in {fmt_dur(time.time() - t0)}; builders ${rnd.cost():.2f}")
    for x in rnd.lines():
        log(x)
    rq = rnd.requests()
    if rq:
        log("\nthe builders' requests (they can't change the lib):\n" + "\n\n".join(rq))
    ok = all(b.state == "built" for b in rnd.builders)
    log("\nnext: look at what they made with `studio.py pipeline assemble <p> --preview` (the critic on the whole film)"
        + ("" if ok else "; a failed or skipped segment: build it yourself, or `pipeline build --only <id>` again"))
    return 0 if ok else 1


def interactive(pdir, stage):
    """An interactive planner's `gate`, `assemble` or `build` (not one inside an autonomous run, which owns state.json)
    makes state.json its own: mode interactive, this stage, running. An autonomous run's state.json is kept in
    work/pipeline/history/state_run-<n>.json first (the Direct page still shows that run from there), and so is an
    interactive one quiet for 12 h (another day's session), so the page shows "Your pipeline" with what this session
    starts. Returns False inside an autonomous run."""
    import director
    if director.run_active(pdir):
        return False
    now = time.time()

    def fn(st):
        old = st.get("mode") != "interactive" or now - float(st.get("updated") or 0) > 12 * 3600   # or another day's
        if st and old:
            hd = os.path.join(pdir, "work", "pipeline", "history")
            jsave(os.path.join(hd, f"state_run-{st['run']}.json" if st.get("run") else f"state_{time.strftime('%Y%m%d-%H%M%S')}.json"), st)
            st.clear()
        st.setdefault("interactive_started", now)
        st.update(mode="interactive", stage=stage, state="running", stage_started=now)
    try:
        update_state(pdir, fn)
    except Exception:
        return False
    return True


def interactive_end(pdir, state):
    try:
        update_state(pdir, lambda st: st.update(state=state) if st.get("mode") == "interactive" else None)
    except Exception:
        pass


# ------------------------------------------------------------------ the clock: estimates and the stages' times
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


def estimate_rough(pdir, overlays=1):
    """(seconds, how) for the final render before there is a valid plan: the whole song with the web engine at the
    delivery size (tools/speeds.json), and `overlays` whole-song transparent passes on top (a plan nearly always has
    the type as one), plus the join, the layers, the mux and the checks."""
    song = song_facts(pdir)
    if not song:
        return 0.0, "no song folder"
    row = ((jload(os.path.join(HERE, "speeds.json"), {}) or {}).get("renderers") or {}).get("web2d") or {}
    spf = (row.get("1080p_parallel") or row.get("1080p") or {}).get("s_per_frame", 0.6)
    px = (song["w"] * song["h"]) / (1920 * 1080)
    return (song["n"] * spf * px * (1 + overlays) + song["n"] * (0.03 + 0.03 * overlays) * max(px, 0.25) + 60,
            "tools/speeds.json (the web engine: the whole song" + (" and one overlay pass" if overlays == 1 else
                                                                   f" and {overlays} overlay passes" if overlays else "") + ")")


GATE_S = 5 * 60                  # the gate: the critic on the language, and the planner's answer to it (at least)
BUILD_MIN_S = 10 * 60            # a builder round needs at least this long
FINAL_MARGIN_S = 8 * 60          # the final starts when the time left is its estimate × 1.3 + this


def final_need(est):
    return est * 1.3 + FINAL_MARGIN_S


def stage_times(budget_s, est, started=0.0, assemble_s=None, revise=False):
    """The pipeline's clock for a budget and a final-render estimate (seconds): when the plan stage ends (30% of the
    budget; a review round has none), when the builders must be done (62% of the budget, at least BUILD_MIN_S after
    the gate, but never later than the preview assemble and 5 min before the final), when the final starts at the
    latest (its estimate × 1.3 + 8 min before the end). ok: the stages fit, i.e. a builder round of at least
    BUILD_MIN_S fits between the plan stage plus the gate and the builders' end. One rule for `--mode auto`, the
    planner's brief, the web app's hint and the runner (PipelineRun.build_deadline)."""
    asm = assemble_s if assemble_s is not None else max(120.0, est * 0.5)
    plan_by = started if revise else started + 0.30 * budget_s
    gate_end = plan_by + (0 if revise else GATE_S)
    final_by = started + budget_s - final_need(est)
    build_by = min(final_by - asm - 5 * 60, max(started + 0.62 * budget_s, gate_end + BUILD_MIN_S))
    window = build_by - gate_end
    return {"plan_by": plan_by, "build_by": build_by, "final_by": final_by, "assemble": asm, "window": window,
            "ok": window >= BUILD_MIN_S}


def pipeline_from(est, revise=False):
    """The smallest budget (seconds, whole minutes, at least 45 min) whose stages fit with this estimate."""
    m = 45
    while m < 24 * 60 and not stage_times(m * 60, est, revise=revise)["ok"]:
        m += 1
    return m * 60


# ------------------------------------------------------------------ status


def status_text(pdir):
    pdir = os.path.abspath(pdir)
    v = validate(pdir)
    st = load_state(pdir)
    rec = jload(renders_path(pdir), {}) or {}
    L = [f"pipeline status: {os.path.basename(pdir)}"]
    if st.get("stage"):
        since = st.get("stage_started")
        L.append(f"stage: {st['stage']}" + (f" (round {st['round']})" if st.get("round") else "")
                 + (f" since {time.strftime('%H:%M', time.localtime(since))}" if is_num(since) else "")
                 + (f" · run {st['run']}" if st.get("run") else "") + (f" · mode {st['mode']}" if st.get("mode") else ""))
    elif not v["plan"]:
        L.append("no pipeline yet: no work/plan.json")
    L.append(plan_text(v, pdir, table=False).splitlines()[0])
    if v["errors"]:
        L.append(f"  ({len(v['errors'])} error{'s' * (len(v['errors']) != 1)}: `studio.py pipeline validate` lists them)")
    if v["items"]:
        L.append("segments:")
        for it in v["items"]:
            cols = []
            for q in ("preview", "final"):
                ok, why = current(pdir, it, q) if it.get("exists") else (False, "no scene")
                r = (rec.get(it["id"]) or {}).get(q) or {}
                there = os.path.exists(outputs(pdir, it, q)[1])
                cols.append(f"{q} " + ("✓" if ok else ("stale" if there else "–"))
                            + (f" ({fmt_dur(r['seconds'])})" if ok and r.get("seconds") else ""))
            label = "overlay" if it["kind"] == "overlay" else it["status"]
            L.append(f"  {it['id']:<9} {mmss(it['t0'])}–{mmss(it['t1'])}  {it['owner']:<7} {label:<7}  {cols[0]:<18} {cols[1]}"
                     + ("" if it.get("exists") else "  (no scene yet)"))
    sessions = st.get("sessions") or []
    if isinstance(sessions, dict):
        sessions = [dict(x, name=k) for k, x in sessions.items()]
    if sessions:
        L.append("sessions:")
        for s in sessions:
            L.append(f"  {(s.get('name') or s.get('role') or '?'):<12} {s.get('model') or '?'}/{s.get('effort') or '?'}  "
                     f"{s.get('state') or '?':<9} ${float(s.get('cost_usd') or 0):.2f}  {s.get('turns') or 0} turns"
                     + (f"  {s['log']}" if s.get("log") else ""))
    import critic
    for g in st.get("gates") or []:
        L.append(f"gate {g.get('gate')}: {time.strftime('%H:%M', time.localtime(g['at'])) if is_num(g.get('at')) else ''} "
                 f"{critic.score_line(g.get('scores'))} · {g.get('file')}")
    for a in (st.get("assembles") or [])[-3:]:
        c = a.get("critique") or {}
        L.append(f"assembled {a.get('quality')}: {a.get('video')}"
                 + (f" (slates: {', '.join(a['placeholders'])})" if a.get("placeholders") else "")
                 + (f" · checks {a['checks']}" if a.get("checks") else "")
                 + (f" · critic {critic.score_line(c.get('scores'))} ({c.get('file')})" if c.get("file") else ""))
    costs = {}
    for s in sessions:
        role = str(s.get("role") or s.get("name") or "?").split(" ")[0]
        costs[role] = costs.get(role, 0.0) + float(s.get("cost_usd") or 0)
    crit = sum(float((g.get("cost_usd") or 0)) for g in st.get("gates") or []) + \
        sum(float(((a.get("critique") or {}).get("cost_usd") or 0)) for a in st.get("assembles") or [])
    crit = max(crit, float((st.get("costs") or {}).get("critic") or 0))
    if crit:
        costs["critic"] = crit
    if costs:
        L.append("costs: " + " · ".join(f"{k} ${x:.2f}" for k, x in costs.items()) + f" · total ${sum(costs.values()):.2f}")
    prev = sorted(glob.glob(os.path.join(pdir, "work", "previews", "preview_*.mp4")), key=os.path.getmtime)[-3:]
    if prev:
        L.append("previews: " + ", ".join(f"{rel(p, pdir)} ({time.strftime('%H:%M', time.localtime(os.path.getmtime(p)))})" for p in prev))
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
    ap = argparse.ArgumentParser(prog="studio.py pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="sub", required=True)
    sp.add_parser("validate", help="check work/plan.json")
    sp.add_parser("status", help="the plan, the renders, the stages, sessions, gates and costs")
    for name in ("render", "assemble"):
        p = sp.add_parser(name)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--preview", action="store_true", help="half size, fast encode (the default)")
        g.add_argument("--final", action="store_true", help="the delivery size")
        if name == "render":
            p.add_argument("--only", help="ids, comma-separated: s1,s3")
        else:
            p.add_argument("--no-look", action="store_true", help="skip eyes.py look on the result")
            p.add_argument("--no-check", action="store_true", help="skip the checks on a final")
            p.add_argument("--no-critic", action="store_true", help="skip the critic on the whole film")
            p.add_argument("--critic-model", help="default: project.json director.critic_model, else sonnet")
            p.add_argument("--critic-effort")
            p.add_argument("--max-usd", type=float)
    p = sp.add_parser("gate", help="the language gate: the critic on the hero preview and the style frames")
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--max-usd", type=float)
    p = sp.add_parser("build", help="builder agents for the builder segments (or --only ids, or the tasks in work/pipeline/tasks.json)")
    p.add_argument("--only", help="segment ids, comma-separated: s1,s3 (any owner)")
    p.add_argument("--builders", type=int, default=3, help="at most this many at once (the rest wait their turn)")
    p.add_argument("--builder-model", help="default: project.json director.model, else opus")
    p.add_argument("--builder-effort", default="high")
    p.add_argument("--budget", default="30m", help="the builders' time (each gets until then)")
    p.add_argument("--critic-model", help="the critic's round on each segment (default: the project's critic; off = none)")
    p.add_argument("--critic-effort")
    p.add_argument("--max-usd", type=float, help="spending cap per critique")
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
    if a.sub in ("assemble", "gate"):
        mine = interactive(pdir, a.sub)            # an interactive planner's: the Direct page shows it
        end = "stopped"
        try:
            if a.sub == "assemble":
                model = None if a.no_critic else critic_settings(pdir, a.critic_model, a.critic_effort)[0]
                r = assemble(pdir, "final" if a.final else "preview", log=say, look=not a.no_look, check=not a.no_check,
                             critic_model=model, critic_effort=a.critic_effort, max_usd=a.max_usd)
            else:
                r = gate(pdir, a.model, a.effort, a.max_usd, log=say)
            end = "done" if r["ok"] else "failed"
        finally:
            if mine:
                interactive_end(pdir, end)
        return 0 if r["ok"] else 1
    if a.sub == "build":
        only = [x.strip() for x in (a.only or "").split(",") if x.strip()] or None
        return build(pdir, only, a.builders, a.builder_model, a.builder_effort, a.budget, a.critic_model, a.critic_effort,
                     a.max_usd, log=say)
    return 1


if __name__ == "__main__":
    sys.exit(main())
