"""The web app's server and API, end to end, with requests (and every page loaded once in headless Chromium).

  python tests/test_server.py [--keep] [--no-browser]

Starts `studio.py serve` on a free port with the stand-in for Claude Code (tests/fake_claude.py: no usage spent), then:
new project -> upload the synthetic song -> brief (notes, format, lyrics) -> reference pictures (upright, no metadata,
named, noted, removed) -> ingest with progress -> the song data (song-map, envelopes, frames, hits, a stem with a range
request) -> story: draft (stand-in Claude) and save -> the lyric timing editor's save (LRC timed on this file, used
as it is; the song analysed again) -> lyrics placed where Whisper heard them (a stand-in transcription: the editor's
align job, then plain-text lyrics analysed with it) -> a director run with live progress (its brief lists the pictures; the
critic's notes on v1) -> the video (range request) -> "Ask the critic" (a critique job on v1, a bad video refused) -> notes
(add, done, delete) -> a review round -> stopping a running director cleanly -> a run started from the Direct page's
form in headless Chromium (one director: effort max by default, Ultracode, a cost cap; the settings it sends and
keeps; the run view while the director's agent works: its line in the live log, the Agents list, '▶ play the scenes
live' offered; after the run the Agents list, the costs per model and the total) -> live preview (GET live, the live server's URL space and what it
refuses, a live note round-trip; the Watch page's live view playing the plan's scenes with the song in headless Chromium,
the size picker, the sound delay, a stateful scene caught up after a seek, N for a live note) -> doctor. Then each page of the UI in headless Chromium: no JavaScript errors, a screenshot per page in
tests/out/ui/; on the brief page a picture is added and named, on the song page the lyric timing editor opens, taps,
undoes and closes, and aligns with Whisper; the Direct page has the critic select and the settings kept, the Watch
page the critic's box with its scores.
Exit code 0 = all passed.
"""
import argparse, io, json, os, re, shutil, socket, subprocess, sys, time

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
        lyric_timing_check(B, name)
        whisper_check(B, name)
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
        critic_check(B, name, st, d)
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
        direct_form_check(B, name, out, browser=not a.no_browser)
        live_check(B, name, out, browser=not a.no_browser)
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


def critic_check(B, name, st, d):
    """The critic: the run used it (the default model, the stand-in answers) and v1 has its notes; "Ask the critic" on
    v1 is a job that writes final/critic_v1.md again; a video outside the project is refused."""
    v1 = next((v for v in st["versions"] if v.get("n") == 1), {})
    c1 = v1.get("critic") or {}
    say(d.get("critic_model") == "sonnet" and bool(c1.get("scores")) and requests.get(B + c1.get("url", "/x")).ok,
        "the run's critic (default sonnet): v1 has the critic's notes", f"{c1.get('file')} {c1.get('scores')}; this run: {len(d.get('critiques') or [])} critique(s)")
    r = requests.post(f"{B}/api/p/{name}/critique", json={"version": 1, "model": "fake"}).json()
    j, _ = wait_job(B, name, want="critique")
    st = requests.get(f"{B}/api/p/{name}").json()
    c2 = next((v.get("critic") for v in st["versions"] if v.get("n") == 1), None) or {}
    # the new notes are the stand-in's ("fake"; the run's were "sonnet"): `at` has 1-second steps, and with the fake
    # critic both can fall in the same second
    say(r.get("ok") and j.get("returncode") == 0 and c2.get("model") == "fake" != c1.get("model") and st.get("critiques"),
        "POST critique (Ask the critic on v1: a job; final/critic_v1.md again)", f"{c2.get('file')} at {c2.get('at')}; {len(st.get('critiques') or [])} in the project")
    r = requests.post(f"{B}/api/p/{name}/critique", json={"video": "../../tools/studio.py"}).json()
    r2 = requests.post(f"{B}/api/p/{name}/critique", json={"version": 99}).json()
    say("error" in r and "error" in r2, "POST critique refuses a video outside the project and an unknown version", f"{r.get('error')}; {r2.get('error')}")


def direct_form_check(B, name, out, browser=True):
    """A run started from the Direct page's form (the stand-in for Claude Code: a director that deploys an agent and
    uses the plan tools). Before it, the page shows the last run (stopped) with its live log. The form has one
    director: the budget, the model, the effort (max by default), the critic, Ultracode (off by default) and a cost cap;
    it sends them to `studio.py direct` and keeps them in project.json. While the agent works the run view (in headless
    Chromium) shows the live log with the agent's lines, the Agents list with it at work, and offers '▶ play the scenes
    live'; afterwards the run's header says ultracode, the cap and the total, the Agents list has it done with its
    duration, and the costs show per model (the director's, the agents', the critic's) with the total (GET director's
    costs and total_usd: the session's cost plus the critic's). No JavaScript errors. The run delivers a version that
    passes the checks."""
    shots = os.path.join(out, "ui")
    os.makedirs(shots, exist_ok=True)
    pdir = os.path.join(STUDIO, "projects", name)
    pw = page = b = None
    errs = []
    form = {"budget": "30", "model": "fake", "effort": "max", "critic_model": "sonnet", "ultracode": True, "max_usd": "25"}
    flag = os.path.join(pdir, "FAKE_DIRECTOR")
    open(flag, "w").write("agents")
    if browser:
        sys.path.insert(0, os.path.join(STUDIO, "tools"))
        from mv.web import launch
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        b, _ = launch(pw, "off")
        page = b.new_page(viewport={"width": 1400, "height": 1000})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pjf = os.path.join(pdir, "project.json")
    pj = json.load(open(pjf, encoding="utf-8"))           # what the pipeline's form saved on every start (effort "high")
    pj["director"] = dict(pj.get("director") or {}, effort="high", mode="auto", builders="3", builder_effort="high")
    pj["director"].pop("ultracode", None)
    json.dump(pj, open(pjf, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    try:
        if page:
            page.goto(f"{B}/#{name}/direct")
            page.wait_for_selector("#dir-live .box", timeout=10000)
            live = page.inner_text("#dir-live")
            defaults = (page.input_value("#d-effort"), page.is_checked("#d-ultra"), page.input_value("#d-cap"))
            say("stopped" in live and page.locator("#dir-ev").count() == 1 and defaults == ("max", False, ""),
                "UI: the Direct page: the last run's view (its live log); the form's defaults (effort max, Ultracode off, no cap; "
                "an effort the old forms saved doesn't count)", f"{live.splitlines()[0][:80] if live else 'empty'}; defaults {defaults}")
            page.fill("#d-budget", form["budget"])
            page.fill("#d-model", form["model"])
            page.select_option("#d-critic", form["critic_model"])
            page.check("#d-ultra")
            page.fill("#d-cap", form["max_usd"])
            sent = []

            def no_nudge(route):                  # the stand-in would go round the final three more times
                body = json.loads(route.request.post_data or "{}")
                sent.append(body)
                route.continue_(post_data=json.dumps(dict(body, nudge=False)))
            page.route("**/api/p/*/direct", no_nudge)
            page.click("#go-direct")
            page.wait_for_timeout(1500)
            page.unroute("**/api/p/*/direct")
            got = sent[0] if sent else {}
            say(all(got.get(k) == x for k, x in form.items()), "UI: the Direct form sends one director's settings (the effort, "
                "the critic, Ultracode, the cost cap)", str({k: got.get(k) for k in form}))
        else:
            requests.post(f"{B}/api/p/{name}/brief", json={"director": dict(form)})
            requests.post(f"{B}/api/p/{name}/direct", json=dict(form, nudge=False))
        t0, live_ok, d = time.time(), None, {}
        while time.time() - t0 < 1500:
            d = requests.get(f"{B}/api/p/{name}/director").json()
            working = [a for a in d.get("agents") or [] if a.get("state") == "running"]
            if page and live_ok is None and working and d.get("state") == "running":     # the run view while the agent works
                page.wait_for_function("() => /agent: build s3 \\(haiku\\) started/.test(($('#dir-ev') || {}).innerText || '')", timeout=15000)
                page.wait_for_function("() => /build s3/.test(($('#dir-agents') || {}).innerText || '')", timeout=15000)
                at_work = page.inner_text("#dir-agents")
                page.screenshot(path=os.path.join(shots, "direct-agent-live.png"), full_page=True)
                live_ok = page.locator("#dir-live a.lv-go").count() == 1 and "running" in at_work and "haiku" in at_work
                say(live_ok, "UI: during a run, the live log shows the director's agent, the Agents list has it at work (its "
                    "description, model, running, how long) and '▶ play the scenes live' is offered", at_work.replace("\n", " | ")[:200])
            if d.get("state") in ("done", "stopped", "error") and not requests.get(f"{B}/api/p/{name}/job").json().get("running"):
                break
            time.sleep(1)
        st = requests.get(f"{B}/api/p/{name}").json()
        v = next((x for x in st["versions"] if x.get("n") == d.get("version")), {})
        ag = (d.get("agents") or [{}])[0]
        who = {r.get("name"): r.get("who") for r in d.get("costs") or []}
        say(d.get("state") == "done" and bool(v.get("checks")) and all(x == "PASS" for x in v["checks"].values())
            and ag.get("description") == "build s3" and ag.get("state") == "done" and (d.get("model_usage") or {}).get("claude-haiku-fake")
            and os.path.exists(os.path.join(pdir, "work", "plan.json")),
            "a run from the web app: the director's agent (in GET director: agents, model_usage), a version that passes the checks",
            f"v{d.get('version')} {v.get('checks')}; agent {ag}; {d.get('stop_reason')}; {time.time() - t0:.0f} s")
        say(who.get("haiku-fake") == "agents" and who.get("fake-agentdirector") == "the director"
            and abs(float(d.get("total_usd") or 0) - float(d.get("cost_usd") or 0) - float(d.get("critic_cost_usd") or 0)) < 1e-6
            and float(d.get("critic_cost_usd") or 0) > 0,
            "GET director: the costs per model, labelled (the director's, its agents'), and the total with the critic's",
            f"{d.get('costs')}; total {d.get('total_usd')} = {d.get('cost_usd')} + {d.get('critic_cost_usd')}")
        cmd = json.load(open(os.path.join(pdir, "work", "director", f"run-{d.get('run')}", "command.json"), encoding="utf-8"))["cmd"]
        tools = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
        kept = st["project"].get("director") or {}
        say(cmd[cmd.index("--effort") + 1] == "max" and "Workflow" in tools and "Agent" in tools
            and cmd[cmd.index("--max-budget-usd") + 1] == "25" and d.get("ultracode") is True and d.get("max_usd") == 25
            and (kept.get("effort"), kept.get("ultracode"), kept.get("max_usd")) == ("max", True, "25")
            and not {"mode", "builders", "builder_effort"} & set(kept),
            "the Direct form's settings reach the run (effort max, Workflow for Ultracode, --max-budget-usd) and are kept "
            "(the old pipeline's settings dropped)",
            f"effort {cmd[cmd.index('--effort') + 1]}; tools {tools}; kept {kept}")
        if page:
            page.reload()
            page.wait_for_selector("#dir-live .box", timeout=10000)
            page.wait_for_timeout(1500)
            head = page.inner_text("#dir-live .row")
            page.screenshot(path=os.path.join(shots, "direct-after.png"), full_page=True)
            say("ultracode" in head and "cap $25" in head and page.is_checked("#d-ultra") and page.input_value("#d-cap") == "25",
                "UI: after the run, its header says ultracode and the cap; the form keeps the settings", head.replace("\n", " ")[:160])
            agents, costs = page.inner_text("#dir-agents"), page.inner_text("#dir-costs")
            total = re.search(r"total \$(\d+\.\d\d)", costs)
            say(re.search(r"done\s*build s3\s*haiku\s*\d+m \d\ds", agents) is not None and "haiku-fake" in costs and "agents" in costs
                and "the director" in costs and "the critic sonnet" in costs and total is not None
                and abs(float(total.group(1)) - float(d.get("total_usd") or 0)) <= 0.0051 and f"${total.group(1)}" in head,
                "UI: after the run, the Agents list (build s3, haiku, done, its duration) and the costs per model (the director's, "
                "the agents', the critic's) with the total", (agents + " || " + costs).replace("\n", " | ")[:300])
            say(not errs, "UI: the Direct page's form and run view (headless Chromium)", "no JS errors" if not errs else "; ".join(errs)[:300])
    finally:
        if os.path.exists(flag):
            os.remove(flag)
        if b:
            b.close()
        if pw:
            pw.stop()


def live_check(B, name, out, browser=True):
    """Live preview: GET live (the project's live server on its own port, the plan's timeline, the scenes, the sizes);
    what the live server serves (the shell, fonts.css, mv.js, a scene, the song; no-store) and refuses (/_mv/abs/
    outside the studio, .., another host, a POST); a live note round-trips (version live, the scene, the timeline). In
    headless Chromium (autoplay allowed) the Watch page's live view plays the plan's scenes with the song: the frames
    report rendered frames, nothing goes wrong, a screenshot; the size picker renders them smaller; the sound delay is
    kept; a stateful scene from work/live.json is caught up after a seek; N saves a live note with its scene."""
    import http.client
    from urllib.parse import urlparse, quote
    pdir = os.path.join(STUDIO, "projects", name)
    L = requests.get(f"{B}/api/p/{name}/live").json()
    base, tl = L.get("base") or "", L.get("timeline") or {}
    port = urlparse(base).port
    scenes = {x["path"] for x in L.get("scenes") or []}
    say(bool(port) and urlparse(base).hostname == "127.0.0.1" and tl.get("source") == "plan.json"
        and [x["id"] for x in tl.get("layers") or []] == ["s1", "s2", "s3", "type"] and tl["layers"][-1]["transparent"]
        and {"work/scenes/s2/scene.js", "work/scenes/type/type.html", "../../tests/scenes/field.js"} <= scenes
        and bool(L.get("song")) and L.get("sizes", {}).get("0.5") == [240, 136]
        and requests.get(f"{B}/api/p/{name}/live").json().get("base") == base,
        "GET live: the project's live server (127.0.0.1, its own port, the same one again), the plan's timeline (s1-s3, the "
        "type on top), the scenes (the plan's and the renders')", f"{base}; {[x['id'] for x in tl.get('layers') or []]}; {len(scenes)} scenes")

    def get(path, host=None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.request(method, path, headers={"Host": host} if host else {})
        r = c.getresponse()
        csp.append(r.getheader("Content-Security-Policy") or "")
        return r.status, r.getheader("Content-Type") or "", r.getheader("Cache-Control"), r.read()
    csp = []
    outside = os.path.join(os.path.dirname(STUDIO), "x.txt").replace("\\", "/").lstrip("/")
    p = {"shell": get("/_mv/live.html?scene=work/scenes/s2/scene.js&scale=0.5"), "fonts.css": get("/_mv/fonts.css"),
         "mv.js": get("/_mv/runtime/mv.js"), "scene": get("/work/scenes/s2/scene.js"), "song": get("/_mv/song/frames.json"),
         "abs outside": get("/_mv/abs/" + quote(outside)), "..": get("/../../tools/studio.py"),
         "abs studio notes": get("/_mv/abs/" + quote(os.path.join(STUDIO, "CLAUDE.md").replace("\\", "/").lstrip("/"))),
         "another host": get("/project.json", host="example.com"), "POST": get("/project.json", method="POST")}
    say(p["shell"][0] == 200 and b'"live": true' in p["shell"][3] and b'"w": 240' in p["shell"][3] and p["fonts.css"][1].startswith("text/css")
        and p["mv.js"][0] == 200 and b"mv:live" in p["mv.js"][3] and p["scene"][0] == 200 and p["scene"][2] == "no-store"
        and p["song"][0] == 200 and p["abs outside"][0] == 403 and p[".."][0] == 403 and p["another host"][0] == 403
        and p["abs studio notes"][0] == 403 and "default-src 'self'" in csp[0]
        and p["POST"][0] in (405, 501), "the live server: the shell (live config, a CSP that keeps it off the network), fonts.css, "
        "mv.js, a scene (no-store), the song; /_mv/abs/ outside the studio or to its own notes, .., another host and a POST refused",
        "; ".join(f"{k} {v[0]}" for k, v in p.items()) + f"; CSP {csp[0][:60]}")
    n0 = len(requests.get(f"{B}/api/p/{name}").json()["notes"])
    notes = requests.post(f"{B}/api/p/{name}/note", json={"version": "live", "t": 21.5, "text": "the type comes in late (live)",
                                                         "scene": "work/scenes/s2/scene.js", "timeline": "plan.json"}).json()
    nt = next((n for n in notes if n.get("text") == "the type comes in late (live)"), {})
    back = next((n for n in requests.get(f"{B}/api/p/{name}/live").json().get("notes") or [] if n.get("id") == nt.get("id")), {})
    done = next((n for n in requests.post(f"{B}/api/p/{name}/note-edit", json={"id": nt.get("id"), "done": True}).json()
                 if n.get("id") == nt.get("id")), {})
    say(len(notes) == n0 + 1 and nt.get("version") == "live" and back.get("scene") == "work/scenes/s2/scene.js"
        and back.get("timeline") == "plan.json" and done.get("done") is True,
        "a live note round-trips (POST note: version live, the scene, the timeline; GET live lists it; note-edit done)", str(back)[:160])
    if not browser:
        return
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    from mv.web import launch
    from playwright.sync_api import sync_playwright
    shots = os.path.join(out, "ui")
    os.makedirs(shots, exist_ok=True)
    errs = []
    lj = os.path.join(pdir, "work", "live.json")
    with sync_playwright() as pw:
        b, _ = launch(pw, "off", extra=["--autoplay-policy=no-user-gesture-required"])
        try:
            page = b.new_page(viewport={"width": 1280, "height": 1000})
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.goto(f"{B}/#{name}/watch")
            page.wait_for_selector(".vtabs .chip", timeout=15000)
            page.click(".vtabs .chip:has-text('live')")
            page.wait_for_selector("#lv-player iframe", timeout=15000)
            page.wait_for_function("() => LVS.player && LVS.player.layers.filter(l => l.el && l.kind === 'scene').every(l => l.st && l.st.ready)", timeout=60000)
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("Space")
            page.wait_for_timeout(6000)
            st = page.evaluate("LVS.player.layers.map(l => ({id: l.id, on: l.on, el: !!l.el, frame: l.st && l.st.frame, fps: l.st && l.st.fps, "
                               "ready: l.st && l.st.ready, nerr: l.st ? l.st.nerr : null}))")
            t_au = page.evaluate("$('#sv-au').currentTime")
            text = page.inner_text("#lv-st")
            page.screenshot(path=os.path.join(shots, "watch-live.png"), full_page=True)
            page.keyboard.press("Space")
            by = {x["id"]: x for x in st}
            s1, ty = by.get("s1") or {}, by.get("type") or {}
            say(t_au > 3 and s1.get("on") and (s1.get("frame") or 0) > 60 and (s1.get("fps") or 0) > 0 and s1.get("nerr") == 0
                and ty.get("on") and ty.get("ready") and ty.get("nerr") == 0 and "fps" in text,
                "UI: the Watch page's live view plays the plan's scenes with the song (headless Chromium, autoplay allowed): "
                "the frames report rendered frames", f"song at {t_au:.1f} s; {st}")
            page.click(".lv-bar [data-scale='0.25']")
            page.wait_for_function("() => LVS.player && LVS.player.layers.some(l => l.st && l.st.ready && l.st.w === 120)", timeout=60000)
            page.fill("#lv-off", "200")
            page.keyboard.press("Tab")
            page.evaluate("svSeek(10, false)")
            page.wait_for_timeout(600)
            off = page.evaluate("[lvOffset(), localStorage.getItem('mv-live-offset'), LVS.player.P.t, $('#sv-au').currentTime]")
            say(off[0] == 200 and off[1] == "200" and abs(off[3] - off[2] - 0.2) < 0.02,
                "UI: the size picker (the scenes at a quarter size) and the sound delay (the picture 200 ms behind the audio, kept)", str(off))
            page.fill("#lv-off", "0")
            page.keyboard.press("Tab")
            json.dump({"layers": [{"scene": "../../tests/scenes/trail.js"}, {"scene": "work/scenes/type/type.html", "transparent": True}]},
                      open(lj, "w", encoding="utf-8"))
            page.wait_for_function("() => [...document.querySelectorAll('#lv-pick option')].some(o => o.value === 'live.json')", timeout=15000)
            page.focus("#lv-pick")
            page.select_option("#lv-pick", "live.json")
            off_pick = page.evaluate("document.activeElement.tagName !== 'SELECT'")      # (picked: Space and N work at once)
            page.wait_for_function("() => LVS.player && LVS.player.layers[0].st && LVS.player.layers[0].st.ready", timeout=60000)
            page.evaluate("svSeek(20, false)")
            page.wait_for_function("() => { const s = LVS.player.layers[0].st; return s && s.ready && s.stateful && s.frame === 600 && !s.catching_up; }", timeout=90000)
            page.keyboard.press("n")
            page.fill("#ntx", "the trail should thin out here (live)")
            page.click("#nsave")
            page.wait_for_function("() => /thin out/.test($('#lv-notes').innerText)", timeout=10000)
            ns = requests.get(f"{B}/api/p/{name}").json()["notes"]
            mine = next((n for n in ns if n.get("text") == "the trail should thin out here (live)"), {})
            say(mine.get("version") == "live" and mine.get("scene") == "../../tests/scenes/trail.js" and abs(mine.get("t", 0) - 20) < 0.1
                and mine.get("timeline") == "live.json" and off_pick,
                "UI: a stateful scene from work/live.json caught up after a seek (frame 600); N saves a live note with the scene and the "
                "timeline (right after picking the timeline: the picker lets go of the keys)", str(mine)[:200] + f"; off the picker {off_pick}")
            # play and pause it, switch the song's source, change the sound delay: the stateful scene never starts again
            # from its setup (its page isn't reloaded: the clock correcting itself is no jump)
            page.evaluate("window.scrollTo(0, 0)")          # (the note box scrolled the player away: Chrome doesn't run an offscreen frame)
            page.evaluate("window.__loads = 0; LVS.player.layers[0].el.addEventListener('load', () => window.__loads++)")
            for k in range(4):
                page.evaluate("$('#sv-au').play()")
                page.wait_for_timeout(900 + 150 * k)
                page.evaluate("$('#sv-au').pause()")
                page.wait_for_timeout(400)
            page.evaluate("$('#sv-au').play()")
            page.wait_for_timeout(700)
            page.evaluate("svSource('mix')")                                                  # the source loaded again (a stem switch)
            page.wait_for_timeout(1500)
            page.evaluate("lsSet('mv-live-offset', '120')")
            page.wait_for_timeout(800)
            page.evaluate("lsSet('mv-live-offset', '0'); $('#sv-au').pause()")
            page.wait_for_timeout(600)
            kept = page.evaluate("[window.__loads, LVS.player.layers[0].st.reloading, LVS.player.layers[0].st.frame, $('#sv-au').currentTime]")
            say(kept[0] == 0 and not kept[1] and kept[2] > 600, "UI: playing, pausing, a switch of the song's source and a new sound "
                "delay don't restart a stateful scene (its page isn't reloaded)", str(kept))
        finally:
            b.close()
            if os.path.exists(lj):
                os.remove(lj)
    say(not errs, "UI: the live view (headless Chromium)", "no JS errors" if not errs else "; ".join(errs)[:300])

def lyric_timing_check(B, name):
    """The lyric timing editor's save: LRC in project.json, marked as timed on this song file, so the analysis uses the
    times as they are (here shifted 3 s on purpose: an offset search would have moved them back); the old lyrics kept;
    then the true times again (with their word times). Changed lyrics on the brief page drop the mark."""
    st = requests.get(f"{B}/api/p/{name}").json()
    m = requests.get(B + st["song"]["map"]).json()
    true = [{"t": x["t"], "text": x["text"], "words": [[w["w"], w["t"]] for w in x["words"]]} for x in m["lyrics"]]
    for x in true:
        x["set"] = True                                                    # set by hand: kept exactly
    shifted = [{"t": round(x["t"] + 3.0, 2), "text": x["text"], "set": True} for x in true]
    shifted[0]["end"] = round(shifted[0]["t"] + 1.7, 2)                    # the user said when the first line stops
    r = requests.post(f"{B}/api/p/{name}/lyrics-timing", json={"lines": shifted}).json()
    say(r.get("ok") and r.get("lines") == len(shifted), "POST lyrics-timing (saved; the song is analysed again)", str(r)[:120])
    say("error" in requests.post(f"{B}/api/p/{name}/lyrics-timing", json={"lines": shifted}).json(), "lyrics-timing waits for a running job")
    j, _ = wait_job(B, name, want="ingest")
    st = requests.get(f"{B}/api/p/{name}").json()
    m2 = requests.get(B + st["song"]["map"]).json()
    meta, pj = m2.get("lyrics_meta") or {}, st["project"]
    dt = [round(b["t"] - a["t"], 2) for a, b in zip(shifted, m2["lyrics"])]
    say(j.get("returncode") == 0 and pj.get("lyrics_synced") and pj["lyrics"].startswith("[00:") and meta.get("offset") == 0
        and "timed in the studio" in meta.get("source", "") and len(m2["lyrics"]) == len(shifted) and abs(sorted(dt)[len(dt) // 2]) <= 0.35,
        "analysed again with the timed lines as they are (no offset search)", f"{meta.get('source')}, offset {meta.get('offset')}, "
        f"line starts moved {min(dt)}..{max(dt)} s (snapped to the voice)")
    l0 = m2["lyrics"][0] if m2["lyrics"] else {}
    say(l0.get("end_src") == "user" and abs(l0.get("end", 0) - shifted[0]["end"]) < 0.02 and f"<{shifted[0]['end'] // 60:02.0f}:" in pj["lyrics"],
        "a line's end set in the editor is kept (an end tag in the LRC)", f"end {l0.get('end')} (set {shifted[0]['end']})")
    kept = os.listdir(os.path.join(STUDIO, "projects", name, "cache", "lyrics"))
    say(any(f.startswith("lyrics_") for f in kept), "the lyrics it replaced are kept in cache/lyrics/")
    say("error" in requests.post(f"{B}/api/p/{name}/lyrics-timing", json={"lines": shifted[:2]}).json(), "lyrics-timing needs 3 lines")
    pj = requests.post(f"{B}/api/p/{name}/brief", json={"lyrics": pj["lyrics"]}).json()
    keep = bool(pj.get("lyrics_synced"))
    pj = requests.post(f"{B}/api/p/{name}/brief", json={"lyrics": pj["lyrics"] + "[00:59.00] one more\n"}).json()
    say(keep and not pj.get("lyrics_synced"), "the brief page keeps the mark for the same lyrics, drops it for changed ones")
    requests.post(f"{B}/api/p/{name}/lyrics-timing", json={"lines": true})
    wait_job(B, name, want="ingest")
    m3 = requests.get(B + requests.get(f"{B}/api/p/{name}").json()["song"]["map"]).json()
    same = all(abs(a["t"] - b["t"]) < 0.02 and len(a["words"]) == len(b["words"]) and
               all(abs(w[1] - v["t"]) < 0.012 for w, v in zip(a["words"], b["words"])) for a, b in zip(true, m3["lyrics"]))
    say(same and len(m3["lyrics"]) == len(true) and "word times" in (m3.get("lyrics_meta") or {}).get("source", ""),
        "word-timed lines keep their word times through the editor", (m3.get("lyrics_meta") or {}).get("source", ""))


def whisper_check(B, name):
    """Lyrics placed where Whisper heard them. Whisper can't be downloaded in a test, so its transcription is a
    stand-in written for this song file: the truth's words with two misheard and one missed. The timing editor's
    "Align with Whisper" job places lines written with the second "hold the light" left out (it comes back as a
    repeat) and "run run run" left out (Whisper heard it: listed to add); then the same plain-text lyrics are
    analysed, placed on the transcription."""
    pdir = os.path.join(STUDIO, "projects", name)
    truth = json.load(open(os.path.join(TESTS, "out", "test_song_truth.json"), encoding="utf-8"))
    song = next(f for f in os.listdir(os.path.join(pdir, "song")) if f.endswith(".mp3"))
    words = [dict(w, end=round(w["t"] + 0.3, 3)) for w in truth["words"]]
    words[3]["w"], words[10]["w"] = "fallin", "lites"
    del words[20]
    tr = {"model": "openai/whisper-large-v3-turbo", "language": "en",
          "song": {"file": song, "size": os.path.getsize(os.path.join(pdir, "song", song))},
          "lines": [{"t": words[0]["t"], "end": words[-1]["end"], "text": " ".join(w["w"] for w in words), "words": words}]}
    os.makedirs(os.path.join(pdir, "cache", "lyrics"), exist_ok=True)
    json.dump(tr, open(os.path.join(pdir, "cache", "lyrics", "transcribed.json"), "w", encoding="utf-8"))
    texts = [x["text"] for x in truth["lines"]]
    sent = [t for k, t in enumerate(texts) if k not in (6, 8)]
    expect = [x for k, x in enumerate(truth["lines"]) if k != 8]
    r = requests.post(f"{B}/api/p/{name}/lyrics-align", json={"lines": [{"text": t} for t in sent]}).json()
    say(r.get("ok"), "POST lyrics-align (Whisper places the editor's lines: a job)", str(r)[:100])
    j, _ = wait_job(B, name, want="align")
    al = requests.get(f"{B}/p/{name}/cache/lyrics/aligned.json").json() if j.get("returncode") == 0 else {"lines": [], "stats": {}}
    err = max([abs(a["t"] - b["t"]) for a, b in zip(al["lines"], expect)] or [99])
    heard = [h["text"] for h in al.get("heard_only") or []]
    say(j.get("returncode") == 0 and [x["text"] for x in al["lines"]] == [x["text"] for x in expect] and err < 0.15
        and al["stats"].get("repeats_added") == 1 and heard == ["run run run"],
        "placed where Whisper heard them: every line in place, the line written once comes back as a repeat, the line left out is listed",
        f"{len(al['lines'])} lines, worst start {err:.2f} s off, {al['stats'].get('repeats_added')} repeat(s), heard only {heard}")
    pj = requests.post(f"{B}/api/p/{name}/brief", json={"lyrics": "\n".join(sent)}).json()
    requests.post(f"{B}/api/p/{name}/ingest")
    j, _ = wait_job(B, name, want="ingest")
    m = requests.get(B + requests.get(f"{B}/api/p/{name}").json()["song"]["map"]).json()
    meta = m.get("lyrics_meta") or {}
    err = max([abs(a["t"] - b["t"]) for a, b in zip(m["lyrics"], expect)] or [99])
    say(j.get("returncode") == 0 and "timed with Whisper" in meta.get("source", "") and len(m["lyrics"]) == len(expect) and err < 0.35
        and [h["text"] for h in meta.get("heard_only") or []] == ["run run run"],
        "plain-text lyrics analysed: placed on the transcription, the line left out listed to add",
        f"{meta.get('source')}, {len(m['lyrics'])} lines, worst start {err:.2f} s off, heard only {[h['text'] for h in meta.get('heard_only') or []]}")
    # saved in the editor with only one line set by hand: the others (2 s off, never touched) go back where Whisper heard them
    lines = [{"t": round(x["t"] + 2.0, 2), "text": x["text"], "set": k == 2} for k, x in enumerate(m["lyrics"])]
    requests.post(f"{B}/api/p/{name}/lyrics-timing", json={"lines": lines})
    j, _ = wait_job(B, name, want="ingest")
    st = requests.get(f"{B}/api/p/{name}").json()
    m2 = requests.get(B + st["song"]["map"]).json()
    meta = m2.get("lyrics_meta") or {}
    off = [round(a["t"] - b["t"], 2) for a, b in zip(m2["lyrics"], expect)]
    say(j.get("returncode") == 0 and st["project"].get("lyrics_set") == [lines[2]["t"]] and abs(off[2] - 2.0) < 0.35
        and all(abs(o) < 0.35 for k, o in enumerate(off) if k != 2) and "placed with Whisper" in meta.get("source", ""),
        "lines saved in the editor but never set by hand are placed with Whisper; the line set by hand stays",
        f"{meta.get('source')}; offsets from the truth {off}")
    requests.post(f"{B}/api/p/{name}/brief", json={"lyrics": "\n".join(sent)})       # back to plain text for what follows
    requests.post(f"{B}/api/p/{name}/ingest")
    wait_job(B, name, want="ingest")


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
            if h.endswith("/listen"):                             # the lyric timing editor: open, hold, drag, undo, close
                say("Whisper heard 1 line" in page.inner_text("#pane"), "UI: the song page says what Whisper heard that isn't in the lyrics")
                n_lyr = len(requests.get(B + requests.get(f"{B}/api/p/{name}").json()["song"]["map"]).json()["lyrics"])
                page.click("#lx-open")
                page.wait_for_selector("#lx-list .lx-r", timeout=5000)
                rows = page.locator("#lx-list .lx-r").count()
                page.evaluate("document.activeElement.blur()")             # off the button: Enter taps, not presses it
                page.evaluate("svSeek(10, false, true)")
                page.keyboard.down("Enter")                                  # held from 10 s to 12.5 s of the song
                page.evaluate("svMedia().currentTime = 12.5")
                page.keyboard.up("Enter")
                page.wait_for_timeout(300)
                held = page.evaluate("[SV.ed.lines[0].t, SV.ed.lines[0].end]")
                tapped = page.locator("#lx-list .lx-r.set").count()
                page.wait_for_function("() => new Promise(r => { const y = scrollY; setTimeout(() => r(scrollY === y && !!SV.xmap && Math.abs(SV.xmap.w0 - SV.w0) < 1e-6), 300); })", timeout=10000)    # the scroll done, the lanes drawn as they are
                geo = page.evaluate("""() => { const v = SV.m.lyrics.find(y => y.k === 0), M = SV.xmap, L = SV.lyLane, r = $('#sv-cv').getBoundingClientRect();
                                         return {x: r.left + M.LX + (v.end - M.w0) * M.pxs - 2, y: r.top + L[0] + L[1] / 2, pxs: M.pxs}; }""")
                page.mouse.move(geo["x"], geo["y"])
                page.mouse.down()
                page.mouse.move(geo["x"] + 40, geo["y"], steps=5)
                page.mouse.up()
                page.wait_for_timeout(300)
                dragged = page.evaluate("SV.ed.lines[0].end")
                page.screenshot(path=os.path.join(shots, "listen-lyric-timing.png"), full_page=True)
                page.keyboard.press("Backspace")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(200)
                undone = page.locator("#lx-list .lx-r.set").count()
                page.click("#lx-align")                                   # the stand-in transcription is there
                page.wait_for_function("SV.ed && !SV.ed.aligning", timeout=120000)
                truth = json.load(open(os.path.join(TESTS, "out", "test_song_truth.json"), encoding="utf-8"))
                n_al = page.evaluate("SV.ed.lines.length")
                page.click(".lx-heard div button")                         # Whisper heard "run run run": add it
                page.wait_for_timeout(300)
                got = page.evaluate("SV.ed.lines.map(x => [x.text, x.t])")
                aligned = len(got) == len(truth["lines"]) and all(a[0] == b["text"] and abs(a[1] - b["t"]) < 0.4 for a, b in zip(got, truth["lines"]))
                say(aligned and n_al == len(truth["lines"]) - 1 and page.locator("#lx-list .lx-r.new").count() == 1,
                    "UI: Align with Whisper fills in the lines; a line it heard is added with one click", f"{n_al} -> {len(got)} lines; {got[7:9]}")
                page.once("dialog", lambda d: d.accept())
                page.click("#lx button:has-text('Cancel')")
                page.wait_for_timeout(300)
                ok = (rows == n_lyr and tapped == 1 and abs(held[0] - 10) < 0.05 and abs((held[1] or 0) - 12.5) < 0.05
                      and abs((dragged or 0) - 12.5 - 40 / geo["pxs"]) < 0.15 and undone == 0 and page.locator("#lx-list").count() == 0)
                say(ok, "UI: the lyric timing editor (hold Enter = start + end, drag a line's end, undo, cancel)",
                    f"{rows}/{n_lyr} lines; held {held}; end dragged to {dragged and round(dragged, 2)}; after undo {undone} set")
            if h.endswith("/watch"):
                page.wait_for_function("() => /ambition/.test(($('#wcrit-md') || {}).innerText || '')", timeout=10000)
                say("ambition" in page.inner_text("#wcritic") and page.locator("#go-critique").count() == 1,
                    "UI: the Watch page's critic box (scores, the notes, Ask the critic)", page.inner_text("#wcritic")[:90].replace("\n", " "))
            if h.endswith("/direct"):
                say(page.locator("#d-critic option").count() == 4 and page.input_value("#d-critic") in ("sonnet", "fake"),
                    "UI: the Direct page's critic select", page.input_value("#d-critic"))
                say(page.locator("#dir-ev").count() == 1 and page.input_value("#d-effort") == "max" and page.is_checked("#d-ultra")
                    and page.input_value("#d-cap") == "25", "UI: the Direct page shows the last run, with the settings kept",
                    f"effort {page.input_value('#d-effort')}, ultracode {page.is_checked('#d-ultra')}, cap {page.input_value('#d-cap')}")
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
