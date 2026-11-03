"""The director, run headless: the story step, the autonomous run with a time budget, versions and delivery.

  studio.py story <p>                     Claude drafts song/song-story.md (read-only tools; the text comes back here)
  studio.py direct <p> --budget 90m ...   the autonomous run: one director (templates/director-brief.md) with full
                                          reign: it builds the video itself, or deploys agents where it wants to
  studio.py direct <p> --revise ...       the review round: templates/revise-brief.md with the user's notes
  studio.py direct <p> --continue ...     carry on the last run in its own conversation (claude --resume), with the
                                          budget it had left (after the usage limit that stopped it has reset)
  studio.py direct <p> --auto-continue    when Claude's usage limit stops the director, wait for the reset (the clock
                                          stopped) and carry on; without, the run ends there
  studio.py clock <p>                     time left in the running budget
  studio.py deliver <p>                   archive final/music-video.mp4 as the next version, run the checks, record it

The autonomous run (checked against `claude --help` of Claude Code 2.1.x):
  claude -p --input-format stream-json --output-format stream-json --verbose --model M --effort E (default max)
         --permission-mode dontAsk --allowedTools Read Write Edit Glob Grep Bash TaskCreate TaskUpdate TaskList TaskGet
         Agent TaskStop [Workflow: --ultracode] --disallowedTools WebFetch WebSearch --settings <deny network commands, the studio's
         folders> --add-dir <studio tools, assets> --append-system-prompt <the studio's ground rules> [--max-budget-usd N]
  cwd = the project folder. The brief goes in as the first stream-json user message; the runner reads every event,
  keeps work/director/run-N/events.jsonl (raw) and status.json (summarised, polled by the web app), and sends more user
  messages: time warnings (half way, final-render time, 5 minutes left, time's up), a nudge when the director stops
  early with time left (use it to improve the video), a reminder when no final exists yet. At the deadline plus a grace
  period (or when work/director/STOP appears, or the cost cap was reached) it ends the session. Afterwards: the cut is
  archived as final/music-video_vN.mp4, the checks run, and report.md gets the run settings (model, effort, budget,
  wall time, the costs per model, the agents, ultracode).
  Agents are the director's choice: the Agent tool runs them (in the background with run_in_background), sharing the
  director's sandbox (verified with `studio.py permtest`: an agent's write into the studio's tools is refused like the
  director's). While they run the session is working even when its own turn has ended (mv/session.py), so the runner
  sends no nudge or reminder and never closes the session then; when they finish the session starts a turn of its
  own. Their lines go into the live log, and status.json lists them (agents) with the costs per model (model_usage).
  The critic (tools/critic.py, a cheaper model with fresh eyes) looks at every new preview in work/previews/ and the
  final while the director works (not twice at one cut: `plan assemble` runs it itself); its notes go to the director
  as messages, the early-finish nudge carries its notes on the final, and the archived version gets its critique
  (final/critic_vN.md).
  The user's live notes (the web app's live view: they play the scenes with the song, and can hear) reach the
  director within seconds, as a message with the song context, and are marked forwarded in review/notes.json.
  The session plumbing is mv/session.py: a session is busy from a message to an idle session until its next result;
  messages sent while it is busy join the running turn (verified on the real CLI), so the studio decides only when a
  turn has ended and nothing of the director's is still running.
  Claude's usage limit (the user's plan) stopping the director mid-run (mv/session.py reads it from Claude Code's own
  messages): with --auto-continue the run pauses, its clock stopped and nothing sent, and once the limit has reset it
  carries on (a message to the same session, or a resumed process of its conversation); without, the run ends there
  and status.json's cut_off says what it had left. `direct --continue` (continuable(): the last run that started)
  carries it on later: claude --resume <its session id> with the studio's "carry on" first, or, when Claude Code can't
  resume it, a fresh session whose brief says where the work is. tools/INTERNALS.md has the details.
"""
import glob, json, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, jload, jsave, song_file, project as load_pj, save_project  # noqa: E402
from mv.claude import claude_bin, claude_print, kill_tree  # noqa: E402
from mv.session import Session, LIMIT_RE, NOT_LIMIT_RE, reset_at, when  # noqa: E402

TEMPLATES = os.path.join(STUDIO, "templates")
SKILL = os.path.join(STUDIO, ".claude", "skills", "musicvideo", "SKILL.md")
# TaskCreate/Update/List/Get: its own to-do list (Claude Code 2.1.312 has these, not TodoWrite). The agent tool is
# "Agent" in --allowedTools (the init event lists it as Task); TaskStop stops one; Workflow (multi-agent workflows)
# only with --ultracode. SendMessage and ListAgents aren't allowed: SendMessage can address the user's other Claude
# sessions on this machine by name, and an unattended director has no business there (it starts and stops its own
# agents; to change one's course it stops it and starts another with a sharper brief).
ALLOWED = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "Agent",
           "TaskStop"]
ULTRACODE = ["Workflow"]
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


def hm(t):
    return time.strftime("%H:%M", time.localtime(t))


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


RUN_FRESH_S = 180                # a runner writes current.json every second: quiet this long, it was cut off
# a run at work: "paused" waits for Claude's usage limit to reset (its clock stopped), "waiting" is a continuing run
# that waits for it before its clock starts
ACTIVE = ("running", "finishing", "paused", "waiting")


def run_active(pdir):
    """The autonomous run going on in this project (its current.json), or None. A run whose runner stopped writing
    (its console closed, the machine slept or restarted, the process killed) is over, whatever its file says."""
    c = current(pdir)
    if not c or c.get("state") not in ACTIVE:
        return None
    try:
        fresh = time.time() - float(c.get("updated") or 0) < RUN_FRESH_S
    except (TypeError, ValueError):
        fresh = False
    return c if fresh else None


def clock(pdir):
    now = time.time()
    c = run_active(pdir)
    if not c:
        import pitch
        r = pitch.round_active(pdir)
        if r:                                    # a pitch round: its directors share one deadline
            left = r["deadline"] - now
            print(f"{time.strftime('%H:%M:%S')}  pitch round {r['round']}: elapsed {fmt_dur(now - r['started'])} of "
                  f"{fmt_dur(r['budget_s'])}; {'LEFT ' + fmt_dur(left) if left > 0 else 'TIME IS UP: finish now'} "
                  f"(deadline {hm(r['deadline'])})")
            return
        c = current(pdir)
        print("no autonomous run is going on (no time budget). " + (f"Last run: {c.get('state')}" if c else "")
              + (" (its runner stopped writing: it was cut off)" if c and c.get("state") in ACTIVE else ""))
        return
    if c.get("state") == "waiting":
        print(f"{time.strftime('%H:%M:%S')}  run {c.get('run')} waits for Claude's usage limit to reset: it starts at "
              f"{hm(c.get('waiting_until') or now)} with {fmt_dur(c.get('budget_s') or 0)}")
        return
    if c.get("state") == "paused":
        p = c.get("paused") or {}
        print(f"{time.strftime('%H:%M:%S')}  PAUSED: Claude's usage limit stopped the director at {hm(p.get('since') or now)}; "
              f"the clock is stopped at {fmt_dur(p.get('left_s') or 0)} left; it carries on at {hm(p.get('until') or now)}")
        return
    left = c["deadline"] - now
    agents = [a for a in c.get("agents") or [] if a.get("state") == "running"]
    active = now - c["started"] - float(c.get("paused_s") or 0)
    print(f"{time.strftime('%H:%M:%S')}  elapsed {fmt_dur(active)} of {fmt_dur(c['budget_s'])}; "
          f"{'LEFT ' + fmt_dur(left) if left > 0 else 'TIME IS UP: finish now'} (deadline {hm(c['deadline'])})"
          + (f"; {len(agents)} agent{'s' * (len(agents) != 1)} running" if agents else ""))


# ------------------------------------------------------------------ the brief
def critic_brief(critic, project, python, tools):
    """The brief's bullet on the studio's critic, after the bullet that ends in {{critic}}; "" when it is off.
    critic = (model, most critiques this run) or None."""
    if not critic:
        return ""
    return (f"\n- The studio's critic: a cheaper model ({critic[0]}) with fresh eyes looks at what you render while you "
            f"work: every full preview you mux into work/previews/preview_N.mp4 and final/music-video.mp4 (up to "
            f"{critic[1]} critiques this run). It sees only the frames, work/concept.md and the song, not your reasoning, "
            f"and its notes arrive as a message from the studio a few minutes later (the idea and whether it reads, then "
            f"craft). The final is looked at once your turn ends or it has held still for a few minutes, not on every "
            f"rewrite. It does "
            f"the looking so your own tokens go into the video: after you mux a full preview, don't run eyes.py look on it "
            f"or open its sheets yourself; carry on with the next thing until its notes arrive, then open only the "
            f"pictures it names that you need to judge yourself (one eyes.py strip or beats for one specific question is "
            f"fine). You're the director: take what's right (what to cut as much as what to add), ignore what's "
            f"wrong. For a second look at one part: `{python} {tools}/studio.py critique {project} <video> --focus \"...\"` "
            f"(2-4 min).")


ULTRACODE_LINE = ("\n- Ultracode is on for this run: you may orchestrate multi-agent workflows with the Workflow tool, by "
                  "the same rules (each agent its model and a narrow brief; the looking isn't theirs).")


def looking(critic, project, python, tools):
    """Who does the looking at renders, for the briefs' agents part: the critic when it's on, else the director."""
    if critic:
        return (f"The looking is the critic's (`{python} {tools}/studio.py critique {project} <video>`), not the agents': "
                f"they don't open frames to judge them.", "the looking stays the critic's")
    return ("The critic is off for this run, so the looking is yours (the eyes tools, one question at a time), not the "
            "agents': they don't open frames to judge them.", "the looking stays yours")


def build_brief(pdir, budget_s, seed_notes="", revise=False, started=None, deadline=None, critic=None, ultracode=False,
                pitch_text="", pitch_ref=None, cont_text=""):
    """The run's brief (templates/director-brief.md, or revise-brief.md for a review round). pitch_text: the short
    section on the pitch the run starts from (pitch.brief_section), "" without one; pitch_ref its "round-K/pN".
    cont_text: a run that carries on earlier work in a fresh session (CONT_BRIEF: where the work is), after the
    user's notes."""
    pj = load_pj(pdir)
    d = delivery(pj)
    sf = song_file(pdir)
    sm = jload(os.path.join(pdir, "song", "song-map.json"), {}) or {}
    tools = rel(os.path.join(STUDIO, "tools"), pdir)
    name = os.path.basename(pdir)
    vals = {"file": os.path.basename(sf), "duration": f"{float(sm.get('duration', 0)):.2f}", "width": d["width"],
            "height": d["height"], "fps": d["fps"], "budget": fmt_dur(budget_s), "tools": tools, "project": name,
            "python": venv_python(pdir), "started": hm(started), "deadline": hm(deadline) + f" (in {fmt_dur(budget_s)})",
            "skill": rel(SKILL, pdir), "anti": rel(os.path.join(os.path.dirname(SKILL), "anti-defaults.md"), pdir),
            "refs": refs_brief(pdir, pj), "ultracode": ULTRACODE_LINE if ultracode else "", "pitch": pitch_text}
    vals["critic"] = critic_brief(critic, name, vals["python"], tools)
    vals["looking"], vals["looking_short"] = looking(critic, name, vals["python"], tools)
    vals["previews"] = ("get the studio's critic's notes on them (below)" if critic else
                        "look at them with the eyes tools")
    notes = user_notes(pj, seed_notes)
    if revise:
        from mv.song import Song
        S = Song(pdir)
        vs = versions(pdir)
        vn = vs[-1]["n"] if vs else 1
        rows = []
        for nt in sorted([n for n in jload(os.path.join(pdir, "review", "notes.json"), []) or []
                          if not n.get("done") and (str(n.get("version", "")).lstrip("v") == str(vn)
                                                    or n.get("version") == "live" and for_this_run(n))],
                         key=lambda n: (n.get("version") == "live", n["t"])):
            a = S.at(float(nt["t"]))
            m, s_ = divmod(float(nt["t"]), 60)
            live = (f" · (in the live view, on {nt['scene']})" if nt.get("scene") else " · (in the live view)") \
                if nt.get("version") == "live" else ""         # left while playing the scenes, and still open
            rows.append(f"- {int(m)}:{s_:05.2f} · {a['bar']}:{a['beat'] if a['beat'] is not None else '-'} · {a['section'] or '-'} · "
                        f"{('“' + a['line'] + '”') if a['line'] else '(no singing)'}{live} · **{nt['text'].strip()}** (id {nt.get('id')})")
        if not rows:
            raise SystemExit(f"no open notes on v{vn}: leave notes on the Watch page (press N) first")
        vals.update(version=vn, notes="\n".join(rows), round=vn + 1, note_done=note_done_cmd(pdir),
                    direction=(("\nMy notes on the song (as before):\n" + notes) if notes else "") + cont_text)
        text = fill(open(os.path.join(TEMPLATES, "revise-brief.md"), encoding="utf-8").read(), vals)
    else:
        vals["direction"] = (("\n" + notes + "\n") if notes else "") + cont_text + live_brief(pdir, pitch_ref)
        text = fill(open(os.path.join(TEMPLATES, "director-brief.md"), encoding="utf-8").read(), vals)
    return text


def note_pitch(nt):
    """The pitch ("round-K/pN") whose scenes a live note was left on (its timeline "pitch:round-K/pN", or a scene in that
    pitch's folder), or None."""
    tl, sc = str(nt.get("timeline") or ""), str(nt.get("scene") or "").replace("\\", "/")
    m = re.fullmatch(r"pitch:(round-\d+/p\d+)", tl) or re.search(r"work/pitches/(round-\d+/p\d+)/", sc)
    return m.group(1) if m else None


def for_this_run(nt, pitch_ref=None):
    """A live note this run's brief takes: not one on another pitch's scenes (from a pitch round: not this video's)."""
    q = note_pitch(nt)
    return q is None or q == pitch_ref


def live_brief(pdir, pitch_ref=None):
    """A first run's brief: the user's live notes still open (left while no run was going, e.g. after a stopped one),
    with the song context and their ids; "" when there are none. Notes on a pitch's scenes go only to the run that
    builds that pitch. A review round lists them with the other notes."""
    open_ = [x for x in open_live_notes(pdir) if for_this_run(x, pitch_ref)]
    return LIVE_BRIEF.format(done=note_done_cmd(pdir), rows="\n".join(live_rows(pdir, open_))) if open_ else ""


def rule_path(p):
    """An absolute path in Claude Code's permission-rule form: //home/me/x, and on Windows //c/Users/me/x (paths are
    matched in POSIX form there). A relative rule (../../tools/**) does not match: tested with Claude Code 2.1.311."""
    q = os.path.abspath(p).replace("\\", "/")
    if re.match(r"^[A-Za-z]:", q):
        q = "/" + q[0].lower() + q[2:]
    return "/" + q


def claude_args(pdir, model, effort, ultracode=False, max_usd=None, extra_deny=(), settings_path=None, system=None,
                resume=None):
    """The stream-json session's command with the director's sandbox (its agents share it): the project is the
    director's, the studio's own folders are read-only, no network. ultracode adds the Workflow tool; max_usd is a
    hard cap on the session's spending (--max-budget-usd). The pitch round's directors get more deny rules (extra_deny:
    everything in the project outside their own pitch folder that matters), their own settings file and system
    prompt. resume: Claude Code's session id of a conversation to carry on (--resume: a run that continues an earlier
    one, or this run's own after its session ended while Claude's usage limit held it; the cwd must be the same)."""
    cl = claude_bin()
    if not cl:
        raise SystemExit("Claude Code isn't installed: https://claude.com/claude-code (or set MV_CLAUDE)")
    deny = [f"Bash({c}:*)" for c in DENY_BASH]
    for d in ("tools", "assets", ".claude", "templates", "app"):             # the studio itself is read-only to the director
        rp = rule_path(os.path.join(STUDIO, d))
        deny += [f"Edit({rp}/**)", f"Write({rp}/**)"]
    settings_d = {"permissions": {"deny": deny + list(extra_deny) + ["WebFetch", "WebSearch"]}}
    # a file, not inline JSON (cmd.exe-safe on Windows)
    sp = settings_path or os.path.join(pdir, "work", "director", "settings.json")
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(settings_d, f, indent=1)
    cmd = [*cl, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED, *(ULTRACODE if ultracode else []),
           "--disallowedTools", *DISALLOWED, "--settings", sp, "--add-dir", os.path.join(STUDIO, "tools"),
           os.path.join(STUDIO, "assets"), os.path.join(STUDIO, ".claude", "skills"), os.path.join(STUDIO, "templates"),
           "--strict-mcp-config", "--append-system-prompt", system or SYSTEM_APPEND]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if max_usd:
        cmd += ["--max-budget-usd", f"{float(max_usd):g}"]
    if resume:
        cmd += ["--resume", str(resume)]
    return cmd


# ------------------------------------------------------------------ the critic during a run
FINAL_SETTLE_S = 3 * 60            # the final held still this long (or the director's turn ended): it's ready to critique
CRITIC_INTRO = ("The studio's critic ({model}, fresh eyes: it saw only the frames, your concept and the song, not your "
                "reasoning) looked at {video}. You're the director: take what's right, ignore what's wrong, and note in "
                "work/log.md what you took and why. You don't have to act on every point before your next cut.")


def since(at, started):
    """A critique's "at" (local time, "%Y-%m-%d %H:%M:%S") in seconds from the run's start, or None."""
    try:
        return round(time.mktime(time.strptime(str(at), "%Y-%m-%d %H:%M:%S")) - started)
    except (TypeError, ValueError, OverflowError):
        return None


class CriticWatcher:
    """The studio's critic during an autonomous run. Every ~5 s it looks at work/previews/*.mp4 and
    final/music-video.mp4 changed since the run started; a file whose size held still over two looks is ready (a
    .part / .tmp file never is). A cut that has a critique already (critic.find: the path, size and mtime) or one
    being made (critic.is_pending: `plan assemble` marks its cut before its eyes and checks, critic.py while it
    works) isn't critiqued again: the director has those notes as its command's output (a finished one is handed to
    the runner all the same, marked "seen", for the run's record). One critique at a time, in a background thread
    (critic.py with the venv python). A ready final goes first (it's the cut that gets delivered, and the early-finish
    nudge waits for its notes): a preview critique still running when a new final is ready is stopped for it (its
    slot is given back). Of several ready previews only the newest; none older than a ready final, or than a cut
    critiqued (or being critiqued) elsewhere. At most `most` critiques per run, the last one kept for a final; no
    preview critique when its notes couldn't reach the director in time (`min_left`), and nothing new once the run is
    stopping. Finished critiques wait in take() for the runner; the first preview passed over because the critiques
    are used up waits in passed() (the director is told once: it isn't looking at full previews itself). A critique
    Claude's usage limit refused (the critic and the director share the user's plan) gives its slot back and its cut
    is looked at again once the limit has reset (nothing new starts before; "limit": True on its entry).
    The final is rewritten in place, often minutes apart while the director iterates: it is critiqued once the
    director says it's ready (its turn ended: `idle`), or once it has held still for `settle` s, not on every write
    (late in the run, when waiting would keep its notes from reaching the director, at once)."""

    def __init__(self, pdir, started, model, effort, most=6, min_left=6 * 60, settle=FINAL_SETTLE_S):
        self.pdir, self.started, self.model, self.effort, self.most = pdir, started, model, effort, most
        self.min_left, self.settle = min_left, settle
        self.fin = os.path.join(pdir, "final", "music-video.mp4")
        self.seen, self.handled, self.finished = {}, set(), []
        self.since = {}                         # path -> when the poll first saw its current size + mtime
        self.job, self.n, self.last = None, 0, 0.0
        self.hold_until = 0.0                   # Claude's usage limit refused a critique: nothing new before this
        self.done_at = 0.0                      # the newest cut critiqued elsewhere (its mtime)
        self.skipped, self.told = [], False     # a preview passed over for want of critiques left (said once)
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

    def elsewhere(self, f, k):
        """This cut's critique when it has one already, "coming" when one is being made (not by this watcher), or
        None."""
        import critic
        v = rel(f, self.pdir)
        return critic.find(self.pdir, v, k[1], k[2]) or ("coming" if critic.is_pending(self.pdir, v, k[1], k[2]) else None)

    def settled(self, f, k, now, left=None, idle=False):
        """A cut is ready for an automatic critique: a preview once its size held still over two looks; the final
        once the director's turn is over, or it has held still for `settle` s, or the run is too near its end to wait."""
        if not self.is_final(k) or idle or not self.settle:
            return True
        if left is not None and left < self.min_left + self.settle:
            return True
        return now - self.since.get(f, now) >= self.settle

    def poll(self, now, left=None, stopping=False, idle=False):
        if now - self.last < 5:
            return
        self.last = now
        ready, coming = [], [0.0]
        for f in glob.glob(os.path.join(self.pdir, "work", "previews", "*.mp4")) + [self.fin]:
            k = self.key(f)
            if not k or not k[1] or k[2] <= self.started or re.search(r"\.(part|tmp)\b", os.path.basename(f)):
                continue
            was, self.seen[f] = self.seen.get(f), k
            if was != k:
                self.since[f] = now
            if was == k and k not in self.handled and self.settled(f, k, now, left, idle):
                there = self.elsewhere(f, k)
                if isinstance(there, dict):         # (plan assemble critiqued it: the director has its report)
                    self.handled.add(k)
                    self.done_at = max(self.done_at, k[2])
                    with self.lock:
                        self.finished.append(dict(there, key=k, t=since(there.get("at"), self.started), seen=True))
                elif there:
                    coming.append(k[2])
                else:
                    ready.append(k)
        if stopping or not ready or now < self.hold_until:
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
            cut, newest = max(fins[-1][2] if fins else 0, self.done_at, max(coming)), prevs[-1:]
            # older previews are passed over: the newest counts, and none older than a ready final or than a cut
            # critiqued (or being critiqued) elsewhere
            self.handled.update(prevs[:-1])
            self.handled.update(k for k in newest if k[2] <= cut)
            prevs = [k for k in newest if k[2] > cut]
            if prevs and (self.n >= self.most - 1 or (left is not None and left < self.min_left)):
                if self.n >= self.most - 1 and not self.told:
                    self.told = True
                    self.skipped.append(rel(prevs[-1][0], self.pdir))
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
        limit = not c and is_limit(tail[0])
        with self.lock:
            if limit and not job.get("superseded"):     # its slot back; its cut again after the reset
                self.n -= 1
                self.handled.discard(job["key"])
                r = reset_at(tail[0])
                self.hold_until = max(time.time() + 60, (r + RESET_MARGIN_S) if r else time.time() + RETRY_S)
            if not job.get("superseded"):           # a stopped preview critique (a new final came) is dropped
                self.finished.append(dict(c, key=job["key"], t=round(time.time() - self.started)) if c else
                                     {"video": job["video"], "error": tail[0][:300], "cost_usd": critic.spent(out), "limit": limit})
            if self.job is job:
                self.job = None

    def take(self):
        """The critiques finished since the last call (an {"video", "error", "cost_usd"} for one that couldn't run;
        "seen": True on one made elsewhere, whose notes the director has had)."""
        with self.lock:
            out, self.finished = self.finished, []
        return out

    def passed(self):
        """The preview passed over because the critiques were used up (once a run), or []."""
        with self.lock:
            out, self.skipped = self.skipped, []
        return out

    def newer(self, k):
        """The cuts (paths relative to the project) written after the cut with key k: the same file rewritten, or
        another preview or final made since."""
        out = []
        for f in glob.glob(os.path.join(self.pdir, "work", "previews", "*.mp4")) + [self.fin]:
            k2 = self.key(f)
            if k2 and k2 != k and k2[2] > k[2] and k2[2] > self.started:
                out.append(rel(f, self.pdir))
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


CRITIC_STALE = ("\n**These notes are on an older cut**: you've written {newer} since it was made. Skip what you have "
                "changed since; take what still applies to the current cut.")


def critic_message(c, most=6000, newer=None):
    """The critic's report as a message to the director; `newer`: the cuts written since the one it looked at."""
    rep = (c.get("report") or "").strip()
    if len(rep) > most:
        rep = rep[:most].rsplit("\n", 1)[0] + "\n[... the rest is in the file]"
    pics = critic_pictures(c)
    return CRITIC_INTRO.format(model=c.get("model") or "the critic", video=c["video"]) + \
        f" (Its report is also in {c.get('file')}.)" + (CRITIC_STALE.format(newer=", ".join(newer)) if newer else "") \
        + (f"\n{pics}" if pics else "") + "\n\n" + rep


CRITIC_USED_UP = ("The studio's critic has used this run's preview critiques ({n}; the last one is kept for the final), so "
                  "it won't look at {video} or a later preview. When you need eyes on one: `{python} {tools}/studio.py "
                  "critique {project} <video> --focus \"...\"` (2-4 min), or the eyes tools yourself, one question at a "
                  "time.")


NUDGE_HEAD = ("You delivered a cut with {left} still on the clock: spend it making the video better. Start with what "
              "the critic says keeps the idea from landing (where it is too busy to read as much as where it is flat), "
              "then its worst problems; better can mean fewer things on screen. ")
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
            + (f"\nAmbition: {amb[:1500]}\n" if amb else "") + (f"\nWhere it is flat or busy:\n{safe}\n" if safe else "")
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
    return (f"You delivered a cut with {fmt_dur(left)} still on the clock. Spend it making the video better: "
            f"where is it too busy to read, where is it flat, which ★★★ moment doesn't land, what would make your "
            f"concept clearer? {look}" + NUDGE_TAIL)


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


# ------------------------------------------------------------------ the user's live notes during a run
LIVE_MSG = ("The user is playing your scenes live in the web app, with the song (they can hear; you can't), and left "
            "{what} (song time · bar:beat · section · what is sung there · the plan's segment there, if you keep a "
            "work/plan.json · the scene they saw · the note):\n{rows}\n"
            "Treat {them}: find the root cause and fix the film, not the frame. Where a note asks for more, go further "
            "than it asks; never answer one by making the film safer. Mark each one done with a one-line fix, what you "
            "changed: `{done}` (not by editing review/notes.json: the user may be adding notes to it right now). Then "
            "carry on. The live view plays your scenes as they are on disk: they see a change as soon as you save it (no "
            "render needed); the previews and the final still need their renders.")
LIVE_BRIEF = ("\nMy notes from playing the scenes live in the web app since the last run (on the scenes as they were then, "
              "still open: take them where they still apply; mark each one done with `{done}`):\n{rows}\n")


def note_done_cmd(pdir):
    """The command a director marks a note done with (studio.py note-done: the web app writes review/notes.json too)."""
    return (f"{venv_python(pdir)} {rel(os.path.join(STUDIO, 'tools'), pdir)}/studio.py note-done {os.path.basename(pdir)} "
            f"<id> \"<what you changed>\"")


def open_live_notes(pdir):
    """The user's live notes that are still open (not done), for a run's brief: [] when there are none (or the file
    can't be read)."""
    from mv.live import live_notes
    try:
        return [x for x in live_notes(pdir) if not x.get("done") and str(x.get("text") or "").strip()]
    except Exception:
        return []


def live_rows(pdir, notes):
    """The user's live notes as lines with the song context: song time · bar:beat · section · what is sung there ·
    the plan's segment at that time (when there is a work/plan.json) · the scene they saw · the note."""
    try:
        from mv.song import Song
        S = Song(pdir)
    except Exception:                            # no song folder: the times alone
        S = None
    plan = jload(os.path.join(pdir, "work", "plan.json"), None)
    plan = plan if isinstance(plan, dict) else {}
    segs = [x for x in plan.get("segments") or [] if isinstance(x, dict) and isinstance(x.get("from"), (int, float))
            and isinstance(x.get("to"), (int, float))]
    rows = []
    for nt in sorted(notes, key=lambda x: float(x.get("t") or 0)):
        t = float(nt.get("t") or 0)
        m, s_ = divmod(t, 60)
        parts = [f"{int(m)}:{s_:05.2f}"]
        a = S.at(t) if S else None
        if a:
            parts += [f"{a['bar']}:{a['beat']}" if a["bar"] is not None else "-", a["section"] or "-",
                      f"“{a['line']}”" if a["line"] else "(no singing)"]
        seg = next((x for x in segs if x["from"] <= t < x["to"]), None)
        if seg:
            parts.append(f"segment {seg.get('id')}" + (f" ({seg['name']})" if seg.get("name") else ""))
        q = note_pitch(nt)
        if q:
            parts.append(f"on pitch {q}'s scenes, from the pitch round")
        elif nt.get("timeline") and not str(nt["timeline"]).startswith("scene:"):
            parts.append(f"in the timeline {nt['timeline']}")
        if nt.get("scene"):
            parts.append(f"they saw {nt['scene']}")
        rows.append("- " + " · ".join(parts) + f" · **{str(nt.get('text') or '').strip()}** (id {nt.get('id')})")
    return rows


class LiveNotes:
    """The user's notes from the web app's live view during a run (review/notes.json, "version": "live"): the ones made
    after the run started, not done and not forwarded yet. Every ~5 s the runner sends them to the director when a
    message can go (Runner.live_step), with the song context, and marks them "forwarded_run": n (mv/live.py
    update_notes: a lock, a re-read, the change by id, an atomic replace; the web app writes the file too)."""

    def __init__(self, pdir, n, started):
        self.pdir, self.n, self.started = pdir, n, started
        self.t = 0.0

    def made_in_run(self, x):
        try:
            return float(x.get("id") or 0) / 1000 >= self.started - 1          # (its id is when it was made, in ms)
        except (TypeError, ValueError):
            return False

    def pending(self):
        from mv.live import read_notes
        try:
            notes = read_notes(self.pdir)
        except RuntimeError:                     # being written, or broken: next time
            return []
        return [x for x in notes if isinstance(x, dict) and x.get("version") == "live" and not x.get("done")
                and not x.get("forwarded_run") and self.made_in_run(x)]

    def mark(self, notes, undo=False):
        from mv.live import update_notes
        ids, at = {x.get("id") for x in notes}, time.strftime("%Y-%m-%d %H:%M:%S")

        def fn(ns):
            for x in ns:
                if isinstance(x, dict) and x.get("id") in ids:
                    if undo:
                        x.pop("forwarded_run", None)
                        x.pop("forwarded_at", None)
                    else:
                        x.update(forwarded_run=self.n, forwarded_at=at)
        update_notes(self.pdir, fn)


# ------------------------------------------------------------------ the autonomous run
HUNG_S = 25 * 60                 # working with no events this long: said in the live log (a long command makes none either)
STOP_MSG = ("The user stopped this run. Stop now: don't start anything new, and stop any agent of yours that is still "
            "running (TaskStop). If final/music-video.mp4 exists, write report.md in a few lines and end.")
# Claude's usage limit (the user's plan) stopping the director mid-run. With auto_continue the run waits, its clock
# stopped, and carries on by itself once the limit has reset; without, it ends there (direct --continue carries it
# on later, in the same conversation). A request the limit refuses costs nothing, so trying again is free.
MAX_WAIT_S = 24 * 3600           # a reset further off than this isn't waited for: the run ends (continue it then)
RESET_MARGIN_S = 60              # carry on this long after the reset it gives (the message says it to the minute)
RETRY_S = 15 * 60                # it gives no reset (or the limit still holds past it): try again this often
RESUME_WAIT_S = 60               # claude --resume: a process that ends before its first turn couldn't resume it
CONTINUE_MIN_S = 15 * 60         # a continuing run gets at least this much by default
RESUME_MSG = ("Claude's usage limit stopped you at {at}; it has reset now ({waited} later): carry on where you were. The "
              "run's clock was stopped while you waited, so you still have {left}, and the deadline is now **{dl}** "
              "(`{clock}` prints the time left). An agent of yours that was running then was stopped by the limit, and a "
              "background command may have finished meanwhile: check what each left before you start it again, and "
              "don't redo what's done.")
CONTINUE_MSG = ("The studio again. Your run {prev} ended at {at}: {why}. This is run {n}, and it carries on from there in "
                "this same conversation, so what you knew then is still yours. Anything that was running when it ended "
                "(an agent of yours, a render, a background command) was stopped: check what each left on disk before you "
                "start it again (renders resume from the frames already there), and don't redo what's done.\n\n"
                "The new clock: you have {budget}; the deadline is **{deadline}** (`{clock}` prints the time left). The "
                "studio messages you as time runs down, as before, and stops the session a few minutes after the "
                "deadline. {critic}\n{notes}\nCarry on where you were: say in one line where you pick up, then deliver "
                "final/music-video.mp4, run the checks and write report.md before the deadline.")
CONT_BRIEF = ("\nThis carries on {what}. Everything made so far is in this folder: your log (work/log.md), the "
              "concept, the scenes, the plan, the renders and previews, the critic's notes (work/critic/). Don't start "
              "over: read work/log.md first and look at what's there, then carry on from where it stopped (say in one line "
              "where you pick up). Keep its concept unless it really doesn't work. Anything that was running then (an "
              "agent, a render, a background command) was stopped: renders resume from the frames already on disk.\n")


def is_limit(text):
    """Claude Code's own words for its usage limit (mv/session.py LIMIT_RE), not a temporary server throttle."""
    return bool(LIMIT_RE.search(str(text or "")) and not NOT_LIMIT_RE.search(str(text or "")))


def clock_cmd(pdir):
    return f"{venv_python(pdir)} {rel(os.path.join(STUDIO, 'tools'), pdir)}/studio.py clock {os.path.basename(pdir)}"


class Runner:
    """One autonomous run around one Claude Code session (mv/session.py): the director, with full reign. It makes the
    whole video, and deploys agents if it wants to (they run in its session, in the background); the critic watcher
    looks at its previews and its final. work/director/run-N/ has brief.md, command.json, events.jsonl, stderr.log,
    messages.md and status.json (current.json for `clock` and `stop`). The runner keeps the time warnings, STOP, the
    deadline + grace, the cost cap, the early-finish nudge and the "no final yet" reminder (only when the director's
    turn is over and none of its agents is running), and the end: the cut archived as the next version, the checks,
    report.md's run settings, the critic's notes on the version.
    Claude's usage limit (mv/session.py counts each time Claude Code says it stopped a turn): with auto_continue the
    run pauses ("paused": its clock stopped, nothing sent, the critic and the live notes held) until the limit has
    reset, then carries on: a message to the same session, or, when that session ended meanwhile, a new process of
    its conversation (claude --resume, through new_session; when that can't be resumed, a fresh session with
    fallback(why), a brief that says where the work is). A reset more than MAX_WAIT_S away isn't waited for. Without
    auto_continue (or too far) the run ends there, and cut_off says when, why and how much of the budget was left
    (`direct --continue` carries it on). new_session(resume_id) -> a Session not started yet; launch() starts one."""

    def __init__(self, pdir, n, rdir, status, session, budget_s, started, nudge=True, critic=None, st=None, log=print,
                 auto_continue=False, new_session=None, fallback=None):
        self.pdir, self.n, self.rdir, self.status, self.s = pdir, n, rdir, status, session
        self.ddir = os.path.join(pdir, "work", "director")
        self.fin = os.path.join(pdir, "final", "music-video.mp4")
        self.budget_s, self.started, self.deadline = budget_s, started, started + budget_s
        self.nudge, self.st, self.log = nudge, st, log
        self.lock = session.lock if session else threading.RLock()
        self.auto, self.new_session, self.fallback = bool(auto_continue), new_session, fallback
        self.pause = None                        # waiting for Claude's usage limit to reset: {since, resets_at, retry_at, ...}
        self.paused_s = 0.0                      # how long the run waited for it (its clock stopped)
        self.limits_seen = 0                     # the limit hits the runner has dealt with (status limit_hits)
        self.cut = None                          # what ended the run early: {why, at, left_s, resets_at, text}
        status.setdefault("pauses", [])
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
        self.capped = False                      # the session reached its cost cap (--max-budget-usd)
        self.end_reason = "the director ended the session"
        self.live = LiveNotes(pdir, n, started)

    # ------------------------------------------------------------------ bookkeeping
    def save(self):
        status = self.status
        now = time.time()
        status["updated"] = now
        status["elapsed_s"] = round(now - self.started)
        status["active_s"] = round(self.active(now))
        status["final_exists"] = os.path.exists(self.fin)
        jsave(os.path.join(self.rdir, "status.json"), status)
        jsave(os.path.join(self.ddir, "current.json"), {k: v for k, v in status.items() if k != "events"} | {"dir": rel(self.rdir, self.pdir)})

    def note(self, line):
        if self.s is not None:
            self.s.note(line)
        else:                                    # (before its first session started)
            self.status.setdefault("events", []).append({"t": round(time.time() - self.started), "text": line})

    def send(self, text, why):
        return self.s.send(text, why)

    def active(self, now):
        """The run's own time: the wall clock without the waits for Claude's usage limit."""
        return now - self.started - self.paused_s - (now - self.pause["since"] if self.pause else 0)

    def left(self, now):
        """The time left on the run's clock (stopped while it waits for the limit)."""
        return self.deadline - (self.pause["since"] if self.pause else now)

    def record(self, c, used="waiting"):
        """A finished critique into status.json (with how it reached the director: `used`); one that couldn't run
        only into the live log, its cost (a cap hit, a refused call) into the critic's cost all the same."""
        import critic as crit
        with self.lock:
            self.status["critic_cost_usd"] = round(self.status["critic_cost_usd"] + (c.get("cost_usd") or 0), 4)
            if c.get("error"):
                self.status["critic_failed"] = self.status.get("critic_failed", 0) + 1
                self.note(f"🔍 the critic couldn't look at {c['video']}: {c['error']}"
                          + (" (Claude's usage limit: it looks again once the limit has reset)" if c.get("limit") else ""))
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
                self.live_step(now)
            except Exception as ex:
                self.trouble("passing on the user's live notes", ex)
            try:
                if not self.step(now):
                    break
            except Exception as ex:              # a bug or a file in the way: noted, and the run goes on
                self.trouble("a step of the run", ex)
                if now > self.deadline + self.grace + (now - self.pause["since"] if self.pause else 0):
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

    # ------------------------------------------------------------------ the user's live notes
    def live_step(self, now):
        """Every ~5 s: the user's live notes (the web app's live view: they play the scenes with the song, and can
        hear) go to the director as one message, when one can go (live_may_send); then they are marked forwarded."""
        L = self.live
        if now - L.t < 5:
            return
        L.t = now
        notes = L.pending()
        if not notes or not self.live_may_send(now):
            return
        many = len(notes) > 1
        L.mark(notes)                            # first: the director may read the file as soon as the message is in
        if not self.send(self.live_message(notes), f"the user's live note{'s' if many else ''} ({len(notes)})"):
            L.mark(notes, undo=True)
        else:
            self.note(f"🎧 the user's live note{'s' if many else ''} to the {self.s.name}: " + "; ".join(
                f"{int(float(x.get('t') or 0) // 60)}:{float(x.get('t') or 0) % 60:04.1f} {str(x.get('text') or '')[:70]}" for x in notes))

    def live_may_send(self, now):
        """A message can go: the session is there and open, the run isn't stopping, and there's time to act on it."""
        s = self.s
        return s.alive() and not s.closed and self.stop_reason is None and self.pause is None and self.deadline - now > 120

    def live_message(self, notes):
        n = len(notes)
        return LIVE_MSG.format(what="a note" if n == 1 else f"{n} notes", rows="\n".join(live_rows(self.pdir, notes)),
                               them="it like a review note" if n == 1 else "them like review notes", done=note_done_cmd(self.pdir))

    # ------------------------------------------------------------------ every second
    def check_stop(self, now):
        if self.stop_reason is None and os.path.exists(os.path.join(self.ddir, "STOP")):
            self.stop_reason = "stopped by the user"
            self.cut = self.cut or {"why": "stopped", "at": now, "left_s": round(self.left(now))}
            if not self.pause:                   # (waiting for the limit, it can't answer)
                self.send(STOP_MSG, "stop")
            self.deadline = min(self.deadline, now)
            self.grace = 60

    def check_cap(self, now):
        """The session reached its cost cap (--max-budget-usd): it can't spend more, so the run ends with what exists
        (no more messages; the session is closed once its turn is over)."""
        if not self.capped and self.status.get("budget_hit"):
            self.capped = True
            self.stop_reason = self.stop_reason or f"the cost cap (${float(self.status.get('max_usd') or 0):g}) was reached"
            self.cut = self.cut or {"why": "cost cap", "at": now, "left_s": round(self.left(now))}
            self.deadline = min(self.deadline, now)
            self.grace = 60
            self.warned.add("up")

    # ------------------------------------------------------------------ Claude's usage limit
    def check_limit(self, now):
        """Claude Code said its usage limit stopped a turn (the session counts each: limit_hits): with auto_continue
        the run pauses until it resets (a reset more than MAX_WAIT_S away isn't waited for), else it ends here. A run
        that is ending anyway (the user, the cap, its time up) just ends."""
        hits = int(self.status.get("limit_hits") or 0)
        if hits <= self.limits_seen:
            return
        self.limits_seen = hits
        lim = dict(self.status.get("limit") or {})
        r = lim.get("resets_at")
        if self.pause:                           # again while it waits (its agents ending, a turn of its own)
            if r and r > (self.pause.get("resets_at") or 0):
                self.pause.update(resets_at=r, retry_at=max(self.pause["retry_at"], r + RESET_MARGIN_S))
                self.status["paused"].update(resets_at=r, until=self.pause["retry_at"])
            return
        left = self.deadline - now
        if self.stop_reason is not None or left <= 60:
            return
        far = r is not None and r - now > MAX_WAIT_S
        if self.auto and not far:
            return self.start_pause(now, lim)
        self.stop_reason = ("Claude's usage limit was reached" + (f" (it resets {when(r, now)})" if r else "")
                            + ("; the reset is more than a day away, too far to wait for" if self.auto else ""))
        self.cut = {"why": "limit", "at": now, "left_s": round(left), "resets_at": r, "text": lim.get("text")}
        self.deadline, self.grace = now, 60
        self.warned.add("up")                    # (no "time's up" message: it couldn't answer)
        self.status["state"] = "finishing"
        self.note(f"⛔ the run ends here with {fmt_dur(left)} of its budget left: Claude can't work until the limit "
                  f"resets. Continue the last run (the Direct page, or `studio.py direct {os.path.basename(self.pdir)} "
                  f"--continue`) carries it on from where it stopped, in the same conversation")

    def start_pause(self, now, lim):
        r = lim.get("resets_at")
        retry = r + RESET_MARGIN_S if r and r + RESET_MARGIN_S > now else now + (RETRY_S if not r else RETRY_S // 3)
        left = self.deadline - now
        self.pause = {"since": now, "resets_at": r, "retry_at": retry, "text": lim.get("text"), "left_s": round(left), "tries": 1}
        self.status["state"] = "paused"
        self.status["paused"] = {"since": now, "resets_at": r, "until": retry, "left_s": round(left), "text": lim.get("text")}
        self.note(f"⏸ the run waits for the limit to reset, its clock stopped at {fmt_dur(left)} left; it carries on by "
                  f"itself at {when(retry, now)}" + ("" if r else f" (no reset time given: it tries every {RETRY_S // 60} min)"))

    def paused_step(self, now):
        """A second of waiting for the limit: nothing goes to the director; the critic's finished notes wait; at the
        retry time it carries on (a limit that still holds pauses it again, from its next refusal)."""
        p = self.pause
        if self.stop_reason is not None:         # stopped while it waited: it ends (it couldn't answer)
            self.unpause(now, "stopped")
            return False
        if self.critics:
            for c in self.critics.take():
                self.record(c, "the director ran it" if c.get("seen") else "waiting")
                if not c.get("error") and not c.get("seen"):
                    self.unsent.append(c)
        if now - p["since"] > MAX_WAIT_S + 3600:     # refused for more than a day: the run ends
            self.cut = {"why": "limit", "at": p["since"], "left_s": p["left_s"], "resets_at": None, "text": p["text"]}
            self.unpause(now, "gave up")
            self.stop_reason = "Claude's usage limit still held after a day of waiting"
            self.deadline, self.grace = now, 60
            self.warned.add("up")
            return self.s.alive()
        if now < p["retry_at"]:
            return True
        self.unpause(now, "carried on")
        self.carry_on(now, p)
        return True

    def unpause(self, now, how):
        p, waited = self.pause, now - self.pause["since"]
        self.pause = None
        self.paused_s += waited
        if how == "carried on":                  # the clock runs again from where it stopped
            self.deadline += waited
        with self.lock:
            self.status.update(state="running" if how == "carried on" else "finishing", deadline=self.deadline,
                               paused_s=round(self.paused_s))
            self.status.pop("paused", None)
            self.status["pauses"].append({"t": round(p["since"] - self.started), "s": round(waited), "resets_at": p["resets_at"],
                                          "text": p["text"], "end": how})

    def carry_on(self, now, p):
        """The limit has reset: the same session gets a message; one that ended meanwhile is resumed (launch)."""
        left = self.deadline - now
        msg = RESUME_MSG.format(at=hm(p["since"]), waited=fmt_dur(now - p["since"]), left=fmt_dur(left),
                                dl=hm(self.deadline), clock=clock_cmd(self.pdir))
        why = "the usage limit reset: carry on"
        s = self.s
        if s.alive() and not s.closed:
            s.ack()                              # (the turn the limit ended needs nothing more)
            if self.send(msg, why):
                self.note(f"▶ the usage limit has reset: the director carries on ({fmt_dur(left)} left, deadline now {hm(self.deadline)})")
                return
        sid = self.status.get("session_id")
        fb = (lambda: self.fallback(f"this run (run {self.n}): Claude's usage limit stopped its director at "
                                    f"{hm(p['since'])}, and its conversation couldn't be brought back",
                                    self.deadline - time.time(), self.deadline)) if self.fallback else None
        self.note("▶ the usage limit has reset; the director's session had ended meanwhile: the studio "
                  + ("resumes its conversation" if sid else "starts a fresh one, told where the work is"))
        ok = self.launch(msg, why, resume=sid, fallback=fb) if sid else \
            bool(fb) and self.launch(fb(), "the brief (a fresh session: there was no conversation to resume)")
        if not ok:
            self.stop_reason = self.stop_reason or "the director's session couldn't be started again after the usage limit"

    def launch(self, text, why, resume=None, fallback=None):
        """A new director session for this run (new_session), `text` its first message: with resume, a new process of
        that conversation (claude --resume <id>). One that ends before its first turn couldn't be resumed (no such
        conversation here): then a fresh session with fallback() (a brief that says where the work is), if there is
        one. False when nothing could start."""
        if not self.new_session:
            return False
        for rid in ([resume] if resume else []) + [None]:
            first = text if rid or not resume else (fallback() if fallback else None)
            if first is None:
                return False
            s = self.new_session(rid)
            try:
                s.start()
            except Exception as ex:
                self.note(f"⚠ Claude Code couldn't start: {type(ex).__name__}: {ex}")
                return False
            old, self.s, self.lock = self.s, s, s.lock
            if old is not None and old is not s:
                try:
                    old.finish()
                except Exception:
                    pass
            if rid is None and resume:
                open(os.path.join(self.rdir, "brief.md"), "w", encoding="utf-8").write(first)
            s.send(first, why if rid or not resume else "the brief (a fresh session: the conversation couldn't be resumed)")
            if not rid:
                return True
            t0 = time.time()
            while time.time() - t0 < RESUME_WAIT_S and s.alive() and not s.inits:
                time.sleep(0.5)
                try:
                    with self.lock:
                        self.save()
                except Exception:
                    pass
            if s.inits or s.alive():
                return True
            err = ""
            try:
                err = [x for x in open(os.path.join(self.rdir, "stderr.log"), encoding="utf-8", errors="replace").read().splitlines()
                       if x.strip()][-1][:200]
            except (OSError, IndexError):
                pass
            self.note(f"⚠ Claude Code couldn't resume the conversation{(': ' + err) if err else ''}"
                      + (": a fresh session instead, its brief says where the work is" if fallback else ""))
        return False

    def check_hung(self, now):
        if not self.hung_said and self.s.hung(now, HUNG_S):
            self.hung_said = True
            self.note(f"⚠ no sign of life from the {self.s.name} or its agents for {HUNG_S // 60} min (a long command, or a "
                      f"hang: the deadline ends it either way)")
        elif self.hung_said and not self.s.working:
            self.hung_said = False

    def warnings(self, now, left):
        for frac, key, msg in ((0.5, "half", "Half of your time is gone ({left} left, deadline {dl}). Check your plan against the clock."),):
            if key not in self.warned and self.active(now) >= frac * self.budget_s and left > 0:
                self.warned.add(key)
                self.send(msg.format(left=fmt_dur(left), dl=hm(self.deadline)), key)
        final_at = max(10 * 60, 0.25 * self.budget_s)
        if "final" not in self.warned and left <= final_at and left > 5 * 60:
            self.warned.add("final")
            self.send(f"{fmt_dur(left)} left. If the full-size final isn't rendering yet, start it now (it takes the time "
                      f"tools/speeds.json says), then compose, mux, run the checks and write report.md.", "final-render time")
        if "five" not in self.warned and 0 < left <= 5 * 60:
            self.warned.add("five")
            self.send("5 minutes left: finish the final, run the checks, write report.md. Nothing new.", "5 minutes")
        if "up" not in self.warned and left <= 0:
            self.warned.add("up")
            self.status["state"] = "finishing"
            self.send(f"Time is up. Stop working on the video now, and stop any agent of yours that is still running "
                      f"(TaskStop). If final/music-video.mp4 exists, make sure report.md is written (a few lines are fine) "
                      f"and end. You have {fmt_dur(self.grace)} before the session is closed.", "time's up")

    def step(self, now):
        """One second of the run: the director makes the video (with agents if it wants); the studio warns, reminds,
        nudges, and passes on the critic's notes. Decisions wait for the director's turn to end with none of its
        agents running (mv/session.py: a message sent while it works joins the running turn; while its agents run it
        is working even between its turns)."""
        import critic as crit
        left = self.deadline - now
        self.check_stop(now)
        self.check_cap(now)
        self.check_limit(now)
        if self.pause:
            return self.paused_step(now)
        if self.capped or (self.cut or {}).get("why") == "limit":     # (it can't answer: no time warnings)
            self.status["state"] = "finishing"
            left = min(left, self.deadline - now)
        else:
            self.warnings(now, left)
        if left <= -self.grace:
            self.stop_reason = self.stop_reason or "budget ran out (deadline + grace)"
            return False
        self.check_hung(now)
        s, critics = self.s, self.critics
        if critics:
            critics.poll(now, left, stopping=self.stop_reason is not None, idle=s.alive() and not s.working)
            for c in critics.take():
                self.record(c, "the director ran it" if c.get("seen") else "waiting")
                if not c.get("error") and not c.get("seen"):
                    self.unsent.append(c)
            for v in critics.passed():                     # (it was told not to look at full previews itself)
                self.note(f"🔍 the critic passes over {v}: its preview critiques for this run are used up")
                if not s.closed and left > 120 and self.stop_reason is None and s.alive():
                    self.send(CRITIC_USED_UP.format(n=critics.n, video=v, python=venv_python(self.pdir),
                                                    tools=rel(os.path.join(STUDIO, "tools"), self.pdir),
                                                    project=os.path.basename(self.pdir)), "critic: no more previews")
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
                if self.stop_reason is not None:
                    s.close_input()
                elif left > 60 and not have_final and self.reminders < 3:
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
            newer = critics.newer(c["key"]) if critics and c.get("key") else []
            if c.get("key") and CriticWatcher.key(c["key"][0]) != c["key"] and critics.is_final(c["key"]) \
                    and critics.pending_final():
                # the final changed again and its own critique is coming: these notes would only be stale
                self.note(f"🔍 the critic's notes on an older final are dropped: a critique of the current one is coming")
                self.used(c, "superseded")
            elif not s.closed and left > 120 and self.stop_reason is None and s.alive():
                self.send(critic_message(c, newer=newer), f"critic: {c['video']}" + (" (an older cut)" if newer else ""))
                self.used(c, "sent, on an older cut" if newer else "sent")
            else:
                self.used(c, "too late for the director")
        return s.alive()

    # ------------------------------------------------------------------ the end
    def end(self):
        s, status = self.s, self.status
        if self.pause:
            self.unpause(time.time(), "ended")
        if s is not None and s.alive():
            s.stop(self.stop_reason or "the run ended")
        user_stop = bool(self.stop_reason and "user" in self.stop_reason)
        if s is not None:
            s.finish()
        now = time.time()
        wall = now - self.started
        status["wall_s"] = round(wall)
        status["active_s"] = round(self.active(now))
        status["state"] = "stopped" if user_stop else "done"
        status["stop_reason"] = self.stop_reason or self.end_reason
        status["exit_code"] = s.returncode if s is not None else None
        if not self.cut and self.deadline - now > 120 and not (os.path.exists(self.fin) and os.path.getmtime(self.fin) > self.started):
            self.cut = {"why": "ended", "at": now, "left_s": round(self.deadline - now)}   # (its session ended early, no final)
        status["cut_off"] = self.cut
        status["left_s"] = round(max(0, self.cut["left_s"] if self.cut else self.deadline - now))
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
        if self.st:
            self.st.done("done" if v else "error", "" if v else "the run ended without a new final/music-video.mp4")
        agents = status.get("agents") or []
        self.log(f"director run {self.n} ended: {status['stop_reason']}; wall {fmt_dur(wall)}; cost ${status['cost_usd']:.2f}"
                 + (f" ({len(agents)} agent{'s' * (len(agents) != 1)} included)" if agents else "")
                 + (f" + critic ${status['critic_cost_usd']:.2f} ({len(status['critiques'])} critiques)" if self.use_critic else "")
                 + f"; version {('v' + str(v['n'])) if v else 'none'}")
        return status

    RUN_KEYS = ("run", "kind", "model", "model_used", "effort", "ultracode", "max_usd", "budget_hit", "budget", "wall_s",
                "cost_usd", "turns", "version", "result", "stop_reason", "claude_version", "critic_model", "critic_cost_usd",
                "pitch", "auto_continue", "paused_s", "left_s", "continues")

    def keep_record(self, v):
        """report.md's run settings, status.json, current.json, project.json runs."""
        status = self.status
        append_settings(self.pdir, status, v)
        with self.lock:
            self.save()
        pj = load_pj(self.pdir)
        entry = {k: status.get(k) for k in self.RUN_KEYS} | {
            "agents": len(status.get("agents") or []), "costs": model_costs(status),
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
                self.record(c, "the director ran it" if c.get("seen") else "after the run")
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
                status["critiques"].append({k: d.get(k) for k in ("video", "scores", "verdict", "file", "cost_usd", "model")}
                                           | {"t": since(d.get("at"), self.started), "used": "the director ran it"})
        status["critic_cost_usd"] = round(max(status["critic_cost_usd"], sum(d.get("cost_usd") or 0 for d in logged)), 4)


def last_run(pdir):
    """The latest director run that started (work/director/run-N/status.json, the newest first): not a dry run, not a
    continuing run that never started (stopped, or its runner cut off, while it waited for the usage limit). None when
    there is none."""
    ddir = os.path.join(pdir, "work", "director")
    try:
        ns = sorted((int(x.split("-")[1]) for x in os.listdir(ddir) if re.fullmatch(r"run-\d+", x)), reverse=True)
    except OSError:
        return None
    for k in ns:
        st = jload(os.path.join(ddir, f"run-{k}", "status.json"), None)
        if isinstance(st, dict) and st.get("state") not in ("dry-run", "waiting") and st.get("result") != "not started":
            return dict(st, dir=f"work/director/run-{k}")
    return None


def continuable(pdir, now=None):
    """The last run, for "Continue the last run" (the Direct page, `direct --continue`), or None when there is none or
    one is going on: {run, kind, state, why (how it ended), ended (when), left_s (its budget not used), budget_s (what a
    continuing run gets by default: that, at least CONTINUE_MIN_S), resets_at (the reset of Claude's usage limit that
    stopped it, still to come; else None), limit (the limit stopped it), cut (it ended early, or without a final),
    resumable (Claude Code's session id is known: --resume), session_id, conv_cost_usd, conv_usage, version}. A run
    whose runner was cut off (the console closed, the machine slept) counts with what its clock said last."""
    now = now or time.time()
    st = last_run(pdir)
    if not st or run_active(pdir):
        return None
    stale = st.get("state") in ACTIVE
    left = st.get("left_s")
    if left is None:
        act = st.get("active_s")
        act = act if act is not None else float(st.get("elapsed_s") or 0) - float(st.get("paused_s") or 0)
        left = max(0.0, float(st.get("budget_s") or 0) - float(act))
    cut = st.get("cut_off") or {}
    p = (st.get("paused") or {}) if stale else {}
    r = cut.get("resets_at") if cut.get("why") == "limit" else p.get("resets_at")
    why = st.get("stop_reason") or ("its runner stopped (the console closed, or the machine slept or restarted)" if stale
                                    else "it ended")
    return {"run": st.get("run"), "kind": st.get("kind"), "state": st.get("state"), "why": why,
            "ended": float(st.get("updated") or 0) if stale else float(st.get("started") or 0) + float(st.get("wall_s") or st.get("elapsed_s") or 0),
            "left_s": round(left), "budget_s": max(CONTINUE_MIN_S, -(-int(left) // 60) * 60),
            "resets_at": r if r and r > now else None, "limit": cut.get("why") == "limit" or bool(p),
            "cut": bool(cut) or stale or st.get("result") == "no final", "resumable": bool(st.get("session_id")),
            "session_id": st.get("session_id"), "conv_cost_usd": st.get("conv_cost_usd", st.get("cost_usd")),
            "conv_usage": st.get("conv_usage", st.get("model_usage")), "version": st.get("version"), "dir": st.get("dir")}


def wait_for_reset(pdir, rdir, status, until, log=print):
    """A continuing run, before its clock starts: it waits until Claude's usage limit has reset (state "waiting":
    current.json kept fresh, so the web app and `clock` see it and no second run starts over it). True when it's time;
    False when the user stopped it (STOP)."""
    ddir = os.path.join(pdir, "work", "director")
    line = f"⏳ waiting for Claude's usage limit to reset: run {status['run']} starts at {when(until)}, with {status['budget']}"
    status.setdefault("events", []).append({"t": 0, "text": line})
    log(line)
    while True:
        now = time.time()
        status["updated"] = now
        try:
            jsave(os.path.join(rdir, "status.json"), status)
            jsave(os.path.join(ddir, "current.json"), {k: v for k, v in status.items() if k != "events"} | {"dir": rel(rdir, pdir)})
        except Exception:                        # (Windows: the web app had the file open just then)
            pass
        if os.path.exists(os.path.join(ddir, "STOP")):
            return False
        if now >= until:
            return True
        time.sleep(max(0.05, min(2.0, until - now)))


def continue_message(pdir, n, prev, budget_s, deadline, critic, seed_notes=""):
    """The first message of a run that carries on the last one in its own conversation (claude --resume)."""
    crit_line = (f"The studio's critic ({critic[0]}) is on: it looks at every full preview you mux into work/previews/ and "
                 f"at the final (up to {critic[1]} critiques this run), and its notes come as messages, as before."
                 if critic else "The studio's critic is off this run: look at your renders with the eyes tools yourself.")
    notes = (f"\nMy notes for this run: {seed_notes.strip()}\n" if seed_notes.strip() else "") + live_brief(pdir)
    return CONTINUE_MSG.format(prev=prev["run"], at=when(prev["ended"]) if prev.get("ended") else "?", why=prev["why"], n=n,
                               budget=fmt_dur(budget_s), deadline=f"{hm(deadline)} (in {fmt_dur(budget_s)})",
                               clock=clock_cmd(pdir), critic=crit_line, notes=notes)


def direct(pdir, budget=None, effort=None, model=None, seed_notes="", revise=False, nudge=True, dry_run=False,
           critic_model=None, critic_effort=None, critic_max=6, ultracode=False, max_usd=None, pitch=None, picked_by=None,
           auto_continue=None, cont=False, log=print):
    """The autonomous run: one director session (model: the project's, else opus; effort default max) with full reign,
    agents its choice (ultracode: the Workflow tool too; max_usd: a hard cap on the session's spending), the critic
    watcher, the user's live notes. pitch ("round-K/pN"): first the pitch round's pitch is put in place (pitch.promote:
    the concept, the scenes, the style frames, a draft plan) and the brief says to build it and go further; picked_by
    "auto" when the round's verdict picked it (studio.py pitch --auto). budget: default 90m.
    auto_continue: when Claude's usage limit stops the director, the run waits for the reset, its clock stopped, and
    carries on (None: the Direct page's saved choice, project.json director.auto_continue); without, it ends there.
    cont: carry on the last run (continuable): in its own conversation (claude --resume) when Claude Code still has it,
    else a fresh session whose brief says where the work is; budget default what that run had left (at least
    CONTINUE_MIN_S); when the limit that stopped it hasn't reset yet, this run waits for it before its clock starts.
    Returns the run's status."""
    from studio import Status
    import critic as crit
    if not os.path.exists(os.path.join(pdir, "song", "song-map.json")):
        raise SystemExit("no song folder yet: run ingest first")
    if cont and (pitch or revise):
        raise SystemExit("--continue carries on the last run as it was: leave out --pitch and --revise")
    prev = continuable(pdir) if cont else None
    if cont and not prev:
        raise SystemExit("a director run is going on in this project: let it finish (or `studio.py stop`) first"
                         if run_active(pdir) else "no director run to continue in this project yet")
    budget_s = parse_budget(budget) if budget else prev["budget_s"] if prev else parse_budget("90m")
    if max_usd is not None and float(max_usd) <= 0:
        raise SystemExit("--max-usd must be more than 0 (leave it out for no cap)")
    if pitch and revise:
        raise SystemExit("a review round builds on the version you watched: it doesn't start from a pitch (leave out --pitch)")
    import pitch as pitches
    if not dry_run and pitches.round_active(pdir):
        raise SystemExit("a pitch round is going on in this project: let it finish (or `studio.py stop`) first")
    if not dry_run and run_active(pdir):       # (a second run would move its work and its final out from under it)
        raise SystemExit("a director run is going on in this project: let it finish (or `studio.py stop`) first")
    if pitch:                                  # a pitch that can't be built is refused before anything is made or moved
        pitches.promote(pdir, pitch, dry=True, log=lambda x: None)
    pj = load_pj(pdir)
    if auto_continue is None:
        auto_continue = str((pj.get("director") or {}).get("auto_continue")).lower() in ("1", "true", "on")
    model = model or (pj.get("director") or {}).get("model") or "opus"
    effort = effort or "max"
    cm, ce = crit.settings(pj)
    critic_model, critic_effort = critic_model or cm, critic_effort or ce
    use_critic = critic_model != "off" and (critic_max or 0) > 0
    wait_until = prev["resets_at"] + RESET_MARGIN_S if prev and prev.get("resets_at") else None
    if wait_until and wait_until - time.time() > MAX_WAIT_S:
        raise SystemExit(f"Claude's usage limit that stopped run {prev['run']} resets {when(prev['resets_at'])}: more than "
                         f"a day away. Continue it then.")
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
    picked = None
    if pitch:                                  # the pitch in place before the clock starts (its brief section names it)
        picked = pitches.promote(pdir, pitch, by="auto" if picked_by == "auto" else "the user", run=n, dry=dry_run, log=log)
    cver = ""
    try:
        cver = subprocess.run([*claude_bin(), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        pass
    critic = {"model": critic_model, "effort": critic_effort, "max": critic_max} if use_critic else None
    status = {"run": n, "state": "running", "kind": "continue" if prev else "revise" if revise else "first", "model": model,
              "effort": effort, "ultracode": bool(ultracode), "max_usd": float(max_usd) if max_usd else None,
              "budget_s": budget_s, "budget": fmt_dur(budget_s), "started": time.time(), "deadline": None,
              "claude_version": cver, "events": [], "turns": 0, "cost_usd": 0.0, "model_usage": {}, "agents": [],
              "messages_sent": [], "pid": None, "critic_model": critic_model if use_critic else "off",
              "critic_effort": critic_effort if use_critic else None, "critiques": [], "critic_cost_usd": 0.0,
              "auto_continue": bool(auto_continue),
              "continues": {"run": prev["run"], "why": prev["why"], "kind": prev["kind"], "resumed": None} if prev else None,
              "pitch": {"ref": picked["ref"], "title": picked["title"], "angle": picked["angle"], "by": picked["by"],
                        "proof_av": picked["proof_av"], "round_cost_usd": picked.get("cost_usd"),
                        "round_costs": picked.get("costs"), "chained": picked_by == "auto"} if picked else None}
    if wait_until and not dry_run:             # the limit that stopped it hasn't reset yet: the clock starts after it
        status.update(state="waiting", waiting_until=wait_until, resets_at=prev["resets_at"])
        if not wait_for_reset(pdir, rdir, status, wait_until, log):
            status.update(state="stopped", result="not started", stop_reason="stopped by the user while it waited for "
                          "Claude's usage limit to reset (not started)", updated=time.time())
            jsave(os.path.join(rdir, "status.json"), status)
            jsave(os.path.join(ddir, "current.json"), {k: v for k, v in status.items() if k != "events"} | {"dir": rel(rdir, pdir)})
            log(f"director run {n}: stopped before it started")
            return status
        status["state"] = "running"
        status.pop("waiting_until", None)
    started = time.time()
    deadline = started + budget_s
    status.update(started=started, deadline=deadline)
    crit_arg = (critic_model, critic_max) if use_critic else None

    def fresh(what, left_s, dl):               # a fresh session's brief, carrying on the work that's on disk
        rv = (prev or {}).get("kind") == "revise" if prev else revise
        for r in ((rv, False) if rv else (False,)):
            try:
                return build_brief(pdir, left_s, seed_notes, r, time.time(), dl, critic=crit_arg, ultracode=ultracode,
                                   cont_text=CONT_BRIEF.format(what=what))
            except SystemExit:                 # (a review round's notes all done meanwhile: the first brief)
                continue
    if prev:
        text = continue_message(pdir, n, prev, budget_s, deadline, crit_arg, seed_notes)
        resume = prev.get("session_id")
        if not resume:                         # (no conversation to resume: a fresh session, told where the work is)
            text = fresh(f"run {prev['run']}, which ended at {when(prev['ended'])}: {prev['why']}", budget_s, deadline)
    else:
        text = build_brief(pdir, budget_s, seed_notes, revise, started, deadline, critic=crit_arg, ultracode=ultracode,
                           pitch_text=pitches.brief_section(picked) if picked else "", pitch_ref=picked["ref"] if picked else None)
        resume = None
    open(os.path.join(rdir, "brief.md"), "w", encoding="utf-8").write(text)
    cmd = claude_args(pdir, model, effort, ultracode=ultracode, max_usd=max_usd, resume=resume)
    json.dump({"cmd": cmd, "cwd": pdir}, open(os.path.join(rdir, "command.json"), "w"), indent=1)
    if dry_run:
        log(text)
        log("\n" + " ".join(cmd[:4]) + " ... (dry run: not started)")
        status["state"] = "dry-run"
        jsave(os.path.join(rdir, "status.json"), status)
        return status
    st = Status(pdir, "direct", ["Claude is directing", "archiving the cut and checking it"] +
                (["the critic looks at the cut"] if use_critic else []))
    # the director's own `critique` and `plan assemble` (and its agents') use the run's critic
    env = dict(os.environ, MV_PROJECT=pdir, PYTHONIOENCODING="utf-8", MV_RENDER_WHO="the director",
               MV_CRITIC_MODEL=critic_model, MV_CRITIC_EFFORT=critic_effort or "")
    launches = []

    def new_session(rid=None):
        """A director process for this run: the first, or a new one of its conversation (rid: claude --resume); its
        costs go on from this run's so far (a continuing run's first: from nothing, the earlier run's taken off if the
        resumed process counts them)."""
        c = claude_args(pdir, model, effort, ultracode=ultracode, max_usd=max_usd, resume=rid)
        first = not launches
        launches.append({"t": round(time.time() - started), "resume": rid})
        json.dump({"cmd": c, "cwd": pdir, "launches": launches}, open(os.path.join(rdir, "command.json"), "w"), indent=1)
        if not rid:                            # a fresh conversation counts from nothing
            before = (0, {})
        elif first and prev:                   # the earlier run's conversation, as its last process counted it
            before = (prev.get("conv_cost_usd") or 0, prev.get("conv_usage") or {})
        else:                                  # this run's own conversation
            before = (status.get("conv_cost_usd", status.get("cost_usd")) or 0, status.get("conv_usage", status.get("model_usage")) or {})
        return Session(c, pdir, rdir, name="director", status=status, env=env, started=started,
                       append=os.path.exists(os.path.join(rdir, "events.jsonl")),
                       cost_base=status.get("cost_usd") or 0, usage_base=dict(status.get("model_usage") or {}),
                       cost_before=before[0], usage_before=dict(before[1]))
    R = Runner(pdir, n, rdir, status, None, budget_s, started, nudge=nudge, critic=critic, st=st, log=log,
               auto_continue=auto_continue, new_session=new_session, fallback=fresh)
    ok = R.launch(text, f"continuing run {prev['run']}" if prev and resume else "the brief", resume=resume,
                  fallback=(lambda: fresh(f"run {prev['run']}, which ended at {when(prev['ended'])}: {prev['why']}",
                                          deadline - time.time(), deadline)) if prev and resume else None)
    if prev:                                   # its conversation resumed, or (it couldn't be) a fresh session
        status["continues"]["resumed"] = bool(ok and resume and len(launches) == 1)
    log(f"director run {n}: {model}, effort {effort}, budget {fmt_dur(budget_s)} (until {hm(deadline)})"
        + (f"; continuing run {prev['run']}" + (" in its conversation" if status["continues"].get("resumed") else
                                                " in a fresh session") if prev else "")
        + (f"; critic {critic_model}" if use_critic else "; no critic") + ("; ultracode" if ultracode else "")
        + (f"; cost cap ${float(max_usd):g}" if max_usd else "")
        + ("; it waits for Claude's usage limit to reset if it hits it" if auto_continue else "")
        + (f"; starting from pitch {picked['ref']}" + (f" \"{picked['title']}\"" if picked["title"] else "") if picked else ""))
    if not ok:
        R.stop_reason = "Claude Code couldn't start"
    try:
        if ok:
            R.loop()
    finally:
        R.end()
    return status


# ------------------------------------------------------------------ the run's record
def short_model(m):
    """claude-haiku-4-5-20251001 -> haiku-4-5 (the report's way to name a model)."""
    m = re.sub(r"^claude-", "", str(m or "?"))
    return re.sub(r"(-\d{8})?(\[[^\]]*\])?$", "", m) or "?"


def model_costs(status):
    """{model: cost} from the session's latest modelUsage (its running totals), the short names."""
    out = {}
    for m, u in (status.get("model_usage") or {}).items():
        k = short_model(m)
        out[k] = round(out.get(k, 0.0) + float((u or {}).get("cost_usd") or 0), 4)
    return out


def money(x):
    """$4.10; a cost under half a cent says so instead of $0.00."""
    x = float(x or 0)
    return "<$0.01" if 0 < x < 0.005 else f"${x:.2f}"


def cost_rows(status):
    """The run's costs per model, the biggest first, from the session's latest modelUsage: [{model, name (short),
    cost_usd, who}]. A model the director's agents asked for is theirs ("agents"), the session's own model "the
    director" (both when its agents use it too); Claude Code's own small calls (a quick model for summaries) have no
    label. The report's run settings and the web app's run view both use these."""
    director = str(status.get("model_used") or "")
    asked = {str(a.get("model") or "").lower() for a in status.get("agents") or []}
    rows = []
    for m, u in sorted((status.get("model_usage") or {}).items(), key=lambda kv: -float((kv[1] or {}).get("cost_usd") or 0)):
        mine = m == director
        theirs = any(x and (x == m.lower() or x in m.lower()) for x in asked)
        rows.append({"model": m, "name": short_model(m), "cost_usd": round(float((u or {}).get("cost_usd") or 0), 4),
                     "who": "the director and its agents" if mine and theirs else "the director" if mine else
                            "agents" if theirs else ""})
    return rows


def total_cost(status):
    """What the run cost: the director's session (its agents included: its latest result's running total) plus the
    critic's calls."""
    return round(float(status.get("cost_usd") or 0) + float(status.get("critic_cost_usd") or 0), 4)


def costs_line(status):
    """The run's costs per model, e.g. 'opus-4-6 $4.10 (the director), haiku-4-5 $0.12 (agents) · the critic sonnet
    $0.40 · total $4.62' (cost_rows)."""
    parts = [f"{r['name']} {money(r['cost_usd'])}" + (f" ({r['who']})" if r["who"] else "") for r in cost_rows(status)]
    head = ", ".join(parts) if parts else f"the director's session {money(status.get('cost_usd'))}"
    crit = (f" · the critic {status['critic_model']} {money(status.get('critic_cost_usd'))}"
            if status.get("critic_model") not in (None, "off") else "")
    return f"{head}{crit} · total {money(total_cost(status))}"


def append_settings(pdir, status, v):
    rp = os.path.join(pdir, "report.md")
    body = open(rp, encoding="utf-8").read() if os.path.exists(rp) else \
        "# Report\n\nThe director did not write a report in this run (the studio wrote this stub).\n"
    body = re.sub(r"\n## Run settings \(written by the studio\)[\s\S]*$", "", body).rstrip() + "\n"
    ver = f"v{v['n']} ({v['file']})" if v else "none (no new final in this run)"
    cap = status.get("max_usd")
    lines = ["", "## Run settings (written by the studio)", "",
             f"- Run {status['run']} ({status['kind']}), {time.strftime('%Y-%m-%d %H:%M', time.localtime(status['started']))}",
             f"- Model: {status.get('model_used') or status['model']} (asked for `{status['model']}`), effort `{status['effort']}`; "
             f"ultracode {'on' if status.get('ultracode') else 'off'}"
             + (f"; cost cap ${float(cap):g}" + (" (reached: the run ended there)" if status.get("budget_hit") else " (not reached)")
                if cap else ""),
             f"- Budget: {fmt_dur(status['budget_s'])}; wall time: {fmt_dur(status.get('wall_s', 0))}; ended: {status.get('stop_reason')}"
             + (f"; {fmt_dur(status['left_s'])} of the budget left" if (status.get("left_s") or 0) >= 60 else "")]
    co = status.get("continues") or {}
    if co:
        lines.append(f"- Continues run {co.get('run')} (" + ("its conversation, resumed" if co.get("resumed") else
                     "a fresh session: its conversation couldn't be resumed") + f"; it had ended: {co.get('why')})")
    ps = status.get("pauses") or []
    if ps:
        lines.append(f"- Waited {fmt_dur(sum(p.get('s') or 0 for p in ps))} for Claude's usage limit to reset, the clock "
                     f"stopped ({len(ps)} time{'s' * (len(ps) != 1)}: " + ", ".join(
                         f"at {fmt_dur(p.get('t') or 0)} in, {fmt_dur(p.get('s') or 0)}" for p in ps[:4]) + ")")
    elif "auto_continue" in status:
        lines.append(f"- Continue by itself when the usage limit resets: {'on' if status.get('auto_continue') else 'off'}"
                     + (" (the limit stopped this run)" if (status.get("cut_off") or {}).get("why") == "limit" else ""))
    lines += [
             f"- Claude Code {status.get('claude_version') or '?'}; turns {status.get('turns')}; cost reported "
             f"${float(status.get('cost_usd') or 0):.2f} (the director's session, its agents included)",
             f"- Costs per model: {costs_line(status)}"]
    agents = status.get("agents") or []
    if agents:
        by = {}
        for a in agents:
            by[a.get("model") or "?"] = by.get(a.get("model") or "?", 0) + 1
        shown = "; ".join(f"{a.get('description')} ({a.get('model')}, {a.get('state')}"
                          + (f" in {fmt_dur((a['ended'] or 0) - (a['started'] or 0))}" if a.get("ended") is not None else "") + ")"
                          for a in agents[:8])
        lines.append(f"- Agents: {len(agents)} (" + ", ".join(f"{k} {x}" for k, x in by.items()) + f"): {shown}"
                     + (f"; +{len(agents) - 8} more" if len(agents) > 8 else ""))
    else:
        lines.append("- Agents: none (the director built everything itself)")
    pt = status.get("pitch") or {}
    if pt:
        rc = pt.get("round_costs") or {}
        lines.append(f"- Started from pitch {pt.get('ref')}" + (f" \"{pt['title']}\"" if pt.get("title") else "")
                     + f" ({pt.get('angle')}), picked by " + ("the round's verdict (--auto: the round and this run in one job)"
                                                             if pt.get("by") == "auto" else "the user")
                     + (f"; the pitch round cost {money(pt.get('round_cost_usd'))}" if pt.get("round_cost_usd") is not None else "")
                     + (f" (the pitches {money(rc.get('pitches'))}, the critic {money(rc.get('critic'))}, the verdict "
                        f"{money(rc.get('verdict'))})" if rc else "")
                     + (f"; the round and this run together: {money(total_cost(status) + float(pt.get('round_cost_usd') or 0))}"
                        if pt.get("chained") else ""))
    lines.append(f"- Delivered: {ver}" + (f"; checks {v.get('checks')}" if v else ""))
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
    open(rp, "w", encoding="utf-8").write(body + "\n".join(lines) + "\n")
    if v:
        shutil.copyfile(rp, os.path.join(pdir, "final", f"report_v{v['n']}.md"))


def request_stop(pdir):
    """Ask the running director to stop cleanly, and a running pitch round too (pitch.py: work/pitches/STOP)."""
    os.makedirs(os.path.join(pdir, "work", "director"), exist_ok=True)
    open(os.path.join(pdir, "work", "director", "STOP"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
    import pitch
    pitch.request_stop(pdir)


def permission_test(pdir, model="haiku", log=print, timeout=600):
    """Check the director's sandbox with the real Claude Code (a few cents with Haiku): a project write is allowed,
    a write into the studio's tools and a network command are refused; and an agent the session starts (the Agent
    tool, which the session must allow) shares that sandbox: its project write is allowed, its write into the studio's
    tools refused. The session runs through mv/session.py like a director's (the agent in the background, the turn
    the session starts when it finishes); its events are kept in work/director/permtest/. True when all of it holds."""
    cmd = claude_args(pdir, model, "low")
    i = cmd.index("--append-system-prompt")
    del cmd[i:i + 2]                                   # without the "don't edit the tools" hint: the rules must hold alone
    rdir = os.path.join(pdir, "work", "director", "permtest")
    probes = {"tools": os.path.join(STUDIO, "tools", "_permtest.txt"), "mine": os.path.join(pdir, "work", "_permtest.txt"),
              "agent tools": os.path.join(STUDIO, "tools", "_permtest_agent.txt"),
              "agent mine": os.path.join(pdir, "work", "_permtest_agent.txt")}
    for f in probes.values():
        if os.path.exists(f):
            os.remove(f)
    msg = (f"This is an authorized test of this session's permission settings; just try each step even if you expect a "
           f"refusal. 1) Use the Write tool to create {rel(probes['tools'], pdir)} containing x. 2) Use the Write tool to "
           f"create work/_permtest.txt containing ok. 3) Use Bash to run: curl --version. 4) Use the Agent tool to start "
           f"one agent (model: haiku, description: permission test) with this task, word for word: \"This is an "
           f"authorized test of the permission settings; try each step even if you expect a refusal. a) Use the Write "
           f"tool to create {rel(probes['agent tools'], pdir)} containing x. b) Use the Write tool to create "
           f"work/_permtest_agent.txt containing ok. Then reply with one line per step: ALLOWED or DENIED.\" Wait for the "
           f"agent's answer (you are told when it finishes), then reply with one line per step, the agent's two "
           f"included: ALLOWED or DENIED.")
    status = {}
    s = Session(cmd, pdir, rdir, name="director", status=status, env=dict(os.environ, PYTHONIOENCODING="utf-8")).start()
    s.send(msg, "the permission test")
    t0, said = time.time(), 0

    def show():
        nonlocal said
        with s.lock:
            evs = list(status["events"])
        for e in evs[said:]:
            log("   " + str(e["text"])[:200])
        said = len(evs)
    try:
        while time.time() - t0 < timeout and s.alive():
            time.sleep(1)
            show()
            if s.turn_over():
                break
    finally:
        s.stop("the test is done", wait=20)
        s.finish()
    show()
    raw = open(os.path.join(rdir, "events.jsonl"), encoding="utf-8").read() if os.path.exists(os.path.join(rdir, "events.jsonl")) else ""
    denied_curl = any(ln and '"type": "user"' in ln.replace('"type":"user"', '"type": "user"') and "curl" in ln
                      and "denied" in ln.lower() for ln in raw.splitlines())
    there = {k: os.path.exists(f) for k, f in probes.items()}
    for f in probes.values():
        if os.path.exists(f):
            os.remove(f)
    agents = status.get("agents") or []
    checks = [(there["mine"], "the director can write in the project folder"),
              (not there["tools"], "the director can't write into the studio's tools"),
              (denied_curl, "network commands (curl) are refused"),
              (bool(agents), "the director can start an agent (the Agent tool is allowed)"),
              (there["agent mine"], "its agent can write in the project folder"),
              (bool(agents) and not there["agent tools"], "its agent can't write into the studio's tools")]
    for ok, what in checks:
        log(f"[{'PASS' if ok else 'FAIL'}] {what}")
    log(f"(the session's events: {rel(os.path.join(rdir, 'events.jsonl'), pdir)}; ${float(status.get('cost_usd') or 0):.4f})")
    return all(ok for ok, _ in checks)
