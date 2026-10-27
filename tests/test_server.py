"""The web app's server and API, end to end, with requests (and every page loaded once in headless Chromium).

  python tests/test_server.py [--keep] [--no-browser]

Starts `studio.py serve` on a free port with the stand-in for Claude Code (tests/fake_claude.py: no usage spent), then:
new project -> upload the synthetic song -> brief (notes, format, lyrics) -> reference pictures (upright, no metadata,
named, noted, removed) -> ingest with progress -> the song data (song-map, envelopes, frames, hits, a stem with a range
request) -> story: draft (stand-in Claude) and save -> a director run with live progress (its brief lists the
pictures) -> the video (range request) -> notes (add, done, delete) -> a review round -> stopping a running director
cleanly -> doctor. Then each page of the UI in headless Chromium: no JavaScript errors, a screenshot per page in
tests/out/ui/; on the brief page a picture is added and named. Exit code 0 = all passed.
"""
import argparse, io, json, os, shutil, socket, subprocess, sys, time

import requests

TESTS = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(TESTS)
RESULTS = []


def say(ok, what, detail=""):
    RESULTS.append((ok, what))
    print(f"[{'PASS' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_job(B, name, timeout=1800, want=None):
    t0, steps_seen = time.time(), set()
    while time.time() - t0 < timeout:
        j = requests.get(f"{B}/api/p/{name}/job").json()
        if j.get("i") is not None and j.get("running"):
            steps_seen.add(j["i"])
        if not j.get("running") and (want is None or j.get("job") == want):
            return j, steps_seen
        time.sleep(1.5)
    raise TimeoutError("job did not finish")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    out = os.path.join(TESTS, "out")
    if not os.path.exists(os.path.join(out, "test_song.mp3")):
        subprocess.run([sys.executable, os.path.join(TESTS, "make_test_song.py"), out], check=True)
    port = free_port()
    B = f"http://127.0.0.1:{port}"
    env = dict(os.environ, MV_CLAUDE=os.path.join(TESTS, "fake_claude.py"), PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen([sys.executable, os.path.join(STUDIO, "tools", "studio.py"), "serve", "--port", str(port), "--no-browser"],
                           cwd=STUDIO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    name = None
    try:
        for _ in range(100):
            try:
                requests.get(B + "/api/projects", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        say(True, f"server up on 127.0.0.1:{port}")
        r = requests.get(B + "/")
        say(r.ok and "MUSIC VIDEO" in r.text, "GET / (the app)", f"{len(r.text)} bytes")
        say(requests.get(B + "/api/formats").json().get("1080p", {}).get("width") == 1920, "GET /api/formats")
        r = requests.post(B + "/api/projects", json={"name": f"srv test {int(time.time()) % 100000}", "title": "Server test"}).json()
        name = r["name"]
        say(bool(name), "POST /api/projects (new)", name)
        say(any(p["name"] == name for p in requests.get(B + "/api/projects").json()), "GET /api/projects lists it")
        with open(os.path.join(out, "test_song.mp3"), "rb") as f:
            r = requests.put(f"{B}/api/p/{name}/upload?filename=Studio Test - Hold the Light.mp3", data=f).json()
        say(r.get("kind") == "song", "PUT upload (the song)", r.get("name", str(r)))
        r = requests.put(f"{B}/api/p/{name}/upload?filename=notes.exe", data=b"x").json()
        say("error" in r, "upload refuses other file types", r.get("error", "")[:60])
        brief = {"title": "Server test", "about": "a light held through a night that changes speed", "mood": "tender, strange",
                 "ideas": "", "avoid": "spectrum bars", "song": {"artist": "Studio Test", "title": "Hold the Light", "language": "en"},
                 "format": {"preset": "custom", "width": 480, "height": 270, "fps": 30},
                 "lyrics": open(os.path.join(out, "test_song_words.lrc"), encoding="utf-8").read()}
        pj = requests.post(f"{B}/api/p/{name}/brief", json=brief).json()
        say(pj.get("format", {}).get("width") == 480 and pj.get("avoid") == "spectrum bars", "POST brief (notes, format, lyrics)")
        r = requests.post(f"{B}/api/p/{name}/brief", json={"format": {"preset": "custom", "width": 99999}}).json()
        say("error" in r, "brief rejects an impossible format", r.get("error", ""))
        refs_check(B, name)
        # ingest with progress
        say(requests.post(f"{B}/api/p/{name}/ingest").json().get("ok"), "POST ingest (starts the job)")
        r = requests.post(f"{B}/api/p/{name}/ingest").json()
        say("already running" in r.get("error", ""), "one job per project", r.get("error", ""))
        j, steps = wait_job(B, name, want="ingest")
        say(j.get("returncode") == 0 and j.get("state") == "done", "ingest finished", f"progress steps seen: {sorted(steps)}; "
                                                                                        f"warnings: {len(j.get('warnings') or [])}")
        say(len(steps) >= 3, "ingest progress reported step by step", f"{len(steps)} of {len(j.get('steps') or [])} steps seen running")
        st = requests.get(f"{B}/api/p/{name}").json()
        sg = st["song"]
        say(bool(sg.get("map")) and len(sg.get("stems", {})) == 4, "project state: song folder + 4 stems")
        m = requests.get(B + sg["map"]).json()
        say(len(m.get("beats", [])) > 100 and len(m.get("sections", [])) >= 3, "GET song-map.json", f"{len(m['beats'])} beats, {len(m['sections'])} sections")
        e = requests.get(B + sg["env"]).json()
        say(e.get("fps") == 10 and len(e.get("loudness_lufs", [])) > 500, "GET envelopes.json")
        fr = requests.get(f"{B}/p/{name}/song/frames.json").json()
        say(fr.get("fps") == 30 and fr.get("frames", 0) > 1900, "GET frames.json", f"{fr.get('frames')} frames")
        say(len(requests.get(f"{B}/p/{name}/song/hits.json").json().get("kick", [])) > 50, "GET hits.json")
        r = requests.get(B + sg["stems"]["drums"], headers={"Range": "bytes=0-99"})
        say(r.status_code == 206 and len(r.content) == 100, "GET a stem with a range request (206)")
        r = requests.get(f"{B}/p/{name}/../../tools/studio.py")
        say(r.status_code in (403, 404), "no files outside the project", str(r.status_code))
        # story
        say(requests.post(f"{B}/api/p/{name}/story-draft").json().get("ok"), "POST story-draft")
        j, _ = wait_job(B, name, want="story")
        st = requests.get(f"{B}/api/p/{name}").json()
        say(j.get("returncode") == 0 and st["story"]["written"], "story drafted (stand-in Claude)", st["story"]["text"][:50].replace("\n", " "))
        text = st["story"]["text"] + "\n## My addition\nThe light is my grandmother's lamp.\n"
        r = requests.post(f"{B}/api/p/{name}/story", json={"text": text}).json()
        say("grandmother" in r.get("text", ""), "POST story (the user's edit saved)")
        # a director run
        say(requests.post(f"{B}/api/p/{name}/direct", json={"budget": 4, "effort": "low", "model": "fake"}).json().get("ok"), "POST direct")
        seen_running = False
        t0 = time.time()
        while time.time() - t0 < 1500:
            d = requests.get(f"{B}/api/p/{name}/director").json()
            seen_running |= d.get("state") == "running" and len(d.get("events", [])) > 0
            if d.get("state") in ("done", "stopped", "error") and not requests.get(f"{B}/api/p/{name}/job").json().get("running"):
                break
            time.sleep(2)
        say(seen_running, "GET director: live events while running")
        say(d.get("state") == "done" and d.get("version") == 1, "director run delivered v1", f"{d.get('stop_reason')}; {len(d.get('events', []))} events")
        b = requests.get(B + d["brief"]).text if d.get("brief") else ""
        say("refs/sam-rivers.jpg: Sam Rivers (the singer; yellow raincoat)" in b and "work/cast.md" in b and "refs/img-2041" not in b,
            "the brief lists the reference pictures", next((x.strip() for x in b.splitlines() if "refs/sam" in x), "not in the brief"))
        st = requests.get(f"{B}/api/p/{name}").json()
        v1 = next((v for v in st["versions"] if v.get("n") == 1), None)
        r = requests.get(B + v1["url"], headers={"Range": "bytes=0-1023"}) if v1 else None
        say(bool(r) and r.status_code == 206 and r.headers.get("Content-Type", "").startswith("video/"), "GET the video (range request)")
        say(bool(v1 and v1.get("checks")) and all(x == "PASS" for x in v1["checks"].values()), "v1 passed the checks", str(v1 and v1.get("checks")))
        say("report" in st["files"], "report.md is there")
        # notes
        notes = requests.post(f"{B}/api/p/{name}/note", json={"version": "v1", "t": 40.5, "text": "hold the stop longer"}).json()
        notes = requests.post(f"{B}/api/p/{name}/note", json={"version": "v1", "t": 12.0, "text": "a temporary note"}).json()
        say(len(notes) == 2, "POST note (x2)")
        nid = next(n["id"] for n in notes if n["text"] == "a temporary note")
        notes = requests.post(f"{B}/api/p/{name}/note-edit", json={"id": nid, "done": True}).json()
        say(next(n for n in notes if n["id"] == nid)["done"], "POST note-edit (done)")
        notes = requests.post(f"{B}/api/p/{name}/note-edit", json={"id": nid, "delete": True}).json()
        say(len(notes) == 1, "POST note-edit (delete)")
        # review round
        requests.post(f"{B}/api/p/{name}/direct", json={"budget": 4, "effort": "low", "model": "fake", "revise": True})
        wait_job(B, name, want="direct")
        st = requests.get(f"{B}/api/p/{name}").json()
        d = requests.get(f"{B}/api/p/{name}/director").json()
        say(any(v.get("n") == 2 for v in st["versions"]) and all(n["done"] for n in st["notes"]), "review round -> v2, the note done",
            f"versions {[v['label'] for v in st['versions']]}; fix: {st['notes'][0].get('fix')}")
        b = requests.get(B + d["brief"]).text if d.get("brief") else ""
        say("hold the stop longer" in b and "bar" in b, "the revise brief quotes the note with its bar and section")
        # stopping a run cleanly
        open(os.path.join(STUDIO, "projects", name, "FAKE_CLAUDE_SLOW"), "w").write("600")
        requests.post(f"{B}/api/p/{name}/direct", json={"budget": 30, "effort": "low", "model": "fake"})
        time.sleep(8)
        r = requests.post(f"{B}/api/p/{name}/stop").json()
        say("stopping" in r or r.get("ok"), "POST stop (a running director)", str(r))
        t0 = time.time()
        while time.time() - t0 < 300 and requests.get(f"{B}/api/p/{name}/job").json().get("running"):
            time.sleep(2)
        d = requests.get(f"{B}/api/p/{name}/director").json()
        say(d.get("state") == "stopped" and time.time() - t0 < 300, "the run stopped cleanly", f"{d.get('stop_reason')} after {time.time() - t0:.0f} s")
        os.remove(os.path.join(STUDIO, "projects", name, "FAKE_CLAUDE_SLOW"))
        r = requests.get(B + "/api/doctor").json()
        say("Music Video Studio" in r.get("text", ""), "GET /api/doctor")
        if not a.no_browser:
            browser_pages(B, name, out)
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()
        if name and not a.keep and all(ok for ok, _ in RESULTS):
            shutil.rmtree(os.path.join(STUDIO, "projects", name), ignore_errors=True)
    bad = [w for ok, w in RESULTS if not ok]
    print(f"\n== server test: {len(RESULTS) - len(bad)} passed, {len(bad)} failed")
    for w in bad:
        print("   FAILED:", w)
    sys.exit(1 if bad else 0)


def refs_check(B, name):
    """Reference pictures: upright (the EXIF rotation applied) without metadata, named from the file name unless it's a
    camera's, transparency kept, broken files and HEIC refused, a name and a note saved, one removed (to trash/)."""
    from PIL import Image
    pdir = os.path.join(STUDIO, "projects", name)
    buf = io.BytesIO()
    ex = Image.Exif()
    ex[0x0112] = 6                                     # stored sideways: shown turned 90° clockwise
    ex[0x010F] = "TestCam"                             # the camera make: metadata that must not survive
    Image.new("RGB", (64, 32), (200, 120, 90)).save(buf, "JPEG", exif=ex)
    r = requests.put(f"{B}/api/p/{name}/upload?filename=Sam Rivers.jpg", data=buf.getvalue()).json()
    ref, size, meta = r.get("ref") or {}, None, None
    if ref.get("file"):
        with Image.open(os.path.join(pdir, ref["file"])) as im:
            size, meta = im.size, dict(im.getexif())
    say(r.get("kind") == "ref" and ref.get("name") == "Sam Rivers" and size == (32, 64) and not meta,
        "PUT upload (a reference picture: upright, no metadata, named from the file)", f"{ref.get('file')} {size} exif {meta}")
    buf = io.BytesIO()
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(buf, "PNG")
    r2 = requests.put(f"{B}/api/p/{name}/upload?filename=IMG_2041.png", data=buf.getvalue()).json()
    ref2 = r2.get("ref") or {}
    say(ref2.get("file", "").endswith(".png") and ref2.get("name") == "", "a camera's file name gives no name; transparency stays PNG",
        ref2.get("file", str(r2)))
    r = requests.put(f"{B}/api/p/{name}/upload?filename=broken.jpg", data=b"not an image").json()
    left = [f for f in os.listdir(os.path.join(pdir, "refs")) if f.endswith(".part") or f.startswith("broken")]
    say("can't read" in r.get("error", "") and not left, "a broken image is refused, nothing left behind", r.get("error", "")[:60])
    r = requests.put(f"{B}/api/p/{name}/upload?filename=IMG_0001.HEIC", data=b"x").json()
    say("JPEG" in r.get("error", ""), "HEIC refused with a hint", r.get("error", ""))
    requests.post(f"{B}/api/p/{name}/ref", json={"id": ref.get("id"), "note": "the singer;\n yellow raincoat"})
    refs = requests.post(f"{B}/api/p/{name}/ref", json={"id": ref2.get("id"), "name": "Lena"}).json()
    st = requests.get(f"{B}/api/p/{name}").json()["project"]
    say([(x["name"], x["note"]) for x in st.get("refs", [])] == [("Sam Rivers", "the singer;\n yellow raincoat"), ("Lena", "")] and refs == st["refs"],
        "POST ref (a name, a note)")
    g = requests.get(f"{B}/p/{name}/{ref.get('file')}")
    say(g.ok and g.headers.get("Content-Type") == "image/jpeg", "GET a reference picture")
    refs = requests.post(f"{B}/api/p/{name}/ref", json={"id": ref2.get("id"), "delete": True}).json()
    f2 = os.path.basename(ref2.get("file", "-"))
    say(len(refs) == 1 and not os.path.exists(os.path.join(pdir, "refs", f2)) and os.path.exists(os.path.join(pdir, "trash", f2)),
        "POST ref (delete: the file goes to trash/)")
    say("error" in requests.post(f"{B}/api/p/{name}/ref", json={"id": 1, "name": "x"}).json(), "POST ref refuses an unknown id")
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    import director
    pj = json.load(open(os.path.join(pdir, "project.json"), encoding="utf-8"))
    line = director.refs_people(pdir, pj)
    say(line.endswith("Sam Rivers (the singer; yellow raincoat)"), "the story step gets who is in the pictures", line)


def browser_pages(B, name, out):
    """Every page once in headless Chromium: no JS errors; a screenshot each (tests/out/ui/)."""
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    from mv.web import launch
    from playwright.sync_api import sync_playwright
    shots = os.path.join(out, "ui")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as pw:
        b, info = launch(pw, "off")
        page = b.new_page(viewport={"width": 1400, "height": 1000})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        for h in ["", f"{name}/song", f"{name}/analyse", f"{name}/listen", f"{name}/story", f"{name}/direct", f"{name}/watch"]:
            n0 = len(errs)
            page.goto(f"{B}/#{h}")
            page.wait_for_timeout(2500)
            if h.endswith("/listen") or h.endswith("/watch"):
                page.keyboard.press("Space")                      # play a moment: the lanes follow
                page.wait_for_timeout(1200)
                page.keyboard.press("Space")
            if h.endswith("/watch"):
                page.keyboard.press("n")
                page.wait_for_timeout(300)
            if h.endswith("/song"):                               # add a picture through the page and name it
                from PIL import Image
                pic = os.path.join(out, "ui_ref.png")
                Image.new("RGB", (48, 64), (90, 140, 200)).save(pic)
                page.set_input_files("#rfi", pic)
                page.wait_for_selector(".ref:nth-child(2) input[data-k=name]", timeout=10000)
                page.fill(".ref:nth-child(2) input[data-k=name]", "Mo")
                page.keyboard.press("Tab")
                page.wait_for_timeout(800)
                refs = requests.get(f"{B}/api/p/{name}").json()["project"]["refs"]
                say(len(refs) == 2 and refs[1]["name"] == "Mo" and page.locator(".ref img").count() == 2,
                    "UI: a picture added on the brief page and named", str([r["name"] for r in refs]))
            fn = os.path.join(shots, (h.split("/")[-1] or "home") + ".png")
            page.screenshot(path=fn, full_page=True)
            ok = len(errs) == n0
            say(ok, f"UI page #{h or 'home'} (headless Chromium)", "no JS errors" if ok else "; ".join(errs[n0:])[:300])
        txt = page.inner_text("#pane")
        say("hold the stop longer" in txt or "No notes" in txt or "Note" in txt, "Watch page shows the notes and the note box")
        b.close()


if __name__ == "__main__":
    main()
