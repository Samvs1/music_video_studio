"""Music Video Studio local web app (standard library, plus Pillow for reference pictures; listens on 127.0.0.1).
Carried over from aftermovie-studio's server (jobs, range requests, project files) and rebuilt for this workflow.

  python tools/studio.py serve            -> http://127.0.0.1:8765/

Pages: Song & brief (upload, notes, reference pictures, delivery format, lyrics) · Analyse (ingest with progress) ·
The song (the song viewer) · Story (draft with Claude, edit) · Direct (the autonomous director with live progress, or
the interactive command) · Watch (each version above the song lanes, notes with N, the report and the checks).

API (JSON):
  GET  /api/projects                     GET  /api/p/<p>                GET  /api/p/<p>/job
  GET  /api/p/<p>/director               GET  /api/doctor               GET  /api/formats
  POST /api/projects {name, title}       PUT  /api/p/<p>/upload?filename=x.mp3 (body = the file; .lrc/.txt = lyrics;
                                                                                 .jpg/.png/.webp = a reference picture)
  POST /api/p/<p>/ref {id, name | note | delete}                         (a reference picture: who it is, a note)
  POST /api/p/<p>/brief {...}            POST /api/p/<p>/ingest         POST /api/p/<p>/song
  POST /api/p/<p>/story-draft            POST /api/p/<p>/story {text}   POST /api/p/<p>/direct {budget, effort, model, seed_notes, revise}
  POST /api/p/<p>/stop                   POST /api/p/<p>/note {version, t, text}
  POST /api/p/<p>/note-edit {id, done | delete | text}                  POST /api/p/<p>/deliver
"""
import json, mimetypes, os, re, shutil, subprocess, sys, threading, time, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

APP = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(APP)
TOOLS = os.path.join(STUDIO, "tools")
sys.path.insert(0, TOOLS)
import studio  # noqa: E402
from mv.common import FORMATS, AUDIO_EXT  # noqa: E402

PY = sys.executable
JOBS = {}            # project -> {"proc", "job", "log", "started"}
LOCK = threading.Lock()
PJ_LOCK = threading.Lock()          # project.json: one writer at a time (the brief, uploads, reference pictures)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
MAX_REFS, MAX_REF_BYTES, REF_SIDE = 24, 40 << 20, 1600      # Claude sees images at ~1568 px on the long side at most
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("audio/mp4", ".m4a")


# ------------------------------------------------------------------ project data
def pdir(name):
    if not re.fullmatch(r"_?[a-z0-9][a-z0-9-]{0,60}", name or ""):          # "_name": internal, by URL only
        raise ValueError("bad project name")
    d = os.path.join(studio.PROJECTS, name)
    if not os.path.isfile(os.path.join(d, "project.json")):
        raise FileNotFoundError(name)
    return d


def jload(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def safe_name(fn):
    base, ext = os.path.splitext(os.path.basename(fn))
    base = re.sub(r"[^A-Za-z0-9._ ()'&,-]+", "_", base).strip(" .")[:80] or "file"
    return base + ext.lower()


def mtime(p):
    return int(os.path.getmtime(p)) if os.path.exists(p) else 0


def song_file(d):
    from mv.common import song_file as sf
    return sf(d)


def list_projects():
    os.makedirs(studio.PROJECTS, exist_ok=True)
    out = []
    for n in sorted(os.listdir(studio.PROJECTS)):
        d = os.path.join(studio.PROJECTS, n)
        pj = jload(os.path.join(d, "project.json"), None) if not n.startswith("_") else None
        if pj is None:
            continue
        s = pj.get("song") or {}
        out.append({"name": n, "title": pj.get("title") or n, "stage": (pj.get("status") or {}).get("stage", "new"),
                    "song": " – ".join(x for x in (s.get("artist"), s.get("title")) if x) or s.get("file") or "",
                    "versions": len(pj.get("versions") or []),
                    "cover": f"/p/{n}/song/overview.png?v={mtime(os.path.join(d, 'song', 'overview.png'))}"
                    if os.path.exists(os.path.join(d, "song", "overview.png")) else "",
                    "updated": os.path.getmtime(os.path.join(d, "project.json"))})
    return sorted(out, key=lambda p: -p["updated"])


def song_info(name, d):
    """What the song viewer needs: the song folder's data, docs and stems."""
    sd = os.path.join(d, "song")
    mp = os.path.join(sd, "song-map.json")
    sf = song_file(d)
    info = {"file": os.path.basename(sf) if sf else None,
            "url": f"/p/{name}/song/{urllib.parse.quote(os.path.basename(sf))}" if sf else None}
    if os.path.isfile(mp):
        v = mtime(mp)
        base = f"/p/{name}/song"
        info.update(map=f"{base}/song-map.json?v={v}", env=f"{base}/envelopes.json?v={v}",
                    docs={f: f"{base}/{f}" for f in ("README.md", "song-map.md", "lyrics.md", "song-story.md", "overview.png",
                                                     "report.json") if os.path.exists(os.path.join(sd, f))},
                    stems={k: f"{base}/stems/{k}.wav" for k in ("vocals", "drums", "bass", "other")
                           if os.path.exists(os.path.join(sd, "stems", f"{k}.wav"))},
                    report=jload(os.path.join(sd, "report.json"), {}))
    return info


def story_state(d):
    sp = os.path.join(d, "song", "song-story.md")
    text = open(sp, encoding="utf-8").read() if os.path.exists(sp) else ""
    return {"text": text, "exists": bool(text), "written": bool(text) and "_2–4 sentences" not in text}


def versions_state(name, d, pj):
    out = []
    for v in pj.get("versions") or []:
        f = os.path.join(d, v["file"])
        if os.path.exists(f):
            item = dict(v, url=f"/p/{name}/{v['file']}?v={mtime(f)}", label=f"v{v['n']}")
            rp = os.path.join(d, "final", f"report_v{v['n']}.md")
            if os.path.exists(rp):
                item["report"] = f"/p/{name}/final/report_v{v['n']}.md"
            out.append(item)
    fin = os.path.join(d, "final", "music-video.mp4")
    if os.path.exists(fin):
        latest = out[-1] if out else None
        same = latest and latest.get("size") == os.path.getsize(fin) and abs(latest.get("mtime", 0) - os.path.getmtime(fin)) < 1
        if not same:
            out.append({"n": None, "label": "current", "file": "final/music-video.mp4", "url": f"/p/{name}/final/music-video.mp4?v={mtime(fin)}",
                        "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(fin)))})
    return out


def project_state(name):
    d = pdir(name)
    pj = jload(os.path.join(d, "project.json"), {})
    files = {k: f"/p/{name}/{v}?v={mtime(os.path.join(d, v))}" for k, v in
             (("report", "report.md"), ("checks", "work/checks/report.md"), ("log", "work/log.md"), ("concept", "work/concept.md"))
             if os.path.exists(os.path.join(d, v))}
    return {"name": name, "studio": STUDIO, "project": pj, "song": song_info(name, d), "story": story_state(d),
            "versions": versions_state(name, d, pj), "notes": jload(os.path.join(d, "review", "notes.json"), []),
            "files": files, "job": job_state(name), "director": director_state(name, brief=False), "formats": FORMATS,
            "status": jload(os.path.join(d, "cache", "status.json"), {})}


def director_state(name, brief=True):
    d = pdir(name)
    cur = jload(os.path.join(d, "work", "director", "current.json"), None)
    if not cur:
        return None
    st = jload(os.path.join(d, cur["dir"], "status.json"), {}) or {}
    out = {k: st.get(k, cur.get(k)) for k in ("run", "state", "kind", "model", "model_used", "effort", "budget", "budget_s",
                                               "started", "deadline", "elapsed_s", "wall_s", "cost_usd", "turns", "version",
                                               "stop_reason", "final_exists", "messages_sent", "claude_version")}
    out["events"] = st.get("events", [])[-200:]
    out["now"] = time.time()
    if brief and os.path.exists(os.path.join(d, cur["dir"], "brief.md")):
        out["brief"] = f"/p/{name}/{cur['dir']}/brief.md"
    return out


# ------------------------------------------------------------------ background jobs (one per project)
def start_job(name, job, args):
    with LOCK:
        j = JOBS.get(name)
        if j and j["proc"].poll() is None:
            raise RuntimeError(f"already running: {j['job']}")
        d = pdir(name)
        logp = os.path.join(d, "cache", "job.log")
        os.makedirs(os.path.dirname(logp), exist_ok=True)
        lf = open(logp, "w", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore")
        proc = subprocess.Popen([PY, "-W", "ignore", os.path.join(TOOLS, "studio.py")] + args, cwd=STUDIO, env=env,
                                stdout=lf, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        JOBS[name] = {"proc": proc, "job": job, "log": logp, "started": time.time()}


def job_state(name):
    j = JOBS.get(name)
    d = os.path.join(studio.PROJECTS, name)
    st = jload(os.path.join(d, "cache", "status.json"), {})
    if not j:
        return {"running": False, "job": st.get("job"), "steps": st.get("steps"), "i": st.get("i"), "state": st.get("state"),
                "warnings": st.get("warnings", []), "error": st.get("error")}
    rc = j["proc"].poll()
    try:
        tail = open(j["log"], encoding="utf-8", errors="replace").read().splitlines()[-16:]
    except Exception:
        tail = []
    out = {"running": rc is None, "job": j["job"], "returncode": rc, "log": tail, "elapsed": round(time.time() - j["started"])}
    if st.get("job") == j["job"]:
        out.update(steps=st.get("steps"), i=st.get("i"), state=st.get("state"), error=st.get("error"), warnings=st.get("warnings", []))
    return out


def stop_job(name):
    j = JOBS.get(name)
    if not j or j["proc"].poll() is not None:
        return {"ok": True, "running": False}
    if j["job"] == "direct":                 # the runner wraps up cleanly (archive, checks, report); force it if it hangs
        import director
        director.request_stop(pdir(name))

        def force():
            time.sleep(180)
            if j["proc"].poll() is None:
                j["proc"].terminate()
        threading.Thread(target=force, daemon=True).start()
        return {"ok": True, "stopping": "the director wraps up within a minute or two"}
    j["proc"].terminate()
    return {"ok": True}


# ------------------------------------------------------------------ writes
BRIEF_KEYS = {"title", "about", "mood", "ideas", "avoid", "lyrics"}


def save_brief(name, body):
    d = pdir(name)
    with PJ_LOCK:
        pj = update_brief(d, body)
    if (pj.get("status") or {}).get("frames_stale"):
        subprocess.run([PY, "-W", "ignore", os.path.join(TOOLS, "studio.py"), "curves", name], cwd=STUDIO,
                       capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        with PJ_LOCK:
            pj = jload(os.path.join(d, "project.json"), {})
            pj.get("status", {}).pop("frames_stale", None)
            studio.save_project(d, pj)
    return pj


def update_brief(d, body):
    pj = jload(os.path.join(d, "project.json"), {})
    for k in BRIEF_KEYS & set(body):
        pj[k] = str(body[k])[:20000 if k == "lyrics" else 6000]
    if isinstance(body.get("song"), dict):
        pj["song"] = dict(pj.get("song") or {}, **{k: str(v)[:200] for k, v in body["song"].items() if k in ("artist", "title", "language")})
    if isinstance(body.get("format"), dict):
        f = body["format"]
        preset = str(f.get("preset") or "custom")
        if preset in FORMATS:
            new = {"preset": preset, **{k: FORMATS[preset][k] for k in ("width", "height", "fps")}}
        else:
            w, h, fps = int(f.get("width") or 1920), int(f.get("height") or 1080), int(f.get("fps") or 30)
            if not (64 <= w <= 7680 and 64 <= h <= 4320 and 1 <= fps <= 120):
                raise ValueError("width / height / fps out of range")
            new = {"preset": "custom", "width": w - w % 2, "height": h - h % 2, "fps": fps}
        old = pj.get("format") or {}
        pj["format"] = new
        if old.get("fps") and old.get("fps") != new["fps"] and os.path.exists(os.path.join(d, "song", "frames.json")):
            pj.setdefault("status", {})["frames_stale"] = True          # frames.json is per fps: remake it
    if isinstance(body.get("director"), dict):
        pj["director"] = {k: str(v)[:40] for k, v in body["director"].items() if k in ("model", "effort", "budget")}
    studio.save_project(d, pj)
    return pj


def save_story(name, body):
    d = pdir(name)
    text = str(body.get("text", ""))
    if len(text) > 200000:
        raise ValueError("too long")
    sp = os.path.join(d, "song", "song-story.md")
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    if os.path.exists(sp):
        os.makedirs(os.path.join(d, "cache", "story"), exist_ok=True)
        shutil.copyfile(sp, os.path.join(d, "cache", "story", f"song-story_{time.strftime('%Y%m%d-%H%M%S')}.md"))
    open(sp, "w", encoding="utf-8").write(text.rstrip() + "\n")
    with PJ_LOCK:
        pj = jload(os.path.join(d, "project.json"), {})
        pj.setdefault("status", {})["story"] = {"by": "user", "at": time.strftime("%Y-%m-%d %H:%M")}
        studio.save_project(d, pj)
    return story_state(d)


def add_note(name, body):
    d = pdir(name)
    p = os.path.join(d, "review", "notes.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    notes = jload(p, [])
    text = str(body.get("text", "")).strip()[:2000]
    if not text:
        raise ValueError("empty note")
    notes.append({"id": int(time.time() * 1000), "version": str(body.get("version", ""))[:20], "t": round(float(body.get("t", 0)), 2),
                  "text": text, "done": False, "at": time.strftime("%Y-%m-%d %H:%M")})
    json.dump(notes, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return notes


def edit_note(name, body):
    d = pdir(name)
    p = os.path.join(d, "review", "notes.json")
    notes = jload(p, [])
    if body.get("delete"):
        notes = [n for n in notes if n["id"] != body.get("id")]
    else:
        for n in notes:
            if n["id"] == body.get("id"):
                if "done" in body:
                    n["done"] = bool(body["done"])
                if "text" in body:
                    n["text"] = str(body["text"])[:2000]
    json.dump(notes, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return notes


def receive(rfile, n, dst):
    with open(dst, "wb") as f:
        left = n
        while left > 0:
            chunk = rfile.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)


def upload(name, filename, rfile, n):
    d = pdir(name)
    fn = safe_name(filename)
    ext = os.path.splitext(fn)[1]
    if ext in IMAGE_EXT:
        return add_ref(d, fn, rfile, n)
    if ext in AUDIO_EXT:
        dst = os.path.join(d, "song", fn)
    elif ext in (".lrc", ".txt"):
        dst = os.path.join(d, "song", "lyrics" + ext)
    elif ext in (".heic", ".heif"):
        raise ValueError("HEIC photos can't be read here: save them as JPEG first")
    else:
        raise ValueError(f"unsupported file type {ext}: upload the song (mp3, wav, m4a, flac), lyrics (.lrc, .txt) "
                         f"or a reference picture (.jpg, .png, .webp)")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    receive(rfile, n, dst + ".part")
    os.replace(dst + ".part", dst)
    if ext in AUDIO_EXT:
        trash = os.path.join(d, "trash")                     # a new song: the old one and everything made from it go
        for f in os.listdir(os.path.join(d, "song")):
            if f != fn and f.lower().endswith(AUDIO_EXT):
                os.makedirs(trash, exist_ok=True)
                os.replace(os.path.join(d, "song", f), os.path.join(trash, f))
                shutil.rmtree(os.path.join(d, "song", "stems"), ignore_errors=True)
                for c in ("audio.json", "beat_this.json", "curves.npz", "curves_extra.json"):
                    if os.path.exists(os.path.join(d, "cache", c)):
                        os.remove(os.path.join(d, "cache", c))
        with PJ_LOCK:
            pj = jload(os.path.join(d, "project.json"), {})
            pj["song"] = dict(pj.get("song") or {}, file=fn)
            pj.pop("analysis", None)
            pj.setdefault("status", {})["stage"] = "song uploaded"
            studio.save_project(d, pj)
        return {"ok": True, "kind": "song", "name": fn}
    return {"ok": True, "kind": "lyrics", "name": os.path.basename(dst)}


# ------------------------------------------------------------------ reference pictures (refs/)
# The people in the song (or anything whose look matters), so the director knows how they look. It draws them in the
# video's own language: the pictures never appear in the video. project.json "refs": [{id, file, name, note, added}].
def ref_name(fn):
    """A name from the file name ("sam.jpg" -> "sam"), none from a camera's ("IMG_2041.jpg", "PXL_20240611_...")."""
    s = re.sub(r"[\s_.-]+", " ", os.path.splitext(fn)[0]).strip()
    return "" if len(re.findall(r"\d", s)) >= 3 else s[:80]


def tidy_image(src, stem, ext):
    """Save src as stem + .jpg (or .png when it has transparency, a unique name): upright (the EXIF rotation applied),
    at most REF_SIDE px on its long side, without its metadata (GPS, camera). Returns the path written."""
    def unique(e):
        p, i = stem + e, 2
        while os.path.exists(p):
            p, i = f"{stem}-{i}{e}", i + 1
        return p
    try:
        from PIL import Image, ImageOps
    except ImportError:                                 # not the studio's venv: keep the file as it came
        dst = unique(".jpg" if ext == ".jpeg" else ext)
        os.replace(src, dst)
        return dst
    try:
        with Image.open(src) as im0:
            im = ImageOps.exif_transpose(im0)
            icc = im.info.get("icc_profile")
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if alpha else "RGB")
            im.thumbnail((REF_SIDE, REF_SIDE), Image.LANCZOS)
            dst = unique(".png" if alpha else ".jpg")
            im.save(dst, **({"optimize": True} if alpha else {"quality": 90}), **({"icc_profile": icc} if icc else {}))
            return dst
    except Image.DecompressionBombError:
        raise ValueError("that image has too many pixels")
    except Exception:
        raise ValueError("can't read that image: is it a JPEG, PNG or WebP?")


def add_ref(d, fn, rfile, n):
    if n > MAX_REF_BYTES:
        raise ValueError(f"that image is {n >> 20} MB: {MAX_REF_BYTES >> 20} MB at most")
    rd = os.path.join(d, "refs")
    os.makedirs(rd, exist_ok=True)
    part = os.path.join(rd, f"upload-{time.time_ns()}.part")
    try:
        receive(rfile, n, part)
        with PJ_LOCK:
            pj = jload(os.path.join(d, "project.json"), {})
            refs = pj.get("refs") or []
            if len(refs) >= MAX_REFS:
                raise ValueError(f"{MAX_REFS} reference pictures at most: a few clear ones per person are enough")
            base, ext = os.path.splitext(fn)
            stem = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40] or "ref"
            dst = tidy_image(part, os.path.join(rd, stem), ext)
            r = {"id": max([int(time.time() * 1000)] + [x.get("id", 0) + 1 for x in refs]),
                 "file": "refs/" + os.path.basename(dst), "name": ref_name(fn), "note": "", "added": time.strftime("%Y-%m-%d %H:%M")}
            pj["refs"] = refs + [r]
            studio.save_project(d, pj)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return {"ok": True, "kind": "ref", "name": r["file"], "ref": r}


def edit_ref(name, body):
    d = pdir(name)
    with PJ_LOCK:
        pj = jload(os.path.join(d, "project.json"), {})
        refs = pj.get("refs") or []
        r = next((x for x in refs if x.get("id") == body.get("id")), None)
        if r is None:
            raise ValueError("no such reference picture")
        if body.get("delete"):
            refs.remove(r)
            f = os.path.join(d, "refs", os.path.basename(r.get("file", "")))
            if os.path.isfile(f):                         # kept in trash/, like a replaced song
                os.makedirs(os.path.join(d, "trash"), exist_ok=True)
                os.replace(f, os.path.join(d, "trash", os.path.basename(f)))
        else:
            for k, most in (("name", 80), ("note", 600)):
                if k in body:
                    r[k] = str(body[k]).strip()[:most]
        pj["refs"] = refs
        studio.save_project(d, pj)
    return refs


# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    server_version = "MusicVideoStudio/1.0"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def send_file(self, path):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".md", ".log", ".lrc")):
            ctype = "text/plain; charset=utf-8"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if (a and b) else size - 1
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    return
                left -= len(chunk)

    def route(self):
        u = urllib.parse.urlparse(self.path)
        return [urllib.parse.unquote(x) for x in u.path.strip("/").split("/") if x], urllib.parse.parse_qs(u.query)

    def do_GET(self):
        parts, q = self.route()
        try:
            if not parts:
                return self.send_file(os.path.join(APP, "static", "index.html"))
            if parts[0] == "static":
                return self.send_file(os.path.join(APP, "static", *[safe_name(p) for p in parts[1:]]))
            if parts[0] == "fonts" and len(parts) == 2:
                return self.send_file(os.path.join(STUDIO, "assets", "fonts", os.path.basename(parts[1])))
            if parts[0] == "p" and len(parts) >= 3:              # files inside a project
                base = pdir(parts[1])
                full = os.path.abspath(os.path.join(base, *parts[2:]))
                if not full.startswith(os.path.abspath(base) + os.sep):
                    return self.send_json({"error": "forbidden"}, 403)
                return self.send_file(full)
            if parts[:2] == ["api", "projects"]:
                return self.send_json(list_projects())
            if parts[:2] == ["api", "formats"]:
                return self.send_json(FORMATS)
            if parts[0] == "api" and parts[1] == "p" and len(parts) == 3:
                return self.send_json(project_state(parts[2]))
            if parts[0] == "api" and parts[1] == "p" and len(parts) == 4 and parts[3] == "job":
                return self.send_json(job_state(parts[2]))
            if parts[0] == "api" and parts[1] == "p" and len(parts) == 4 and parts[3] == "director":
                return self.send_json(director_state(parts[2]) or {})
            if parts[:2] == ["api", "doctor"]:
                r = subprocess.run([PY, os.path.join(TOOLS, "studio.py"), "doctor"], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", cwd=STUDIO, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                return self.send_json({"text": r.stdout + r.stderr, "ok": r.returncode == 0})
            self.send_json({"error": "not found"}, 404)
        except FileNotFoundError:
            self.send_json({"error": "no such project"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)

    def do_PUT(self):
        parts, q = self.route()
        try:
            if parts[0] == "api" and parts[1] == "p" and len(parts) == 4 and parts[3] == "upload":
                return self.send_json(upload(parts[2], q.get("filename", ["file"])[0], self.rfile,
                                             int(self.headers.get("Content-Length") or 0)))
            self.send_json({"error": "not found"}, 404)
        except FileNotFoundError:
            self.send_json({"error": "no such project"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)

    def do_POST(self):
        parts, q = self.route()
        try:
            body = self.body_json()
            if parts[:2] == ["api", "projects"]:
                name = studio.slugify(body.get("name") or body.get("title") or "music-video")
                if os.path.exists(os.path.join(studio.PROJECTS, name)):
                    name = f"{name}-{int(time.time()) % 10000}"

                class A:
                    pass
                a = A()
                a.name, a.title, a.song = name, body.get("title") or body.get("name") or name, None
                studio.cmd_new(a)
                return self.send_json({"name": name})
            if parts[0] == "api" and parts[1] == "p" and len(parts) == 4:
                name, act = parts[2], parts[3]
                pdir(name)
                if act == "brief":
                    return self.send_json(save_brief(name, body))
                if act == "ingest":
                    start_job(name, "ingest", ["ingest", name] + (["--force"] if body.get("force") else []))
                    return self.send_json({"ok": True})
                if act == "song":
                    start_job(name, "song", ["song", name])
                    return self.send_json({"ok": True})
                if act == "story-draft":
                    start_job(name, "story", ["story", name] + (["--model", str(body["model"])] if body.get("model") else []))
                    return self.send_json({"ok": True})
                if act == "story":
                    return self.send_json(save_story(name, body))
                if act == "direct":
                    budget = str(body.get("budget") or "90m")
                    if re.fullmatch(r"\d+", budget):
                        budget += "m"
                    args = ["direct", name, "--budget", budget, "--effort", str(body.get("effort") or "high")]
                    if body.get("model"):
                        args += ["--model", str(body["model"])]
                    if str(body.get("seed_notes") or "").strip():
                        args += ["--seed-notes", str(body["seed_notes"])[:4000]]
                    if body.get("revise"):
                        args.append("--revise")
                    start_job(name, "direct", args)
                    return self.send_json({"ok": True})
                if act == "stop":
                    return self.send_json(stop_job(name))
                if act == "note":
                    return self.send_json(add_note(name, body))
                if act == "note-edit":
                    return self.send_json(edit_note(name, body))
                if act == "ref":
                    return self.send_json(edit_ref(name, body))
                if act == "deliver":
                    r = subprocess.run([PY, os.path.join(TOOLS, "studio.py"), "deliver", name], capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", cwd=STUDIO, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                    return self.send_json({"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-2000:]})
            self.send_json({"error": "not found"}, 404)
        except FileNotFoundError:
            self.send_json({"error": "no such project"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)


def serve(port=8765):
    os.makedirs(studio.PROJECTS, exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Music Video Studio running at http://127.0.0.1:{port}/  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
