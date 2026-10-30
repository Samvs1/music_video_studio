"""Live preview: the web app plays a project's scenes in the browser with the song, no render, so the one person who
can hear (the user) gets their ears in early, mid-run. Three parts:

  LiveServer(pdir)   a small HTTP server for one project on 127.0.0.1 (an ephemeral port, GET only, no-store: an edited
                     scene shows on reload) with exactly the renderer's URL space (mv/web.py Router.file_for:
                     /_mv/runtime, /_mv/vendor, /_mv/fonts, /_mv/song, the project's files at /), plus the live shell
                     /_mv/live.html?scene=<path from the project>&scale=0.5&transparent=0|1&seed=1: the renderer's shell
                     with the runtime in live mode (mv.js "live preview": the parent page owns the clock). An HTML scene
                     is sent to its own URL (?mv-live=1&...) and gets the same head, config included. It serves nothing
                     outside the project, the runtime, the vendored libraries and the fonts; /_mv/abs/ only for files
                     under the studio's tools/, tests/ and assets/ (the examples, the tests' scenes: not another project,
                     the studio's .git, cache or private notes). Every page gets a Content-Security-Policy that keeps it
                     off the network (as a render is: a scene that fetches from the internet fails live too). The web
                     app starts one per project, lazily.
  state(pdir)        what the live view can play: the timelines, in order: work/live.json (the director's override);
                     work/plan.json (the plan tools' segments in their slots, a Blender segment as its rendered
                     preview or a labelled slate, the overlays on top); the newest renders in work/renders/ (from their .mv.json
                     sidecars: a web scene plays live, any other pass as its video; opaque passes by range, the newest
                     wins; transparent passes on top); each pitch's draft plan (work/pitches/round-K/pN/plan.json). And
                     the scenes there are, each playable alone.
  notes              review/notes.json read with care and changed atomically (update_notes: a lock, a re-read, the
                     change by id, an atomic replace), since the web app and the director's runner both write it, and
                     the director marks notes done through it too (note_done: `studio.py note-done <p> <id> "<fix>"`).
                     Live notes have "version": "live", the scene the user saw and the timeline it was in.

A layer (what the player stacks, bottom to top): {id, kind: scene|video|slate, label, scene (a path from the project),
src (the live shell's URL on the live server), video (a path from the project), video_t0 (the song time of the
video's first frame), slots: [[a, b], ...] (song frames, b excluded), transparent, mode, opacity, note, version}.
"""
import glob, json, os, re, threading, time, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from .common import STUDIO, FONTS_DIR, PROJECTS, delivery, file_lock, jload, jsave
from . import web

WEB_EXT = (".js", ".mjs", ".html", ".htm")
BLEND = {"normal": "normal", "screen": "screen", "add": "plus-lighter", "multiply": "multiply", "lighten": "lighten",
         "darken": "darken", "difference": "difference"}          # compose.py's modes as CSS mix-blend-mode
CTYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".json": "application/json",
          ".wasm": "application/wasm", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}   # (Windows' registry can say text/plain for .js)
SKIP_DIRS = ("renders", "eyes", "node_modules", "__pycache__")
NOTES = os.path.join("review", "notes.json")
ABS_DIRS = ("tools", "tests", "assets")          # the studio's folders a live page may load from by /_mv/abs/
CSP = ("default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "       # this server only: no network, as in a render
       "frame-ancestors http://127.0.0.1:* http://localhost:*")              # only a page on this machine may frame it


def _under(p, root, f):
    p, root = f(p), f(root)
    return p == root or p.startswith(root.rstrip("\\/") + os.sep)


def inside(p, root):
    """p is root or below it: by its name first (a path that isn't, e.g. a \\\\host\\share one on Windows, is never
    opened: that would reach out to the network), then with symbolic links followed (case as the file system has it)."""
    lex = lambda x: os.path.normcase(os.path.abspath(x))
    if os.path.splitdrive(lex(p))[0] != os.path.splitdrive(lex(root))[0] or not _under(p, root, lex):
        return False
    return _under(p, root, lambda x: os.path.normcase(os.path.realpath(x)))


def studio_ok(pdir, p):
    """A file outside the project a live page may still load: under the studio's tools/, tests/ or assets/ (e.g. the
    tests' scenes in tests/scenes, the web engine's examples), never another project."""
    return any(inside(p, os.path.join(STUDIO, d)) for d in ABS_DIRS) and (not inside(p, PROJECTS) or inside(p, pdir))


def rel(p, pdir):
    return os.path.relpath(p, pdir).replace("\\", "/")


def resolve(pdir, path):
    """A path as a timeline names it (from the project, or absolute) -> (absolute path or None, why not)."""
    if not isinstance(path, str) or not path.strip():
        return None, "no path"
    q = path.strip().replace("\\", "/")
    full = os.path.normpath(q if os.path.isabs(q) or re.match(r"^[A-Za-z]:", q) else os.path.join(pdir, q))
    if not (inside(full, pdir) or studio_ok(pdir, full)):
        return None, f"{path} is outside the project"
    return full, ""


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def facts(pdir):
    fm = jload(os.path.join(pdir, "song", "frames.json"), None) or {}
    sm = jload(os.path.join(pdir, "song", "song-map.json"), None) or {}
    d = delivery(jload(os.path.join(pdir, "project.json"), {}) or {})
    dur = float(fm.get("duration") or sm.get("duration") or 0)
    return {"fps": d["fps"], "w": d["width"], "h": d["height"], "duration": dur, "n": int(round(dur * d["fps"])),
            "sections": sm.get("sections") or []}


def live_size(pdir, scale):
    """(w, h, fps) of a live page at this scale of the delivery size: even numbers, as a render works them out."""
    d = delivery(jload(os.path.join(pdir, "project.json"), {}) or {})
    return (max(2, int(round(d["width"] * scale / 2)) * 2), max(2, int(round(d["height"] * scale / 2)) * 2), d["fps"])


def shell_src(scene_rel, transparent=False, seed=1):
    """The live shell's URL on the live server (the player adds &scale=)."""
    return "/_mv/live.html?" + urllib.parse.urlencode({"scene": scene_rel, "transparent": int(bool(transparent)), "seed": seed})


def version(full, pdir):
    """When the scene or anything it imports last changed (the live view reloads a layer when this moves)."""
    t = 0.0
    for f in [full] + web.reached(full, pdir, most=200):
        try:
            t = max(t, os.path.getmtime(f))
        except OSError:
            pass
    return round(t, 3)


def merge(slots):
    out = []
    for a, b in sorted(slots):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def blend(mode):
    return BLEND.get(mode, "normal") if isinstance(mode, str) else "normal"


def opacity_of(x):
    return max(0.0, min(1.0, float(x))) if is_num(x) else 1.0


def scene_layer(pdir, lid, label, full, slots, transparent=False, seed=1, mode="normal", opacity=1.0, note=""):
    r = rel(full, pdir)
    return {"id": lid, "kind": "scene", "label": label, "scene": r, "src": shell_src(r, transparent, seed), "slots": merge(slots),
            "transparent": bool(transparent), "mode": blend(mode), "opacity": opacity_of(opacity), "note": note,
            "version": version(full, pdir)}


def video_layer(pdir, lid, label, full, slots, t0, transparent=False, mode="normal", opacity=1.0, note=""):
    return {"id": lid, "kind": "video", "label": label, "video": rel(full, pdir), "video_t0": round(float(t0), 4),
            "slots": merge(slots), "transparent": bool(transparent), "mode": blend(mode), "opacity": opacity_of(opacity),
            "note": note, "version": round(os.path.getmtime(full), 3)}


def slate(lid, label, slots, note):
    return {"id": lid, "kind": "slate", "label": label, "slots": merge(slots), "transparent": False, "mode": "normal",
            "opacity": 1.0, "note": note}


def seed_of(args):
    for k, x in enumerate(args or []):
        try:
            if x == "--seed" and k + 1 < len(args):
                return int(args[k + 1])
            if x.startswith("--seed="):
                return int(x.split("=", 1)[1])
        except ValueError:
            pass
    return 1


# ------------------------------------------------------------------ the timelines
def livejson_timeline(pdir, F):
    """work/live.json: {"layers": [{"scene": "work/scenes/world.js", "from": 0, "to": 61.2}, {"scene": ..., "section":
    "chorus 2"}, {"video": "work/renders/stage.mp4"}, {"scene": "work/scenes/type.html", "transparent": true}]}: in order,
    bottom to top; from / to in song seconds, or section (a name or a list of names: those sections only); default the
    whole song. A video sits in its slot from its .mv.json (or from / to). A layer it can't use is left out with a
    warning (a section that isn't there never stretches it over the whole song); the others still play."""
    p = os.path.join(pdir, "work", "live.json")
    if not os.path.exists(p):
        return None
    tl = {"source": "live.json", "label": "work/live.json (the director's timeline)", "layers": [], "warnings": []}
    W = tl["warnings"].append
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (ValueError, OSError) as ex:
        W(f"work/live.json can't be read: {ex}")
        return tl
    xs = d.get("layers") if isinstance(d, dict) else d if isinstance(d, list) else None
    if not isinstance(xs, list):
        W('work/live.json needs {"layers": [...]}')
        return tl
    for k, x in enumerate(xs):
        where = f"layers[{k}]"
        if not isinstance(x, dict):
            W(f"{where}: must be an object")
            continue
        try:
            lay = livejson_layer(pdir, F, x, k, lambda m: W(f"{where}: {m}"))
        except Exception as ex:                  # one odd field: this layer is left out, the others play
            W(f"{where}: couldn't use it ({type(ex).__name__}: {ex})")
            continue
        if lay:
            tl["layers"].append(lay)
    return tl


def livejson_layer(pdir, F, x, k, W):
    """One layer of work/live.json (livejson_timeline), or None (W says why)."""
    fps, n = F["fps"], F["n"]
    secs, named = [(0.0, F["duration"])], False
    if is_num(x.get("from")) or is_num(x.get("to")):           # from / to: that range
        secs, named = [(float(x["from"]) if is_num(x.get("from")) else 0.0, float(x["to"]) if is_num(x.get("to")) else F["duration"])], True
    elif x.get("section") or x.get("sections"):                 # a section, or a list of them: those sections only
        sec = x.get("section") or x.get("sections")
        names = [sec] if isinstance(sec, str) else [s for s in sec if isinstance(s, str)] if isinstance(sec, list) else []
        found = [s for s in F["sections"] if s.get("name") in names and is_num(s.get("t0")) and is_num(s.get("t1"))]
        missing = [nm for nm in names if nm not in [s.get("name") for s in found]]
        if missing or not names:
            W(f"no section {', '.join(repr(nm) for nm in missing) or repr(sec)} (the song's: "
              f"{', '.join(s.get('name', '') for s in F['sections'])})" + ("" if found else ": left out"))
        if not found:
            return None
        secs, named = [(float(s["t0"]), float(s["t1"])) for s in found], True
    lid = str(x.get("id") or f"L{k + 1}")[:40]
    mode, op = x.get("mode", "normal"), x.get("opacity", 1)
    if not (mode is None or isinstance(mode, str) and mode in BLEND):
        W(f"mode {mode!r} isn't one of {', '.join(BLEND)}: normal")
    if not (op is None or is_num(op)):
        W(f"opacity {op!r} isn't a number: 1")
    tr = bool(x.get("transparent"))
    if x.get("video"):
        full, why = resolve(pdir, x["video"])
        if not full or not os.path.isfile(full) or not full.lower().endswith((".mp4", ".webm", ".m4v")):
            W(why or f"{x['video']} is not a video file there")
            return None
        side = jload(full + ".mv.json", None) or {}
        t0 = float(side["t0"]) if is_num(side.get("t0")) else (secs[0][0] if named else 0.0)
        if not named and is_num(side.get("t0")) and is_num(side.get("t1")):
            secs = [(float(side["t0"]), float(side["t1"]))]
        elif not named:
            W(f"{x['video']} has no .mv.json: it is shown from 0 (give from / to)")
        slots = [[max(0, int(round(a0 * fps))), min(n, int(round(a1 * fps)))] for a0, a1 in secs]
        slots = [s for s in slots if s[1] > s[0]]
        if not slots:
            W("an empty slot (" + ", ".join(f"{a0:g}-{a1:g} s" for a0, a1 in secs) + ")")
            return None
        return video_layer(pdir, lid, os.path.basename(full), full, slots, t0, tr, mode, op, "a rendered pass (muted, synced to the song)")
    full, why = resolve(pdir, x.get("scene"))
    if not full:
        W(why if x.get("scene") else "needs a scene or a video")
        return None
    if not full.lower().endswith(WEB_EXT) or not os.path.isfile(full):
        W(f"{x['scene']} " + ("isn't a web scene (.js / .html): only web scenes play live" if os.path.isfile(full)
                              else "doesn't exist"))
        return None
    slots = [[max(0, int(round(a0 * fps))), min(n, int(round(a1 * fps)))] for a0, a1 in secs]
    slots = [s for s in slots if s[1] > s[0]]
    if not slots:
        W("an empty slot (" + ", ".join(f"{a0:g}-{a1:g} s" for a0, a1 in secs) + ")")
        return None
    return scene_layer(pdir, lid, rel(full, pdir), full, slots, tr, int(x["seed"]) if is_num(x.get("seed")) else 1, mode, op)


def plan_layers(pdir, F, plan=None, pitch_dir=None):
    """The layers of a plan (work/plan.json, or a pitch's draft): each segment in its slot (a web scene live; a
    Blender one, or one without a playable scene, as its rendered preview or a labelled slate), the overlays on top.
    (layers, warnings) or None when there is no plan."""
    import plan as plans
    v = plans.validate(pdir, plan=plan)
    if v.get("plan") is None or not v.get("items"):
        return None
    layers, warn = [], []
    for it in v["items"]:
        if it.get("bad_range") or it["b"] <= it["a"]:
            warn.append(f"{it['id']}: its range isn't valid (plan validate says why)")
            continue
        slots = [[it["a"], it["b"]]]
        label = it["id"] + (f" · {it['name']}" if it.get("name") else "")
        ov = it["kind"] == "overlay"
        tr = bool(ov and it.get("transparent"))
        full = it.get("scene_abs")
        if pitch_dir and str(it.get("scene") or "").startswith("work/scenes/"):     # a draft that names its final place:
            alt = os.path.join(pitch_dir, "scenes", *it["scene"].split("/")[2:])       # the pitch's own copy first (the
            full = alt if os.path.isfile(alt) else full                                # project's may be another's)
        there = bool(full) and os.path.isfile(full)
        if it["renderer"] == "web" and there:
            layers.append(scene_layer(pdir, it["id"], label, full, slots, tr, seed_of(it.get("render_args")),
                                      it.get("mode", "normal"), it.get("opacity", 1.0)))
            continue
        out = plans.outputs(pdir, it, "preview")[1] if not pitch_dir else ""
        if out.endswith(".mp4") and os.path.isfile(out):
            side = jload(out + ".mv.json", None) or {}
            fresh = plans.current(pdir, it, "preview")[0] if it["renderer"] == "blender" else True
            layers.append(video_layer(pdir, it["id"], label, out, slots, side.get("t0", it["t0"]), tr, it.get("mode", "normal"),
                                      it.get("opacity", 1.0), "its preview render" + ("" if fresh else
                                      " (older than its scene: render it again to see the change)")))
            continue
        why = ("a Blender segment, not rendered yet (Blender scenes play as their preview render)" if it["renderer"] == "blender"
               else "not written yet" if it.get("scene") else "no scene")
        if ov:
            warn.append(f"{it['id']}: {why}: not shown")
            continue
        layers.append(slate(it["id"], label, slots, why))
    return layers, warn


def plan_timeline(pdir, F):
    r = plan_layers(pdir, F)
    if r is None:
        return None
    return {"source": "plan.json", "label": "the plan (work/plan.json)", "layers": r[0], "warnings": r[1]}


def renders_timeline(pdir, F):
    """The newest renders in work/renders/ (their .mv.json sidecars): a web scene plays live, any other opaque pass
    (Blender, compose encode) as its video; opaque passes by range, the newest wins; transparent passes on top.
    compose's concat / layer / mux outputs are left out: their passes are there on their own."""
    root = os.path.join(pdir, "work", "renders")
    if not os.path.isdir(root):
        return None
    fps, n = F["fps"], F["n"]
    passes = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.endswith((".parts", ".frames")) and x not in SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".mv.json"):
                continue
            sp = os.path.join(dirpath, fn)
            d = jload(sp, None)
            if not isinstance(d, dict) or not isinstance(d.get("range"), list) or len(d["range"]) != 2:
                continue
            if str(d.get("made_by") or "").startswith(("compose concat", "compose layer", "compose mux")):
                continue
            sf = d.get("fps") or fps
            a, b = (int(round(x / sf * fps)) if sf != fps else int(x) for x in d["range"])
            a, b = max(0, a), min(n, b)
            if b <= a:
                continue
            out, tr, when = sp[:-len(".mv.json")], bool(d.get("transparent")), os.path.getmtime(sp)
            sc = d.get("scene")
            if isinstance(sc, str) and sc.lower().endswith(WEB_EXT):
                full, _ = resolve(pdir, sc)
                if full and os.path.isfile(full):
                    passes.append({"kind": "scene", "full": full, "a": a, "b": b, "tr": tr, "when": when,
                                   "seed": int(d["seed"]) if is_num(d.get("seed")) else 1})
                    continue
            if not tr and out.lower().endswith(".mp4") and os.path.isfile(out):
                passes.append({"kind": "video", "full": out, "a": a, "b": b, "tr": False, "when": when,
                               "t0": d["t0"] if is_num(d.get("t0")) else a / fps})
    if not passes:
        return None
    covered, keyed = [], {}

    def cut(a, b):
        parts = [[a, b]]
        for c0, c1 in covered:
            parts = [p for x0, x1 in parts for p in ([x0, min(x1, c0)], [max(x0, c1), x1]) if p[1] > p[0]]
        return parts
    for p in sorted([p for p in passes if not p["tr"]], key=lambda p: -p["when"]):       # newest first: it wins
        free = cut(p["a"], p["b"])
        if not free:
            continue
        covered += free
        k = (p["kind"], p["full"], p.get("seed"))
        if k in keyed:
            keyed[k]["slots"] += free
        else:
            keyed[k] = dict(p, slots=free)
    opaque = sorted(keyed.values(), key=lambda p: min(s[0] for s in p["slots"]))
    tops = {}
    for p in sorted([p for p in passes if p["tr"]], key=lambda p: p["when"]):             # newest last: on top
        tops.setdefault((p["full"], p.get("seed")), dict(p, slots=[]))["slots"].append([p["a"], p["b"]])
        tops[(p["full"], p.get("seed"))]["when"] = p["when"]
    layers = []
    for k, p in enumerate(opaque + sorted(tops.values(), key=lambda p: p["when"])):
        lid = f"r{k + 1}"
        if p["kind"] == "scene":
            layers.append(scene_layer(pdir, lid, rel(p["full"], pdir), p["full"], p["slots"], p["tr"], p.get("seed", 1)))
        else:
            layers.append(video_layer(pdir, lid, rel(p["full"], pdir), p["full"], p["slots"], p["t0"], note="a rendered pass"))
    return {"source": "renders", "label": "the renders (work/renders/)", "layers": layers, "warnings": []}


def pitch_timelines(pdir, F):
    out = []
    for pp in sorted(glob.glob(os.path.join(pdir, "work", "pitches", "round-*", "p*", "plan.json"))):
        plan = jload(pp, None)
        if not isinstance(plan, dict):
            continue
        r = plan_layers(pdir, F, plan, pitch_dir=os.path.dirname(pp))
        if r is None:
            continue
        rk = rel(os.path.dirname(pp), os.path.join(pdir, "work", "pitches"))
        out.append({"source": f"pitch:{rk}", "label": f"pitch {rk.replace('/', ' / ')}: its draft plan", "layers": r[0], "warnings": r[1]})
    return out


def scene_list(pdir, named=()):
    """Every scene there is (work/scenes/**, the pitches' scenes, any scene a timeline names), each playable alone:
    [{path, kind: module|html, mtime, version (the newest change of it or anything it imports: the live view reloads
    it when this moves), src}]. A shared lib's files aren't scenes; a .js file is one when it has an `export default`."""
    roots = [os.path.join(pdir, "work", "scenes")] + sorted(glob.glob(os.path.join(pdir, "work", "pitches", "round-*", "p*", "scenes")))
    found = set()
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.endswith(".parts") and not x.startswith(".")
                             and not (dirpath == root and x == "lib"))
            for fn in files:
                if fn.lower().endswith(WEB_EXT):
                    found.add(os.path.join(dirpath, fn))
    for r in named:
        full, _ = resolve(pdir, r)
        if full and os.path.isfile(full):
            found.add(full)
    out = []
    for f in sorted(found):
        kind = "html" if f.lower().endswith((".html", ".htm")) else "module"
        if kind == "module":
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    if "export default" not in fh.read(256 * 1024):
                        continue                         # a helper module it imports, not a scene
            except OSError:
                continue
        r = rel(f, pdir)
        out.append({"path": r, "kind": kind, "mtime": round(os.path.getmtime(f), 3), "version": version(f, pdir), "src": shell_src(r)})
    return out


def state(pdir):
    """What the live view plays (the web app's GET /api/p/<p>/live, without the server's URLs)."""
    pdir = os.path.abspath(pdir)
    F = facts(pdir)
    tls = []
    for fn in (livejson_timeline, plan_timeline, renders_timeline):
        try:
            tl = fn(pdir, F)
        except Exception as ex:                          # a half-written file mid-run: say so, show the rest
            tl = {"source": fn.__name__.replace("_timeline", ""), "label": fn.__name__, "layers": [],
                  "warnings": [f"couldn't read it: {type(ex).__name__}: {ex}"]}
        if tl:
            tls.append(tl)
    try:
        tls += pitch_timelines(pdir, F)
    except Exception:
        pass
    named = sorted({x["scene"] for tl in tls for x in tl["layers"] if x.get("scene")})
    return {"delivery": {"w": F["w"], "h": F["h"], "fps": F["fps"]}, "n": F["n"], "duration": F["duration"],
            "sizes": {s: list(live_size(pdir, float(s))[:2]) for s in ("0.25", "0.5", "1")},
            "timelines": tls, "scenes": scene_list(pdir, named)}


# ------------------------------------------------------------------ review/notes.json (the web app and the runner write it)
_LOCK = threading.RLock()


def read_notes(pdir, tries=20):
    """The notes ([] when there are none). A file that can't be read this instant (being replaced, on Windows) is read
    again; one that stays unreadable raises: it is never taken for empty (that would lose every note on the next write)."""
    p = os.path.join(pdir, NOTES)
    for k in range(tries):
        if not os.path.exists(p):
            return []
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, list) else []
        except (ValueError, OSError):
            if k == tries - 1:
                raise RuntimeError("review/notes.json can't be read (is it valid JSON?)")
        time.sleep(0.05)
    return []


def update_notes(pdir, fn):
    """Change review/notes.json in place: under a lock (between threads, and a lock file between processes), re-read,
    fn(notes) changes the list (or returns a new one), an atomic replace. Returns the notes as written."""
    p = os.path.join(pdir, NOTES)
    with _LOCK, file_lock(p):
        notes = read_notes(pdir)
        out = fn(notes)
        notes = notes if out is None else out
        jsave(p, notes)
        return notes


def live_notes(pdir):
    return [x for x in read_notes(pdir) if isinstance(x, dict) and x.get("version") == "live"]


def note_done(pdir, nid, fix="", undo=False):
    """Mark one note done with its one-line fix (or open again): the director's way to answer a note while the user may
    add notes in the web app (`studio.py note-done`; update_notes, so neither write loses the other's). The note as
    written, or None when there is no note with that id."""
    hit = []

    def fn(ns):
        for x in ns:
            if isinstance(x, dict) and str(x.get("id")) == str(nid).strip():
                if undo:
                    x["done"] = False
                else:
                    x["done"] = True
                    if str(fix or "").strip():
                        x["fix"] = " ".join(str(fix).split())[:500]
                hit.append(x)
    update_notes(pdir, fn)
    return hit[0] if hit else None


# ------------------------------------------------------------------ the live server (one per project)
def _params(q):
    def one(k, d):
        return (q.get(k) or [d])[0]
    try:
        scale = min(1.0, max(0.05, float(one("scale", "0.5"))))
    except ValueError:
        scale = 0.5
    try:
        seed = max(0, min(2 ** 31, int(one("seed", "1"))))
    except ValueError:
        seed = 1
    return scale, one("transparent", "0") in ("1", "true"), seed


class LiveServer:
    """The project's live server: GET only, on 127.0.0.1 (an ephemeral port), the renderer's URL space + the live shell
    (the module docstring). start() returns it; .url is its base."""

    def __init__(self, pdir):
        self.pdir = os.path.abspath(pdir)
        self.router = web.Router(self.pdir, self.pdir, 0, 0, False)
        self.httpd, self.port = None, None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            server_version = "MusicVideoStudioLive/1.0"

            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                srv.handle(self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name=f"live {os.path.basename(self.pdir)}").start()
        return self

    def alive(self):
        return self.httpd is not None

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # ------------------------------------------------------------------ requests
    def send(self, h, code, body, ctype="text/plain; charset=utf-8", headers=()):
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.send_header("Content-Security-Policy", CSP)
        for k, x in headers:
            h.send_header(k, x)
        h.end_headers()
        h.wfile.write(body)

    def file(self, path):
        """The file a URL path maps to (the renderer's mapping), or None when this server doesn't serve it."""
        p = urllib.parse.unquote(path)
        if "\x00" in p:
            return None
        fp = os.path.normpath(self.router.file_for(path))
        for prefix, root in (("/_mv/runtime/", web.RUNTIME), ("/_mv/vendor/", web.VENDOR), ("/_mv/fonts/", FONTS_DIR),
                             ("/_mv/song/", os.path.join(self.pdir, "song"))):
            if p.startswith(prefix):
                return fp if inside(fp, root) else None
        if p.startswith("/_mv/abs/"):
            return fp if inside(fp, self.pdir) or studio_ok(self.pdir, fp) else None
        if p.startswith("/_mv/"):
            return None
        return fp if inside(fp, self.pdir) else None

    def head(self, q):
        scale, tr, seed = _params(q)
        w, h, fps = live_size(self.pdir, scale)
        return web.head_inject(w, h, tr, web.live_config(w, h, fps, seed, tr))

    def shell(self, h, q):
        scene = (q.get("scene") or [""])[0]
        full, why = resolve(self.pdir, scene)
        if not full:
            return self.send(h, 403, f"not served here: {why}".encode("utf-8"))
        if not os.path.isfile(full):
            return self.send(h, 404, f"no such scene: {scene}".encode("utf-8"))
        if not full.lower().endswith(WEB_EXT):
            return self.send(h, 400, f"{scene} isn't a web scene (.js / .mjs / .html): only web scenes play live".encode("utf-8"))
        scale, tr, seed = _params(q)
        w, hh, fps = live_size(self.pdir, scale)
        url = web.Router(self.pdir, full, w, hh, tr).scene_url()
        if full.lower().endswith((".html", ".htm")):     # its own URL, so what it refers to resolves as in a render
            loc = url + "?" + urllib.parse.urlencode({"mv-live": 1, "scale": scale, "transparent": int(tr), "seed": seed})
            return self.send(h, 302, b"", headers=[("Location", loc)])
        return self.send(h, 200, web.live_shell_html(url, w, hh, fps, seed, tr).encode("utf-8"), "text/html; charset=utf-8")

    def handle(self, h):
        try:
            host = (h.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):     # (a page that renamed itself: no)
                return self.send(h, 403, b"not this host")
            u = urllib.parse.urlsplit(h.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/_mv/live.html":
                return self.shell(h, q)
            if u.path == "/_mv/fonts.css":
                return self.send(h, 200, web.fonts_css().encode("utf-8"), "text/css; charset=utf-8")
            if u.path == "/favicon.ico":                 # (a live page opened on its own asks for one)
                return self.send(h, 204, b"")
            fp = self.file(u.path)
            if fp is None:
                return self.send(h, 403, b"not served here: the live server serves the project, the studio's runtime, "
                                         b"its libraries and its fonts")
            if not os.path.isfile(fp):
                return self.send(h, 404, f"not found: {u.path}".encode("utf-8"))
            ext = os.path.splitext(fp)[1].lower()
            import mimetypes
            ctype = CTYPES.get(ext) or mimetypes.guess_type(fp)[0] or "application/octet-stream"
            with open(fp, "rb") as f:
                body = f.read()
            if ext in (".html", ".htm") and "mv-live" in q:        # an HTML scene in live mode: the shell's head
                html, inj = body.decode("utf-8", "replace"), self.head(q)
                html = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + inj, html, count=1, flags=re.I) \
                    if re.search(r"<head", html, re.I) else inj + html
                body = html.encode("utf-8")
            return self.send(h, 200, body, ctype)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            return None
        except Exception as ex:
            try:
                return self.send(h, 500, f"{type(ex).__name__}: {ex}".encode("utf-8"))
            except Exception:
                return None
