"""The web engine's driver: renders an HTML/JS scene frame by frame in headless Chromium (Playwright), deterministically.

Used by tools/web-engine/render.py (the CLI) and the benchmark. Pieces:
  find_browser()  MV_CHROME, Playwright's own Chromium, Chrome, Edge (always on Windows 11), Chromium
  launch()        GPU when there is one (WebGL renderer checked), SwiftShader (software WebGL) otherwise
  Router          every request goes to local files: /_mv/* = the runtime, the vendored libraries and the fonts;
                  /_mv/song/* = the project's song folder; everything else = the project folder. Nothing is fetched
                  from the network at render time.
  render()        chunks on a global grid (default 2 s), workers in parallel (one browser each), resume after a crash
                  (finished chunks are kept per scene version), H.264 chunks joined frame-exact, or PNG sequences
                  with alpha; a sidecar <out>.mv.json with the frame range and every text that was on screen.
"""
import base64, glob, hashlib, json, mimetypes, os, re, shutil, subprocess, sys, time, urllib.parse

from .common import STUDIO, FONTS_DIR, ROOT, ffmpeg, h264_args, delivery, jsave, jload

ENGINE = os.path.join(STUDIO, "tools", "web-engine")
RUNTIME = os.path.join(ENGINE, "runtime")
VENDOR = os.path.join(ENGINE, "vendor")
ORIGIN = "http://mv.local"
RUNTIME_VERSION = "1"

FAMILIES = {   # file -> (CSS family, weight, style)
    "Inter[opsz,wght].ttf": ("Inter", "100 900", "normal"), "SpaceMono-Regular.ttf": ("Space Mono", "400", "normal"),
    "SpaceMono-Bold.ttf": ("Space Mono", "700", "normal"), "IBMPlexMono-Regular.ttf": ("IBM Plex Mono", "400", "normal"),
    "IBMPlexMono-Bold.ttf": ("IBM Plex Mono", "700", "normal"), "Anton-Regular.ttf": ("Anton", "400", "normal"),
    "BebasNeue-Regular.ttf": ("Bebas Neue", "400", "normal"), "ArchivoNarrow[wght].ttf": ("Archivo Narrow", "400 700", "normal"),
    "SpaceGrotesk[wght].ttf": ("Space Grotesk", "300 700", "normal"), "Syne[wght].ttf": ("Syne", "400 800", "normal"),
    "Unbounded[wght].ttf": ("Unbounded", "200 900", "normal"),
    "Fraunces[SOFT,WONK,opsz,wght].ttf": ("Fraunces", "100 900", "normal"),
    "InstrumentSerif-Regular.ttf": ("Instrument Serif", "400", "normal"),
    "InstrumentSerif-Italic.ttf": ("Instrument Serif", "400", "italic"),
    "PlayfairDisplay[wght].ttf": ("Playfair Display", "400 900", "normal"),
    "PlayfairDisplay-Italic[wght].ttf": ("Playfair Display", "400 900", "italic"),
    "Caveat[wght].ttf": ("Caveat", "400 700", "normal"), "PermanentMarker-Regular.ttf": ("Permanent Marker", "400", "normal"),
    "SpecialElite-Regular.ttf": ("Special Elite", "400", "normal"), "NotoSans[wdth,wght].ttf": ("Noto Sans", "100 900", "normal"),
    "NotoSansGeorgian[wdth,wght].ttf": ("Noto Sans Georgian", "100 900", "normal"),
}


def fonts_css():
    out = []
    for f in sorted(os.listdir(FONTS_DIR)):
        if not f.lower().endswith((".ttf", ".otf", ".woff2")):
            continue
        fam, wt, st = FAMILIES.get(f, (re.sub(r"[\[\-_].*", "", os.path.splitext(f)[0]), "400", "normal"))
        out.append(f"@font-face{{font-family:'{fam}';src:url('/_mv/fonts/{urllib.parse.quote(f)}');font-weight:{wt};"
                   f"font-style:{st};font-display:block}}")
    return "\n".join(out)


def font_families():
    return sorted({v[0] for k, v in FAMILIES.items() if os.path.exists(os.path.join(FONTS_DIR, k))})


IMPORTMAP = {"imports": {
    "three": "/_mv/vendor/three/three.module.js", "three/addons/": "/_mv/vendor/three/addons/",
    "three/webgpu": "/_mv/vendor/three/three.webgpu.js", "three/tsl": "/_mv/vendor/three/three.tsl.js",
    "animejs": "/_mv/vendor/anime.esm.js", "simplex-noise": "/_mv/vendor/simplex-noise.js"}}


def head_inject(w, h, transparent):
    bg = "transparent" if transparent else "#000"
    return (f'<meta charset="utf-8"><link rel="stylesheet" href="/_mv/fonts.css">'
            f'<style>:root{{--mv-w:{w}px;--mv-h:{h}px;--mv-s:{min(w, h) / 1080:.6f}}}'
            f'html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden;background:{bg}}}'
            f'#stage{{position:relative;width:{w}px;height:{h}px;overflow:hidden}}</style>'
            f'<script type="importmap">{json.dumps(IMPORTMAP)}</script>'
            '<script src="/_mv/runtime/mv.js"></script><script src="/_mv/vendor/d3.min.js"></script>')


def shell_html(scene_url, w, h, transparent):
    return (f"<!doctype html><html><head>{head_inject(w, h, transparent)}</head><body><div id=\"stage\"></div>"
            f"<script type=\"module\">import scene from {json.dumps(scene_url)}; MV.scene(scene);</script></body></html>")


class Router:
    """Serves the studio runtime, vendor files, fonts, the song folder and the project folder to the page."""

    def __init__(self, project, scene_path, w, h, transparent):
        self.project = os.path.abspath(project)
        self.scene_path = os.path.abspath(scene_path)
        self.w, self.h, self.transparent = w, h, transparent

    def scene_url(self):
        rel = os.path.relpath(self.scene_path, self.project).replace("\\", "/")
        if rel.startswith(".."):
            return "/_mv/abs/" + urllib.parse.quote(self.scene_path.replace("\\", "/").lstrip("/"))
        return "/" + urllib.parse.quote(rel)

    def page_url(self):
        if self.scene_path.lower().endswith((".html", ".htm")):
            return ORIGIN + self.scene_url()
        return ORIGIN + "/_mv/shell.html"

    def file_for(self, path):
        p = urllib.parse.unquote(path)
        if p.startswith("/_mv/runtime/"):
            return os.path.join(RUNTIME, p[len("/_mv/runtime/"):])
        if p.startswith("/_mv/vendor/"):
            return os.path.join(VENDOR, p[len("/_mv/vendor/"):])
        if p.startswith("/_mv/fonts/"):
            return os.path.join(FONTS_DIR, p[len("/_mv/fonts/"):])
        if p.startswith("/_mv/song/"):
            return os.path.join(self.project, "song", p[len("/_mv/song/"):])
        if p.startswith("/_mv/abs/"):
            q = p[len("/_mv/abs/"):]
            return q if re.match(r"^[A-Za-z]:", q) else "/" + q
        return os.path.join(self.project, p.lstrip("/"))

    def handle(self, route):
        url = urllib.parse.urlparse(route.request.url)
        path = url.path
        try:
            if path == "/_mv/shell.html":
                return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                     body=shell_html(self.scene_url(), self.w, self.h, self.transparent))
            if path == "/_mv/fonts.css":
                return route.fulfill(status=200, content_type="text/css", body=fonts_css())
            fp = os.path.normpath(self.file_for(path))
            if not os.path.isfile(fp):
                return route.fulfill(status=404, content_type="text/plain", body=f"not found: {path}")
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            if fp.endswith((".js", ".mjs")):
                ctype = "text/javascript"
            body = open(fp, "rb").read()
            if os.path.abspath(fp) == self.scene_path and fp.lower().endswith((".html", ".htm")):
                html = body.decode("utf-8")
                inj = head_inject(self.w, self.h, self.transparent)
                html = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + inj, html, count=1, flags=re.I) if re.search(r"<head", html, re.I) \
                    else inj + html
                body = html.encode("utf-8")
            return route.fulfill(status=200, content_type=ctype, body=body)
        except Exception as ex:
            return route.fulfill(status=500, content_type="text/plain", body=str(ex))


# ------------------------------------------------------------------ the browser
def find_browser():
    """[(path or None, label)] in order of preference; None = Playwright's own managed Chromium."""
    c = []
    if os.environ.get("MV_CHROME"):
        c.append((os.environ["MV_CHROME"], "MV_CHROME"))
    c.append((None, "Playwright Chromium"))
    if os.name == "nt":
        for env, sub, lab in (("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe", "Chrome"),
                              ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe", "Chrome"),
                              ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe", "Chrome"),
                              ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe", "Edge"),
                              ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe", "Edge")):
            base = os.environ.get(env)
            if base and os.path.isfile(os.path.join(base, sub)):
                c.append((os.path.join(base, sub), lab))
    else:
        roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), "/opt/pw-browsers", os.path.expanduser("~/.cache/ms-playwright")]
        for r in [x for x in roots if x]:
            for pat, lab in (("chromium-*/chrome-linux*/chrome", "Chromium (Playwright folder)"),
                             ("chromium_headless_shell-*/chrome-*/chrome-headless-shell", "Chromium headless shell")):
                for p in sorted(glob.glob(os.path.join(r, pat)), reverse=True):
                    c.append((p, lab))
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                c.append((p, name))
        mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(mac):
            c.append((mac, "Chrome"))
    seen, out = set(), []
    for p, lab in c:
        k = os.path.realpath(p) if p else None
        if k not in seen:
            seen.add(k)
            out.append((p, lab))
    return out


GPU_ARGS = ["--ignore-gpu-blocklist", "--enable-gpu-rasterization", "--enable-zero-copy"] + \
    (["--use-angle=d3d11"] if os.name == "nt" else [])
CPU_ARGS = ["--use-angle=swiftshader", "--use-gl=angle", "--enable-unsafe-swiftshader"]
COMMON_ARGS = ["--hide-scrollbars", "--mute-audio", "--disable-background-timer-throttling",
               "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows", "--force-color-profile=srgb",
               "--font-render-hinting=none", "--disable-lcd-text"]

PROBE = """() => { const c = document.createElement('canvas'); const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return null; const e = gl.getExtension('WEBGL_debug_renderer_info');
  return {renderer: e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER), webgl2: !!c.getContext('webgl2')}; }"""


def software(renderer):
    return not renderer or any(k in renderer.lower() for k in ("swiftshader", "llvmpipe", "software", "basic render"))


def launch(pw, gpu="auto", path=None):
    """(browser, info). gpu: auto (GPU if a real one renders WebGL, else SwiftShader) | on | off."""
    errs = []
    cands = [(path, "given")] if path else find_browser()
    for exe, label in cands:
        modes = {"on": ["gpu"], "off": ["cpu"], "auto": ["gpu", "cpu"]}[gpu]
        for mode in modes:
            args = COMMON_ARGS + (GPU_ARGS if mode == "gpu" else CPU_ARGS)
            try:
                b = pw.chromium.launch(headless=True, executable_path=exe, args=args)
            except Exception as ex:
                errs.append(f"{label}: {str(ex).splitlines()[0][:160]}")
                break
            try:
                pg = b.new_page()
                r = pg.evaluate(PROBE)
                pg.close()
            except Exception as ex:
                r = None
                errs.append(f"{label} probe: {ex}")
            renderer = (r or {}).get("renderer")
            if mode == "gpu" and gpu == "auto" and software(renderer):
                b.close()
                continue
            info = {"browser": label, "path": exe or "playwright", "version": b.version, "mode": mode,
                    "webgl": renderer, "webgl2": (r or {}).get("webgl2"), "gpu": bool(renderer) and not software(renderer)}
            return b, info
    raise RuntimeError("no usable Chromium for the web engine. Install one: `python -m playwright install chromium`, "
                       "or set MV_CHROME to Chrome / Edge.\n  " + "\n  ".join(errs[-6:]))


def probe_browser(gpu="auto"):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, info = launch(pw, gpu)
        b.close()
        return info


# ------------------------------------------------------------------ rendering
def scene_key(scene_path, project, cfg):
    """What decides a chunk's pixels: the scene's folder (its code and assets; renders and caches excluded), the
    runtime, the song data, the render settings. Any change renders the scene's chunks again."""
    h = hashlib.sha1()
    d = os.path.dirname(os.path.abspath(scene_path))
    files = {os.path.abspath(scene_path)}
    for root, dirs, fs in os.walk(d):
        dirs[:] = [x for x in dirs if not x.endswith(".parts") and x not in ("__pycache__", "node_modules", "renders", "eyes")]
        for f in fs:
            if not f.endswith((".mp4", ".mov", ".mv.json", ".part", ".tmp")):
                files.add(os.path.join(root, f))
        if root == d and os.path.abspath(d) in (os.path.abspath(project), os.path.abspath(os.path.join(project, "work"))):
            break                                    # a scene loose in the project / work folder: don't hash everything below
    for f in sorted(files):
        try:
            if os.path.getsize(f) < 50e6:
                h.update(os.path.relpath(f, d).encode("utf-8", "replace"))
                h.update(open(f, "rb").read())
        except OSError:
            pass
    h.update(open(os.path.join(RUNTIME, "mv.js"), "rb").read())
    for f in ("song-map.json", "frames.json", "hits.json"):
        p = os.path.join(project, "song", f)
        h.update(f"{f}:{os.path.getmtime(p) if os.path.exists(p) else 0}".encode())
    h.update(json.dumps(cfg, sort_keys=True).encode())
    return h.hexdigest()[:16]


def merge_texts(per_frame, fps, space=None):
    """Per-frame text lists -> appearances: {text, role, f0, f1, t0, t1, box, boxes, color, size, overflow, opacity_min}."""
    open_, out = {}, []
    for f in sorted(per_frame):
        seen = set()
        for e in per_frame[f]:
            k = (e["text"], e.get("role", "text"))
            if k in seen:
                continue
            seen.add(k)
            box = [round(e["x"], 1), round(e["y"], 1), round(e["w"], 1), round(e["h"], 1)]
            a = open_.get(k)
            if a and a["f1"] == f - 1:
                a["f1"] = f
                x0, y0 = min(a["box"][0], box[0]), min(a["box"][1], box[1])
                x1, y1 = max(a["box"][0] + a["box"][2], box[0] + box[2]), max(a["box"][1] + a["box"][3], box[1] + box[3])
                a["box"] = [x0, y0, round(x1 - x0, 1), round(y1 - y0, 1)]
                if (f - a["f0"]) % max(1, fps // 2) == 0:
                    a["boxes"].append([f] + box)
                a["last"] = [f] + box
                a["overflow"] |= bool(e.get("overflow"))
                a["opacity_min"] = min(a["opacity_min"], e.get("opacity", 1))
                a["size"] = max(a["size"] or 0, e.get("size") or 0) or None
            else:
                a = {"text": e["text"], "role": e.get("role", "text"), "f0": f, "f1": f, "box": box, "boxes": [[f] + box],
                     "last": [f] + box, "color": e.get("color"), "size": e.get("size"), "overflow": bool(e.get("overflow")),
                     "opacity_min": e.get("opacity", 1)}
                open_[k] = a
                out.append(a)
    for a in out:
        if a["boxes"][-1][0] != a["last"][0]:
            a["boxes"].append(a["last"])
        del a["last"]
        a["t0"], a["t1"] = round(a["f0"] / fps, 4), round((a["f1"] + 1) / fps, 4)
        if space:
            a["space"] = list(space)
    return out


def _capture(cdp, page, jpeg):
    """One frame from the compositor. jpeg: False = PNG (lossless, alpha), or a JPEG quality (Chrome's PNG encoder is
    ~7x slower than its JPEG one on busy frames; at quality 98 the JPEG step is invisible after the H.264 encode)."""
    try:
        r = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": int(jpeg), "optimizeForSpeed": True,
                                                "captureBeyondViewport": False} if jpeg else
                     {"format": "png", "optimizeForSpeed": True, "captureBeyondViewport": False})
        return base64.b64decode(r["data"])
    except Exception:
        return page.screenshot(type="jpeg" if jpeg else "png", quality=int(jpeg) if jpeg else None, omit_background=not jpeg)


def _open_page(pw, job):
    b, info = launch(pw, job["gpu"], job.get("browser_path"))
    ctx = b.new_context(viewport={"width": job["w"], "height": job["h"]}, device_scale_factor=1)
    ctx.add_init_script(f"window.__MV_CONFIG = {json.dumps({'w': job['w'], 'h': job['h'], 'fps': job['fps'], 'seed': job['seed'], 'transparent': bool(job['transparent'])})};")
    page = ctx.new_page()
    page.set_default_timeout(180000)
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console.append(f"[pageerror] {e}"))
    router = Router(job["project"], job["scene"], job["w"], job["h"], job["transparent"])
    page.route(ORIGIN + "/**", router.handle)
    page.goto(router.page_url(), wait_until="load")
    try:
        meta = page.evaluate("() => MV._start()")
    except Exception as ex:
        errs = page.evaluate("() => window.__mvErrors || []") if not page.is_closed() else []
        raise RuntimeError(f"scene setup failed: {ex}\n" + "\n".join(errs + console[-10:]))
    cdp = ctx.new_cdp_session(page)
    if job["transparent"]:
        cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
    return b, page, cdp, meta, console, info


def _check(page, console, where):
    errs = page.evaluate("() => window.__mvErrors.splice(0)")
    if errs:
        raise RuntimeError(f"scene error at {where}: " + "\n".join(errs[:5]) + ("\n" + "\n".join(console[-5:]) if console else ""))


def _worker(job):
    """Render a list of chunks in one browser. Returns {'chunks': [...], 'info': browser info, 'logs': [...]}."""
    from playwright.sync_api import sync_playwright
    logs = []
    done = []
    with sync_playwright() as pw:
        b, page, cdp, meta, console, info = _open_page(pw, job)
        stateful = bool(meta.get("stateful"))
        next_frame = 0
        try:
            for ch in job["chunks"]:
                f0, f1, out = ch["f0"], ch["f1"], ch["out"]
                if stateful and next_frame != f0:
                    start = 0 if meta.get("preroll") is None else max(0, f0 - int(meta["preroll"] * job["fps"]))
                    if next_frame > start and next_frame <= f0:
                        start = next_frame
                    page.evaluate("([a, b]) => MV._preroll(a, b)", [start, f0])
                texts = {}
                tmp = out + ".part"
                if job["mode"] == "png":
                    os.makedirs(job["png_dir"], exist_ok=True)
                    ff = None
                else:
                    ff = subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(job["fps"]),
                                           "-c:v", "mjpeg" if job["jpeg"] else "png", "-i", "-", *job["venc"],
                                           "-r", str(job["fps"]), "-video_track_timescale", str(job["fps"] * 1000),
                                           "-an", "-f", "mp4", tmp], stdin=subprocess.PIPE)
                for i in range(f0, f1):
                    tx = page.evaluate("i => MV._frame(i)", i)
                    if tx:
                        texts[i] = tx
                    _check(page, console, f"frame {i}")
                    img = _capture(cdp, page, job["jpeg"])
                    if ff:
                        ff.stdin.write(img)
                    else:
                        with open(os.path.join(job["png_dir"], f"frame_{i:06d}.png"), "wb") as fh:
                            fh.write(img)
                if ff:
                    ff.stdin.close()
                    if ff.wait():
                        raise RuntimeError(f"ffmpeg failed on chunk {f0}-{f1}")
                    os.replace(tmp, out)
                logs += page.evaluate("() => MV._logs()")
                jsave(out + ".json", {"f0": f0, "f1": f1, "texts": {str(k): v for k, v in texts.items()},
                                      "intents": page.evaluate("() => MV._intents()")}, indent=None)
                done.append(ch)
                next_frame = f1
                print(f"   chunk {f0}-{f1} done", flush=True)
        finally:
            b.close()
    return {"chunks": done, "info": info, "logs": logs + console[-20:]}


def plan_chunks(a, b, per):
    """Chunks on a global grid of `per` frames, clipped to [a, b)."""
    out, f = [], a
    while f < b:
        g = min(b, (f // per + 1) * per)
        out.append((f, g))
        f = g
    return out


def render(scene, out, project=None, frames=None, width=None, height=None, fps=None, quality="final", scale=1.0,
           transparent=False, workers=None, gpu="auto", seed=1, chunk_s=2.0, fresh=False, lossless=False, log=print):
    """Render scene frames [a, b) to `out` (.mp4 = H.264 chunks joined frame-exact; a folder or .mov with transparent=True).
    Returns the sidecar dict (also written to <out>.mv.json)."""
    from concurrent.futures import ProcessPoolExecutor
    project = os.path.abspath(project or ROOT)
    d = delivery(jload(os.path.join(project, "project.json"), {}))
    fps = fps or d["fps"]
    w = int(round((width or d["width"]) * scale / 2)) * 2
    h = int(round((height or d["height"]) * scale / 2)) * 2
    fm = jload(os.path.join(project, "song", "frames.json"), None)
    if fm is None:
        raise SystemExit("song/frames.json is missing: run ingest first")
    n_total = int(round(fm["duration"] * fps))
    a, b = frames or (0, n_total)
    a, b = max(0, a), min(n_total, b)
    if b <= a:
        raise SystemExit(f"empty frame range {a}:{b}")
    out = os.path.abspath(out)
    mode = "png" if transparent and not out.lower().endswith(".mov") else ("png" if transparent else "mp4")
    jpeg = False if (transparent or lossless) else (88 if quality == "preview" else 98)
    cfg = {"w": w, "h": h, "fps": fps, "seed": seed, "quality": quality, "transparent": transparent, "rt": RUNTIME_VERSION,
           "capture": "png" if not jpeg else f"jpeg{jpeg}"}
    key = scene_key(scene, project, cfg)
    parts = out + ".parts"
    cdir = os.path.join(parts, key)
    if fresh:
        shutil.rmtree(cdir, ignore_errors=True)
    os.makedirs(cdir, exist_ok=True)
    png_dir = out if (mode == "png" and not out.lower().endswith(".mov")) else os.path.join(cdir, "png")
    if mode == "png" and png_dir == out and (fresh or any(os.path.basename(x) != key for x in glob.glob(os.path.join(parts, "*")))):
        for f in glob.glob(os.path.join(out, "frame_*.png")):      # frames of an older scene version: stale
            os.remove(f)
    per = max(1, int(round(chunk_s * fps)))
    chunks = []
    for f0, f1 in plan_chunks(a, b, per):
        p = os.path.join(cdir, f"c_{f0:06d}_{f1:06d}.mp4")
        done = os.path.exists(p + ".json") and (mode == "png" or os.path.exists(p))
        chunks.append({"f0": f0, "f1": f1, "out": p, "done": done})
    todo = [c for c in chunks if not c["done"]]
    log(f"web-engine: {os.path.relpath(scene, project)} frames {a}-{b} ({(b - a) / fps:.1f} s) at {w}x{h} {fps} fps, "
        f"{len(chunks)} chunks, {len(chunks) - len(todo)} already done (resume)")
    info = None
    logs = []
    t0 = time.time()
    if todo:
        nw = max(1, min(workers or default_workers(), len(todo)))
        groups = [todo[i * len(todo) // nw:(i + 1) * len(todo) // nw] for i in range(nw)]
        venc = h264_args("preview" if quality == "preview" else "final", chunk=True)
        jobs = [{"project": project, "scene": os.path.abspath(scene), "w": w, "h": h, "fps": fps, "seed": seed, "gpu": gpu,
                 "transparent": transparent, "mode": mode, "jpeg": jpeg, "venc": venc, "png_dir": png_dir,
                 "chunks": [{"f0": c["f0"], "f1": c["f1"], "out": c["out"]} for c in g]} for g in groups if g]
        if len(jobs) == 1:
            res = [_worker(jobs[0])]
        else:
            import multiprocessing as mp
            with ProcessPoolExecutor(len(jobs), mp_context=mp.get_context("spawn")) as ex:
                res = list(ex.map(_worker, jobs))
        info = res[0]["info"]
        for r in res:
            logs += r["logs"]
    el = time.time() - t0
    nfr = sum(c["f1"] - c["f0"] for c in todo)
    if nfr:
        log(f"   rendered {nfr} frames in {el:.1f} s ({el / nfr:.3f} s/frame with {len(todo) and min(workers or default_workers(), len(todo))} workers)")
    # join
    if mode == "mp4":
        lst = os.path.join(cdir, "list.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("".join(f"file '{os.path.basename(c['out'])}'\n" for c in chunks))
        tmp = out + ".tmp.mp4"
        r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                            "-video_track_timescale", str(fps * 1000), "-movflags", "+faststart", tmp])
        if r.returncode:
            raise RuntimeError("could not join the chunks")
        os.replace(tmp, out)
    elif out.lower().endswith(".mov"):
        r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-framerate", str(fps), "-start_number", str(a), "-i",
                            os.path.join(png_dir, "frame_%06d.png"), "-frames:v", str(b - a), "-c:v", "prores_ks",
                            "-profile:v", "4", "-pix_fmt", "yuva444p10le", out])
        if r.returncode:
            raise RuntimeError("could not encode the ProRes 4444 layer")
    per_frame, intents = {}, []
    for c in chunks:
        j = jload(c["out"] + ".json", {})
        for k, v in (j.get("texts") or {}).items():
            per_frame[int(k)] = v
        intents += j.get("intents") or []
    side = {"scene": os.path.relpath(scene, project).replace("\\", "/"), "range": [a, b], "fps": fps, "w": w, "h": h,
            "t0": round(a / fps, 4), "t1": round(b / fps, 4), "transparent": transparent, "format": "png" if mode == "png" and not out.endswith(".mov") else os.path.splitext(out)[1][1:],
            "browser": info, "texts": merge_texts(per_frame, fps, (w, h)), "intents": [dict(x) for x in {json.dumps(i, sort_keys=True): i for i in intents}.values()],
            "logs": sorted(set(logs))[:50], "rendered_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    jsave(out.rstrip("/\\") + ".mv.json", side)
    for old in glob.glob(os.path.join(parts, "*")):              # older scene versions' chunks are not needed any more
        if os.path.basename(old) != key:
            shutil.rmtree(old, ignore_errors=True)
    log(f"   -> {out}")
    return side


def default_workers():
    n = os.cpu_count() or 4
    return max(1, min(6, n // 2))


def stills(scene, times, out_dir, project=None, width=None, height=None, scale=1.0, transparent=False, gpu="auto", seed=1, log=print):
    """PNG frames of a scene at song times (style frames, quick looks). Returns the paths."""
    from playwright.sync_api import sync_playwright
    project = os.path.abspath(project or ROOT)
    d = delivery(jload(os.path.join(project, "project.json"), {}))
    w = int(round((width or d["width"]) * scale / 2)) * 2
    h = int(round((height or d["height"]) * scale / 2)) * 2
    job = {"project": project, "scene": os.path.abspath(scene), "w": w, "h": h, "fps": d["fps"], "seed": seed, "gpu": gpu,
           "transparent": transparent}
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    with sync_playwright() as pw:
        b, page, cdp, meta, console, info = _open_page(pw, job)
        try:
            frames = sorted({int(round(t * d["fps"])) for t in times})
            if meta.get("stateful") and frames:
                page.evaluate("([a, b]) => MV._preroll(a, b)", [0, frames[0]])
            last = frames[0] if frames else 0
            for f in frames:
                if meta.get("stateful") and f > last + 1:
                    page.evaluate("([a, b]) => MV._preroll(a, b)", [last + 1, f])
                page.evaluate("i => MV._frame(i)", f)
                _check(page, console, f"frame {f}")
                p = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(scene))[0]}_{f / d['fps']:08.3f}.png")
                open(p, "wb").write(_capture(cdp, page, False))
                paths.append(p)
                last = f
                log(p)
        finally:
            b.close()
    return paths
