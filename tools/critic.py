"""The studio's critic: a cheaper model with fresh eyes looks at a rendered video and reports back in text, so the
director (often the most expensive model) doesn't have to spend its own tokens looking at every picture.

  python tools/studio.py critique <p> <video> [--model sonnet] [--effort medium] [--focus "..."] [--concept PATH]
                                              [--out DIR] [--max-usd 2] [--no-look] [--pictures a.png,b.png]
  python tools/studio.py critique <p> v2      a delivered version: also saved as final/critic_v2.md (+ .json)
  python tools/studio.py run <p> critic.py <video> ...                     (the same as the first form)

<video> is relative to the project: a preview at any size, a section render (its .mv.json says where it sits), the
final, a pitch's proof clip.
1. Eyes first: unless work/eyes/<name>/index.md is newer than the video (or --no-look), eyes.py look; then a strip
   (8 consecutive frames) at up to three of the strongest ★★★ moments inside the video's range.
2. The prompt (templates/critic-prompt.md): the intent (work/concept.md or --concept), the song's essentials
   (work/song-notes.md, else work/critic/song-brief.md, made once from song/song-map.md without the bar-by-bar), the
   user's notes (their avoid is a hard rule), the anti-defaults and the craft rules (inlined from the skill, so they
   stay in sync), what the eyes measured, the checks when they are about this video, the pictures to open, and the
   previous critique (what got fixed). Not report.md or work/log.md: fresh eyes judge the picture against the intent,
   not the director's account of it. --pictures adds stills to open with the video's (the pipeline's language gate:
   the style frames).
3. One headless print-mode call (mv/claude.py): read-only tools, cwd = the project folder, the prompt on stdin.
4. work/critic/<name>_NN.md + .json (verdict, scores, problems, keep, safe, anti_defaults, fixed_since_last; plus the
   video, its size and mtime, model, effort, cost_usd, duration_s, at) and a line in work/critic/log.jsonl. With
   --out DIR: DIR/critic.md + DIR/critic.json instead (no log line; the pitch round uses this).
It prints the report, then one line:  critic: ambition 7 · song fit 6 · craft 8 · readability 9 · $0.12 · work/critic/x_02.md
Exit 0 whatever it found; 1 only when it couldn't run (no pictures of this cut, no answer, or Claude Code's own error
text instead of a critique: a usage limit, a login): then nothing is written but a log line with the error and what it
cost, and the last line is  critic: couldn't run: <why> ($0.0310 spent).
"""
import argparse, glob, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, jload, jsave, probe, project as load_pj, need_project  # noqa: E402
from mv.claude import claude_print, pid_alive, READ_ONLY  # noqa: E402

TEMPLATE = os.path.join(STUDIO, "templates", "critic-prompt.md")
SKILL_DIR = os.path.join(STUDIO, ".claude", "skills", "musicvideo")
DEFAULT_MODEL, DEFAULT_EFFORT, DEFAULT_MAX_USD = "sonnet", "medium", 2.0
SCORES = (("ambition", "ambition"), ("song_fit", "song fit"), ("craft", "craft"), ("readability", "readability"))
LISTS = ("keep", "safe", "anti_defaults", "fixed_since_last")


def rel(p, start):
    return os.path.relpath(p, start).replace("\\", "/")


def mmss(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:04.1f}"


# ------------------------------------------------------------------ settings and results (also used by director.py)
def settings(pj):
    """(model, effort) of the automatic critic from project.json "director" (critic_model "off" = no critic). Inside an
    autonomous run the run's critic wins (MV_CRITIC_MODEL / MV_CRITIC_EFFORT, set for the planner's and the builders'
    sessions), so their own `critique` and `pipeline` commands use the critic the run was started with."""
    d = pj.get("director") or {}
    return (str(os.environ.get("MV_CRITIC_MODEL") or d.get("critic_model") or DEFAULT_MODEL),
            str(os.environ.get("MV_CRITIC_EFFORT") or d.get("critic_effort") or DEFAULT_EFFORT))


def score_line(scores):
    """'ambition 7 · song fit 6 · craft 8 · readability 9' (a ? where the critic gave none)."""
    s = scores or {}
    return " · ".join(f"{lab} {s.get(k) if s.get(k) is not None else '?'}" for k, lab in SCORES)


def result_line(d):
    cost = f"${d['cost_usd']:.2f}" if isinstance(d.get("cost_usd"), (int, float)) else "$?"
    return f"critic: {score_line(d.get('scores'))} · {cost} · {d.get('file')}"


class NoAnswer(RuntimeError):
    """The critic couldn't run; .cost is what the call spent anyway (a cap hit, a refused call), when Claude said."""
    def __init__(self, msg, cost=None):
        super().__init__(msg)
        self.cost = cost


def spent(out):
    """What a critic.py run that couldn't answer spent, from its "critic: couldn't run: ... ($0.031 spent)" line."""
    m = re.findall(r"^critic: couldn't run: .*\(\$([\d.]+) spent\)\s*$", out or "", re.M)
    return float(m[-1]) if m else None


def critiques(pdir):
    """Every critique in work/critic/ (the json files), oldest first."""
    out = []
    for f in glob.glob(os.path.join(pdir, "work", "critic", "*.json")):
        d = jload(f, None)
        if isinstance(d, dict) and d.get("video"):
            out.append(dict(d, json=rel(f, pdir)))
    return sorted(out, key=lambda d: (d.get("at") or "", d["json"]))


def find(pdir, video, size=None, mtime=None):
    """The latest critique of this exact file (its path, and its size + mtime when given), or None."""
    hits = [d for d in critiques(pdir) if d["video"] == video and (size is None or d.get("size") == size)
            and (mtime is None or abs((d.get("mtime") or 0) - mtime) < 1)]
    return hits[-1] if hits else None


def load(pdir, md_file):
    """A written critique (its .md path relative to the project): the json, with the markdown report as "report"."""
    base = os.path.splitext(os.path.join(pdir, md_file))[0]
    d = jload(base + ".json", None)
    if not isinstance(d, dict):
        return None
    d["report"] = open(base + ".md", encoding="utf-8").read() if os.path.exists(base + ".md") else ""
    return d


def from_output(pdir, out):
    """The critique a critic.py run wrote, from its last line ("critic: ... · work/critic/x_02.md"), or None."""
    lines = [x.strip() for x in (out or "").splitlines() if x.startswith("critic: ") and x.strip().endswith(".md")]
    return load(pdir, lines[-1].rsplit(" · ", 1)[-1]) if lines else None


def parse(text):
    """The critic's answer -> (markdown, data, warning). The last ```json block is the data; missing or broken: the
    markdown is kept, the scores are None, and the warning says so."""
    text = (text or "").strip()
    blocks = list(re.finditer(r"```(?:json\w*)?\s*(\{[\s\S]*?\})\s*```", text, re.I))   # ```json, ```JSON, ```jsonc
    data, warn = None, ""
    if blocks:                                   # only the last one: an earlier block may be an example or a draft
        m = blocks[-1]
        try:
            data = json.loads(m.group(1))
            text = (text[:m.start()] + text[m.end():]).strip()
        except Exception:
            data = None
        if not isinstance(data, dict):
            data = None
    if data is None:
        warn = "the critic's answer had no readable json block: the report is kept, the scores are missing"
        if blocks:
            warn = "the critic's json block didn't parse: the report is kept, the scores are missing"
        data = {}
    sc = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    scores = {}
    for k, _ in SCORES:
        try:
            scores[k] = max(1, min(10, int(round(float(sc.get(k))))))
        except Exception:
            scores[k] = None
    probs = []
    for p in data.get("problems") or []:
        if isinstance(p, dict):
            q = {k: p.get(k) for k in ("severity", "t0", "t1", "where", "what", "push")}
            sev = str(q["severity"] or "").strip().lower()
            q["severity"] = sev if sev in ("high", "medium", "low") else "medium"
            for k in ("t0", "t1"):
                try:
                    q[k] = round(float(q[k]), 2)
                except Exception:
                    q[k] = None
            probs.append(q)
    out = {"verdict": str(data.get("verdict") or "").strip(), "scores": scores if any(v is not None for v in scores.values()) else None,
           "problems": probs[:12]}
    for k in LISTS:
        v = data.get(k) or []
        out[k] = [str(x) for x in (v if isinstance(v, list) else [v])][:12]
    return text, out, warn


def problems_text(d, n=4):
    """The top problems as short lines (for a message to the director)."""
    out = []
    for i, p in enumerate((d.get("problems") or [])[:n], 1):
        span = f"{mmss(p['t0'])}–{mmss(p['t1'])}" if p.get("t0") is not None and p.get("t1") is not None else \
            mmss(p["t0"]) if p.get("t0") is not None else "?"
        out.append(f"{i}. [{p.get('severity')}] {span} · {p.get('where') or '-'}: {p.get('what') or ''}"
                   + (f" Push: {p['push']}" if p.get("push") else ""))
    return "\n".join(out)


# ------------------------------------------------------------------ what the critic reads
def section(md, title, stop=r"^## "):
    """One '## title' section of a markdown text (without the next section)."""
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n([\s\S]*?)(?={stop}|\Z)", md, re.M)
    return m.group(0).strip() if m else ""


def craft_rules():
    sk = open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8").read()
    body = section(sk, "Craft rules")
    return re.sub(r"^## [^\n]*\n", "", body).strip() or "(the craft rules are in .claude/skills/musicvideo/SKILL.md)"


def anti_defaults():
    t = open(os.path.join(SKILL_DIR, "anti-defaults.md"), encoding="utf-8").read()
    return re.sub(r"^# [^\n]*\n", "", t).strip()


def song_brief(pdir):
    """The song's essentials: the director's own digest (work/song-notes.md) when there is one, else the song map's
    "At a glance", "Sections" and "Moments" (written once to work/critic/song-brief.md; not the bar-by-bar)."""
    notes = os.path.join(pdir, "work", "song-notes.md")
    if os.path.exists(notes):
        return rel(notes, pdir), open(notes, encoding="utf-8").read().strip()
    sm = os.path.join(pdir, "song", "song-map.md")
    brief = os.path.join(pdir, "work", "critic", "song-brief.md")
    if not os.path.exists(sm):
        return None, "(no song map: the song hasn't been analysed)"
    if not os.path.exists(brief) or os.path.getmtime(brief) < os.path.getmtime(sm):
        md = open(sm, encoding="utf-8").read()
        head = md.split("\n## ", 1)[0].strip()
        parts = [head] + [section(md, t) for t in ("At a glance", "Sections", "Moments")]
        os.makedirs(os.path.dirname(brief), exist_ok=True)
        open(brief, "w", encoding="utf-8").write("\n\n".join(p for p in parts if p) + "\n")
    return rel(brief, pdir), open(brief, encoding="utf-8").read().strip()


def user_notes(pj):
    out = []
    for k, lab in (("about", "What the song is about to them"), ("mood", "Mood / direction"), ("ideas", "Ideas")):
        v = str(pj.get(k) or "").strip()
        if v:
            out.append(f"- {lab}: {v}")
    return "\n".join(out) or "(no notes: the director had a free hand)"


def describe(pdir, video, song):
    """What the video is: (kind, [t0, t1] in song seconds, a few lines for the prompt)."""
    v = rel(video, pdir)
    pr = probe(video)
    vs = next(s for s in pr["streams"] if s["codec_type"] == "video")
    w, h = int(vs["width"]), int(vs["height"])
    dur = float(pr["format"].get("duration") or 0)
    d = delivery(load_pj(pdir))
    side = jload(video + ".mv.json", None) or {}
    t0 = (side.get("range") or [0])[0] / d["fps"]
    t1 = t0 + dur
    whole = song.duration and t0 < 0.5 and t1 > song.duration - 1.0
    if v.startswith("final/"):
        m = re.search(r"_v(\d+)\.mp4$", v)
        kind = f"version v{m.group(1)} of the final cut" if m else "the final cut"
    elif v.startswith("work/previews/"):
        kind = "a preview the director rendered while working"
    elif v.startswith("work/pitches/"):
        kind = "a pitch's proof clip: one director's pitch for the whole video, shown on a short window of the song"
    elif v.startswith("work/segments/"):
        kind = ("one segment of the film, rendered on its own (the pipeline: the planner and builder agents each build "
                "segments in one shared visual language; the studio joins them)")
    else:
        kind = "a render the director made while working"
    size = f"{w}×{h} (the delivery size)" if (w, h) == (d["width"], d["height"]) else \
        f"{w}×{h} (the delivery is {d['width']}×{d['height']}: judge sizes relative to the frame)"
    secs = [s["name"] for s in song.sections if s["t1"] > t0 and s["t0"] < t1]
    span = f"the whole song ({mmss(t0)}–{mmss(t1)})" if whole else \
        f"song time {mmss(t0)}–{mmss(t1)}" + (f" ({', '.join(secs)})" if secs else "") + ": only this part of the song"
    return kind, [round(t0, 3), round(t1, 3)], f"`{v}`: {kind}. {size}, {d['fps']} fps, {span}."


def eyes_dir(video):
    import eyes                                          # eyes.py decides where a video's eyes go
    return eyes.out_dir(video)


def run_tool(pdir, args, timeout=1800):
    env = dict(os.environ, MV_PROJECT=pdir, PYTHONIOENCODING="utf-8", MV_IN_VENV="1")
    cmd = [sys.executable, "-W", "ignore", os.path.join(HERE, args[0])] + list(args[1:])
    try:
        return subprocess.run(cmd, cwd=STUDIO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{args[0]} {args[1] if len(args) > 1 else ''} took longer than {timeout // 60} min")


def busy_marker(path):
    """eyes.py look's .looking file, if a look is really going on: a marker left behind by a look that was killed (a
    Bash timeout, taskkill) names a process that is gone, and is removed."""
    if not os.path.exists(path):
        return False
    try:
        pid = open(path, encoding="utf-8").read().strip()
    except OSError:
        return False
    age = time.time() - os.path.getmtime(path)
    alive = (pid_alive(pid) and age < 1800) if pid.isdigit() else age < 900    # (a look never takes 30 min)
    if not alive:
        try:
            os.remove(path)
        except OSError:
            pass
    return alive


def wait_quiet(d, since, log, quiet=20, most=360):
    """The director may be looking at the same video right now: wait while eyes.py look says it is working (its
    .looking file, for at most `most` seconds) or something else wrote into the folder in the last `quiet` seconds,
    so the two don't make the same pictures twice."""
    t0, said = time.time(), False
    busy = os.path.join(d, ".looking")
    while time.time() - t0 < most:
        ms = [os.path.getmtime(f) for f in glob.glob(os.path.join(d, "*")) if os.path.isfile(f)]
        newest = max(ms or [0])
        looking = busy_marker(busy)
        if not looking and (newest <= since or time.time() - newest > quiet):
            return
        if not said:
            log("eyes: something is looking at this video already; waiting for it")
            said = True
        time.sleep(3)


def strongest_moments(song, t0, t1, n=3):
    """Up to n ★★★ moments inside [t0, t1): the ones where most happens at once (and the ending), in song order."""
    ms = [m for m in song.moments(3) if t0 + 0.2 <= m["t"] <= t1 - 0.2]
    rank = lambda m: len(m["kind"].split("+")) + (1 if "ending" in m["kind"] else 0) + \
        (0.5 if any(k in m["kind"] for k in ("stop", "drop", "tempo")) else 0)    # noqa: E731
    return sorted(sorted(ms, key=lambda m: -rank(m))[:n], key=lambda m: m["t"])


def newer(p, t):
    return os.path.exists(p) and os.path.getmtime(p) > t


def look(pdir, video, song, rng, log, fresh_look=True):
    """Eyes first: the look (unless it's newer than the video), then strips at the strongest ★★★ moments. Only
    pictures made after the video count: an eyes folder is per file name, so older ones show an earlier cut.
    Returns ([(picture path relative to the project, what it is)], the eyes folder, warnings)."""
    d = eyes_dir(video)
    vm = os.path.getmtime(video)
    idx = os.path.join(d, "index.md")
    warns = []
    if fresh_look and not newer(idx, vm):
        wait_quiet(d, vm, log)
    if fresh_look and not newer(idx, vm):
        log(f"eyes: looking at {rel(video, pdir)} (a minute or two)...")
        r = run_tool(pdir, ["eyes.py", "look", video])
        if r.returncode:
            log("eyes.py look failed: " + (r.stdout + r.stderr)[-600:])
    if os.path.exists(idx) and not newer(idx, vm):
        warns.append("the eyes' look at this video (sheets, moments, beats, motion) is from an earlier cut with this "
                     "name" + (" (eyes.py look failed)" if fresh_look else " (--no-look)") + ": left out")
    pics = []
    if newer(idx, vm):
        for k, p in re.findall(r"^- \*\*(\w+)\*\*: `([^`]+)`", open(idx, encoding="utf-8").read(), re.M):
            if os.path.exists(os.path.join(pdir, p)):
                pics.append((p, {"sheet": "sheet: frames spread over the video", "moments": "moments: a frame on each ★★★ moment",
                                 "beats": "beat sheet of the loudest section", "motion": "motion plot"}.get(k, k)))
    for m in strongest_moments(song, rng[0], rng[1]):
        at = f"{m['t']:.3f}"
        p = os.path.join(d, f"strip_at{float(at):g}.png")          # the name eyes.py strip gives it
        if fresh_look and not newer(p, vm):
            r = run_tool(pdir, ["eyes.py", "strip", video, "--at", at, "--n", "8"], timeout=600)
            found = [x for x in r.stdout.strip().splitlines() if x.endswith(".png")]
            p = found[-1] if found else p
        if newer(p, vm):
            pics.append((rel(p, pdir), f"strip around {mmss(m['t'])} ({m['pos']}): ★★★ {m['text']}"))
    listed = {os.path.normcase(os.path.join(pdir, p)) for p, _ in pics}
    extra = sorted((f for f in glob.glob(os.path.join(d, "*.png")) if os.path.normcase(f) not in listed and os.path.getmtime(f) > vm),
                   key=os.path.getmtime, reverse=True)[:3]           # what else the director looked at on this cut
    pics += [(rel(f, pdir), "another look the director made at this video") for f in extra]
    if not pics:
        raise NoAnswer(f"the eyes couldn't make pictures of {rel(video, pdir)}"
                       + ("" if fresh_look else " (with --no-look there were none of this cut)"))
    return pics, d, warns


def trimmed(path, most=45, keep=None):
    if not path or not os.path.exists(path):
        return ""
    lines = open(path, encoding="utf-8").read().splitlines()
    if keep:
        lines = [x for x in lines if keep(x)]
    return "\n".join(lines[:most] + ([f"... ({len(lines) - most} more lines in {os.path.basename(path)})"] if len(lines) > most else []))


def checks_about(pdir, video):
    """work/checks/report.md, when it is about this cut (the same file, or a copy of it made after it)."""
    rp = os.path.join(pdir, "work", "checks", "report.json")
    rj = jload(rp, None) or {}
    cv = rj.get("video")
    if not cv or not os.path.exists(os.path.join(pdir, "work", "checks", "report.md")):
        return ""
    cpath = os.path.join(pdir, cv) if not os.path.isabs(cv) else cv
    same = os.path.normcase(os.path.abspath(cpath)) == os.path.normcase(os.path.abspath(video)) or \
        (os.path.exists(cpath) and os.path.getsize(cpath) == os.path.getsize(video))
    if not same or os.path.getmtime(rp) < os.path.getmtime(video) - 1:
        return ""
    body = trimmed(os.path.join(pdir, "work", "checks", "report.md"), 30,
                   keep=lambda x: x.startswith("#") or "FAIL" in x or "WARN" in x)
    return "### The studio's checks on this cut (only the verdicts and what didn't pass)\n" + body


def previous(pdir, out, video):
    if out:
        return "(none for this one: it is judged on its own)"
    size, vm = os.path.getsize(video), os.path.getmtime(video)
    # the newest critique of this cut or of one made before it: asked about v1 after v2, the critic compares v1 with
    # what came before v1, not with v2 (an older cut can't have "fixed" a newer one's problems)
    cs = [d for d in critiques(pdir) if d.get("size") == size or (d.get("mtime") or 0) < vm - 1]
    if not cs:
        return "(none earlier: this is the first critique of this cut or of one before it; `fixed_since_last` stays empty)"
    d = cs[-1]
    head = (f"`{d['json']}`, of `{d['video']}` at {d.get('at')}: {score_line(d.get('scores'))}. Its verdict: "
            f"{d.get('verdict') or '-'}\nIts problems:\n{problems_text(d, 8) or '(none listed)'}\n")
    if d.get("size") == size:                            # the same cut (or a copy of it): nothing can have changed
        return head + ("It looked at **this very cut**: nothing has changed since, so `fixed_since_last` stays empty. "
                       "Judge it afresh; where you see it differently, say so.")
    return head + ("Say in `fixed_since_last` what of this is fixed in the video you see now (where it covers the same "
                   "part of the song); don't repeat a problem that is gone.")


def build_prompt(pdir, video, what, pics, eyes_d, concept=None, focus="", out=None):
    pj = load_pj(pdir)
    vals = {"what": what}
    cp = concept if concept else os.path.join(pdir, "work", "concept.md")
    cp = cp if os.path.isabs(cp) else os.path.join(pdir, cp)
    if os.path.exists(cp):
        vals["concept"] = f"(from `{rel(cp, pdir)}`)\n\n" + open(cp, encoding="utf-8").read().strip()
    else:
        vals["concept"] = ("(the director hasn't written work/concept.md yet: judge the video against the song and the "
                           "user's notes, and say what idea you can read in the pictures)")
    src, vals["song"] = song_brief(pdir)
    if src:
        vals["song"] = f"(from `{src}`)\n\n" + vals["song"]
    vals["notes"] = user_notes(pj)
    vals["avoid"] = str(pj.get("avoid") or "").strip() or "(nothing)"
    vals["anti"], vals["craft"] = anti_defaults(), craft_rules()
    idx, vm = os.path.join(eyes_d, "index.md"), os.path.getmtime(video)
    vals["eyes"] = open(idx, encoding="utf-8").read().strip() if newer(idx, vm) else \
        "(the eyes couldn't measure this cut: judge from the pictures below)"
    sync = os.path.join(eyes_d, "sync.md")
    vals["sync"] = ("### Sync in detail (from `" + rel(sync, pdir) + "`)\n" + trimmed(sync, 40)) if newer(sync, vm) else ""
    vals["checks"] = checks_about(pdir, video)
    vals["pictures"] = "\n".join(f"- `{p}`: {what}" for p, what in pics) or "(none: the eyes couldn't make pictures)"
    vals["previous"] = previous(pdir, out, video)
    vals["focus"] = (f"**The director asks you to look especially at:** {focus.strip()}\n") if (focus or "").strip() else ""
    text = open(TEMPLATE, encoding="utf-8").read()
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(vals.get(m.group(1), "")), text)


# ------------------------------------------------------------------ the critique
def also_pictures(pdir, pictures):
    """Stills to open with the video's own pictures (--pictures: e.g. the style frames): [(path, what it is)] and a
    warning for each one that isn't there."""
    out, warns = [], []
    for p in pictures or ():
        p = str(p).strip()
        if not p:
            continue
        ap = os.path.abspath(p if os.path.isabs(p) else os.path.join(pdir, p))
        if not os.path.isfile(ap):
            warns.append(f"a picture to look at too isn't there: {p}")
            continue
        what = ("a style frame: the look the director is aiming for (a still, not a frame of this video)"
                if "style" in rel(ap, pdir).lower() else "a still the director asked you to look at too (not a frame of this video)")
        out.append((rel(ap, pdir), what))
    return out, warns


def critique(pdir, video, model=None, effort=None, focus="", concept=None, out=None, max_usd=DEFAULT_MAX_USD,
             fresh_look=True, log=print, pictures=()):
    """Look, ask, write. Returns the critique (dict, as written to the json) or raises RuntimeError when it couldn't
    run (no Claude Code, no pictures, no answer: NoAnswer, with what the call cost). pictures: stills to open too."""
    from mv.song import Song
    pdir = os.path.abspath(pdir)
    video = os.path.abspath(video if os.path.isabs(video) else os.path.join(pdir, video))
    if not os.path.isfile(video):
        raise RuntimeError(f"no such video: {rel(video, pdir)}")
    if concept:
        concept = os.path.abspath(concept if os.path.isabs(concept) else os.path.join(pdir, concept))
        if not os.path.isfile(concept):
            raise RuntimeError(f"no such concept: {rel(concept, pdir)}")
    pj = load_pj(pdir)
    m0, e0 = settings(pj)
    model = model or (m0 if m0 != "off" else DEFAULT_MODEL)
    effort = effort or e0
    song = Song(pdir)
    size, mtime = os.path.getsize(video), os.path.getmtime(video)
    kind, rng, what = describe(pdir, video, song)
    pics, eyes_d, warns = look(pdir, video, song, rng, log, fresh_look)
    more, w2 = also_pictures(pdir, pictures)
    pics, warns = pics + more, warns + w2
    prompt = build_prompt(pdir, video, what, pics, eyes_d, concept, focus, out)
    cdir = os.path.join(pdir, "work", "critic")
    od = (out if os.path.isabs(out) else os.path.join(pdir, out)) if out else cdir
    os.makedirs(od, exist_ok=True)
    open(os.path.join(od, "prompt.md" if out else "last-prompt.md"), "w", encoding="utf-8").write(prompt)   # what it was asked
    log(f"the critic ({model}, effort {effort}) is looking at {rel(video, pdir)}: {len(pics)} pictures (2-4 min)...")
    r = claude_print(prompt, pdir, model=model, effort=effort or None, tools=READ_ONLY, timeout=900, max_usd=max_usd)
    res = r["json"]
    cost = round(r["cost_usd"], 4) if isinstance(r["cost_usd"], (int, float)) else None
    md, data, warn = parse(r["text"])
    failed = r["returncode"] or res.get("is_error")
    if not r["text"].strip() or (failed and warn):
        # no answer, or Claude Code's own error text (a usage limit, a login, an overloaded API) instead of a critique:
        # nothing is written as a critique (so nothing reuses it), only a line in the log with what it cost
        why = (r["text"].strip()[:300] if r["text"].strip() else "") or res.get("subtype") or \
            (r["stderr"] or r["stdout"]).strip()[-400:] or f"exit {r['returncode']}"
        if "budget" in str(res.get("subtype") or why):
            why = f"it reached its ${max_usd:g} cap before answering ({res.get('subtype') or why})"
        if not out:
            with open(os.path.join(cdir, "log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "video": rel(video, pdir), "size": size,
                                    "mtime": mtime, "model": model, "effort": effort, "error": why[:300], "cost_usd": cost},
                                   ensure_ascii=False) + "\n")
        raise NoAnswer(f"the critic didn't answer: {why}", cost)
    warnings = r["warnings"] + warns + ([warn] if warn else [])
    if failed:
        warnings.append(f"Claude Code reported an error ({res.get('subtype') or 'exit ' + str(r['returncode'])}), "
                        f"but the critique came through whole")
    stem = os.path.splitext(os.path.basename(video))[0]
    if out:
        mdp, jp = os.path.join(od, "critic.md"), os.path.join(od, "critic.json")
    else:
        nn = [int(x.group(1)) for f in os.listdir(cdir) for x in [re.fullmatch(re.escape(stem) + r"_(\d{2,})\.json", f)] if x]
        base = os.path.join(cdir, f"{stem}_{max(nn or [0]) + 1:02d}")
        mdp, jp = base + ".md", base + ".json"
    used = list((res.get("modelUsage") or {}).keys())
    d = {"video": rel(video, pdir), "size": size, "mtime": mtime, "kind": kind, "range": rng, **data,
         "model": model, "model_used": used[0] if len(used) == 1 else used or None, "effort": effort,
         "cost_usd": cost, "duration_s": r["duration_s"], "at": time.strftime("%Y-%m-%d %H:%M:%S"), "file": rel(mdp, pdir),
         "pictures": [p for p, _ in pics], "focus": focus or "", "warnings": warnings,
         "concept": rel(concept, pdir) if concept else ("work/concept.md" if os.path.exists(os.path.join(pdir, "work", "concept.md")) else None)}
    open(mdp, "w", encoding="utf-8").write(md.rstrip() + "\n")
    jsave(jp, d)
    if not out:
        with open(os.path.join(cdir, "log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({k: d.get(k) for k in ("at", "video", "size", "mtime", "model", "effort", "scores", "verdict",
                                                     "cost_usd", "file")}, ensure_ascii=False) + "\n")
    return d


def command(pdir, video, model=None, effort=None, max_usd=None, out=None, concept=None, focus="", fresh_look=True, python=None,
            pictures=()):
    """critic.py as a command list + env, for running it in the background (the director runner, the pitch round, the
    pipeline's gates)."""
    cmd = [python or sys.executable, "-W", "ignore", os.path.join(HERE, "critic.py"), video]
    for flag, v in (("--model", model), ("--effort", effort), ("--max-usd", max_usd), ("--out", out), ("--concept", concept),
                    ("--focus", focus), ("--pictures", ",".join(pictures or ()))):
        if v:
            cmd += [flag, str(v)]
    if not fresh_look:
        cmd.append("--no-look")
    return cmd, dict(os.environ, MV_PROJECT=os.path.abspath(pdir), PYTHONIOENCODING="utf-8", MV_IN_VENV="1")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--model", help=f"default: project.json director.critic_model, else {DEFAULT_MODEL}")
    ap.add_argument("--effort", help=f"default: project.json director.critic_effort, else {DEFAULT_EFFORT}")
    ap.add_argument("--focus", default="", help="what to look at especially")
    ap.add_argument("--concept", help="the intent to judge against (default work/concept.md; a pitch: its pitch.md)")
    ap.add_argument("--out", help="write critic.md + critic.json in this folder instead of work/critic/")
    ap.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help="spending cap for this critique")
    ap.add_argument("--no-look", action="store_true", help="use the pictures that are there; don't run eyes.py look")
    ap.add_argument("--pictures", default="", help="more stills to open, comma-separated (e.g. the style frames)")
    a = ap.parse_args()
    pdir = need_project()
    try:
        d = critique(pdir, a.video, a.model, a.effort, a.focus, a.concept, a.out, a.max_usd, not a.no_look,
                     log=lambda s: print(s, flush=True), pictures=[x for x in a.pictures.split(",") if x.strip()])
    except RuntimeError as e:
        cost = getattr(e, "cost", None)
        print(f"critic: couldn't run: {e}" + (f" (${cost:.4f} spent)" if cost else ""), flush=True)
        sys.exit(1)
    print("\n" + open(os.path.join(pdir, d["file"]), encoding="utf-8").read().rstrip() + "\n", flush=True)
    for w in d.get("warnings") or []:
        print(f"warning: {w}", flush=True)
    if d.get("pictures"):                      # made already: the director opens only the ones it needs
        print("the pictures it looked at: " + ", ".join(d["pictures"]), flush=True)
    print(result_line(d), flush=True)


if __name__ == "__main__":
    main()
