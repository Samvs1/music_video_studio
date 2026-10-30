"""The director, run headless: the story step, the autonomous run with a time budget, versions and delivery.

  studio.py story <p>                     Claude drafts song/song-story.md (read-only tools; the text comes back here)
  studio.py direct <p> --budget 90m ...   the autonomous run: the pipeline (templates/plan-brief.md, the planner with
                                          builders and the critic), or solo (templates/director-brief.md, one session)
  studio.py direct <p> --revise ...       the review round: templates/revise-brief.md with the user's notes
  studio.py clock <p>                     time left in the running budget (a builder: its own build time)
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
  The session plumbing is mv/session.py: a session is busy from a message to an idle session until its next result;
  messages sent while it is busy join the running turn (verified on the real CLI), so the studio decides only when a
  turn has ended.

Two modes (--mode auto|pipeline|solo; auto = the pipeline, but solo under 45 min, with --builders 0, for a review
round of a video made without a valid plan, or when the pipeline's stages don't fit before the final must start:
pipeline.stage_times, one rule for this, the planner's brief and the web app's hint):
  solo      the run above: one session (--model, --effort high) makes the whole video; the critic watcher.
  pipeline  the same shape every run (PipelineRun): the planner (--planner-model / --model, --planner-effort max) plans
            and makes the first draft (the hero segment, the lib, the style frames, work/plan.json); gate 1, the critic
            on the language; builders (--builders N at once, --builder-model: the planner's, --builder-effort high) build
            the other segments in parallel, one critic round each; the studio assembles previews (the checks, the
            critic's film report to the planner); the planner revises (itself, or builders again via
            work/pipeline/tasks.json); the final (the planner's call, or the studio's when time is short). Live state in
            work/pipeline/state.json. A review round (--revise) starts at stage 4.
"""
import glob, json, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, jload, jsave, song_file, project as load_pj, save_project  # noqa: E402
from mv.claude import claude_bin, claude_print, kill_tree  # noqa: E402
from mv.session import Session, summarize, user_msg  # noqa: E402,F401

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


def deliver(pdir, run=None, log=print, report=None):
    """Archive final/music-video.mp4 as the next version (unless it already is one), run the checks, record it.
    report: the checks' report.json of this exact file when they just ran on it (the pipeline's final): not run again."""
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
    r = None
    if report is None:
        env = dict(os.environ, MV_PROJECT=pdir, PYTHONIOENCODING="utf-8", MV_IN_VENV="1")
        r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(HERE, "checks.py"), "all", dst], cwd=STUDIO, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    rep = report if report is not None else (jload(os.path.join(pdir, "work", "checks", "report.json"), {}) or {})
    v = {"n": n, "file": f"final/music-video_v{n}.mp4", "at": time.strftime("%Y-%m-%d %H:%M"), "size": size, "mtime": mtime,
         "checks": rep.get("verdicts"), "sync": (rep.get("sync") or {}).get("share"), "run": run}
    pj = load_pj(pdir)
    pj.setdefault("versions", []).append(v)
    pj.setdefault("status", {}).update(stage=f"v{n} delivered", latest=f"v{n}")
    save_project(pj, pdir)
    log(f"delivered v{n}: {dst}  checks: {rep.get('verdicts')}")
    if r is not None and r.returncode:
        log(r.stdout[-1500:])
    return v


# ------------------------------------------------------------------ the clock
def current(pdir):
    return jload(os.path.join(pdir, "work", "director", "current.json"), None)


RUN_FRESH_S = 180                # a runner writes current.json every second: quiet this long, it was cut off


def run_active(pdir):
    """The autonomous run going on in this project (its current.json), or None. A run whose runner stopped writing
    (its console closed, the machine slept or restarted, the process killed) is over, whatever its file says."""
    c = current(pdir)
    if not c or c.get("state") not in ("running", "finishing"):
        return None
    try:
        fresh = time.time() - float(c.get("updated") or 0) < RUN_FRESH_S
    except (TypeError, ValueError):
        fresh = False
    return c if fresh else None


def clock(pdir):
    now = time.time()
    own = os.environ.get("MV_DEADLINE")                  # a builder: its own build time (set for its session)
    if own and own.isdigit():
        left = int(own) - now
        print(f"{time.strftime('%H:%M:%S')}  {os.environ.get('MV_DEADLINE_WHAT') or 'your time'}: "
              f"{'LEFT ' + fmt_dur(left) if left > 0 else 'TIME IS UP: render what you have and end your turn'} "
              f"(until {time.strftime('%H:%M', time.localtime(int(own)))})")
        return
    c = run_active(pdir)
    if not c:
        c = current(pdir)
        print("no autonomous run is going on (no time budget). " + (f"Last run: {c.get('state')}" if c else "")
              + (" (its runner stopped writing: it was cut off)" if c and c.get("state") in ("running", "finishing") else ""))
        return
    left = c["deadline"] - now
    print(f"{time.strftime('%H:%M:%S')}  elapsed {fmt_dur(now - c['started'])} of {fmt_dur(c['budget_s'])}; "
          f"{'LEFT ' + fmt_dur(left) if left > 0 else 'TIME IS UP: finish now'} (deadline {time.strftime('%H:%M', time.localtime(c['deadline']))})")
    if c.get("mode") == "pipeline":
        import pipeline
        st = pipeline.load_state(pdir)
        if st.get("stage"):
            fb = st.get("final_by")
            print(f"pipeline stage: {STAGE_NAMES.get(st['stage'], st['stage'])}"
                  + (f"; the studio starts the final by {time.strftime('%H:%M', time.localtime(fb))} at the latest"
                     if isinstance(fb, (int, float)) and st["stage"] not in ("final", "solo") else ""))


# ------------------------------------------------------------------ the autonomous run
# summarize (the live log's line for an event) and user_msg (one stream-json user message) live in mv/session.py with
# the session plumbing; they are imported above under the same names.


def critic_brief(critic, project, python, tools):
    """The brief's bullet on the studio's critic, after the bullet that ends in {{critic}}; "" when it is off.
    critic = (model, most critiques this run) or None."""
    if not critic:
        return ""
    return (f"\n- The studio's critic: a cheaper model ({critic[0]}) with fresh eyes looks at what you render while you "
            f"work: every full preview you mux into work/previews/preview_N.mp4 and final/music-video.mp4 (up to "
            f"{critic[1]} critiques this run). It sees only the frames, work/concept.md and the song, not your reasoning, "
            f"and its notes arrive as a message from the studio a few minutes later (ambition first, then craft). It does "
            f"the looking so your own tokens go into the video: after you mux a full preview, don't run eyes.py look on it "
            f"or open its sheets yourself; carry on with the next thing until its notes arrive, then open only the "
            f"pictures it names that you need to judge yourself (one eyes.py strip or beats for one specific question is "
            f"fine). You're the director: take what's right, push harder where it says you played safe, ignore what's "
            f"wrong. For a second look at one part: `{python} {tools}/studio.py critique {project} <video> --focus \"...\"` "
            f"(2-4 min).")


def build_brief(pdir, budget_s, seed_notes="", revise=False, started=None, deadline=None, critic=None, pipeline=""):
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
        vals.update(version=vn, notes="\n".join(rows), round=vn + 1, pipeline=pipeline,
                    direction=("\nMy notes on the song (as before):\n" + notes) if notes else "")
        fmt_ = f"{d['width']}×{d['height']}, {d['fps']} fps, H.264 yuv420p with AAC audio, the same length as the song file"
        if pipeline:                           # the studio renders, checks and delivers the final (the note below)
            vals.update(deliver=f"The studio renders the next final/music-video.mp4 ({fmt_}) from work/plan.json and runs "
                                f"the checks on it: don't render or assemble the final yourself.",
                        time_note="The studio starts the final by itself when the time left is what it needs.",
                        finish="When the final is there, the studio sends you its checks and the critic's notes: fix any FAIL.",
                        when_done="")
        else:
            vals.update(deliver=f"Deliver final/music-video.mp4 again, {fmt_}.",
                        time_note="Leave enough time for the final render and the checks.",
                        finish="Before you finish, check that the file plays to the end and that the visuals are in sync "
                               "with the song.",
                        when_done=f" When done: `{vals['python']} {tools}/studio.py check {name}` must pass.")
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


def claude_args(pdir, model, effort, deny=(), settings=None, system=SYSTEM_APPEND):
    """The stream-json session's command with the director's sandbox. A builder adds its own deny rules (the
    project's files that aren't its own: pipeline.builder_deny) and keeps its settings file next to its session."""
    cl = claude_bin()
    if not cl:
        raise SystemExit("Claude Code isn't installed: https://claude.com/claude-code (or set MV_CLAUDE)")
    more = list(deny)
    deny = [f"Bash({c}:*)" for c in DENY_BASH]
    for d in ("tools", "assets", ".claude", "templates", "app"):             # the studio itself is read-only to the director
        rp = rule_path(os.path.join(STUDIO, d))
        deny += [f"Edit({rp}/**)", f"Write({rp}/**)"]
    settings_d = {"permissions": {"deny": deny + more + ["WebFetch", "WebSearch"]}}
    sp = settings or os.path.join(pdir, "work", "director", "settings.json")    # a file, not inline JSON (cmd.exe-safe on Windows)
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(settings_d, f, indent=1)
    cmd = [*cl, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED, "--disallowedTools", *DISALLOWED,
           "--settings", sp, "--add-dir", os.path.join(STUDIO, "tools"), os.path.join(STUDIO, "assets"),
           os.path.join(STUDIO, ".claude", "skills"), os.path.join(STUDIO, "templates"),
           "--strict-mcp-config", "--append-system-prompt", system]
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
    .part / .tmp file never is). One critique at a time, in a background thread (critic.py with the venv python).
    A ready final goes first (it's the cut that gets delivered, and the early-finish nudge waits for its notes): a
    preview critique still running when a new final is ready is stopped for it (its slot is given back). Of several
    ready previews only the newest; none older than a ready final. At most `most` critiques per run, the last one kept
    for a final; no preview critique when its notes couldn't reach the director in time (`min_left`), and nothing new
    once the run is stopping. Finished critiques wait in take() for the runner."""

    def __init__(self, pdir, started, model, effort, most=6, min_left=6 * 60):
        self.pdir, self.started, self.model, self.effort, self.most = pdir, started, model, effort, most
        self.min_left = min_left
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

    def is_final(self, k):
        return k[0] == os.path.normcase(os.path.abspath(self.fin))

    def poll(self, now, left=None, stopping=False):
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
        if stopping or not ready:
            return
        ready.sort(key=lambda k: k[2])
        fins = [k for k in ready if self.is_final(k)]
        prevs = [k for k in ready if not self.is_final(k)]
        with self.lock:
            job = self.job
            if job and fins and not self.is_final(job["key"]) and not job.get("superseded"):
                job["superseded"] = True            # the director delivered: the final's notes matter more now
                self.n -= 1
                proc = job.get("proc")
            elif job:
                return
            else:
                proc = None
        if proc:
            kill_tree(proc)
        with self.lock:
            if self.job:
                return                              # the stopped one ends in its thread; the final starts next look
            cut, newest = (fins[-1][2] if fins else 0), prevs[-1:]
            self.handled.update(prevs[:-1])                         # older previews are passed over: the newest counts
            self.handled.update(k for k in newest if k[2] <= cut)   # and none older than a ready final
            prevs = [k for k in newest if k[2] > cut]
            if prevs and (self.n >= self.most - 1 or (left is not None and left < self.min_left)):
                self.handled.update(prevs)          # the last critique is kept for a final; too late to reach it
                prevs = []
            if fins and self.n >= self.most:
                self.handled.update(fins)
                fins = []
            k = fins[-1] if fins else prevs[-1] if prevs else None
            if not k:
                return
            self.handled.add(k)
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
            if job.get("superseded"):
                kill_tree(job["proc"])
            out = job["proc"].communicate(timeout=1500)[0]
            c = critic.from_output(self.pdir, out)
        except Exception as ex:
            out += f"\n({ex})"
            if job["proc"]:
                kill_tree(job["proc"])
        tail = [x for x in out.strip().splitlines() if x.strip()][-1:] or ["no output"]
        with self.lock:
            if not job.get("superseded"):           # a stopped preview critique (a new final came) is dropped
                self.finished.append(dict(c, key=job["key"], t=round(time.time() - self.started)) if c else
                                     {"video": job["video"], "error": tail[0][:300], "cost_usd": critic.spent(out)})
            if self.job is job:
                self.job = None

    def take(self):
        """The critiques finished since the last call (an {"video", "error", "cost_usd"} for one that couldn't run)."""
        with self.lock:
            out, self.finished = self.finished, []
        return out

    def pending_final(self):
        """A critique of the current final/music-video.mp4 is running or will start (it hasn't been handled yet)."""
        k = self.key(self.fin)
        with self.lock:
            if not k or k[2] <= self.started:
                return False
            if self.job and self.job["key"] == k:
                return True
            return k not in self.handled and self.n < self.most

    def stop(self, wait_key=None, timeout=900, abort=None):
        """At the end of the run: a critique of wait_key (the final) still running may finish (it's reused for the
        version), unless abort() says the user stopped; any other is stopped (with what it started)."""
        with self.lock:
            job = self.job
        if not job:
            return
        if wait_key and job["key"] == wait_key:
            t0 = time.time()
            while time.time() - t0 < timeout and self.job is job and not (abort and abort()):
                time.sleep(1)
        if self.job is job:
            for _ in range(20):
                if job["proc"]:
                    break
                time.sleep(0.5)
            if job["proc"]:
                kill_tree(job["proc"])
            t0 = time.time()
            while time.time() - t0 < 20 and self.job is job:
                time.sleep(0.5)


def critic_pictures(c, most=12):
    """The pictures the critic looked at, for the director to open the few it needs (they're already made)."""
    ps = c.get("pictures") or []
    return (f"The pictures it looked at (made already: open only the ones you need to judge yourself): "
            + ", ".join(ps[:most]) + (f" (+{len(ps) - most} more)" if len(ps) > most else "") + ".") if ps else ""


def critic_message(c, most=6000):
    rep = (c.get("report") or "").strip()
    if len(rep) > most:
        rep = rep[:most].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
    pics = critic_pictures(c)
    return CRITIC_INTRO.format(model=c.get("model") or "the critic", video=c["video"]) + \
        f" (Its report is also in {c.get('file')}.)" + (f"\n{pics}" if pics else "") + "\n\n" + rep


NUDGE_HEAD = ("You delivered a cut with {left} still on the clock: spend it making the video better, and bolder. Start "
              "where the critic says you played safe and with what it says the boldest version of your concept would do, "
              "not with polish; then its worst problems. ")
NUDGE_TAIL = ("\n\nThen render, check and deliver again. Keep the current final until the new one passes the checks. Then "
              "rewrite report.md so it describes the cut you delivered (no 'Round' heading: those are for the user's "
              "review rounds).")


def critic_nudge(c, left, seen=False):
    """The early-finish nudge with the critic's notes on the final: its whole report (the Ambition section first, as
    the critic wrote it), or, when the director has had the report already (a message, or it ran the critic itself),
    the gist again: the ambition push, where it plays safe, the worst problems."""
    import critic
    head = NUDGE_HEAD.format(left=fmt_dur(left))
    if not seen:
        return head + "\n\n" + critic_message(c) + NUDGE_TAIL
    amb = re.sub(r"^## [^\n]*\n", "", critic.section(c.get("report") or "", "Ambition")).strip()
    safe = "\n".join(f"- {x}" for x in (c.get("safe") or [])[:5])
    probs = critic.problems_text(c, 4)
    return (head + f"You had the critic's notes on this cut ({c.get('file')}); the gist again:\n\n"
            f"Its verdict: {c.get('verdict') or '-'} ({critic.score_line(c.get('scores'))})\n"
            + (f"\nAmbition: {amb[:1500]}\n" if amb else "") + (f"\nWhere it plays safe:\n{safe}\n" if safe else "")
            + (f"\nIts worst problems:\n{probs}\n" if probs else "") + NUDGE_TAIL)


def plain_nudge(left, pdir, critic_on=False, coming=False):
    """The early-finish nudge without the critic's notes on this cut. With the critic on, the director doesn't do
    the full look itself: the critic's notes are on their way, or it asks the critic."""
    if not critic_on:
        return (f"You delivered a cut with {fmt_dur(left)} still on the clock. Spend it making the video better: look at it "
                f"again with the eyes tools (sheets of each section, the beat sheets of the biggest sections, the motion "
                f"plot), pick the weakest parts and improve them, then render, check and deliver again. Keep the current "
                f"final until the new one passes the checks. Then rewrite report.md so it describes the cut you delivered "
                f"(no 'Round' heading: those are for the user's review rounds).")
    tools = rel(os.path.join(STUDIO, "tools"), pdir)
    look = ("The critic's notes on this cut are on their way (they come as a message): start on what you already know is "
            "weakest meanwhile." if coming else
            f"For fresh eyes on it, ask the critic (a cheaper model looks at the pictures and reports back) rather than "
            f"opening every sheet yourself: `{venv_python(pdir)} {tools}/studio.py critique {os.path.basename(pdir)} "
            f"final/music-video.mp4 --focus \"...\"` (2-4 min).")
    return (f"You delivered a cut with {fmt_dur(left)} still on the clock. Spend it making the video better, and bolder: "
            f"where does it play safe, which ★★★ moment doesn't change the picture's rules, what would the boldest "
            f"version of your concept do? {look}" + NUDGE_TAIL)


def run_streamed(cmd, env, log, timeout=2400, abort=None):
    """A studio tool with each line of its output passed to log as it comes (the web app's job log shows it live),
    stopped with what it started at the timeout or when abort() says so. Returns (exit code, None when it was
    stopped; the output)."""
    p = subprocess.Popen(cmd, cwd=STUDIO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    t0, stopped, out = time.time(), [], []

    def guard():
        while p.poll() is None:
            if time.time() - t0 > timeout or (abort and abort()):
                stopped.append(1)
                kill_tree(p)
                return
            time.sleep(1)
    threading.Thread(target=guard, daemon=True).start()
    try:
        for line in p.stdout:
            out.append(line)
            log(line.rstrip())
        p.wait()
    except BaseException:                    # Ctrl+C: the critic doesn't outlive the one who asked for it
        kill_tree(p)
        raise
    return (None if stopped else p.returncode), "".join(out)


def critique_version(pdir, v, model=None, effort=None, force=False, reuse_only=False, extra=(), log=print, abort=None):
    """The critic's notes on a delivered version: final/critic_vN.md + .json and a short "critic" entry on the version
    in project.json. Without force, a critique of that exact cut is reused (the runner's, made while the director
    worked); otherwise critic.py looks at it (at final/music-video.mp4 while that is still the same cut: its eyes are
    usually there already; else at final/music-video_vN.mp4), its output shown as it comes; abort() stops it.
    Returns (the critique, True if it was made now) or (None, False)."""
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
        rc, out = run_streamed(cmd, env, log, timeout=2400, abort=abort)
        c = critic.from_output(pdir, out) if rc == 0 else None
        if not c:
            log("the critic couldn't run" + (" (stopped)" if rc is None else ": see above"))
            return None, False
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


def logged_critiques(pdir, since):
    """The critic's log lines (work/critic/log.jsonl) written since a time: every critic.py run, the director's own
    ones and the ones that couldn't answer included (those with their cost)."""
    t0 = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    out = []
    try:
        for ln in open(os.path.join(pdir, "work", "critic", "log.jsonl"), encoding="utf-8").read().splitlines():
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if isinstance(d, dict) and str(d.get("at") or "") >= t0:
                out.append(d)
    except OSError:
        pass
    return out


HUNG_S = 25 * 60                 # busy with no events this long: said in the live log (a long command makes none either)
STAGE_NAMES = {"plan": "1. plan", "gate": "gate 1: the language", "build": "2. build", "assemble": "3. assemble",
               "revise": "4. revise", "final": "5. final", "solo": "solo (the rest of the run)", "done": "done"}
STOP_MSG = ("The user stopped this run. Stop now: don't start anything new. If final/music-video.mp4 exists, write "
            "report.md in a few lines and end.")


class Runner:
    """One autonomous run around one Claude Code session (mv/session.py): work/director/run-N/ (brief.md,
    command.json, events.jsonl, stderr.log, status.json; current.json for `clock` and `stop`), the time warnings,
    STOP, the deadline + grace, and the end: the cut archived as the next version, the checks, report.md's run
    settings, the critic's notes on the version. As it stands this is solo mode, the director run as it always was:
    one session makes the whole video while the critic watcher looks at its previews and its final. PipelineRun runs
    the pipeline's stages around the planner instead (and falls back to this when the plan never validates)."""

    def __init__(self, pdir, n, rdir, status, session, budget_s, started, nudge=True, critic=None, st=None, log=print):
        self.pdir, self.n, self.rdir, self.status, self.s = pdir, n, rdir, status, session
        self.ddir = os.path.join(pdir, "work", "director")
        self.fin = os.path.join(pdir, "final", "music-video.mp4")
        self.budget_s, self.started, self.deadline = budget_s, started, started + budget_s
        self.nudge, self.st, self.log = nudge, st, log
        self.lock = session.lock
        self.use_critic = bool(critic)
        self.critic_model = critic["model"] if critic else "off"
        self.critic_effort = critic.get("effort") if critic else None
        self.critic_max = critic.get("max", 6) if critic else 0
        self.critics = CriticWatcher(pdir, started, self.critic_model, self.critic_effort, self.critic_max) if critic else None
        self.warned = set()
        self.nudges = self.reminders = 0
        self.grace = min(600, max(180, int(0.1 * budget_s)))
        self.stop_reason = None
        self.unsent = []                         # the critic's finished notes the director hasn't had yet
        self.wait_t0 = None                      # since when the runner waits for the critic's notes on the final
        self.hung_said = False
        self.mode = "solo"
        self.end_reason = "the director ended the session"

    # ------------------------------------------------------------------ bookkeeping
    def save(self):
        status = self.status
        status["updated"] = time.time()
        status["elapsed_s"] = round(time.time() - self.started)
        status["final_exists"] = os.path.exists(self.fin)
        jsave(os.path.join(self.rdir, "status.json"), status)
        jsave(os.path.join(self.ddir, "current.json"), {k: v for k, v in status.items() if k != "events"} | {"dir": rel(self.rdir, self.pdir)})

    def note(self, line):
        self.s.note(line)

    def send(self, text, why):
        return self.s.send(text, why)

    def record(self, c, used="waiting"):
        """A finished critique into status.json (with how it reached the director: `used`); one that couldn't run
        only into the live log, its cost (a cap hit, a refused call) into the critic's cost all the same."""
        import critic as crit
        with self.lock:
            self.status["critic_cost_usd"] = round(self.status["critic_cost_usd"] + (c.get("cost_usd") or 0), 4)
            if c.get("error"):
                self.status["critic_failed"] = self.status.get("critic_failed", 0) + 1
                self.note(f"🔍 the critic couldn't look at {c['video']}: {c['error']}")
                return
            self.status["critiques"].append({k: c.get(k) for k in ("t", "video", "scores", "verdict", "file", "cost_usd", "model")}
                                            | {"used": used})
            self.note(f"🔍 critic on {c['video']}: {crit.score_line(c.get('scores'))}")

    def used(self, c, how):
        with self.lock:
            for x in self.status["critiques"]:
                if x.get("file") == c.get("file"):
                    x["used"] = how

    # ------------------------------------------------------------------ the loop
    def loop(self):
        while True:
            time.sleep(1)
            now = time.time()
            try:
                with self.lock:
                    self.save()
            except Exception as ex:              # e.g. Windows: the web app had the file open just then
                self.trouble("saving the run's status", ex)
            try:
                if not self.step(now):
                    break
            except Exception as ex:              # a bug or a file in the way: noted, and the run goes on
                self.trouble("a step of the run", ex)
                if now > self.deadline + self.grace:
                    self.stop_reason = self.stop_reason or "budget ran out (deadline + grace)"
                    break

    def trouble(self, what, ex):
        """An error in the runner's own loop: into work/director/run-N/runner_errors.log, and once a minute into
        the live log. The run carries on."""
        import traceback
        try:
            with open(os.path.join(self.rdir, "runner_errors.log"), "a", encoding="utf-8") as f:
                f.write(f"\n{time.strftime('%H:%M:%S')} {what}:\n{traceback.format_exc()}")
        except OSError:
            pass
        if time.time() - getattr(self, "t_trouble", 0) > 60:
            self.t_trouble = time.time()
            self.note(f"⚠ the studio hit an error in {what}: {type(ex).__name__}: {ex} (work/director/run-{self.n}/runner_errors.log); "
                      f"it carries on")

    def step(self, now):
        return self.solo_step(now)

    def check_stop(self, now):
        if self.stop_reason is None and os.path.exists(os.path.join(self.ddir, "STOP")):
            self.stop_reason = "stopped by the user"
            self.send(STOP_MSG, "stop")
            self.deadline = min(self.deadline, now)
            self.grace = 60
            self.on_stop(now)

    def on_stop(self, now):
        pass

    def check_hung(self, now):
        if not self.hung_said and self.s.hung(now, HUNG_S):
            self.hung_said = True
            self.note(f"⚠ no sign of life from the {self.s.name} for {HUNG_S // 60} min (a long command, or a hang: the "
                      f"deadline ends it either way)")
        elif self.hung_said and not self.s.busy:
            self.hung_said = False

    def solo_warnings(self, now, left):
        for frac, key, msg in ((0.5, "half", "Half of your time is gone ({left} left, deadline {dl}). Check your plan against the clock."),):
            if key not in self.warned and now - self.started >= frac * self.budget_s and left > 0:
                self.warned.add(key)
                self.send(msg.format(left=fmt_dur(left), dl=time.strftime("%H:%M", time.localtime(self.deadline))), key)
        final_at = max(10 * 60, 0.25 * self.budget_s)
        if "final" not in self.warned and left <= final_at and left > 5 * 60:
            self.warned.add("final")
            self.send(f"{fmt_dur(left)} left. If the full-size final isn't rendering yet, start it now (it takes the time "
                      f"tools/speeds.json says), then compose, mux, run the checks and write report.md.", "final-render time")
        if "five" not in self.warned and 0 < left <= 5 * 60:
            self.warned.add("five")
            self.send("5 minutes left: finish the final, run the checks, write report.md. Nothing new.", "5 minutes")
        self.time_up(left)

    def time_up(self, left):
        if "up" not in self.warned and left <= 0:
            self.warned.add("up")
            self.status["state"] = "finishing"
            self.send(f"Time is up. Stop working on the video now. If final/music-video.mp4 exists, make sure report.md is "
                      f"written (a few lines are fine) and end. You have {fmt_dur(self.grace)} before the session is closed.", "time's up")

    def solo_step(self, now):
        """One second of a solo run: the director makes the whole video; the studio warns, reminds, nudges, and
        passes on the critic's notes. Decisions wait for the director's turn to end (mv/session.py: a message sent
        while it works joins the running turn)."""
        import critic as crit
        left = self.deadline - now
        self.check_stop(now)
        self.solo_warnings(now, left)
        if left <= -self.grace:
            self.stop_reason = self.stop_reason or "budget ran out (deadline + grace)"
            return False
        self.check_hung(now)
        s, critics = self.s, self.critics
        if critics:
            critics.poll(now, left, stopping=self.stop_reason is not None)
            for c in critics.take():
                self.record(c)
                if not c.get("error"):
                    self.unsent.append(c)
        if s.turn_over(now) and not s.closed:
            have_final = os.path.exists(self.fin) and os.path.getmtime(self.fin) > self.started
            fkey = CriticWatcher.key(self.fin) if have_final else None
            final_c = next((c for c in self.unsent if c.get("key") == fkey), None)
            if critics and have_final and final_c is None and left > 120 and self.stop_reason is None and \
                    critics.pending_final() and now - (self.wait_t0 or now) < 240:
                if self.wait_t0 is None:                          # the next message carries the critic's notes
                    self.wait_t0 = now
                    self.note("… waiting for the critic's notes on the final (up to 4 min)")
            else:
                self.wait_t0 = None
                s.ack()
                if left > 60 and not have_final and self.reminders < 3:
                    self.reminders += 1
                    self.send(f"There is no final/music-video.mp4 from this run yet and {fmt_dur(left)} are left. Carry on "
                              f"and deliver it (then the checks and report.md).", "no final yet")
                elif self.nudge and have_final and left > max(6 * 60, 0.12 * self.budget_s) and self.nudges < 3:
                    self.nudges += 1
                    had = crit.find(self.pdir, "final/music-video.mp4", fkey[1], fkey[2]) if critics and not final_c else None
                    had = crit.load(self.pdir, had["file"]) if had else None
                    if final_c:
                        self.unsent.remove(final_c)
                        self.send(critic_nudge(final_c, left), "use the time left, with the critic's notes")
                        self.used(final_c, "in the nudge")
                    elif had:                                     # it had them already: the gist again
                        self.send(critic_nudge(had, left, seen=True), "use the time left, with the critic's notes")
                    else:
                        self.send(plain_nudge(left, self.pdir, bool(critics), bool(critics) and critics.pending_final()),
                                  "use the time left")
                elif final_c and left > 120:
                    self.unsent.remove(final_c)
                    self.send(critic_message(final_c), f"critic: {final_c['video']}")
                    self.used(final_c, "sent")
                else:
                    s.close_input()
        for c in list(self.unsent):                                # the critic's other notes go in as they come
            self.unsent.remove(c)
            if not s.closed and left > 120 and self.stop_reason is None and s.alive():
                self.send(critic_message(c), f"critic: {c['video']}")
                self.used(c, "sent")
            else:
                self.used(c, "too late for the director")
        return s.alive()

    # ------------------------------------------------------------------ the end
    def after_session(self):
        """What else has to stop with the session (the pipeline: its builders, a running assemble)."""

    def end(self):
        s, status = self.s, self.status
        if s.alive():
            s.stop(self.stop_reason or "the run ended")
        self.after_session()
        user_stop = bool(self.stop_reason and "user" in self.stop_reason)
        s.finish()
        wall = time.time() - self.started
        status["wall_s"] = round(wall)
        status["state"] = "stopped" if user_stop else "done"
        status["stop_reason"] = self.stop_reason or self.end_reason
        status["exit_code"] = s.returncode
        if self.st:
            self.st.step(1)
        v = None
        if os.path.exists(self.fin) and os.path.getmtime(self.fin) > self.started:
            v = deliver(self.pdir, run=self.n, log=self.log)
        else:
            self.note("no new final/music-video.mp4 from this run")
        status["version"] = v["n"] if v else None
        status["result"] = "delivered" if v else "no final"
        if not v and not user_stop:
            status["stop_reason"] = f"{status['stop_reason']}, without a new final/music-video.mp4"
        self.keep_record(v)
        if self.use_critic:                      # then the critic's last look (a Stop here loses only its notes)
            try:
                self.critic_after(v, user_stop)
            except BaseException:                # Ctrl+C: the critic's job doesn't outlive the run
                if self.critics:
                    self.critics.stop()
                raise
            self.keep_record(v)
        self.ended(v)
        if self.st:
            self.st.done("done" if v else "error", "" if v else "the run ended without a new final/music-video.mp4")
        cost = status["cost_usd"] + float(status.get("builders_cost_usd") or 0)
        self.log(f"director run {self.n} ended: {status['stop_reason']}; wall {fmt_dur(wall)}; cost ${cost:.2f}"
                 + (f" + critic ${status['critic_cost_usd']:.2f} ({len(status['critiques'])} critiques)" if self.use_critic else "")
                 + f"; version {('v' + str(v['n'])) if v else 'none'}")
        return status

    def ended(self, v):
        """After the record is kept (the pipeline: its state.json says done)."""

    RUN_KEYS = ("run", "kind", "model", "model_used", "effort", "budget", "wall_s", "cost_usd", "turns", "version",
                "result", "stop_reason", "claude_version", "critic_model", "critic_cost_usd")
    MORE_KEYS = ("mode", "builders", "builder_model", "builder_effort", "builders_cost_usd", "costs")

    def keep_record(self, v):
        """report.md's run settings, status.json, current.json, project.json runs."""
        status = self.status
        append_settings(self.pdir, status, v)
        with self.lock:
            self.save()
        pj = load_pj(self.pdir)
        entry = {k: status.get(k) for k in self.RUN_KEYS} | {k: status[k] for k in self.MORE_KEYS if k in status} | {
            "critiques": len(status["critiques"]), "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.started))}
        runs = pj.setdefault("runs", [])
        if runs and runs[-1].get("run") == self.n and runs[-1].get("at") == entry["at"]:
            runs[-1] = entry
        else:
            runs.append(entry)
        save_project(pj, self.pdir)

    def critic_after(self, v, user_stop):
        """A critique of the final still running may finish (it's reused for the version); the archived version gets
        the critic's notes; the critic's log adds what the director asked of it itself, and every cost."""
        status, pdir = self.status, self.pdir
        stopping = lambda: os.path.exists(os.path.join(self.ddir, "STOP"))      # noqa: E731
        if v and self.st:
            self.st.step(2)
        if self.critics:
            self.critics.stop(wait_key=None if user_stop or not os.path.exists(self.fin) else CriticWatcher.key(self.fin),
                              abort=stopping)
            for c in self.critics.take():
                self.record(c, "after the run")
        if v:
            try:
                c, fresh = critique_version(pdir, v, self.critic_model, self.critic_effort, reuse_only=user_stop or stopping(),
                                            log=self.log, abort=stopping)
                if c:
                    status["version_critique"] = f"final/critic_v{v['n']}.md"
                    status["version_scores"] = c.get("scores")
                    if fresh:
                        self.record(dict(c, t=round(time.time() - self.started)), "after the run")
            except Exception as ex:
                self.note(f"🔍 the critic couldn't look at v{v['n']}: {ex}")
        if stopping() and not user_stop:
            self.note("🔍 the critic's last look was stopped by the user")
        logged = logged_critiques(pdir, self.started)
        known = {x.get("file") for x in status["critiques"]}
        for d in logged:
            if d.get("file") and d["file"] not in known:
                try:
                    t = round(time.mktime(time.strptime(d["at"], "%Y-%m-%d %H:%M:%S")) - self.started)
                except Exception:
                    t = None
                status["critiques"].append({k: d.get(k) for k in ("video", "scores", "verdict", "file", "cost_usd", "model")}
                                           | {"t": t, "used": "the director ran it" if self.mode == "solo" else "a session ran it"})
        status["critic_cost_usd"] = round(max(status["critic_cost_usd"], sum(d.get("cost_usd") or 0 for d in logged)), 4)


# ------------------------------------------------------------------ the pipeline's stages (studio.py direct, pipeline mode)
CRITIC_MAX_USD = 2.0
LANG_REVISE_S = 10 * 60          # the planner's last revision of the language after gate 1, at most
BUILD_MIN_S = 10 * 60            # a builder round needs at least this long
JOB_LINE = re.compile(r"^\s*(render \(|render:|assemble \(|gate|critic:|the critic|checks:|   checks|   eyes|   NOTE|"
                      r"  [a-z0-9_-]+ +\d+:\d\d\.\d\d)")

PLAN_DUE = ("The plan stage ends now ({left} left; the final starts by about {final_by}). Finish work/plan.json (\"stage\": "
            "\"planned\"), the lib, the style frames and the hero's preview, then end your turn: five minutes from now the "
            "studio gates the plan as it is, and without a valid plan.json the rest of the run is solo.")
PLAN_MISSING = ("There is no work/plan.json yet ({left} left; the plan stage ends at {plan_by}). Write it (the format is in "
                "your brief), check it (`{validate}`), render the hero (`{render_hero}`), then end your turn when it says "
                "\"stage\": \"planned\". Reminder {k} of 3: after that the studio switches this run to solo.")
PLAN_INVALID = ("work/plan.json isn't valid yet (try {k} of 3: after the third the rest of the run is solo). Fix what it "
                "says, check it again (`{validate}`) and end your turn.\n\n{text}")
PLAN_PLANNING = ("work/plan.json is valid but still says \"stage\": \"planning\". When the plan, the lib, the style frames and "
                 "the hero's preview are ready, set it to \"planned\" and end your turn: that starts gate 1. ({left} left; "
                 "the plan stage ends at {plan_by}.)")
PLAN_NOT_READY = ("work/plan.json validates, but it isn't ready for the gate (try {k} of 3: after the third the rest of the run is "
                  "solo): the builders start from it, so it needs what a \"planned\" plan has. Set \"stage\": \"planned\", fix "
                  "what it says, check it (`{validate}`) and end your turn.\n\n{text}")
HERO_FAILED = ("Gate 1 couldn't look at the language: {error}. Fix the hero (`{render_hero}` shows the error; the log is in "
               "work/segments/{hero}/render_preview.log) and end your turn (try {k} of 3).")
GATE_MSG = (
    "Gate 1, the language. work/plan.json is valid ({nseg} segments, hero {hero}) and the studio's critic ({model}, fresh "
    "eyes: it saw only the hero's frames, the style frames, your concept and the song, not your reasoning) looked at "
    "{video}.\nThis is the last cheap moment to change the language. When you end this turn the lib, the style frames "
    "and the rules are locked, and {next}. Where the critic is right, revise now: push harder where it says you played "
    "safe (never retreat to something safer), in the lib, the style frames, the hero (re-render: `{render_hero}`) and "
    "the builders' briefs in plan.json. Ignore what's wrong. If the language is right as it is, end your turn now. Note "
    "in work/log.md what you took and why.\nTime left: {left} (until {deadline}). The language locks by {lock_by} at the "
    "latest; the final starts by about {final_by} (it takes about {est} here, from {how}).\n\n{report}")
BUILD_MSG = (
    "{head}The language is locked. {builders}\nNow build your own segments ({mine}) and the overlays ({overlays}): render "
    "each with `{render}`, set its status to \"drafted\" in plan.json, and end your turn when they're done. Don't change "
    "what the lib's existing code does (the builders build on it; adding to it is fine), and keep the joins the briefs "
    "promise (enter / exit). When you and the builders are done, the studio assembles a preview of the whole film and "
    "the critic looks at it.\nTime left: {left} (until {deadline}); the final starts by about {final_by} (it takes about "
    "{est} here).")
BUILDER_FAILED = ("Builder {id} {what}: {why}. {stands}You can take it over: set its owner to planner in plan.json and build "
                  "work/scenes/{id}/ yourself (render: `{render}`), or give its time to a neighbour. A preview shows a slate "
                  "where a segment is missing; the final needs every segment (as a last resort the studio stands a "
                  "neighbour's scene in).")
ASSEMBLE_FAILED = (
    "The studio couldn't assemble the preview (round {k}): {error}\nThe log is work/pipeline/assemble.log; each "
    "segment's render log is work/segments/<id>/render_preview.log.\n\nThe segments:\n{segments}\n{requests}{notes}\n"
    "Fix what it says (repair it, don't retreat to something safer; re-render a segment: `{render}`), then end your turn: "
    "the studio assembles again and the critic looks. Time left: {left} (until {deadline}); the final starts by {final_by} at the latest (it takes about {est} here).")
ASSEMBLE_MSG = (
    "The studio assembled a preview of the whole film (round {k}): {video} (half size){slates}. {checks}\nTime left: {left} (until "
    "{deadline}). The final render takes about {est} here ({how}); the studio starts it by {final_by} at the latest.\n\n"
    "The segments:\n{segments}\n{requests}{notes}\nNow it's stage 4 (revise, round {k}), and you decide:\n"
    "- fix what you want yourself (re-render a segment: `{render}`), and/or\n- {tasks}\n"
    "- then end your turn: the studio assembles again and the critic looks again.\n"
    "- Or, when the film is ready, set \"stage\": \"final\" in work/plan.json and end your turn: the studio renders the final.\n"
    "Push the film where the critic says it plays safe; never trade a bold idea for a safe one to save time.\n\n"
    "{critic_head}\n\n{report}")
REVISE_REMIND = ("You ended your turn without a next step. Write work/pipeline/tasks.json for builders, or change a scene "
                 "yourself (the studio assembles again when one changed), or set \"stage\": \"final\" in work/plan.json, "
                 "then end your turn. ({left} left; the final starts by {final_by} at the latest.) Reminder {k} of 3: then "
                 "the studio starts the final.")
TASKS_BAD = ("work/pipeline/tasks.json has problems, so no builder went out:\n{problems}\nFix it (or delete it) and end your "
             "turn. (Reminder {k} of 3: then the studio starts the final.)")
NO_TIME_TASKS = ("There's no time for another builder round ({left} left; a round needs {need} and the final starts by "
                 "{final_by}), so the tasks in work/pipeline/tasks.json stay undone. Fix what matters most yourself, then end "
                 "your turn (or set the stage to final).")
FORCED_MSG = ("Time is short ({left} left; the final render takes about {est} here), so the studio is starting the final "
              "now from work/plan.json as it is{stopped}. Don't start anything new and don't touch the scenes while it "
              "renders. Write report.md meanwhile (what you made and the idea behind it, how, what you checked, the "
              "timestamps a human should check by ear); the checks and the critic's notes on the final follow.")
FINAL_MSG = (
    "The final is assembled: final/music-video.mp4 ({size}, the whole song){stand}. Checks: {checks} (work/checks/report.md)"
    "{fails}.\nNow: fix any FAIL (re-render only what you change, then `{assemble_final}`), write report.md (what you made "
    "and the idea behind it, how, what you checked, the timestamps a human should check by ear) and end your turn. Time "
    "left: {left}; a final render takes about {est}.{critic}")
FINAL_FAILED = ("The final couldn't be assembled: {error}\nFix it now ({left} left): repair the scene, or give its time to a "
                "neighbour in plan.json, then end your turn: the studio assembles the final again. (As a last resort it "
                "stands a neighbour's scene in over a segment that's missing.)")
NO_FINAL = ("The final couldn't be assembled ({error}), and there's no time to try again. Write report.md (what you made, "
            "what's missing and why) and end your turn.")
SOLO_MSG = ("The studio is switching this run to solo: {why}. From now on you make the whole video yourself, with no builders "
            "and no plan.json: deliver final/music-video.mp4 ({w}×{h}, {fps} fps, H.264 yuv420p + AAC, the length of the "
            "song, its audio unchanged), run `{check}` and write report.md. What you've made so far (work/scenes/, the lib, "
            "the style frames, the concept) is yours to use.{critic} Time left: {left} (until {deadline}).")
NUDGE_PIPE_TAIL = ("\n\nYou're back in stage 4: fix what you can yourself, or write work/pipeline/tasks.json for builders, then "
                   "end your turn (the studio assembles again and the critic looks). When it's ready, set \"stage\": \"final\" "
                   "in work/plan.json again and end your turn: the studio renders the final (the cut you delivered is kept "
                   "as {kept}, so a bolder try costs nothing). Keep report.md about the cut you deliver (no 'Round' heading: "
                   "those are for the user's review rounds).")


class PipelineRun(Runner):
    """The pipeline's stages around one planner session (the module doc and README "How it works" have the picture):
    1 plan (until ~30% of the budget) -> gate 1 (validate, the critic on the hero and the style frames, the planner's
    last revision of the language) -> 2 build (builders for the builder segments, in parallel; the planner builds its
    own) -> 3 assemble (a preview of the whole film, the checks that apply, the critic's film report) -> 4 revise (the
    planner fixes, dispatches builders with work/pipeline/tasks.json, or says final) -> 3 again while time allows ->
    5 final (the planner's "final", or the studio's when the time left reaches the final's estimate x 1.3 + 8 min).
    Every way out ends with a final whenever one can be made: a plan that never validates falls back to solo; a
    builder that crashes, hangs or runs out of time fails (the planner hears, and may take it over); a planner that
    ends its turn without the stage's output gets up to three reminders, then the studio moves on with what exists;
    a planner whose session ends lets the studio assemble the final alone; a broken plan.json at the final is
    replaced by the last one that validated; a segment that is still missing at the final gets a neighbour's scene
    as a stand-in. STOP, the time warnings, the deadline + grace and the early-finish nudge work as in solo mode.
    Everything is in work/pipeline/state.json (the web app polls it) and the run's status.json."""

    def __init__(self, *a, builders=3, builder_model=None, builder_effort="high", revise=False, pl=None, **kw):
        super().__init__(*a, **kw)
        import pipeline
        self.pl = pl or pipeline
        self.critics = None                      # the pipeline calls the critic at its gates (a solo fallback starts the watcher)
        self.mode = "pipeline"
        self.end_reason = "the pipeline finished"
        self.n_builders, self.builder_model, self.builder_effort = builders, builder_model, builder_effort or "high"
        self.stage, self.stages, self.round, self.build_no = None, [], 0, 0
        self.tries = self.reminded = 0
        self.job = self.build = None
        self.lang_t = None                       # gate 1's report went out: the planner's last revision of the language
        self.planner_done = False
        self.plan_by = self.started + 0.30 * self.budget_s
        self.plan_late = False
        self.build_by = None
        self.est, self.est_t = None, 0.0         # (seconds, how) of the final render, re-estimated every 30 s
        self.last_v = None                       # the last validation
        self.last_ok_v = None                    # the last validation that passed (the builders' fallback, the estimate)
        self.last_valid = None                   # the last plan.json that validated (put back if the planner breaks it)
        self.snap = None                         # the scenes as they were at the last assemble
        self.final_tries = 0
        self.final = None                        # the final assemble's result
        self.kept = None                         # the version a final of this run was kept as (keep_cut)
        self.wrap = self.fixing = False
        self.studio_only = False
        self.builder_lines, self.requests = [], []
        self.extra_notes = []                    # more for the next assemble report (e.g. the user's live notes)
        self.msgs = []
        self.t_pub = self.t_cost = 0.0
        self.old_sessions = []                   # the builders of finished rounds (state.json keeps them)
        self.crit_seen = 0
        self.inline = False                      # tests: run the long steps in this thread
        self.revise_start = revise
        self.python, self.tools = venv_python(self.pdir), rel(os.path.join(STUDIO, "tools"), self.pdir)

    def begin(self):
        """The run's first state: stage 1, or stage 4 for a review round (the plan as the last run left it)."""
        if self.revise_start:
            self.validate()
            self.pl.set_stage(self.pdir, "planned")      # "final" is the planner's signal: it says it again when ready
            self.snap = self.scene_snapshot()
            self.round = 0
            self.set_stage("revise")
        else:
            self.set_stage("plan")

    # ------------------------------------------------------------------ helpers
    def cmd(self, *args):
        return f"{self.python} {self.tools}/studio.py {' '.join(args)}"

    def hero_cmd(self, v=None):
        hero = (v or self.last_v or {}).get("hero") or "<hero>"
        return self.cmd("pipeline render", os.path.basename(self.pdir), "--preview --only", hero)

    def validate(self, ready=False):
        """plan.json now (ready: with the rules a "planned" plan follows, whatever its stage says: what the gate and the
        builders need)."""
        v = self.pl.validate(self.pdir, ready=ready)
        self.last_v = v
        if v.get("ok"):
            self.last_ok_v = v
            self.last_valid = json.loads(json.dumps(v["plan"]))
        return v

    def final_est(self, now=None):
        now = now or time.time()
        if self.est is None or now - self.est_t > 30:
            self.est_t = now
            try:
                v = self.last_ok_v
                self.est = self.pl.estimate_final(self.pdir, v) if v else self.pl.estimate_rough(self.pdir)
            except Exception as ex:
                self.note(f"(couldn't estimate the final render: {ex})")
                self.est = self.est or (600.0, "a guess")
        return self.est

    def final_need(self, now=None):
        return self.final_est(now)[0] * 1.3 + 8 * 60

    def final_by(self, now=None):
        return self.deadline - self.final_need(now)

    def assemble_est(self):
        prev = [a for a in self.pl.load_state(self.pdir).get("assembles") or [] if a.get("quality") == "preview" and a.get("seconds")]
        return float(prev[-1]["seconds"]) if prev else max(120.0, (self.est or (600.0, ""))[0] * 0.5)

    def build_deadline(self, now, first=True):
        cap = self.started + 0.62 * self.budget_s if first else now + 0.25 * self.budget_s
        return min(self.final_by(now) - self.assemble_est() - 5 * 60, max(cap, now + BUILD_MIN_S))

    def time_for_builders(self, now):
        return self.n_builders > 0 and self.build_deadline(now, first=False) - now >= BUILD_MIN_S

    def time_for_assemble(self, now):
        return self.final_by(now) - now >= self.assemble_est() + 120

    def times(self, now):
        left = self.deadline - now
        est, how = self.final_est(now)
        return {"left": fmt_dur(left), "deadline": hm(self.deadline), "final_by": hm(self.final_by(now)), "est": fmt_dur(est),
                "how": how, "plan_by": hm(self.plan_by)}

    def scene_snapshot(self):
        """Every scene file and plan.json with its mtime: what the planner changed since."""
        snap = {}
        for root, dirs, fs in os.walk(os.path.join(self.pdir, "work", "scenes")):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "renders", "eyes")]
            for f in fs:
                p = os.path.join(root, f)
                try:
                    snap[p] = os.path.getmtime(p)
                except OSError:
                    pass
        p = os.path.join(self.pdir, "work", "plan.json")
        snap[p] = os.path.getmtime(p) if os.path.exists(p) else 0
        return snap

    def send(self, text, why):
        ok = self.s.send(text, why)
        self.msgs.append({"t": round(time.time() - self.started), "to": "planner", "why": why, "ok": ok})
        del self.msgs[:-80]
        return ok

    def ask(self, text, why):
        """A message the planner answers with a turn: turns that ended before it don't count as its answer."""
        self.s.ack()
        return self.send(text, why)

    def set_stage(self, stage):
        now = time.time()
        if self.stages and not self.stages[-1].get("ended"):
            self.stages[-1]["ended"] = now
        self.stage = stage
        rec = {"stage": stage, "started": now}
        if stage == "build":
            rec["round"] = self.build_no
        elif stage in ("assemble", "revise") and self.round:
            rec["round"] = self.round
        self.stages.append(rec)
        self.status["stage"] = stage
        self.note(f"▸ stage {STAGE_NAMES.get(stage, stage)}")
        self.publish(now, force=True)

    def record_c(self, c, kind, who, used):
        """A critique the pipeline asked for (gate 1, a builder's round, an assemble, the final) into status.json."""
        import critic as crit
        if not c or not c.get("file"):
            return
        with self.lock:
            if any(x.get("file") == c["file"] for x in self.status["critiques"]):
                return
            self.status["critiques"].append({k: c.get(k) for k in ("video", "scores", "verdict", "file", "cost_usd", "model")}
                                            | {"t": round(time.time() - self.started), "kind": kind, "who": who, "used": used})
            self.status["critic_cost_usd"] = round(self.status["critic_cost_usd"] + (c.get("cost_usd") or 0), 4)
        self.note(f"🔍 critic on {c.get('video')} ({kind}): {crit.score_line(c.get('scores'))}")

    def spawn(self, kind, fn):
        """A long studio step (the gate, an assemble, the final) in a thread; step() picks up its result. fn(abort,
        log): abort() turns true when the step must stop; log lines go to work/pipeline/<kind>.log (the main ones also
        to the live log)."""
        ev = threading.Event()
        logf = os.path.join(self.pdir, "work", "pipeline", f"{kind}.log")
        os.makedirs(os.path.dirname(logf), exist_ok=True)
        job = {"kind": kind, "abort": ev, "done": False, "result": None, "error": None, "t0": time.time(), "log": logf}
        with open(logf, "a", encoding="utf-8") as f:
            f.write(f"\n# {kind} ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")

        def log(line):
            line = str(line)
            try:
                with open(logf, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
            if JOB_LINE.match(line):
                self.note("  " + line.strip()[:300])

        def run():
            try:
                job["result"] = fn(ev.is_set, log)
            except Exception as ex:
                import traceback
                job["error"] = f"{type(ex).__name__}: {ex}"
                log(traceback.format_exc())
            job["done"] = True
        self.job = job
        if self.inline:
            run()
        else:
            job["thread"] = threading.Thread(target=run, daemon=True)
            job["thread"].start()
        return job

    def working(self):
        return bool(self.job and not self.job["done"]) or bool(self.build and not self.build.done)

    # ------------------------------------------------------------------ every second
    def step(self, now):
        if self.mode == "solo":                  # the plan never validated: solo for the rest
            self.publish(now)
            return self.solo_step(now)
        left = self.deadline - now
        self.check_stop(now)
        self.pipe_warnings(now, left)
        self.check_hung(now)
        self.poll_job(now)
        if self.build:
            self.build.poll(now)
            for c in self.build.critiques[self.crit_seen:]:
                self.record_c(c, "builder", c.get("who"), c.get("used"))
            self.crit_seen = len(self.build.critiques)
        self.publish(now)
        if left <= -self.grace:
            self.stop_reason = self.stop_reason or "budget ran out (deadline + grace)"
            return False
        if self.mode == "solo":                  # it switched just now
            return True
        if self.stop_reason:                     # stopping: nothing new; the planner ends its turn, or the grace ends it
            if self.s.turn_over(now):
                self.s.ack()
                self.s.close_input()
            return self.s.alive() or self.working()
        if not self.s.alive() and not self.s.closed and not self.studio_only:
            self.planner_gone(now)
        if self.studio_only:
            return self.studio_step(now)
        if self.stage in ("plan", "gate", "build", "assemble", "revise") and self.last_valid and left <= self.final_need(now):
            if self.stage in ("plan", "gate"):   # nothing built yet: a final now would be the hero everywhere
                return self.to_solo(now, self.no_room(now))
            return self.start_final(now, f"time is short: {fmt_dur(left)} left, and the final render takes about "
                                         f"{fmt_dur(self.final_est(now)[0])} here", forced=True)
        return getattr(self, "on_" + self.stage)(now)

    def no_room(self, now):
        est, how = self.final_est(now)
        return (f"the final render of this plan takes about {fmt_dur(est)} here ({how}) and would have to start by "
                f"{hm(self.final_by(now))}, so no room is left for the pipeline's stages (builders, a preview, the "
                f"studio's final). Keep the idea at full strength, and make sure its final render finishes in the time left")

    def pipe_warnings(self, now, left):
        if "half" not in self.warned and now - self.started >= 0.5 * self.budget_s and left > 0:
            self.warned.add("half")
            self.send(f"Half of your time is gone ({fmt_dur(left)} left, deadline {hm(self.deadline)}). Check your plan "
                      f"against the clock: the final starts by {hm(self.final_by(now))} at the latest.", "half")
        if "plan" not in self.warned and self.stage == "plan" and now >= self.plan_by and left > 0:
            self.warned.add("plan")
            self.send(PLAN_DUE.format(**self.times(now)), "the plan stage ends")
        if "five" not in self.warned and 0 < left <= 5 * 60:
            self.warned.add("five")
            fin = " (the final is rendering)" if self.stage == "final" and self.working() else \
                " (the final is there)" if os.path.exists(self.fin) and os.path.getmtime(self.fin) > self.started else ""
            self.send(f"5 minutes left: write report.md now{fin}. Nothing new.", "5 minutes")
        self.time_up(left)

    def poll_job(self, now):
        j = self.job
        if not j or not j["done"]:
            return
        self.job = None
        if j.get("error"):
            self.note(f"✗ the studio's {j['kind']} failed: {j['error']} (work/pipeline/{j['kind']}.log)")
        if j["abort"].is_set() and j["kind"] != "final":
            return                               # stopped on purpose: the stage moved on
        getattr(self, j["kind"] + "_done")(j["result"] or {"ok": False, "error": j.get("error") or "no result"}, now)

    def publish(self, now=None, force=False):
        """work/pipeline/state.json, every 2 s (the web app polls it), and the costs in status.json."""
        now = now or time.time()
        if not force and now - self.t_pub < 2:
            return
        self.t_pub = now
        st = self.status
        if now - self.t_cost > 10:               # every critique this run (failed ones too) is in the critic's log
            self.t_cost = now
            logged = sum(d.get("cost_usd") or 0 for d in logged_critiques(self.pdir, self.started))
            st["critic_cost_usd"] = round(max(st["critic_cost_usd"], logged), 4)
        cur = self.build.sessions() if self.build else []
        bcost = round(sum(float(x.get("cost_usd") or 0) for x in self.old_sessions + cur), 4)
        st["builders_cost_usd"] = bcost
        st["costs"] = {"planner": round(float(st.get("cost_usd") or 0), 4), "builders": bcost,
                       "critic": st["critic_cost_usd"], "total": round(float(st.get("cost_usd") or 0) + bcost + st["critic_cost_usd"], 4)}
        planner = {"name": "planner" if self.mode == "pipeline" else "director", "role": "planner", "model": st.get("model"),
                   "model_used": st.get("model_used"), "effort": st.get("effort"),
                   "state": ("done" if not self.s.alive() else "working" if self.s.busy else "idle"),
                   "cost_usd": st["costs"]["planner"], "turns": st.get("turns"), "log": rel(os.path.join(self.rdir, "events.jsonl"), self.pdir),
                   "status": rel(os.path.join(self.rdir, "status.json"), self.pdir), "last": self.s.tail(6)}
        segs = {}
        for it in (self.last_v or {}).get("items") or []:
            segs[it["id"]] = {"kind": it["kind"], "owner": it["owner"], "status": it["status"], "from": round(it["t0"], 3),
                              "to": round(it["t1"], 3), "renderer": it["renderer"], "scene": it["scene"], "exists": it["exists"]}
        for x in self.old_sessions + cur:
            if x.get("segment") in segs:
                segs[x["segment"]].update(builder=x.get("name"), builder_state=x.get("state"), critique=x.get("critique"),
                                          error=x.get("error"), round=x.get("round"))
        fb = self.final_by(now) if self.stage not in (None, "plan") or self.last_valid else None

        def fn(d):
            d.update(run=self.n, mode=self.mode, state=st.get("state"), stage=self.stage, round=self.round, build_round=self.build_no,
                     stage_started=self.stages[-1]["started"] if self.stages else None, stages=self.stages,
                     started=self.started, deadline=self.deadline, budget_s=self.budget_s, plan_by=self.plan_by,
                     build_by=self.build_by, final_by=fb, final_est=(self.est or (None,))[0],
                     sessions=[planner] + self.old_sessions + cur, segments=segs, messages=self.msgs[-60:],
                     costs=st["costs"], critiques=st["critiques"],
                     settings={"planner": [st.get("model"), st.get("effort")], "builders": self.n_builders,
                               "builder": [self.builder_model, self.builder_effort],
                               "critic": [self.critic_model if self.use_critic else "off", self.critic_effort]})
        try:
            self.pl.update_state(self.pdir, fn)
        except Exception as ex:
            self.note(f"(couldn't write work/pipeline/state.json: {ex})")

    # ------------------------------------------------------------------ 1. plan
    def on_plan(self, now):
        s = self.s
        if s.turn_over(now):
            s.ack()
            return self.after_plan_turn(now)
        if not self.plan_late and now >= self.plan_by + 300:
            self.plan_late = True
            v = self.validate()
            if v["ok"]:
                self.note("▸ the plan stage's time is up: the studio gates the plan as it is")
                return self.start_gate(now)
            return self.to_solo(now, "there was no valid work/plan.json five minutes after the plan stage's deadline")
        return True

    def after_plan_turn(self, now):
        v = self.validate()
        t = self.times(now)
        val = self.cmd("pipeline validate", os.path.basename(self.pdir))
        if v.get("plan") is None and any("no work/plan.json" in e for e in v["errors"]):
            if self.reminded >= 3:
                return self.to_solo(now, "the planner ended its turn four times without a work/plan.json")
            self.reminded += 1
            self.ask(PLAN_MISSING.format(**t, validate=val, render_hero=self.hero_cmd(v), k=self.reminded), "no plan.json yet")
        elif not v["ok"]:
            self.tries += 1
            if self.tries >= 3:
                return self.to_solo(now, "work/plan.json didn't validate after three tries")
            self.ask(PLAN_INVALID.format(k=self.tries, validate=val, text=self.pl.plan_text(v, self.pdir, table=False)),
                     f"plan.json isn't valid ({self.tries} of 3)")
        elif v["stage"] == "planning" and self.reminded < 3:
            self.reminded += 1
            self.ask(PLAN_PLANNING.format(**t), "plan.json still says planning")
        else:
            return self.start_gate(now)
        return True

    # ------------------------------------------------------------------ gate 1: the language
    def start_gate(self, now):
        self.reminded = 0
        self.lang_t = None
        self.set_stage("gate")
        cm = self.critic_model if self.use_critic else "off"
        self.spawn("gate", lambda abort, log: self.pl.gate(self.pdir, cm, self.critic_effort, CRITIC_MAX_USD, log=log, abort=abort))
        return True

    def gate_done(self, res, now):
        import critic as crit
        v = self.validate()
        t = self.times(now)
        if res.get("errors") or (not res.get("ok") and not v["ok"]):
            self.tries += 1
            if self.tries >= 3:
                return self.to_solo(now, "work/plan.json didn't validate after three tries")
            self.set_stage("plan")
            val = self.cmd("pipeline validate", os.path.basename(self.pdir))
            if v["ok"]:                          # it validates as "planning", but the gate needs a planned plan
                self.ask(PLAN_NOT_READY.format(k=self.tries, validate=val, text=res.get("text") or "\n".join(res["errors"])),
                         f"plan.json isn't ready for the gate ({self.tries} of 3)")
            else:
                self.ask(PLAN_INVALID.format(k=self.tries, validate=val, text=self.pl.plan_text(v, self.pdir, table=False)),
                         f"plan.json isn't valid ({self.tries} of 3)")
            return True
        if not res.get("video"):                 # the hero's preview couldn't be made
            self.tries += 1
            if self.tries >= 3:
                return self.to_solo(now, f"the hero's preview couldn't be made three times ({res.get('error')})")
            self.set_stage("plan")
            self.ask(HERO_FAILED.format(error=res.get("error"), render_hero=self.hero_cmd(v), hero=v.get("hero"), k=self.tries),
                     "the hero's preview couldn't be made")
            return True
        if v.get("stage") == "planning":         # gated as it was: from now on it is a planned plan
            self.pl.set_stage(self.pdir, "planned")
            self.note("▸ plan.json said planning: the studio gated it as it is and set it to planned")
            v = self.validate()
        c = crit.load(self.pdir, res["critique"]["file"]) if res.get("critique") else None
        if not c:
            why = ("the critic is off in this run" if not self.use_critic else
                   f"the critic couldn't look at it ({res.get('error') or 'no answer'})")
            return self.start_build(now, head=f"Gate 1: plan.json is valid and the hero's preview is there; {why}, so nobody "
                                              f"else looked at the language. ")
        self.record_c(c, "gate", "planner", "sent to the planner")
        segs = [it for it in v["items"] if it["kind"] == "segment"]
        bids = [it["id"] for it in segs if it["owner"] == "builder" and it["status"] != "built"]
        nxt = (f"the builders start on {', '.join(bids)}" if bids and self.n_builders > 0 else
               "you build the rest (no builders in this run)" if bids else "you build the rest")
        self.lang_t = now
        rep = (c.get("report") or "").strip()
        if len(rep) > 6000:
            rep = rep[:6000].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
        self.ask(GATE_MSG.format(nseg=len(segs), hero=v.get("hero"), model=c.get("model") or self.critic_model,
                                 video=res.get("video"), next=nxt, render_hero=self.hero_cmd(v),
                                 lock_by=hm(max(self.plan_by + 300, now + LANG_REVISE_S)), report=rep, **t),
                 "gate 1: the critic on the language")
        return True

    def on_gate(self, now):
        if self.job or self.lang_t is None:
            return True                          # the gate is running
        s = self.s
        if s.turn_over(now):
            s.ack()
            v = self.validate()
            if v["ok"]:
                return self.start_build(now)
            self.tries += 1
            if self.tries >= 3:
                return self.to_solo(now, "work/plan.json didn't validate after three tries")
            self.ask(PLAN_INVALID.format(k=self.tries, validate=self.cmd("pipeline validate", os.path.basename(self.pdir)),
                                         text=self.pl.plan_text(v, self.pdir, table=False)), f"plan.json isn't valid ({self.tries} of 3)")
            return True
        if now > max(self.plan_by + 300, self.lang_t + LANG_REVISE_S):
            v = self.validate()
            if v["ok"]:
                return self.start_build(now, head="Your time for the language is up: it is locked as it is now. ")
        return True

    # ------------------------------------------------------------------ 2. build
    def start_build(self, now, tasks=None, head=""):
        v = self.validate(ready=True)            # builders start from the brief: what a planned plan has
        if not v["ok"]:
            if not self.last_ok_v:
                return self.to_solo(now, "work/plan.json isn't valid")
            self.note("plan.json isn't valid right now: the builders start from the last plan that was")
            v = self.last_ok_v
        segs = [it for it in v["items"] if it["kind"] == "segment"]
        if tasks is None:
            ids = [it["id"] for it in segs if it["owner"] == "builder" and it["status"] != "built"]
        else:
            ids = [i for i in tasks if i in {it["id"] for it in segs}]
        if tasks is None and self.final_by(now) - now < BUILD_MIN_S and any(it["status"] not in ("drafted", "built") for it in segs):
            return self.to_solo(now, self.no_room(now))     # not even the planner could build the rest before the final
        self.build_no += 1
        self.set_stage("build")
        self.planner_done = False
        t = self.times(now)
        by = self.build_deadline(now, first=tasks is None)
        self.build_by = None
        if not ids:
            line = "No segment is a builder's: everything is yours." if tasks is None else "No task named a segment."
        elif self.n_builders <= 0:
            line = f"This run has no builders, so the builder segments ({', '.join(ids)}) are yours too."
        elif by - now < BUILD_MIN_S:
            line = (f"There's no time for builders ({t['left']} left, and the final starts by {t['final_by']}), so "
                    f"{', '.join(ids)} {'is' if len(ids) == 1 else 'are'} yours: build {'it' if len(ids) == 1 else 'them'}, "
                    f"or give the time to a neighbour in plan.json.")
        else:
            self.build_by = by
            last = {}
            if tasks:
                import critic as crit
                for i in ids:
                    c = crit.find(self.pdir, f"work/segments/{i}/{i}_preview.mp4")
                    if c:
                        last[i] = crit.load(self.pdir, c["file"])
            self.build = self.new_round(v, ids, tasks, by, last)
            queued = ids[self.n_builders:]
            line = (f"Builders ({self.builder_model}, effort {self.builder_effort}) are on {', '.join(ids[:self.n_builders])}"
                    + (f" (then {', '.join(queued)}, as one finishes)" if queued else "") + f" until {hm(by)}"
                    + (" with your tasks" if tasks else "") + ": each writes its scene, renders its segment, gets one critic "
                    "round and revises once. They write only in their own folders: their requests for the lib come with the "
                    "assemble report.")
        mine = [f"{it['id']} ({it['status']})" for it in segs if it["id"] not in (ids if self.build else [])
                and (it["owner"] == "planner" or not self.build)]
        ovs = [f"{it['id']} ({'there' if it['exists'] else 'not written yet'})" for it in v["items"] if it["kind"] == "overlay"]
        if tasks is not None:
            head = head or "Your tasks went out. "
        self.ask(BUILD_MSG.format(head=head, builders=line, mine=", ".join(mine) or "none", overlays=", ".join(ovs) or "none",
                                  render=self.cmd("pipeline render", os.path.basename(self.pdir), "--preview --only <id>"), **t),
                 "builders started" if self.build else "build your segments")
        return True

    def new_round(self, v, ids, tasks, deadline, last):
        return self.pl.BuildRound(self.pdir, v, ids, tasks, self.n_builders, self.builder_model, self.builder_effort,
                                  self.critic_model if self.use_critic else None, self.critic_effort, CRITIC_MAX_USD, deadline,
                                  self.build_no, self.started, on_event=self.builder_event, last=last,
                                  python=self.python, tools=self.tools)

    def builder_event(self, state, b, why):
        self.note(f"🔨 {b.name}: {state}" + (f" ({why})" if why else ""))
        if state in ("failed", "skipped") and not self.stop_reason and self.stage == "build" and self.s.alive():
            it = b.it
            ok = it.get("exists") and os.path.exists(self.pl.outputs(self.pdir, it, "preview")[1])
            stands = (f"Its scene ({it['scene']}) and a preview from earlier are there. " if ok else "")
            self.send(BUILDER_FAILED.format(id=b.id, what="failed" if state == "failed" else "didn't start", why=why or state,
                                            stands=stands, render=self.cmd("pipeline render", os.path.basename(self.pdir),
                                                                           "--preview --only", b.id)),
                      f"builder {b.id} {state}")
            self.planner_done = False            # it may take the segment over: wait for its turn

    def on_build(self, now):
        s = self.s
        if s.turn_over(now):
            s.ack()
            self.planner_done = True
        b = self.build
        if b and not b.done:
            if self.build_by and now > self.build_by + self.pl.BUILDER_GRACE_S + 60 and not b.stopping:
                b.stop("the build time is up", "late")
            return True
        late = now > (self.build_by or (self.final_by(now) - self.assemble_est() - 120))
        if self.planner_done or late:
            return self.start_assemble(now)
        return True

    def retire_build(self):
        if self.build:
            self.build.retire()                  # a stopped builder's render or critique still running stops now
            self.builder_lines = self.build.lines()
            self.requests = self.build.requests()
            self.old_sessions += self.build.sessions()
            self.build = None
            self.crit_seen = 0

    # ------------------------------------------------------------------ 3. assemble
    def start_assemble(self, now):
        if self.build:
            for c in self.build.critiques[self.crit_seen:]:
                self.record_c(c, "builder", c.get("who"), c.get("used"))
        self.retire_build()
        self.round += 1
        self.set_stage("assemble")
        self.snap = self.scene_snapshot()
        cm = self.critic_model if self.use_critic else None
        self.spawn("assemble", lambda abort, log: self.pl.assemble(self.pdir, "preview", log=log, look=True, check=True,
                                                                   critic_model=cm, critic_effort=self.critic_effort,
                                                                   max_usd=CRITIC_MAX_USD, abort=abort))
        return True

    def on_assemble(self, now):
        return True                              # the studio assembles (a thread); assemble_done picks it up

    def assemble_done(self, res, now):
        import critic as crit
        c = crit.load(self.pdir, res["critique"]["file"]) if res.get("critique") else None
        if c:
            self.record_c(c, "assemble", "planner", "sent to the planner")
        self.set_stage("revise")
        self.reminded = 0
        self.ask(self.assemble_message(res, c, now), f"assemble report (round {self.round})")
        self.builder_lines, self.requests = [], []
        return True

    def assemble_message(self, res, c, now):
        t = self.times(now)
        v = self.validate()
        slates = f"; {', '.join(res['placeholders'])} {'is a slate' if len(res['placeholders']) == 1 else 'are slates'} (not built, or the render failed)" \
            if res.get("placeholders") else ""
        ch = res.get("checks") or {}
        checks = ("Checks on it: " + " · ".join(f"{k} {x}" for k, x in ch.items())
                  + (f" · sync: {res['sync']}" if res.get("sync") else "") + " (work/checks/<check>/report.md)."
                  + ("".join(f"\n  {x}" for x in (res.get("check_lines") or [])[:6]))) if ch else \
            (f"Sync: {res['sync']}." if res.get("sync") else "")
        lines = []
        by_b = {x.split(":")[0][2:]: x for x in self.builder_lines}
        for it in v["items"] if v.get("items") else (self.last_ok_v or {}).get("items") or []:
            if it["kind"] != "segment":
                continue
            lines.append(by_b.get(it["id"]) or f"- {it['id']}: {it['owner']}{', the hero' if it['id'] == v.get('hero') else ''}, "
                                                  f"{it['status']}" + ("" if it["exists"] else " (no scene yet)"))
        req = ("\nThe builders' requests (they can't change the lib; you decide):\n" + "\n\n".join(self.requests) + "\n") if self.requests else ""
        notes = ("\n" + "\n".join(self.extra_notes) + "\n") if self.extra_notes else ""
        self.extra_notes = []
        render = self.cmd("pipeline render", os.path.basename(self.pdir), "--preview --only <id>")
        if not res.get("ok"):
            return ASSEMBLE_FAILED.format(k=self.round, error=res.get("error") or "no result", segments="\n".join(lines),
                                          requests=req, notes=notes, render=render, **t)
        fix_by = self.build_deadline(now, first=False)
        tasks = ("write work/pipeline/tasks.json [{\"segment\": \"s3\", \"what\": \"...\"}] to send builders back to segments "
                 f"(one more builder round fits: until about {hm(fix_by)})" if self.time_for_builders(now) else
                 "(there's no time for another builder round now: what needs fixing, fix yourself)")
        if c:
            rep = (c.get("report") or "").strip()
            if len(rep) > 6000:
                rep = rep[:6000].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
            pics = critic_pictures(c)
            ch_ = (f"The studio's critic ({c.get('model') or self.critic_model}, fresh eyes: it saw the frames of the whole "
                   f"film, your concept and the song, not your reasoning) on {res.get('video')} ({c.get('file')}): take "
                   f"what's right, push harder where it says you played safe, ignore what's wrong, and note in work/log.md "
                   f"what you took and why." + (f"\n{pics}" if pics else ""))
        else:
            rep = ""
            ch_ = ("(The critic is off in this run: look at the preview yourself with the eyes tools.)" if not self.use_critic
                   else "(The critic couldn't look at this preview: judge it with the eyes tools.)")
        return ASSEMBLE_MSG.format(k=self.round, video=res.get("video") or "(none)", slates=slates, checks=checks,
                                   segments="\n".join(lines), requests=req, notes=notes, tasks=tasks, render=render,
                                   critic_head=ch_, report=rep, **t)

    # ------------------------------------------------------------------ 4. revise
    def on_revise(self, now):
        s = self.s
        if not s.turn_over(now):
            return True
        s.ack()
        t = self.times(now)
        v = self.validate()
        if v["ok"] and v["stage"] == "final":
            return self.start_final(now, "the planner set the stage to final")
        if not v["ok"]:
            if self.reminded >= 3:
                return self.start_final(now, "plan.json didn't validate after three reminders (the last one that did is used)")
            self.reminded += 1
            self.ask(PLAN_INVALID.format(k=self.reminded, validate=self.cmd("pipeline validate", os.path.basename(self.pdir)),
                                         text=self.pl.plan_text(v, self.pdir, table=False))
                     .replace("after the third the rest of the run is solo", "after the third the studio starts the final "
                              "from the last plan that was valid"), "plan.json isn't valid")
            return True
        tasks, problems = self.pl.read_tasks(self.pdir, v)
        if problems or tasks:
            if problems or not self.time_for_builders(now):
                if self.reminded >= 3:
                    return self.start_final(now, "three turns without a next step that could run")
                self.reminded += 1
                if problems:
                    self.ask(TASKS_BAD.format(problems="\n".join(f"- {p}" for p in problems), k=self.reminded), "tasks.json has problems")
                else:
                    self.ask(NO_TIME_TASKS.format(need=fmt_dur(BUILD_MIN_S), **t), "no time for builders")
                return True
            self.pl.consume_tasks(self.pdir, self.build_no + 1)
            return self.start_build(now, tasks=tasks)
        if self.scene_snapshot() != self.snap:
            if self.time_for_assemble(now):
                return self.start_assemble(now)
            return self.start_final(now, "the scenes changed, and there's no time for another preview")
        if self.reminded >= 3:
            return self.start_final(now, "the planner ended three turns without a next step")
        self.reminded += 1
        self.ask(REVISE_REMIND.format(k=self.reminded, **t), "no next step")
        return True

    # ------------------------------------------------------------------ 5. final
    def start_final(self, now, why, forced=False):
        self.note(f"▸ the final: {why}")
        stopped = []
        if self.build and not self.build.done:
            stopped = [b.id for b in self.build.builders if b.state not in self.pl.Builder.END]
            self.build.stop("the studio starts the final", "forced")
        prev = self.job
        if prev:
            prev["abort"].set()
        self.job = None
        self.wrap = self.fixing = False
        if self.stage != "final":
            self.set_stage("final")
        if forced and self.s.alive():
            t = self.times(now)
            self.send(FORCED_MSG.format(left=t["left"], est=t["est"],
                                        stopped=f" (the builders on {', '.join(stopped)} are stopped: what they rendered stands)"
                                        if stopped else ""), "the final starts now")
        self.spawn("final", lambda abort, log: self.final_job(abort, log, prev))
        return True

    def restore_plan(self, log):
        """plan.json doesn't validate at the final: the last one that did goes back (the broken one is kept)."""
        if not self.last_valid:
            return False
        p = os.path.join(self.pdir, "work", "plan.json")
        bk = os.path.join(self.pdir, "work", "pipeline", f"plan_broken_{time.strftime('%H%M%S')}.json")
        if os.path.exists(p):
            os.makedirs(os.path.dirname(bk), exist_ok=True)
            shutil.copyfile(p, bk)
        jsave(p, self.last_valid)
        log(f"plan.json didn't validate: the last plan that did is back (the broken one: {rel(bk, self.pdir)})")
        self.note(f"▸ plan.json didn't validate: the studio put back the last one that did ({rel(bk, self.pdir)} kept)")
        return True

    def keep_cut(self, log):
        """final/music-video.mp4 from this run, before the studio renders another one over it: kept as the next version
        (the cut that exists is never lost). With the checks of the final that made it, when it is still that file."""
        f = self.final or {}
        if not f.get("ok") or not (os.path.exists(self.fin) and os.path.getmtime(self.fin) > self.started):
            return None                          # no good final from this run yet
        mt = os.path.getmtime(self.fin)
        if (self.kept or {}).get("mtime") == mt:
            return self.kept                     # kept already (the nudge did it)
        rep = f.get("check_report") if f.get("mtime") == mt else None
        if rep is None:                          # the planner assembled it again itself: its own checks, if they are newer
            rp = os.path.join(self.pdir, "work", "checks", "report.json")
            rep = jload(rp, {}) or {}
            rep = rep if rep.get("video") == "final/music-video.mp4" and os.path.getmtime(rp) >= mt else {}
        try:
            v = deliver(self.pdir, run=self.n, log=log, report=rep or {})
        except Exception as ex:
            self.note(f"(couldn't keep the cut as a version: {ex})")
            return None
        if v:
            self.kept = dict(v, mtime=mt)
            self.note(f"▸ the cut delivered so far is kept as v{v['n']}")
        return v

    def final_job(self, abort, log, prev=None):
        if prev and prev.get("thread"):
            prev["thread"].join(120)             # the step it replaces stops first (its renders hold the queue)
        b, t0 = self.build, time.time()
        while b and not b.done and time.time() - t0 < 150 and not abort():
            time.sleep(1)                        # the builders wrap up (what they rendered stands)
        if b and b.done:
            b.retire()
        self.keep_cut(log)
        v = self.pl.validate(self.pdir)
        if not v["ok"]:
            self.restore_plan(log)
        left = self.deadline - time.time()
        # the first try leaves a missing segment to the planner (it can fix it); after that, or with no time or no
        # planner, a neighbour's scene stands in
        can_fix = self.s.alive() and not self.studio_only and self.final_tries == 0 and left > self.final_need() + 5 * 60
        res = self.pl.assemble(self.pdir, "final", log=log, look=True, check=True, abort=abort, stand_ins=not can_fix)
        res["stand_ins_tried"] = not can_fix
        self.final_facts(res)
        if res.get("ok") and self.use_critic and not abort() and self.deadline - time.time() > 6 * 60:
            v = self.pl.validate(self.pdir)
            segs = [it for it in v["items"] if it["kind"] == "segment"]
            joins = ", ".join(f"{a['id']}→{b_['id']} at {self.pl.mmss(b_['t0'])}" for a, b_ in zip(segs, segs[1:]))
            focus = self.pl.FILM_FOCUS.format(outline=self.pl.outline(v), joins=joins or "one segment: no joins", holes="") + (
                " This is the final cut: say what can still be fixed in the minutes left (one render of what changes), and "
                "what is for a review round.")
            res["critique_full"] = self.pl.run_critic(self.pdir, os.path.join(self.pdir, "final", "music-video.mp4"), focus,
                                                      self.critic_model, self.critic_effort, CRITIC_MAX_USD, log=log, abort=abort)
        return res

    def final_facts(self, res):
        """What keep_cut needs to know of a final it made: the file's mtime and its checks' report."""
        if res.get("ok") and os.path.exists(self.fin):
            res["mtime"] = os.path.getmtime(self.fin)
            rep = jload(os.path.join(self.pdir, "work", "checks", "report.json"), {}) or {}
            res["check_report"] = rep if rep.get("video") == "final/music-video.mp4" else {}
        return res

    def final_done(self, res, now):
        self.final = res
        t = self.times(now)
        if res.get("error") == "stopped" and not res.get("ok"):
            self.wrap = True
            return True
        if res.get("ok"):
            c = res.get("critique_full")
            self.record_c(c, "final", "planner", "sent to the planner")
            self.wrap = True
            self.ask(self.final_message(res, c, now), "the final: fix any FAIL, write report.md")
            return True
        self.final_tries += 1
        if not res.get("stand_ins_tried") and self.s.alive() and not self.studio_only and \
                self.deadline - now > self.final_need(now) + 5 * 60:
            self.fixing = True
            self.ask(FINAL_FAILED.format(error=res.get("error"), left=t["left"]), "the final couldn't be assembled")
        elif not res.get("stand_ins_tried") and self.deadline - now > self.final_est(now)[0] + 3 * 60:
            self.note("▸ the final again, with stand-ins where segments are missing")
            self.spawn("final", lambda abort, log: self.final_facts(dict(self.pl.assemble(
                self.pdir, "final", log=log, look=True, check=True, abort=abort, stand_ins=True), stand_ins_tried=True)))
        else:
            self.wrap = True
            self.ask(NO_FINAL.format(error=res.get("error")), "no final")
        return True

    def final_message(self, res, c, now):
        t = self.times(now)
        ch = res.get("checks") or {}
        fails = [x for x in res.get("check_lines") or [] if "[FAIL]" in x]
        stand = ("; stand-ins: " + "; ".join(self.pl.stand_in_text(x) for x in res["stand_ins"]) if res.get("stand_ins") else "")
        d = delivery(load_pj(self.pdir))
        if c:
            rep = (c.get("report") or "").strip()
            if len(rep) > 6000:
                rep = rep[:6000].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
            crit_txt = (f"\n\nThe studio's critic ({c.get('model') or self.critic_model}, fresh eyes) on the final "
                        f"({c.get('file')}): if it finds a high-severity problem you can fix in the time left, fix it and "
                        f"assemble the final again; the rest is for the next round.\n\n{rep}")
        else:
            crit_txt = ""
        return FINAL_MSG.format(size=f"{d['width']}×{d['height']}", stand=stand,
                                checks=" · ".join(f"{k} {x}" for k, x in ch.items()) or "couldn't run",
                                fails="".join(f"\n  {x}" for x in fails[:8]),
                                assemble_final=self.cmd("pipeline assemble", os.path.basename(self.pdir), "--final --no-critic"),
                                left=t["left"], est=t["est"], critic=crit_txt)

    def on_final(self, now):
        if self.job:
            return True
        s = self.s
        if not s.alive():
            return False
        if self.fixing and self.deadline - now <= self.final_need(now):
            self.fixing = False                  # the planner's fix didn't come in time: the final with stand-ins now
            return self.start_final(now, "the planner's fix of the final didn't come in time", forced=True)
        if s.turn_over(now):
            s.ack()
            if self.fixing:
                self.fixing = False
                return self.start_final(now, "the planner fixed what stopped it")
            if self.wrap:
                left = self.deadline - now
                if self.nudge and (self.final or {}).get("ok") and self.nudges < 3 and \
                        left > self.final_need(now) + max(10 * 60, 0.12 * self.budget_s):
                    return self.nudge_back(now, left)
                s.close_input()
        return True

    def nudge_back(self, now, left):
        """The planner delivered with time to spare: back to stage 4, with the critic's notes on the final."""
        self.nudges += 1
        self.pl.set_stage(self.pdir, "planned")
        kept = self.keep_cut(self.note)
        tail = NUDGE_PIPE_TAIL.format(kept=f"v{kept['n']}" if kept else "a version")
        c = (self.final or {}).get("critique_full")
        if c:
            text = NUDGE_HEAD.format(left=fmt_dur(left)) + "\n\n" + critic_message(c) + tail
        else:
            text = (f"You delivered a cut with {fmt_dur(left)} still on the clock: spend it making the video better, and "
                    f"bolder: where does it play safe, which ★★★ moment doesn't change the picture's rules, what would the "
                    f"boldest version of your concept do?" + tail)
        self.round += 1
        self.set_stage("revise")
        self.reminded = 0
        self.snap = self.scene_snapshot()
        self.ask(text, "use the time left, with the critic's notes" if c else "use the time left")
        return True

    # ------------------------------------------------------------------ when things go wrong
    def to_solo(self, now, why):
        """The plan never validated: the planner makes the whole video itself, as a solo director (the critic watcher
        takes over from the pipeline's gates)."""
        if self.build:
            self.build.stop("the run switched to solo", "forced")
        if self.job:
            self.job["abort"].set()
            self.job = None
        self.mode = "solo"
        self.end_reason = "the director ended the session"
        self.status["mode"] = "solo (after the pipeline)"
        self.status["mode_why"] = why
        self.set_stage("solo")
        if self.use_critic:
            self.critics = CriticWatcher(self.pdir, self.started, self.critic_model, self.critic_effort, self.critic_max)
        d = delivery(load_pj(self.pdir))
        t = self.times(now)
        crit_txt = (" Mux full previews into work/previews/preview_N.mp4: the studio's critic looks at each one and at the "
                    "final, and sends you its notes." if self.use_critic else "")
        self.ask(SOLO_MSG.format(why=why, w=d["width"], h=d["height"], fps=d["fps"], check=self.cmd("check", os.path.basename(self.pdir)),
                                 critic=crit_txt, left=t["left"], deadline=t["deadline"]), "switching to solo")
        return True

    def planner_gone(self, now):
        self.studio_only = True
        rc = self.s.returncode
        self.note(f"■ the planner's session ended (exit {rc}) before the run did: the studio goes on with what exists")
        self.end_reason = f"the planner's session ended early (exit {rc}); the studio finished with what existed"

    def studio_step(self, now):
        """The planner is gone: the builders finish, then the studio assembles the final from the plan (with stand-ins
        where a segment is missing: nobody is left to fix it)."""
        if self.stage == "final":
            if self.working() or self.wrap:
                return self.working()
            return self.start_final(now, "the planner's session ended before the final was settled")
        if self.build and not self.build.done:
            if self.build_by and now > self.build_by + self.pl.BUILDER_GRACE_S + 60 and not self.build.stopping:
                self.build.stop("the build time is up", "late")
            return True
        if self.job:
            return True
        self.validate()
        if self.last_valid:
            return self.start_final(now, "the planner's session ended")
        self.note("no valid work/plan.json, and the planner is gone: no final")
        return False

    def on_stop(self, now):
        if self.build:
            self.build.stop("stopped by the user", "user")
        if self.job:
            self.job["abort"].set()

    def after_session(self):
        user = bool(self.stop_reason and "user" in self.stop_reason)
        if self.build and not self.build.done:
            self.build.stop(self.stop_reason or "the run ended", "user" if user else "forced")
            t0 = time.time()
            while not self.build.done and time.time() - t0 < 100:
                time.sleep(1)
            for b in self.build.builders:
                if b.s and b.s.alive():
                    kill_tree(b.s.proc)
        if self.job and not self.job["done"]:
            self.job["abort"].set()
            th = self.job.get("thread")
            if th:
                th.join(60)
        if self.build:
            for c in self.build.critiques[self.crit_seen:]:
                self.record_c(c, "builder", c.get("who"), c.get("used"))
            self.retire_build()
        if self.stages and not self.stages[-1].get("ended"):
            self.stages[-1]["ended"] = time.time()

    def ended(self, v):
        self.stage = "done" if self.mode == "pipeline" else self.stage
        self.publish(force=True)


# ------------------------------------------------------------------ choosing the mode, the briefs
def choose_mode(pdir, mode, budget_s, builders, revise):
    """("solo"|"pipeline", why). auto: solo under 45 min, with --builders 0, for a review round of a video made without
    a valid plan, or when the pipeline's stages don't fit before the final must start (pipeline.stage_times: the plan
    stage, the gate, a builder round of 10 min, a preview, then the final's estimate × 1.3 + 8 min). Before there is
    a plan, the estimate is the whole song with the web engine plus one overlay pass (the type)."""
    import pipeline
    if mode == "solo":
        return "solo", "asked for"
    if mode != "pipeline" and builders is not None and builders <= 0:
        return "solo", "--builders 0"
    if mode != "pipeline" and budget_s < 45 * 60:
        return "solo", f"a budget under 45 min ({fmt_dur(budget_s)}): the pipeline's stages wouldn't pay off"
    v = pipeline.validate(pdir) if revise or mode != "pipeline" else None
    if revise and not v["ok"]:
        return "solo", "a review round of a video made without a valid work/plan.json"
    if mode != "pipeline":
        est, how = pipeline.estimate_final(pdir, v) if v and v["ok"] else pipeline.estimate_rough(pdir)
        if not pipeline.stage_times(budget_s, est, revise=revise)["ok"]:
            return "solo", (f"the final render alone takes about {fmt_dur(est)} here ({how}), so the pipeline's stages "
                            f"(the plan, the gate, a builder round, a preview, then the final) need about "
                            f"{fmt_dur(pipeline.pipeline_from(est, revise))} on this machine, more than {fmt_dur(budget_s)}")
    return "pipeline", "asked for" if mode == "pipeline" else "auto"


def hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def plan_brief(pdir, budget_s, seed_notes, started, deadline, critic, builders, builder_model, builder_effort, est):
    """templates/plan-brief.md: the user's brief as the director gets it, and the pipeline around the planner."""
    import pipeline
    pj = load_pj(pdir)
    d = delivery(pj)
    sf = song_file(pdir)
    sm = jload(os.path.join(pdir, "song", "song-map.json"), {}) or {}
    tools = rel(os.path.join(STUDIO, "tools"), pdir)
    name = os.path.basename(pdir)
    py = venv_python(pdir)
    t = pipeline.stage_times(budget_s, est[0], started)              # the same clock the runner keeps
    final_by, plan_by, build_by = t["final_by"], t["plan_by"], t["build_by"]
    pw, ph = pipeline.web_size(pdir, "preview")
    notes = user_notes(pj, seed_notes)
    cm = critic["model"] if critic else None
    vals = {"file": os.path.basename(sf), "duration": f"{float(sm.get('duration', 0)):.2f}", "width": d["width"],
            "height": d["height"], "fps": d["fps"], "budget": fmt_dur(budget_s), "tools": tools, "project": name, "python": py,
            "started": hm(started), "deadline": hm(deadline) + f" (in {fmt_dur(budget_s)})", "skill": rel(SKILL, pdir),
            "anti": rel(os.path.join(os.path.dirname(SKILL), "anti-defaults.md"), pdir), "refs": refs_brief(pdir, pj),
            "direction": ("\n" + notes + "\n") if notes else "", "plan_by": hm(plan_by),
            "build_by": hm(build_by) if t["ok"] else f"{hm(build_by)} (tight: the final render takes most of this budget)",
            "final_by": hm(final_by), "final_est": fmt_dur(est[0]), "final_how": est[1], "builders": builders,
            "builder_model": builder_model, "builder_effort": builder_effort, "preview_size": f"{pw}×{ph}",
            "frames": int(round(float(sm.get("duration", 0)) * d["fps"])),
            "critic_model": cm or "off",
            "critic": (f"a cheaper model ({cm}) with fresh eyes, at every gate: it sees only the frames, the style frames, "
                       f"your concept and the song, never your reasoning, and its report comes to whoever must act on it"
                       if cm else "off in this run: nobody else looks at the renders, so look yourself (the eyes tools)"),
            "no_builders": "" if builders > 0 else (" This run has no builders: you build every segment yourself (owner "
                                                    "planner), and the pipeline still gates, assembles and finishes it.")}
    return fill(open(os.path.join(TEMPLATES, "plan-brief.md"), encoding="utf-8").read(), vals)


def revise_pipeline_note(pdir, critic, builders, final_by=None, est=None):
    """The revise brief's block for a review round in pipeline mode (it starts at stage 4)."""
    tools = rel(os.path.join(STUDIO, "tools"), pdir)
    name, py = os.path.basename(pdir), venv_python(pdir)
    return (f"\n\n<!-- mv:planner -->\nThis video was made with the studio's pipeline, and this round starts at its stage 4 "
            f"(revise): you are the planner. work/plan.json has the segments (who built each, its scene in "
            f"work/scenes/<id>/), the lib (work/scenes/lib/) and the overlays. For each note: fix it yourself (re-render "
            f"what you change: `{py} {tools}/studio.py pipeline render {name} --preview --only <id>`), or send a builder "
            "back to a segment" + (f" (up to {builders} at once)" if builders > 0 else " (this run has no builders)")
            + ": write work/pipeline/tasks.json [{\"segment\": \"s3\", \"what\": \"the note, and what you want\"}] and "
            "end your turn. Whenever you end your turn after changing a scene, the studio assembles a preview of the "
            "whole film" + (f" and the critic ({critic['model']}) looks at it" if critic else "") + "; its report comes "
            "to you. When the film is ready, set \"stage\": \"final\" in work/plan.json and end your turn: the studio "
            "renders final/music-video.mp4 and runs the checks (it also starts the final by itself when the time left "
            "is what the final needs" + (f": by {hm(final_by)} at the latest; it takes about {fmt_dur(est)} here" if final_by else "")
            + "). Don't run `pipeline build`, `pipeline gate` or `pipeline assemble` yourself: the studio runs them when you "
            "end your turn (running one yourself costs another critique). Fix the film, not the frame: a note can move a "
            "boundary in plan.json. Never answer a note by making the film safer than it is: where a note asks for more, "
            "go further than it asks.")


def direct(pdir, budget="90m", effort=None, model=None, seed_notes="", revise=False, nudge=True, dry_run=False,
           critic_model=None, critic_effort=None, critic_max=6, mode="auto", planner_model=None, planner_effort=None,
           builders=3, builder_model=None, builder_effort=None, log=print):
    """The autonomous run. mode auto | pipeline | solo (auto: the pipeline when its stages fit, but solo under 45 min,
    with builders 0, and in the other cases choose_mode lists). Solo: one session makes the whole video (model, effort
    default high).
    Pipeline: the planner (planner_model or model, planner_effort or effort, default max) with builders (builder_model:
    the planner's; builder_effort high) and the critic at the gates. Returns the run's status."""
    from studio import Status
    import critic as crit
    if not os.path.exists(os.path.join(pdir, "song", "song-map.json")):
        raise SystemExit("no song folder yet: run ingest first")
    budget_s = parse_budget(budget)
    pj = load_pj(pdir)
    base_model = model or (pj.get("director") or {}).get("model") or "opus"
    cm, ce = crit.settings(pj)
    critic_model, critic_effort = critic_model or cm, critic_effort or ce
    run_mode, mode_why = choose_mode(pdir, mode, budget_s, builders, revise)
    pipe = run_mode == "pipeline"
    use_critic = critic_model != "off" and (pipe or (critic_max or 0) > 0)
    if pipe:
        model, effort = planner_model or base_model, planner_effort or effort or "max"
        builder_model, builder_effort = builder_model or model, builder_effort or "high"
    else:
        model, effort = base_model, effort or "high"
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
    critic = {"model": critic_model, "effort": critic_effort, "max": critic_max} if use_critic else None
    if pipe:
        import pipeline
        est = pipeline.estimate_final(pdir) if revise else pipeline.estimate_rough(pdir)
        if revise:                             # (the critic comes at the pipeline's gates: the note says so)
            fb = pipeline.stage_times(budget_s, est[0], started, revise=True)["final_by"]
            text = build_brief(pdir, budget_s, seed_notes, True, started, deadline, critic=None,
                               pipeline=revise_pipeline_note(pdir, critic, builders, fb, est[0]))
        else:
            text = plan_brief(pdir, budget_s, seed_notes, started, deadline, critic, builders, builder_model, builder_effort, est)
    else:
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
              "critiques": [], "critic_cost_usd": 0.0, "mode": run_mode, "mode_why": mode_why}
    if pipe:
        status.update(builders=builders, builder_model=builder_model, builder_effort=builder_effort, stage=None,
                      builders_cost_usd=0.0, costs={})
    if dry_run:
        log(text)
        log("\n" + " ".join(cmd[:4]) + f" ... (dry run: not started; mode {run_mode}: {mode_why})")
        status["state"] = "dry-run"
        jsave(os.path.join(rdir, "status.json"), status)
        return status
    if pipe:                                   # a fresh state.json for this run (the last one is kept)
        import pipeline
        sp = pipeline.state_path(pdir)
        if os.path.exists(sp):
            hd = os.path.join(pdir, "work", "pipeline", "history")
            os.makedirs(hd, exist_ok=True)
            os.replace(sp, os.path.join(hd, f"state_{time.strftime('%Y%m%d-%H%M%S')}.json"))
        st = Status(pdir, "direct", ["the pipeline: the planner, the builders and the critic", "archiving the cut and checking it"]
                    + (["the critic looks at the cut"] if use_critic else []))
    else:
        st = Status(pdir, "direct", ["Claude is directing", "archiving the cut and checking it"] +
                    (["the critic looks at the cut"] if use_critic else []))
    env = dict(os.environ, MV_PROJECT=pdir, PYTHONIOENCODING="utf-8")
    if pipe:
        env.update(MV_RENDER_WHO="planner", MV_CRITIC_MODEL=critic_model if use_critic else "off",
                   MV_CRITIC_EFFORT=critic_effort or "")
    session = Session(cmd, pdir, rdir, name="planner" if pipe else "director", status=status, env=env, started=started)
    session.start()
    if pipe:
        R = PipelineRun(pdir, n, rdir, status, session, budget_s, started, nudge=nudge, critic=critic, st=st, log=log,
                        builders=builders, builder_model=builder_model, builder_effort=builder_effort, revise=revise)
    else:
        R = Runner(pdir, n, rdir, status, session, budget_s, started, nudge=nudge, critic=critic, st=st, log=log)
    R.send(text, "the brief")
    if pipe:
        R.begin()
        log(f"director run {n} (pipeline): planner {model}, effort {effort}; up to {builders} builders ({builder_model}, "
            f"effort {builder_effort}); budget {fmt_dur(budget_s)} (until {hm(deadline)}); "
            + (f"critic {critic_model}" if use_critic else "no critic"))
    else:
        log(f"director run {n}: {model}, effort {effort}, budget {fmt_dur(budget_s)} (until {hm(deadline)})"
            + (f"; critic {critic_model}" if use_critic else "; no critic") + (f" (solo: {mode_why})" if mode != "solo" else ""))
    try:
        R.loop()
    finally:
        R.end()
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
    mode = str(status.get("mode") or "solo")
    if mode != "solo":
        import pipeline
        st = pipeline.load_state(pdir)
        bs = [x for x in st.get("sessions") or [] if isinstance(x, dict) and x.get("role") == "builder"]
        built = ", ".join(f"{x.get('segment')} {x.get('state')}" for x in bs)
        lines.append(f"- Pipeline: the planner ({status.get('model_used') or status['model']}, effort `{status['effort']}`) "
                     f"${status.get('cost_usd', 0):.2f}; {len(bs)} builder session{'s' * (len(bs) != 1)} "
                     f"(`{status.get('builder_model')}`, effort `{status.get('builder_effort')}`, up to {status.get('builders')} "
                     f"at once) ${float(status.get('builders_cost_usd') or 0):.2f}" + (f": {built}" if built else ""))
        stg = st.get("stages") or []
        if stg:
            lines.append("- Stages (minutes from the start): " + " · ".join(
                f"{STAGE_NAMES.get(x['stage'], x['stage'])}" + (f" ({x['round']})" if x.get("round") else "")
                + f" {int((x['started'] - status['started']) // 60)}" for x in stg if isinstance(x.get("started"), (int, float))))
        if mode.startswith("solo"):
            lines.append(f"- Switched to solo: {status.get('mode_why')}")
    elif status.get("mode_why") and status.get("mode_why") != "asked for":
        lines.append(f"- Mode: solo ({status['mode_why']})")
    if status.get("critic_model") not in (None, "off"):
        from critic import score_line
        cs = status.get("critiques") or []
        lines.append(f"- Critic: {status['critic_model']} (effort `{status.get('critic_effort')}`): {len(cs)} critique"
                     f"{'' if len(cs) == 1 else 's'}" + (f" (+{status['critic_failed']} that couldn't run)" if status.get("critic_failed") else "")
                     + f", ${status.get('critic_cost_usd', 0):.2f}"
                     + (f"; on this version: {score_line(status.get('version_scores'))} ({status['version_critique']})"
                        if status.get("version_critique") else ""))
    elif "critic_model" in status:
        lines.append("- Critic: off")
    if mode != "solo":
        co = status.get("costs") or {}
        lines.append(f"- Costs by role: planner ${float(co.get('planner') or status.get('cost_usd') or 0):.2f} · builders "
                     f"${float(co.get('builders') or status.get('builders_cost_usd') or 0):.2f} · critic "
                     f"${float(status.get('critic_cost_usd') or 0):.2f}")
    open(rp, "w", encoding="utf-8").write(body + "\n".join(lines) + "\n")
    if v:
        shutil.copyfile(rp, os.path.join(pdir, "final", f"report_v{v['n']}.md"))


def request_stop(pdir):
    os.makedirs(os.path.join(pdir, "work", "director"), exist_ok=True)
    open(os.path.join(pdir, "work", "director", "STOP"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))


def permission_test(pdir, model="haiku", log=print, builder=False):
    """Check the director's sandbox with the real Claude Code (a few cents with Haiku): a project write is allowed,
    a write into the studio's tools and a network command are refused. Returns True when all three hold.
    builder=True checks a pipeline builder's sandbox instead (the project needs a valid work/plan.json)."""
    if builder:
        return builder_permission_test(pdir, model, log)
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


def builder_permission_test(pdir, model="haiku", log=print):
    """A builder's sandbox with the real Claude Code: a write in its own scene folder is allowed; one in the lib, in
    another segment's folder, and to a single project file the deny list names (work/live.json) are refused."""
    import pipeline
    v = pipeline.validate(pdir)
    segs = [it for it in v["items"] if it["kind"] == "segment"]
    if not v["ok"] or len(segs) < 2:
        log("permtest --builder needs a valid work/plan.json with two segments or more (studio.py pipeline validate)")
        return False
    me, other = segs[0]["id"], segs[1]["id"]
    sd = os.path.join(pdir, "work", "pipeline", "permtest")
    cmd = claude_args(pdir, model, "low", deny=pipeline.builder_deny(pdir, v, me), settings=os.path.join(sd, "settings.json"))
    i = cmd.index("--append-system-prompt")
    del cmd[i:i + 2]
    probes = [("mine", os.path.join(pdir, "work", "scenes", me, "_permtest.txt"), True),
              ("lib", os.path.join(pdir, "work", "scenes", "lib", "_permtest.txt"), False),
              ("other", os.path.join(pdir, "work", "scenes", other, "_permtest.txt"), False),
              ("file", os.path.join(pdir, "work", "live.json"), False)]
    keep = open(probes[3][1], "rb").read() if os.path.exists(probes[3][1]) else None
    for _, f, _ in probes:
        if os.path.exists(f):
            os.remove(f)
    steps = " ".join(f"{k + 1}) Use the Write tool to create {rel(f, pdir)} containing ok." for k, (_, f, _) in enumerate(probes))
    msg = (f"This is an authorized test of this session's permission settings; just try each step even if you expect a "
           f"refusal. {steps} Then reply with one line per step: ALLOWED or DENIED.")
    p = subprocess.Popen(cmd, cwd=pdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    p.stdin.write(user_msg(msg))
    p.stdin.flush()
    for line in p.stdout:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        sline = summarize(ev)
        if sline:
            log("   " + sline[:200])
        if ev.get("type") == "result":
            p.stdin.close()
    p.wait(timeout=300)
    ok = True
    for name, f, want in probes:
        there = os.path.exists(f)
        good = there == want
        ok &= good
        log(f"[{'PASS' if good else 'FAIL'}] a builder (of {me}) " + {"mine": "can write in its own scene folder",
                                                                       "lib": "can't write into the lib",
                                                                       "other": f"can't write into {other}'s folder",
                                                                       "file": "can't write a project file on its deny list (work/live.json)"}[name])
        if there:
            os.remove(f)
    if keep is not None:
        open(probes[3][1], "wb").write(keep)
    return ok
