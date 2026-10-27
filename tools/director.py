"""The director, run headless: the story step, the autonomous run with a time budget, versions and delivery.

  studio.py story <p>                     Claude drafts song/song-story.md (read-only tools; the text comes back here)
  studio.py direct <p> --budget 90m ...   fill templates/director-brief.md, run Claude Code headless in the project folder
  studio.py direct <p> --revise ...       the review round: templates/revise-brief.md with the user's notes
  studio.py clock <p>                     time left in the running budget
  studio.py deliver <p>                   archive final/music-video.mp4 as the next version, run the checks, record it

The autonomous run (checked against `claude --help` of Claude Code 2.1.x):
  claude -p --input-format stream-json --output-format stream-json --verbose --model M --effort E
         --permission-mode dontAsk --allowedTools Read Write Edit Glob Grep Bash TodoWrite
         --disallowedTools WebFetch WebSearch --settings <deny network commands> --add-dir <studio tools, assets>
         --append-system-prompt <the studio's ground rules>
  cwd = the project folder. The brief goes in as the first stream-json user message; the runner reads every event,
  keeps work/director/run-N/events.jsonl (raw) and status.json (summarised, polled by the web app), and sends more user
  messages: time warnings (half way, final-render time, 5 minutes left, time's up), a nudge when the director stops
  early with time left (use it to improve the video), a reminder when no final exists yet. At the deadline plus a grace
  period (or when work/director/STOP appears) it ends the session. Afterwards: the cut is archived as
  final/music-video_vN.mp4, the checks run, and report.md gets the run settings (model, effort, budget, wall time, cost).
"""
import json, os, re, shutil, signal, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, jload, jsave, song_file, project as load_pj, save_project  # noqa: E402

TEMPLATES = os.path.join(STUDIO, "templates")
SKILL = os.path.join(STUDIO, ".claude", "skills", "musicvideo", "SKILL.md")
ALLOWED = ["Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Bash", "TodoWrite"]
DISALLOWED = ["WebFetch", "WebSearch"]
DENY_BASH = ["curl", "wget", "Invoke-WebRequest", "iwr", "Invoke-RestMethod", "irm", "pip install", "pip3 install",
             "python -m pip", "npm install", "npm i", "npx", "yarn", "pnpm", "git push", "git clone", "ssh", "scp",
             "winget", "apt", "apt-get", "brew", "choco", "playwright install"]
SYSTEM_APPEND = ("You are running unattended inside Music Video Studio as the director of one music video. Nobody "
                 "answers questions: decide and keep going. Stay inside this project folder (the studio tools may be "
                 "read and run, not edited). No network: everything you need is installed. Watch the clock.")


def claude_bin():
    """The claude command as a list (MV_CLAUDE overrides; a .py there runs with this Python: tests use a stand-in)."""
    c = os.environ.get("MV_CLAUDE") or shutil.which("claude")
    if not c:
        return None
    return [sys.executable, c] if c.endswith(".py") else [c]


def parse_budget(s):
    """'90m', '1h30m', '2h', '45' (minutes), '5400s' -> seconds."""
    s = str(s).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return int(float(s) * 60)
    tot, ok = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", s):
        tot += float(num) * {"h": 3600, "m": 60, "s": 1}[unit]
        ok = True
    if not ok:
        raise ValueError(f"can't read the budget {s!r}: use e.g. 90m, 1h30m, 45")
    return int(tot)


def fmt_dur(sec):
    sec = int(max(0, sec))
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def rel(p, start):
    return os.path.relpath(p, start).replace("\\", "/")


def venv_python(pdir):
    py = os.path.join(STUDIO, ".venv", "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
    return rel(py if os.path.exists(py) else sys.executable, pdir)


def fill(text, values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), "")), text)


def user_notes(pj, seed=""):
    out = []
    for k, lab in (("about", "What the song is about to me"), ("mood", "Mood / direction"), ("ideas", "Ideas"),
                   ("avoid", "Avoid")):
        v = str(pj.get(k) or "").strip()
        if v:
            out.append(f"{lab}: {v}")
    if seed.strip():
        out.append(f"Also: {seed.strip()}")
    return "\n".join(out)


# ------------------------------------------------------------------ the story step
def story(pdir, model=None, log=print):
    from studio import Status
    st = Status(pdir, "story", ["Claude reads the song folder and your notes", "writing song/song-story.md"])
    try:
        cl = claude_bin()
        if not cl:
            raise RuntimeError("Claude Code isn't installed (https://claude.com/claude-code)")
        pj = load_pj(pdir)
        sm = jload(os.path.join(pdir, "song", "song-map.json"), None)
        if not sm:
            raise RuntimeError("no song folder yet: run Analyse (ingest) first")
        title = f"{sm.get('artist')} – {sm.get('title')}" if sm.get("artist") else sm.get("title") or sm["file"]
        prompt = fill(open(os.path.join(TEMPLATES, "story-prompt.md"), encoding="utf-8").read(),
                      {"notes": user_notes(pj) or "(no notes given)", "title": title, "date": time.strftime("%Y-%m-%d")})
        cmd = [*cl, "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk", "--allowedTools", "Read",
               "--disallowedTools", "Write", "Edit", "Bash", "WebFetch", "WebSearch", "--no-session-persistence"]
        if model or (pj.get("director") or {}).get("model"):
            cmd += ["--model", model or pj["director"]["model"]]
        log("Claude is reading the song folder (1-3 min)...")
        r = subprocess.run(cmd, cwd=pdir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
        try:
            res = json.loads(r.stdout)
            text = res.get("result", "")
        except Exception:
            res, text = {}, r.stdout
        st.step(1)
        text = re.sub(r"^```(?:markdown)?\s*|\s*```\s*$", "", text.strip())
        if "# Song story" not in text or "## What it's about" not in text:
            raise RuntimeError("Claude didn't return a song story: " + (text or r.stderr)[-400:])
        sp = os.path.join(pdir, "song", "song-story.md")
        if os.path.exists(sp) and "_2–4 sentences" not in open(sp, encoding="utf-8").read():
            os.makedirs(os.path.join(pdir, "cache", "story"), exist_ok=True)
            shutil.copyfile(sp, os.path.join(pdir, "cache", "story", f"song-story_{time.strftime('%Y%m%d-%H%M%S')}.md"))
        open(sp, "w", encoding="utf-8").write(text.strip() + "\n")
        pj = load_pj(pdir)
        pj.setdefault("status", {})["story"] = {"by": "claude", "at": time.strftime("%Y-%m-%d %H:%M"),
                                               "cost_usd": res.get("total_cost_usd")}
        save_project(pj, pdir)
        log(f"-> {sp}")
        st.done()
    except Exception as e:
        st.done("error", str(e))
        raise


# ------------------------------------------------------------------ versions
def versions(pdir):
    pj = load_pj(pdir)
    out = []
    for v in pj.get("versions") or []:
        if os.path.exists(os.path.join(pdir, v["file"])):
            out.append(v)
    return out


def deliver(pdir, run=None, log=print):
    """Archive final/music-video.mp4 as the next version (unless it already is one), run the checks, record it."""
    fin = os.path.join(pdir, "final", "music-video.mp4")
    if not os.path.exists(fin):
        log("no final/music-video.mp4 to deliver")
        return None
    pj = load_pj(pdir)
    vs = pj.setdefault("versions", [])
    size, mtime = os.path.getsize(fin), os.path.getmtime(fin)
    for v in vs:                                        # the same cut delivered twice is one version
        if v.get("size") == size and abs(v.get("mtime", 0) - mtime) < 1:
            log(f"already delivered as v{v['n']}")
            return v
    n = max([v["n"] for v in vs] or [0]) + 1
    dst = os.path.join(pdir, "final", f"music-video_v{n}.mp4")
    shutil.copyfile(fin, dst)
    for ext in (".mv.json",):
        if os.path.exists(fin + ext):
            shutil.copyfile(fin + ext, dst + ext)
    if os.path.exists(os.path.join(pdir, "report.md")):
        shutil.copyfile(os.path.join(pdir, "report.md"), os.path.join(pdir, "final", f"report_v{n}.md"))
    env = dict(os.environ, MV_PROJECT=pdir, PYTHONIOENCODING="utf-8", MV_IN_VENV="1")
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(HERE, "checks.py"), "all", dst], cwd=STUDIO, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    rep = jload(os.path.join(pdir, "work", "checks", "report.json"), {}) or {}
    v = {"n": n, "file": f"final/music-video_v{n}.mp4", "at": time.strftime("%Y-%m-%d %H:%M"), "size": size, "mtime": mtime,
         "checks": rep.get("verdicts"), "sync": (rep.get("sync") or {}).get("share"), "run": run}
    pj = load_pj(pdir)
    pj.setdefault("versions", []).append(v)
    pj.setdefault("status", {}).update(stage=f"v{n} delivered", latest=f"v{n}")
    save_project(pj, pdir)
    log(f"delivered v{n}: {dst}  checks: {rep.get('verdicts')}")
    if r.returncode:
        log(r.stdout[-1500:])
    return v


# ------------------------------------------------------------------ the clock
def current(pdir):
    return jload(os.path.join(pdir, "work", "director", "current.json"), None)


def clock(pdir):
    c = current(pdir)
    if not c or c.get("state") not in ("running", "finishing"):
        print("no autonomous run is going on (no time budget). " + (f"Last run: {c.get('state')}" if c else ""))
        return
    now = time.time()
    left = c["deadline"] - now
    print(f"{time.strftime('%H:%M:%S')}  elapsed {fmt_dur(now - c['started'])} of {fmt_dur(c['budget_s'])}; "
          f"{'LEFT ' + fmt_dur(left) if left > 0 else 'TIME IS UP: finish now'} (deadline {time.strftime('%H:%M', time.localtime(c['deadline']))})")


# ------------------------------------------------------------------ the autonomous run
def summarize(ev):
    """One short line (or None) for the web app's live log."""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"session started · model {ev.get('model')} · {len(ev.get('tools') or [])} tools · permission {ev.get('permissionMode')}"
    if t == "assistant":
        out = []
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append("» " + re.sub(r"\s+", " ", c["text"]).strip()[:400])
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
                if c.get("name") == "TodoWrite":
                    todos = inp.get("todos") or []
                    arg = " | ".join(f"{'✓' if x.get('status') == 'completed' else '→' if x.get('status') == 'in_progress' else '·'} {x.get('content', '')[:40]}" for x in todos[:8])
                out.append(f"⚙ {c.get('name')}: {str(arg)[:220]}")
        return "\n".join(out) or None
    if t == "user":
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                txt = c.get("content")
                txt = txt if isinstance(txt, str) else json.dumps(txt)[:300]
                txt = re.sub(r"\s+", " ", txt)[:240]
                return f"  ✗ {txt}"
        return None
    if t == "result":
        return (f"■ turn finished ({ev.get('subtype')}): {ev.get('num_turns')} turns, "
                f"${(ev.get('total_cost_usd') or 0):.2f}, {round((ev.get('duration_ms') or 0) / 60000, 1)} min")
    return None


def user_msg(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"


def build_brief(pdir, budget_s, seed_notes="", revise=False, started=None, deadline=None):
    pj = load_pj(pdir)
    d = delivery(pj)
    sf = song_file(pdir)
    sm = jload(os.path.join(pdir, "song", "song-map.json"), {}) or {}
    tools = rel(os.path.join(STUDIO, "tools"), pdir)
    name = os.path.basename(pdir)
    vals = {"file": os.path.basename(sf), "duration": f"{float(sm.get('duration', 0)):.2f}", "width": d["width"],
            "height": d["height"], "fps": d["fps"], "budget": fmt_dur(budget_s), "tools": tools, "project": name,
            "python": venv_python(pdir), "started": time.strftime("%H:%M", time.localtime(started)),
            "deadline": time.strftime("%H:%M", time.localtime(deadline)) + f" (in {fmt_dur(budget_s)})",
            "skill": rel(SKILL, pdir), "anti": rel(os.path.join(os.path.dirname(SKILL), "anti-defaults.md"), pdir)}
    notes = user_notes(pj, seed_notes)
    if revise:
        from mv.song import Song
        S = Song(pdir)
        vs = versions(pdir)
        vn = vs[-1]["n"] if vs else 1
        rows = []
        for nt in sorted([n for n in jload(os.path.join(pdir, "review", "notes.json"), []) or []
                          if not n.get("done") and str(n.get("version", "")).lstrip("v") == str(vn)], key=lambda n: n["t"]):
            a = S.at(float(nt["t"]))
            m, s_ = divmod(float(nt["t"]), 60)
            rows.append(f"- {int(m)}:{s_:05.2f} · {a['bar']}:{a['beat'] if a['beat'] is not None else '-'} · {a['section'] or '-'} · "
                        f"{('“' + a['line'] + '”') if a['line'] else '(no singing)'} · **{nt['text'].strip()}**")
        if not rows:
            raise SystemExit(f"no open notes on v{vn}: leave notes on the Watch page (press N) first")
        vals.update(version=vn, notes="\n".join(rows), round=vn + 1,
                    direction=("\nMy notes on the song (as before):\n" + notes) if notes else "")
        text = fill(open(os.path.join(TEMPLATES, "revise-brief.md"), encoding="utf-8").read(), vals)
    else:
        vals["direction"] = ("\n" + notes + "\n") if notes else ""
        vals["revise_note"] = ""
        text = fill(open(os.path.join(TEMPLATES, "director-brief.md"), encoding="utf-8").read(), vals)
    return text


def rule_path(p):
    """An absolute path in Claude Code's permission-rule form: //home/me/x, and on Windows //c/Users/me/x (paths are
    matched in POSIX form there). A relative rule (../../tools/**) does not match: tested with Claude Code 2.1.311."""
    q = os.path.abspath(p).replace("\\", "/")
    if re.match(r"^[A-Za-z]:", q):
        q = "/" + q[0].lower() + q[2:]
    return "/" + q


def claude_args(pdir, model, effort):
    cl = claude_bin()
    if not cl:
        raise SystemExit("Claude Code isn't installed: https://claude.com/claude-code (or set MV_CLAUDE)")
    deny = [f"Bash({c}:*)" for c in DENY_BASH]
    for d in ("tools", "assets", ".claude", "templates", "app"):             # the studio itself is read-only to the director
        rp = rule_path(os.path.join(STUDIO, d))
        deny += [f"Edit({rp}/**)", f"Write({rp}/**)"]
    settings = {"permissions": {"deny": deny + ["WebFetch", "WebSearch"]}}
    cmd = [*cl, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED, "--disallowedTools", *DISALLOWED,
           "--settings", json.dumps(settings), "--add-dir", os.path.join(STUDIO, "tools"), os.path.join(STUDIO, "assets"),
           os.path.join(STUDIO, ".claude", "skills"), os.path.join(STUDIO, "templates"),
           "--strict-mcp-config", "--append-system-prompt", SYSTEM_APPEND]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def direct(pdir, budget="90m", effort="high", model=None, seed_notes="", revise=False, nudge=True, dry_run=False, log=print):
    from studio import Status
    if not os.path.exists(os.path.join(pdir, "song", "song-map.json")):
        raise SystemExit("no song folder yet: run ingest first")
    budget_s = parse_budget(budget)
    pj = load_pj(pdir)
    model = model or (pj.get("director") or {}).get("model") or "opus"
    ddir = os.path.join(pdir, "work", "director")
    os.makedirs(ddir, exist_ok=True)
    n = 1 + max([int(x.split("-")[1]) for x in os.listdir(ddir) if re.fullmatch(r"run-\d+", x)] or [0])
    rdir = os.path.join(ddir, f"run-{n}")
    os.makedirs(rdir)
    if os.path.exists(os.path.join(ddir, "STOP")):
        os.remove(os.path.join(ddir, "STOP"))
    fin = os.path.join(pdir, "final", "music-video.mp4")
    if os.path.exists(fin):                    # the cut that exists now is never lost: archive it first
        deliver(pdir, run=None, log=log)
    try:                                       # tools/installed.md: what this machine has, for the director
        subprocess.run([sys.executable, os.path.join(HERE, "doctor.py")], capture_output=True, timeout=180,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except Exception:
        pass
    started = time.time()
    deadline = started + budget_s
    text = build_brief(pdir, budget_s, seed_notes, revise, started, deadline)
    open(os.path.join(rdir, "brief.md"), "w", encoding="utf-8").write(text)
    cmd = claude_args(pdir, model, effort)
    json.dump({"cmd": cmd, "cwd": pdir}, open(os.path.join(rdir, "command.json"), "w"), indent=1)
    cver = ""
    try:
        cver = subprocess.run([*claude_bin(), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        pass
    status = {"run": n, "state": "running", "kind": "revise" if revise else "first", "model": model, "effort": effort,
              "budget_s": budget_s, "budget": fmt_dur(budget_s), "started": started, "deadline": deadline,
              "claude_version": cver, "events": [], "turns": 0, "cost_usd": 0.0, "messages_sent": [], "pid": None}
    if dry_run:
        log(text)
        log("\n" + " ".join(cmd[:4]) + " ... (dry run: not started)")
        status["state"] = "dry-run"
        jsave(os.path.join(rdir, "status.json"), status)
        return status
    st = Status(pdir, "direct", ["Claude is directing", "archiving the cut and checking it"])

    def save():
        status["updated"] = time.time()
        status["elapsed_s"] = round(time.time() - started)
        status["final_exists"] = os.path.exists(fin)
        jsave(os.path.join(rdir, "status.json"), status)
        jsave(os.path.join(ddir, "current.json"), {k: v for k, v in status.items() if k != "events"} | {"dir": rel(rdir, pdir)})

    def note(line):
        status["events"].append({"t": round(time.time() - started), "text": line})
        del status["events"][:-300]
    proc = subprocess.Popen(cmd, cwd=pdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            env=dict(os.environ, MV_PROJECT=pdir, PYTHONIOENCODING="utf-8"),
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    status["pid"] = proc.pid
    raw = open(os.path.join(rdir, "events.jsonl"), "w", encoding="utf-8")
    errlog = open(os.path.join(rdir, "stderr.log"), "w", encoding="utf-8")
    results = []
    lock = threading.Lock()

    def reader():
        for line in proc.stdout:
            raw.write(line)
            raw.flush()
            try:
                ev = json.loads(line)
            except Exception:
                continue
            with lock:
                s = summarize(ev)
                if s:
                    note(s)
                if ev.get("type") == "result":
                    status["turns"] += ev.get("num_turns") or 0
                    status["cost_usd"] = round(status["cost_usd"] + (ev.get("total_cost_usd") or 0), 4)
                    status["usage"] = ev.get("usage")
                    results.append(ev)
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    status["session_id"] = ev.get("session_id")
                    status["model_used"] = ev.get("model")

    def errreader():
        for line in proc.stderr:
            errlog.write(line)
            errlog.flush()
    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=errreader, daemon=True).start()

    def send(text, why):
        try:
            proc.stdin.write(user_msg(text))
            proc.stdin.flush()
            status["messages_sent"].append({"t": round(time.time() - started), "why": why})
            with lock:
                note(f"✉ studio → director ({why})")
        except Exception as ex:
            note(f"(could not message the director: {ex})")
    send(text, "the brief")
    log(f"director run {n}: {model}, effort {effort}, budget {fmt_dur(budget_s)} (until {time.strftime('%H:%M', time.localtime(deadline))})")
    warned = set()
    nudges, reminders = 0, 0
    grace = min(600, max(180, int(0.1 * budget_s)))
    seen_results = 0
    stop_reason = None
    stdin_open = True
    try:
        while proc.poll() is None:
            time.sleep(1)
            now = time.time()
            left = deadline - now
            with lock:
                save()
            if os.path.exists(os.path.join(ddir, "STOP")) and stop_reason is None:
                stop_reason = "stopped by the user"
                send("The user stopped this run. Stop now: don't start anything new. If final/music-video.mp4 exists, "
                     "write report.md in a few lines and end.", "stop")
                deadline = min(deadline, now)
                grace = 60
            for frac, key, msg in ((0.5, "half", "Half of your time is gone ({left} left, deadline {dl}). Check your plan against the clock."),):
                if key not in warned and now - started >= frac * budget_s and left > 0:
                    warned.add(key)
                    send(msg.format(left=fmt_dur(left), dl=time.strftime("%H:%M", time.localtime(deadline))), key)
            final_at = max(10 * 60, 0.25 * budget_s)
            if "final" not in warned and left <= final_at and left > 5 * 60:
                warned.add("final")
                send(f"{fmt_dur(left)} left. If the full-size final isn't rendering yet, start it now (it takes the time "
                     f"tools/speeds.json says), then compose, mux, run the checks and write report.md.", "final-render time")
            if "five" not in warned and 0 < left <= 5 * 60:
                warned.add("five")
                send("5 minutes left: finish the final, run the checks, write report.md. Nothing new.", "5 minutes")
            if "up" not in warned and left <= 0:
                warned.add("up")
                status["state"] = "finishing"
                send(f"Time is up. Stop working on the video now. If final/music-video.mp4 exists, make sure report.md is "
                     f"written (a few lines are fine) and end. You have {fmt_dur(grace)} before the session is closed.", "time's up")
            if left <= -grace:
                stop_reason = stop_reason or "budget ran out (deadline + grace)"
                break
            if len(results) > seen_results and stdin_open:
                seen_results = len(results)
                have_final = os.path.exists(fin) and os.path.getmtime(fin) > started
                if left > 60 and not have_final and reminders < 3:
                    reminders += 1
                    send(f"There is no final/music-video.mp4 from this run yet and {fmt_dur(left)} are left. Carry on "
                         f"and deliver it (then the checks and report.md).", "no final yet")
                elif nudge and have_final and left > max(8 * 60, 0.25 * budget_s) and nudges < 2:
                    nudges += 1
                    send(f"You delivered a cut with {fmt_dur(left)} still on the clock. Spend it making the video better: "
                         f"look at it again with the eyes tools (sheets of each section, the beat sheets of the biggest "
                         f"sections, the motion plot), pick the weakest parts and improve them, then render, check and "
                         f"deliver again. Keep the current final until the new one passes the checks. Update report.md.", "use the time left")
                else:
                    stdin_open = False
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
    finally:
        if proc.poll() is None:
            note(f"■ ending the session: {stop_reason}")
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
        time.sleep(0.5)
        raw.close()
        errlog.close()
        wall = time.time() - started
        status["wall_s"] = round(wall)
        status["state"] = "stopped" if stop_reason and "user" in stop_reason else "done"
        status["stop_reason"] = stop_reason or "the director ended the session"
        status["exit_code"] = proc.returncode
        st.step(1)
        v = None
        if os.path.exists(fin) and os.path.getmtime(fin) > started:
            v = deliver(pdir, run=n, log=log)
        else:
            note("no new final/music-video.mp4 from this run")
        status["version"] = v["n"] if v else None
        append_settings(pdir, status, v)
        with lock:
            save()
        pj = load_pj(pdir)
        pj.setdefault("runs", []).append({k: status.get(k) for k in ("run", "kind", "model", "model_used", "effort", "budget",
                                                                     "wall_s", "cost_usd", "turns", "version", "stop_reason",
                                                                     "claude_version")} | {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(started))})
        save_project(pj, pdir)
        st.done("done" if v else "error", "" if v else "the run ended without a new final/music-video.mp4")
        log(f"director run {n} ended: {status['stop_reason']}; wall {fmt_dur(wall)}; cost ${status['cost_usd']:.2f}; "
            f"version {('v' + str(v['n'])) if v else 'none'}")
    return status


def append_settings(pdir, status, v):
    rp = os.path.join(pdir, "report.md")
    body = open(rp, encoding="utf-8").read() if os.path.exists(rp) else \
        "# Report\n\nThe director did not write a report in this run (the studio wrote this stub).\n"
    body = re.sub(r"\n## Run settings \(written by the studio\)[\s\S]*$", "", body).rstrip() + "\n"
    ver = f"v{v['n']} ({v['file']})" if v else "none (no new final in this run)"
    lines = ["", "## Run settings (written by the studio)", "",
             f"- Run {status['run']} ({status['kind']}), {time.strftime('%Y-%m-%d %H:%M', time.localtime(status['started']))}",
             f"- Model: {status.get('model_used') or status['model']} (asked for `{status['model']}`), effort `{status['effort']}`",
             f"- Budget: {fmt_dur(status['budget_s'])}; wall time: {fmt_dur(status.get('wall_s', 0))}; ended: {status.get('stop_reason')}",
             f"- Claude Code {status.get('claude_version') or '?'}; turns {status.get('turns')}; cost reported ${status.get('cost_usd', 0):.2f}",
             f"- Delivered: {ver}" + (f"; checks {v.get('checks')}" if v else "")]
    open(rp, "w", encoding="utf-8").write(body + "\n".join(lines) + "\n")
    if v:
        shutil.copyfile(rp, os.path.join(pdir, "final", f"report_v{v['n']}.md"))


def request_stop(pdir):
    os.makedirs(os.path.join(pdir, "work", "director"), exist_ok=True)
    open(os.path.join(pdir, "work", "director", "STOP"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))


def permission_test(pdir, model="haiku", log=print):
    """Check the director's sandbox with the real Claude Code (a few cents with Haiku): a project write is allowed,
    a write into the studio's tools and a network command are refused. Returns True when all three hold."""
    cmd = claude_args(pdir, model, "low")
    i = cmd.index("--append-system-prompt")
    del cmd[i:i + 2]                                   # without the "don't edit the tools" hint: the rules must hold alone
    probe = os.path.join(STUDIO, "tools", "_permtest.txt")
    mine = os.path.join(pdir, "work", "_permtest.txt")
    for f in (probe, mine):
        if os.path.exists(f):
            os.remove(f)
    msg = (f"This is an authorized test of this session's permission settings; just try each step even if you expect a "
           f"refusal. 1) Use the Write tool to create {rel(probe, pdir)} containing x. 2) Use the Write tool to create "
           f"work/_permtest.txt containing ok. 3) Use Bash to run: curl --version. Then reply with one line per step: "
           f"ALLOWED or DENIED.")
    p = subprocess.Popen(cmd, cwd=pdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    p.stdin.write(user_msg(msg))
    p.stdin.flush()
    denied_curl = False
    for line in p.stdout:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        s = summarize(ev)
        if s:
            log("   " + s[:200])
        if ev.get("type") == "user" and "curl" in json.dumps(ev) and "denied" in json.dumps(ev).lower():
            denied_curl = True
        if ev.get("type") == "result":
            p.stdin.close()
    p.wait(timeout=300)
    ok_tools = not os.path.exists(probe)
    ok_mine = os.path.exists(mine)
    for f in (probe, mine):
        if os.path.exists(f):
            os.remove(f)
    log(f"[{'PASS' if ok_mine else 'FAIL'}] the director can write in the project folder")
    log(f"[{'PASS' if ok_tools else 'FAIL'}] the director can't write into the studio's tools")
    log(f"[{'PASS' if denied_curl else 'FAIL'}] network commands (curl) are refused")
    return ok_tools and ok_mine and denied_curl
