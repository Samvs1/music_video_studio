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
  The critic (tools/critic.py, a cheaper model with fresh eyes) looks at every new preview in work/previews/ and the
  final while the director works; its notes go to the director as messages, the early-finish nudge carries its notes
  on the final, and the archived version gets its critique (final/critic_vN.md).
"""
import glob, json, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, jload, jsave, song_file, project as load_pj, save_project  # noqa: E402
from mv.claude import claude_bin, claude_print, kill_tree  # noqa: E402

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


def refs_list(pdir, pj):
    """The user's reference pictures that exist (project.json "refs", files in refs/): [(file, name, note)]."""
    out = []
    for r in pj.get("refs") or []:
        f = str(r.get("file") or "").replace("\\", "/")
        if f and os.path.isfile(os.path.join(pdir, f)):
            out.append((f, re.sub(r"\s+", " ", str(r.get("name") or "")).strip(),
                        re.sub(r"\s+", " ", str(r.get("note") or "")).strip()))
    return out


def refs_people(pdir, pj):
    """One line naming who (or what) the reference pictures show, for the story step (which reads text only)."""
    seen = []
    for _, name, note in refs_list(pdir, pj):
        x = name + (f" ({note})" if note else "") if name else note
        if x and x not in seen:
            seen.append(x)
    return f"In my reference pictures (the director will draw them): {'; '.join(seen)}" if seen else ""


def refs_brief(pdir, pj):
    """The brief's bullet on the reference pictures, after the bullet that ends in {{refs}}; "" when there are none."""
    rs = refs_list(pdir, pj)
    if not rs:
        return ""
    return ("\n- I put reference pictures in refs/: the people in the song (or anything else whose look matters to me), "
            "so you know how they look. Look at each one once (the Read tool shows images) and write down what makes "
            "each recognisable in work/cast.md (hair, skin tone, face, glasses, build, clothes, colours as hex; if "
            "work/cast.md exists, add only the pictures it doesn't cover). They say who is in the song and how they look, "
            "not how to draw them: the style is yours, as free as the rest of the video (a few lines, a silhouette, a "
            "colour pair, a puppet, a dancer made of type), as long as I can tell who is who. They are references, not "
            "material: the pictures, or images made from their pixels (filtered, cut out, auto-traced), never appear in "
            "the video.\n" +
            "\n".join(f"  - {f}: {name or '(no name given)'}" + (f" ({note})" if note else "") for f, name, note in rs))


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
        notes = "\n".join(x for x in (user_notes(pj), refs_people(pdir, pj)) if x)
        prompt = fill(open(os.path.join(TEMPLATES, "story-prompt.md"), encoding="utf-8").read(),
                      {"notes": notes or "(no notes given)", "title": title, "date": time.strftime("%Y-%m-%d")})
        log("Claude is reading the song folder (1-3 min)...")
        # one headless print-mode call with the Read tool only (mv/claude.py: the prompt on stdin)
        r = claude_print(prompt, pdir, model=model or (pj.get("director") or {}).get("model"), tools=["Read"], timeout=1200)
        res, text = r["json"], r["text"]
        st.step(1)
        text = re.sub(r"^```(?:markdown)?\s*|\s*```\s*$", "", text.strip())
        if "# Song story" not in text or "## What it's about" not in text:
            raise RuntimeError("Claude didn't return a song story: " + (text or r["stderr"])[-400:])
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


def critic_brief(critic, project, python, tools):
    """The brief's bullet on the studio's critic, after the bullet that ends in {{critic}}; "" when it is off.
    critic = (model, most critiques this run) or None."""
    if not critic:
        return ""
    return (f"\n- The studio's critic: a cheaper model ({critic[0]}) with fresh eyes looks at what you render while you "
            f"work: every full preview you mux into work/previews/preview_N.mp4 and final/music-video.mp4 (up to "
            f"{critic[1]} critiques this run). It sees only the frames, work/concept.md and the song, not your reasoning, "
            f"and its notes arrive as a message from the studio (ambition first, then craft). It runs by itself: keep "
            f"working. You're the director: take what's right, push harder where it says you played safe, ignore what's "
            f"wrong. For a second look at one part: `{python} {tools}/studio.py critique {project} <video> --focus \"...\"` "
            f"(2-4 min).")


def build_brief(pdir, budget_s, seed_notes="", revise=False, started=None, deadline=None, critic=None):
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
            "skill": rel(SKILL, pdir), "anti": rel(os.path.join(os.path.dirname(SKILL), "anti-defaults.md"), pdir),
            "refs": refs_brief(pdir, pj)}
    vals["critic"] = critic_brief(critic, name, vals["python"], tools)
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
    sp = os.path.join(pdir, "work", "director", "settings.json")    # a file, not inline JSON (cmd.exe-safe on Windows)
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    json.dump(settings, open(sp, "w", encoding="utf-8"), indent=1)
    cmd = [*cl, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED, "--disallowedTools", *DISALLOWED,
           "--settings", sp, "--add-dir", os.path.join(STUDIO, "tools"), os.path.join(STUDIO, "assets"),
           os.path.join(STUDIO, ".claude", "skills"), os.path.join(STUDIO, "templates"),
           "--strict-mcp-config", "--append-system-prompt", SYSTEM_APPEND]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


# ------------------------------------------------------------------ the critic during a run
CRITIC_INTRO = ("The studio's critic ({model}, fresh eyes: it saw only the frames, your concept and the song, not your "
                "reasoning) looked at {video}. You're the director: take what's right, push harder where it says you "
                "played safe, ignore what's wrong, and note in work/log.md what you took and why.")


class CriticWatcher:
    """The studio's critic during an autonomous run. Every ~5 s it looks at work/previews/*.mp4 and
    final/music-video.mp4 changed since the run started; a file whose size held still over two looks is ready (a
    .part / .tmp file never is). One critique at a time, in a background thread (critic.py with the venv python);
    when several files are ready, only the newest (the older ones are passed over). At most `most` critiques per run,
    the last one kept for a final. Finished critiques wait in take() for the runner."""

    def __init__(self, pdir, started, model, effort, most=6):
        self.pdir, self.started, self.model, self.effort, self.most = pdir, started, model, effort, most
        self.fin = os.path.join(pdir, "final", "music-video.mp4")
        self.seen, self.handled, self.finished = {}, set(), []
        self.job, self.n, self.last = None, 0, 0.0
        self.lock = threading.Lock()

    @staticmethod
    def key(path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime)

    def poll(self, now):
        if now - self.last < 5:
            return
        self.last = now
        ready = []
        for f in glob.glob(os.path.join(self.pdir, "work", "previews", "*.mp4")) + [self.fin]:
            k = self.key(f)
            if not k or not k[1] or k[2] <= self.started or re.search(r"\.(part|tmp)\b", os.path.basename(f)):
                continue
            was, self.seen[f] = self.seen.get(f), k
            if was == k and k not in self.handled:
                ready.append(k)
        with self.lock:
            if self.job or not ready:
                return
            ready.sort(key=lambda k: k[2])
            self.handled.update(ready)                          # the newest is critiqued; the older ones are passed over
            k = ready[-1]
            if self.n >= self.most or (k[0] != self.key(self.fin)[0] if self.key(self.fin) else True) and self.n >= self.most - 1:
                return                                          # the last critique is kept for a final
            self.n += 1
            self.job = {"key": k, "video": rel(k[0], self.pdir), "proc": None}
            threading.Thread(target=self._run, args=(self.job,), daemon=True).start()

    def _run(self, job):
        import critic
        cmd, env = critic.command(self.pdir, job["video"], self.model, self.effort)
        out, c = "", None
        try:
            job["proc"] = subprocess.Popen(cmd, cwd=STUDIO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                           encoding="utf-8", errors="replace",
                                           creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            out = job["proc"].communicate(timeout=1500)[0]
            c = critic.from_output(self.pdir, out)
        except Exception as ex:
            out += f"\n({ex})"
            if job["proc"]:
                kill_tree(job["proc"])
        tail = [x for x in out.strip().splitlines() if x.strip()][-1:] or ["no output"]
        with self.lock:
            self.finished.append(dict(c, key=job["key"], t=round(time.time() - self.started)) if c else
                                 {"video": job["video"], "error": tail[0][:300]})
            self.job = None

    def take(self):
        """The critiques finished since the last call (an {"video", "error"} for one that couldn't run)."""
        with self.lock:
            out, self.finished = self.finished, []
        return out

    def pending_final(self):
        """A critique of the current final/music-video.mp4 is running or will start (it hasn't been handled yet)."""
        k = self.key(self.fin)
        with self.lock:
            if not k or k[2] <= self.started:
                return False
            if self.job:
                return self.job["key"] == k or (k not in self.handled and self.n < self.most)
            return k not in self.handled and self.n < self.most

    def stop(self, wait_key=None, timeout=300):
        """At the end of the run: a critique of wait_key (the final) still running may finish (it's reused for the
        version); any other is stopped."""
        with self.lock:
            job = self.job
        if not job:
            return
        if wait_key and job["key"] == wait_key:
            t0 = time.time()
            while time.time() - t0 < timeout and self.job:
                time.sleep(1)
        if self.job is job:
            for _ in range(20):
                if job["proc"]:
                    break
                time.sleep(0.5)
            if job["proc"]:
                kill_tree(job["proc"])
            t0 = time.time()
            while time.time() - t0 < 20 and self.job:
                time.sleep(0.5)


def critic_message(c, most=6000):
    rep = (c.get("report") or "").strip()
    if len(rep) > most:
        rep = rep[:most].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
    return CRITIC_INTRO.format(model=c.get("model") or "the critic", video=c["video"]) + \
        f" (Its report is also in {c.get('file')}.)\n\n" + rep


def critic_nudge(c, left):
    import critic
    probs = critic.problems_text(c, 4)
    return (f"You delivered a cut with {fmt_dur(left)} still on the clock: spend it making the video better. "
            + CRITIC_INTRO.format(model=c.get("model") or "the critic", video=c["video"])
            + f"\n\nIts verdict: {c.get('verdict') or '-'} ({critic.score_line(c.get('scores'))})\n"
            + (f"Its worst problems:\n{probs}\n" if probs else (c.get("report") or "")[:3000] + "\n")
            + f"(The whole report: {c.get('file')}.)\n\nThen render, check and deliver again. Keep the current final "
              f"until the new one passes the checks. Then rewrite report.md so it describes the cut you delivered (no "
              f"'Round' heading: those are for the user's review rounds).")


def critique_version(pdir, v, model=None, effort=None, force=False, reuse_only=False, extra=(), log=print):
    """The critic's notes on a delivered version: final/critic_vN.md + .json and a short "critic" entry on the version
    in project.json. Without force, a critique of that exact cut is reused (the runner's, made while the director
    worked); otherwise critic.py looks at it (at final/music-video.mp4 while that is still the same cut: its eyes are
    usually there already; else at final/music-video_vN.mp4). Returns (the critique, True if it was made now) or
    (None, False)."""
    import critic
    fin = os.path.join(pdir, "final", "music-video.mp4")
    same = os.path.exists(fin) and os.path.getsize(fin) == v.get("size") and abs(os.path.getmtime(fin) - (v.get("mtime") or 0)) < 1
    c = None if force else (critic.find(pdir, "final/music-video.mp4", v.get("size"), v.get("mtime"))
                            or critic.find(pdir, v["file"], v.get("size")))
    c = critic.load(pdir, c["file"]) if c else None
    fresh = c is None
    if c is None:
        if reuse_only:
            return None, False
        m0, e0 = critic.settings(load_pj(pdir))
        cmd, env = critic.command(pdir, "final/music-video.mp4" if same else v["file"],
                                  model if model and model != "off" else (m0 if m0 != "off" else critic.DEFAULT_MODEL),
                                  effort or e0)
        cmd += list(extra)
        log(f"the critic is looking at v{v['n']} (2-4 min)...")
        r = subprocess.run(cmd, cwd=STUDIO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=2400)
        c = critic.from_output(pdir, r.stdout)
        if not c:
            log("the critic couldn't run: " + (r.stdout + r.stderr).strip()[-800:])
            return None, False
        log(r.stdout.strip())
    n = v["n"]
    shutil.copyfile(os.path.join(pdir, c["file"]), os.path.join(pdir, "final", f"critic_v{n}.md"))
    jsave(os.path.join(pdir, "final", f"critic_v{n}.json"), dict({k: x for k, x in c.items() if k != "report"}, version=n))
    pj = load_pj(pdir)
    for x in pj.get("versions") or []:
        if x.get("n") == n:
            x["critic"] = {"file": f"final/critic_v{n}.md", "scores": c.get("scores"), "verdict": c.get("verdict"),
                           "model": c.get("model"), "cost_usd": c.get("cost_usd"), "at": c.get("at"), "from": c.get("file")}
    save_project(pj, pdir)
    log(f"v{n}: {critic.result_line(dict(c, file=f'final/critic_v{n}.md'))}")
    return c, fresh


def direct(pdir, budget="90m", effort="high", model=None, seed_notes="", revise=False, nudge=True, dry_run=False,
           critic_model=None, critic_effort=None, critic_max=6, log=print):
    from studio import Status
    import critic as crit
    if not os.path.exists(os.path.join(pdir, "song", "song-map.json")):
        raise SystemExit("no song folder yet: run ingest first")
    budget_s = parse_budget(budget)
    pj = load_pj(pdir)
    model = model or (pj.get("director") or {}).get("model") or "opus"
    cm, ce = crit.settings(pj)
    critic_model, critic_effort = critic_model or cm, critic_effort or ce
    use_critic = critic_model != "off" and (critic_max or 0) > 0
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
    text = build_brief(pdir, budget_s, seed_notes, revise, started, deadline,
                       critic=(critic_model, critic_max) if use_critic else None)
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
              "claude_version": cver, "events": [], "turns": 0, "cost_usd": 0.0, "messages_sent": [], "pid": None,
              "critic_model": critic_model if use_critic else "off", "critic_effort": critic_effort if use_critic else None,
              "critiques": [], "critic_cost_usd": 0.0}
    if dry_run:
        log(text)
        log("\n" + " ".join(cmd[:4]) + " ... (dry run: not started)")
        status["state"] = "dry-run"
        jsave(os.path.join(rdir, "status.json"), status)
        return status
    st = Status(pdir, "direct", ["Claude is directing", "archiving the cut and checking it"] +
                (["the critic looks at the cut"] if use_critic else []))

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
                status["last_event"] = time.time()
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
    log(f"director run {n}: {model}, effort {effort}, budget {fmt_dur(budget_s)} (until {time.strftime('%H:%M', time.localtime(deadline))})"
        + (f"; critic {critic_model}" if use_critic else "; no critic"))
    warned = set()
    nudges, reminders = 0, 0
    grace = min(600, max(180, int(0.1 * budget_s)))
    seen_results = 0
    stop_reason = None
    stdin_open = True
    critics = CriticWatcher(pdir, started, critic_model, critic_effort, critic_max) if use_critic else None
    unsent = []                                  # the critic's finished notes the director hasn't had yet
    wait_t0 = None                               # since when the runner waits for the critic's notes on the final

    def record(c):
        with lock:
            if c.get("error"):
                note(f"🔍 the critic couldn't look at {c['video']}: {c['error']}")
                return
            status["critiques"].append({k: c.get(k) for k in ("t", "video", "scores", "verdict", "file", "cost_usd", "model")})
            status["critic_cost_usd"] = round(status["critic_cost_usd"] + (c.get("cost_usd") or 0), 4)
            note(f"🔍 critic on {c['video']}: {crit.score_line(c.get('scores'))}")
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
            if critics:
                critics.poll(now)
                for c in critics.take():
                    record(c)
                    if not c.get("error"):
                        unsent.append(c)
            if len(results) > seen_results and stdin_open:
                have_final = os.path.exists(fin) and os.path.getmtime(fin) > started
                fkey = CriticWatcher.key(fin) if have_final else None
                final_c = next((c for c in unsent if c.get("key") == fkey), None)
                queued = len(status["messages_sent"]) - len(results)      # messages it hasn't answered yet
                if queued > 0 and now - status.get("last_event", now) < 90:
                    pass                                                  # another turn is coming: decide after it
                elif critics and have_final and final_c is None and left > 120 and stop_reason is None and \
                        critics.pending_final() and now - (wait_t0 or now) < 240:
                    if wait_t0 is None:                                   # the next message carries the critic's notes
                        wait_t0 = now
                        with lock:
                            note("… waiting for the critic's notes on the final (up to 4 min)")
                else:
                    wait_t0 = None
                    seen_results = len(results)
                    if left > 60 and not have_final and reminders < 3:
                        reminders += 1
                        send(f"There is no final/music-video.mp4 from this run yet and {fmt_dur(left)} are left. Carry on "
                             f"and deliver it (then the checks and report.md).", "no final yet")
                    elif nudge and have_final and left > max(6 * 60, 0.12 * budget_s) and nudges < 3:
                        nudges += 1
                        if final_c:
                            unsent.remove(final_c)
                            send(critic_nudge(final_c, left), "use the time left, with the critic's notes")
                        else:
                            send(f"You delivered a cut with {fmt_dur(left)} still on the clock. Spend it making the video better: "
                                 f"look at it again with the eyes tools (sheets of each section, the beat sheets of the biggest "
                                 f"sections, the motion plot), pick the weakest parts and improve them, then render, check and "
                                 f"deliver again. Keep the current final until the new one passes the checks. Then rewrite report.md so it "
                                 f"describes the cut you delivered (no 'Round' heading: those are for the user's review rounds).", "use the time left")
                    elif final_c and left > 120:
                        unsent.remove(final_c)
                        send(critic_message(final_c), f"critic: {final_c['video']}")
                    else:
                        stdin_open = False
                        try:
                            proc.stdin.close()
                        except Exception:
                            pass
            for c in list(unsent):                                        # the critic's other notes go in as they come
                unsent.remove(c)
                if stdin_open and left > 120 and stop_reason is None and proc.poll() is None:
                    send(critic_message(c), f"critic: {c['video']}")
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
        user_stop = bool(stop_reason and "user" in stop_reason)
        if critics:                              # a critique of the final still running may finish: it's reused below
            critics.stop(wait_key=None if user_stop or not os.path.exists(fin) else CriticWatcher.key(fin))
            for c in critics.take():
                record(c)
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
        if v and use_critic:                     # the archived version gets the critic's notes (reused when it has them)
            st.step(2)
            try:
                c, fresh = critique_version(pdir, v, critic_model, critic_effort, reuse_only=user_stop, log=log)
                if c:
                    status["version_critique"] = f"final/critic_v{v['n']}.md"
                    status["version_scores"] = c.get("scores")
                    if fresh:
                        record(dict(c, t=round(time.time() - started)))
            except Exception as ex:
                note(f"🔍 the critic couldn't look at v{v['n']}: {ex}")
        append_settings(pdir, status, v)
        with lock:
            save()
        pj = load_pj(pdir)
        pj.setdefault("runs", []).append({k: status.get(k) for k in ("run", "kind", "model", "model_used", "effort", "budget",
                                                                     "wall_s", "cost_usd", "turns", "version", "stop_reason",
                                                                     "claude_version", "critic_model", "critic_cost_usd")}
                                         | {"critiques": len(status["critiques"]),
                                            "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(started))})
        save_project(pj, pdir)
        st.done("done" if v else "error", "" if v else "the run ended without a new final/music-video.mp4")
        log(f"director run {n} ended: {status['stop_reason']}; wall {fmt_dur(wall)}; cost ${status['cost_usd']:.2f}"
            + (f" + critic ${status['critic_cost_usd']:.2f} ({len(status['critiques'])} critiques)" if use_critic else "")
            + f"; version {('v' + str(v['n'])) if v else 'none'}")
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
    if status.get("critic_model") not in (None, "off"):
        cs = status.get("critiques") or []
        lines.append(f"- Critic: {status['critic_model']} (effort `{status.get('critic_effort')}`): {len(cs)} critique"
                     f"{'' if len(cs) == 1 else 's'}, ${status.get('critic_cost_usd', 0):.2f}"
                     + (f"; on this version {status.get('version_scores') and ' · '.join(f'{k.replace(chr(95), chr(32))} {x}' for k, x in status['version_scores'].items())} "
                        f"({status['version_critique']})" if status.get("version_critique") else ""))
    elif "critic_model" in status:
        lines.append("- Critic: off")
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
