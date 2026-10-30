"""The pitch round: an optional step before the director. Several directors pitch the video at the same time, each from
a different angle, each proving its idea on the same short window of the song; the studio's critic looks at every
proof, one verdict ranks them (ambition first: the safe option never wins), and the user picks the one the director
builds (or, with --auto, the verdict's winner is built at once, in the same job). A run without a pitch round is
exactly as before, and the director that builds the winner has full reign as always.

  studio.py pitch <p> [--n 3] [--budget 20m] [--model M] [--effort high] [--critic-model sonnet] [--critic-effort E]
                      [--proof "chorus 1" | --proof 24-44] [--angles story,wild,...] [--seed-notes "..."] [--max-usd N]
                      [--auto [--direct-budget 90m] [--direct-effort max] [--direct-model M] [--direct-max-usd N]
                       [--ultracode]]
  studio.py direct <p> --pitch round-K/pN [...]   promote that pitch, then the director builds it (director.py)
  studio.py stop <p>                              a running round wraps up cleanly: each pitch ends, the critic and the
                                                  verdict run on what exists (a stop while they work ends them too)
  studio.py permtest <p> --pitch                  a pitch's sandbox checked with the real Claude Code (cents with Haiku)

work/pitches/round-K/
  round.json          the round: its settings, the proof window, the angles, the deadline, its state and stage; per
                      pitch its angle, state, cost (its session's latest running total), model_usage, agents, turns,
                      title, idea, proof, proof_av, style frames, the critic's summary, error, the last log lines; the
                      verdict; picked; the costs (pitches, critic, verdict, total); warnings
  p1/ ... pN/         each pitch's folder, the only place its director may write: pitch.md, style/, scenes/ (lib/ for
                      shared code), proof.mp4, an optional draft plan.json, its notes; then the studio's proof_av.mp4
                      (the proof with the song's audio at the window, AAC), critic.md + critic.json (the critic on the
                      proof), eyes/ (the pictures)
  sessions/pN/        its session: brief.md, command.json, settings.json (its sandbox), events.jsonl, messages.md,
                      stderr.log, status.json (its live log, its agents, its costs)
  verdict.md + .json  the verdict: the ranking, the winner, why, the ideas worth grafting from the others

The angles (each pitch a different start): the story, the wild card, the sound and the structure, one medium pushed to
its limit, against the grain; with the user's ideas in the brief, "your ideas taken to the extreme" comes first.
The proof window, the same for every pitch, chosen by the studio: the strongest ★★★ moment with music on both sides
(a chorus start or a drop first, then the biggest lift), from 2 bars before it to about 16 s after, on downbeats,
12-24 s, inside the song; --proof sets it (a section name: the same rule around its start; or a-b in seconds).
Each pitch is a headless director at pitch scale (mv/session.py, like `direct`): the director's sandbox plus deny
rules on writes outside its own pitch folder (the other pitches, final/, report.md, review/, project.json, song/,
work/scenes/, work/kit/, work/plan.json, work/concept.md, ...), cwd = the project folder, its own time budget, all N at
once (their renders wait in the render queue), agents its choice. The studio sends each one time warnings, a reminder
when something is missing, and one push to go further when it ends early with time left; at the deadline plus a grace
it ends the session. Then: proof_av.mp4 for each proof; the critic on each (critic.py --concept pN/pitch.md --out pN/,
at most 2 at once); one verdict call (the critic's model, templates/pitch-verdict.md, read-only tools).
The sandbox stops a pitch's file tools, not every command it runs: files in the project's own folders that change
during the round are named in round.json's warnings.
"""
import argparse, glob, os, re, shutil, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from mv.common import delivery, ffmpeg, frames_of, h264_args, jload, jsave, probe, song_file, project as load_pj  # noqa: E402
from mv.claude import claude_print, kill_tree, READ_ONLY  # noqa: E402
from mv.session import Session  # noqa: E402
import director as D  # noqa: E402

TEMPLATES = os.path.join(STUDIO, "templates")
PITCHES = os.path.join("work", "pitches")
STOP = os.path.join("work", "pitches", "STOP")
MIN_N, MAX_N = 2, 5
VERDICT_MAX_USD = 3.0
CRITIC_AT_ONCE = 2                 # critiques at once (each runs the eyes, then a Claude call)
FRESH_S = 180                      # a round whose runner stopped writing round.json this long ago was cut off

ANGLES = {
    "story": ("the story",
              "a protagonist and what happens to them. Someone (or something) we follow through the song, who wants "
              "something, is changed by the song's turns and ends up somewhere other than where they started; the "
              "sections are its chapters and the ★★★ moments are what happens to it. Not a montage of moods: a story a "
              "viewer could retell after one watch."),
    "wild": ("the wild card",
             "the most unexpected idea that still belongs to this song. The one nobody else in the round will have: a "
             "premise that makes people ask how anyone thought of it, and then they can't imagine the song without it. "
             "It comes out of this song (a line, a sound, its shape), not out of strangeness for its own sake."),
    "structure": ("the sound and the structure",
                  "the song's form as choreography. Its sections, repeats and ★★★ moments are the acts; its instruments "
                  "are the cast (the drums, the bass, the voice, each a performer with its own way of moving); the "
                  "arrangement made visible, so a viewer sees the song being built up and taken apart. Not a "
                  "visualiser: a choreography of the form, composed bar by bar, whose rules change exactly where the "
                  "song changes."),
    "medium": ("one medium pushed to its limit",
               "one material, taken further than anyone would: only type, or one continuous line, or paper, or a single "
               "3D object, or light alone. Everything the video does, it does with that one thing; the limit is the "
               "idea, and the ★★★ moments are where the medium does what nobody thought it could."),
    "against": ("against the grain",
                "the obvious reading of this song, turned inside out: if it sounds sad, a picture of joy that makes it "
                "sadder; if it's loud, a stillness that makes it louder; if the lyric says one thing, a picture that "
                "shows what it hides. Committed, not ironic: the contrast is the idea, and it has to hold for the whole "
                "song."),
    "ideas": ("your ideas taken to the extreme",
              "my own ideas for this video (in my notes below), taken further than I dared: the version of them I "
              "wouldn't have thought possible. Keep what I meant; push how far it goes."),
}
ORDER = ("story", "wild", "structure", "medium", "against")
ORDER_IDEAS = ("ideas", "story", "wild", "structure", "medium")

PITCH_SYSTEM = ("You are running unattended inside Music Video Studio as one of several directors pitching one music "
                "video at the same time (pitch {k} of {n}, pitch round {round}). Nobody answers questions: decide and keep "
                "going. Write only inside your pitch folder {dir}/ (the studio tools may be read and run, not edited; "
                "the project's own files and the other pitches' folders are not yours). No network: everything you "
                "need is installed. Watch the clock.")
STOP_MSG = ("The user stopped the pitch round. Stop now: don't start anything new, and stop any agent of yours that is "
            "still running (TaskStop). If pitch.md isn't written yet, write it in a few lines from what you have, and "
            "end.")
NUDGE = ("You have {left} left and your pitch is delivered. Before you end: look at your style frames and your proof "
         "against your angle once more. Is it unmistakably yours, the boldest version you can make well, something the "
         "user will remember next to the other pitches? Is the signature moment in the proof the idea at full strength? "
         "If you can see how to push it further in the time left, do it, render the proof again and update pitch.md; "
         "if this is your best, end your turn: nothing else is needed.")


def rel(p, start):
    return os.path.relpath(p, start).replace("\\", "/")


def mmss(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:04.1f}"


def est(sec):
    """A render estimate in words: '40 s', '12 min', '1 h 05 min'."""
    sec = max(0, float(sec))
    return f"{int(round(sec))} s" if sec < 90 else D.fmt_dur(sec)


def round_dir(pdir, k):
    return os.path.join(pdir, PITCHES, f"round-{int(k)}")


def round_numbers(pdir):
    out = []
    for d in glob.glob(os.path.join(pdir, PITCHES, "round-*")):
        m = re.fullmatch(r"round-(\d+)", os.path.basename(d))
        if m and os.path.isdir(d):
            out.append(int(m.group(1)))
    return sorted(out)


def stop_mtime(pdir):
    try:
        return os.path.getmtime(os.path.join(pdir, STOP))
    except OSError:
        return None


def request_stop(pdir):
    """Ask a running pitch round to stop (director.request_stop writes this too, with the director's STOP)."""
    os.makedirs(os.path.join(pdir, PITCHES), exist_ok=True)
    with open(os.path.join(pdir, STOP), "w") as f:
        f.write(f"{time.time():.3f}")


# ------------------------------------------------------------------ the angles and the proof window
def has_ideas(pj):
    return bool(str(pj.get("ideas") or "").strip())


def pick_angles(pj, n, wanted=None):
    """The angles for n pitches: the user's choice (--angles), else the default order (with the user's ideas first
    when they wrote some)."""
    if wanted:
        keys = [x.strip().lower() for x in wanted if x.strip()]
        bad = [x for x in keys if x not in ANGLES]
        if bad:
            raise SystemExit(f"no angle {', '.join(bad)}: the angles are {', '.join(ANGLES)}")
        if len(set(keys)) != len(keys):
            raise SystemExit("each pitch needs its own angle: --angles names one twice")
        if "ideas" in keys and not has_ideas(pj):
            raise SystemExit("the angle 'ideas' takes the user's ideas to the extreme: the brief has no ideas yet")
        return keys
    order = ORDER_IDEAS if has_ideas(pj) else ORDER
    return list(order[:n])


def moment_score(S, m):
    """How strong a ★★★ moment is as the proof's centre: a chorus start or a drop first, then the biggest lift, a key
    change, the loudest stretch; a little for each other thing that happens there."""
    kinds = str(m.get("kind") or "").split("+")
    txt = str(m.get("text") or "")
    sec = S.section_at(float(m["t"]) + 0.05)
    chorus = "section" in kinds and sec is not None and (sec.get("label") == "chorus" or "chorus" in str(sec.get("name")).lower())
    lift = max([float(x) for x in re.findall(r"\+(\d+(?:\.\d+)?) LU", txt)] or [0.0])
    return ((4.0 if chorus or "drop" in kinds else 0.0) + lift / 3 + 2.0 * ("key change" in kinds) + 1.0 * ("peak" in kinds)
            + 0.5 * sum(k in kinds for k in ("high note", "stop", "a cappella", "enters", "tempo change")) + 0.25 * len(kinds))


def window_around(S, t, fps, n):
    """(a, b, t0, t1): [a, b) song frames, from the downbeat 2 bars before the bar of t to the downbeat nearest to
    t + 16 s, 12-24 s long, inside the song (a moment placed just before its downbeat, like a stop, counts for that
    bar); t0 and t1 the exact times they were taken from (for bar:beat)."""
    dbs = S.downbeats or [0.0]
    p = S.pos(t)
    bar = p[0] if p else 0
    if p and p[1] > 3.5 and bar + 1 < len(dbs):
        bar += 1
    t0 = dbs[max(0, min(len(dbs) - 1, bar - 2))] if bar >= 2 else (0.0 if dbs[0] < 1.0 else dbs[0])
    dur = S.duration
    cands = [d for d in dbs if 12.0 <= d - t0 <= 24.0] + ([dur] if 12.0 <= dur - t0 <= 24.0 else [])
    if cands:
        t1 = min(cands, key=lambda d: abs(d - (t + 16.0)))
    else:
        t1 = min(dur, t0 + 24.0)
        if t1 - t0 < 12.0:                     # the song ends too soon: start earlier
            back = [d for d in dbs if d <= t1 - 12.0]
            t0 = back[-1] if back else 0.0
    a, b = int(round(t0 * fps)), min(n, int(round(t1 * fps)))
    return a, max(a + 1, b), t0, t1


def pos_str(S, t):
    if t >= S.duration - 0.05:
        return "the end"
    if t < (S.downbeats or [0.0])[0] - 0.05:
        return "the start"
    p = S.pos(t)
    return f"{p[0]}:{round(p[1], 1):g}" if p else "-"


def proof_window(pdir, spec=None):
    """The proof window: {t0, t1, a, b, fps, seconds, pos0, pos1, moment, why, spec, sections}. spec: None (the studio
    chooses), a section name, or 'a-b' in seconds."""
    from mv.song import Song
    S = Song(pdir)
    fps = delivery(load_pj(pdir))["fps"]
    n = int(round(S.duration * fps))
    moment, why = None, ""
    spec = str(spec or "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", spec) if spec else None
    if m:
        x, y = float(m.group(1)), float(m.group(2))
        if not 0 <= x < y <= S.duration + 0.01:
            raise SystemExit(f"--proof {spec}: give a window inside the song (0 to {S.duration:.2f} s), the start first")
        if y - x < 2:
            raise SystemExit(f"--proof {spec}: a proof of at least 2 s, please (12-24 s is best)")
        a, b = int(round(x * fps)), min(n, int(round(y * fps)))
        x0, x1 = x, y
        why = "the window you asked for"
    elif spec:
        sec = next((s for s in S.sections if s["name"].lower() == spec.lower()), None)
        if not sec:
            raise SystemExit(f"--proof {spec!r}: no such section ({', '.join(s['name'] for s in S.sections)}), or give a-b in seconds")
        a, b, x0, x1 = window_around(S, float(sec["t0"]), fps, n)
        moment = {"t": round(float(sec["t0"]), 3), "pos": pos_str(S, float(sec["t0"])), "text": f"{sec['name']} starts"}
        why = f"{sec['name']}, the section you asked for: it starts at {mmss(sec['t0'])}"
    else:
        last = S.beats[-1] if S.beats else S.duration
        ok = []
        for x in S.moments(3):
            kinds = str(x.get("kind") or "").split("+")
            if "ending" in kinds or not set(kinds) - {"music starts", "music ends", "beat grid ends"}:
                continue
            p = S.pos(float(x["t"]))
            if p and p[0] >= 2 and last - float(x["t"]) >= 8.0:        # music on both sides
                ok.append(x)
        if not ok:                                # nothing qualifies: the loudest section's start
            secs = sorted(S.sections[1:] or S.sections, key=lambda s: -float(s.get("loud") or -99))
            if secs:
                ok = [{"t": float(secs[0]["t0"]), "pos": pos_str(S, float(secs[0]["t0"])), "text": f"{secs[0]['name']} starts (the loudest section)",
                       "kind": "section"}]
        if ok:
            best = max(ok, key=lambda x: (moment_score(S, x), -float(x["t"])))
            moment = {"t": round(float(best["t"]), 3), "pos": best.get("pos") or pos_str(S, float(best["t"])), "text": best.get("text")}
            a, b, x0, x1 = window_around(S, float(best["t"]), fps, n)
            why = f"{best.get('text')} at {mmss(best['t'])} (bar:beat {moment['pos']}), the song's strongest ★★★ moment with music on both sides"
        else:                                     # no moments at all: the middle of the song
            mid = S.duration / 2
            a, b, x0, x1 = window_around(S, mid, fps, n)
            why = "the middle of the song (the song map has no ★★★ moments)"
    t0, t1 = a / fps, b / fps
    secs = [s["name"] for s in S.sections if s["t1"] > t0 + 0.05 and s["t0"] < t1 - 0.05]
    return {"t0": round(t0, 3), "t1": round(t1, 3), "a": a, "b": b, "fps": fps, "seconds": round((b - a) / fps, 2),
            "pos0": pos_str(S, x0), "pos1": pos_str(S, x1), "moment": moment, "why": why, "spec": spec or None,
            "sections": secs}


def window_text(w):
    return (f"{mmss(w['t0'])}–{mmss(w['t1'])} of the song ({w['seconds']:g} s, bar:beat {w['pos0']} to {w['pos1']}, "
            f"song frames {w['a']}:{w['b']} at {w['fps']} fps): {w['why']}")


# ------------------------------------------------------------------ what it takes on this machine
def speeds_rows(pdir):
    """[(renderer, seconds)] for the whole song's final render on this machine (tools/speeds.json), at the delivery
    size; [] without a benchmark."""
    sp = (jload(os.path.join(HERE, "speeds.json"), {}) or {}).get("renderers") or {}
    d = delivery(load_pj(pdir))
    sm = jload(os.path.join(pdir, "song", "song-map.json"), {}) or {}
    n = float(sm.get("duration") or 0) * d["fps"]
    px = d["width"] * d["height"] / (1920 * 1080)
    out = []
    for label, key, row in (("the web engine, 2D canvas", "web2d", "1080p_parallel"), ("the web engine, WebGL 3D", "web3d", "1080p_parallel"),
                            ("Blender Workbench", "blender_workbench", "1080p"), ("Blender Eevee", "blender_eevee", "1080p"),
                            ("Blender Cycles", "blender_cycles", "1080p")):
        r = sp.get(key) or {}
        s = (r.get(row) or r.get("1080p") or {}).get("s_per_frame")
        if s:
            out.append((label, float(s) * n * px))
    return out


def speeds_text(pdir):
    rows = speeds_rows(pdir)
    if not rows:
        return "(no benchmark on this machine yet: tools/speeds.json has no rows; `studio.py benchmark` measures them)"
    return "; ".join(f"{label} ≈ {est(s)}" for label, s in rows) + " (plus the encode and the checks: a few minutes)"


def proof_estimate(pdir, w):
    """The proof's render time at preview size with the web engine, on its own (tools/speeds.json)."""
    sp = (jload(os.path.join(HERE, "speeds.json"), {}) or {}).get("renderers") or {}
    s = ((sp.get("web2d") or {}).get("540p_preview") or {}).get("s_per_frame") or 0.45
    d = delivery(load_pj(pdir))
    px = max(0.25, (d["width"] * d["height"] / 4) / (960 * 540))
    return est((w["b"] - w["a"]) * s * px + 20)


def direct_budget_text(pdir, auto=None):
    b = (auto or {}).get("budget") or ((load_pj(pdir).get("director") or {}).get("budget")) or "90m"
    try:
        return D.fmt_dur(D.parse_budget(b))
    except ValueError:
        return "about 90 min"


# ------------------------------------------------------------------ a pitch's sandbox
def deny_rules(pdir, rdir, k, n):
    """The deny rules a pitch's session gets on top of the director's: no writes outside its own pitch folder where it
    matters (the project's own files, the other pitches, the round's own files, other rounds)."""
    out = []

    def no(path, tree=False):
        p = D.rule_path(path) + ("/**" if tree else "")
        out.extend([f"Edit({p})", f"Write({p})"])
    for f in ("report.md", "project.json", "work/plan.json", "work/concept.md", "work/live.json", "work/song-notes.md",
              "work/log.md", "work/cast.md"):
        no(os.path.join(pdir, *f.split("/")))
    for d in ("final", "review", "song", "refs", "work/scenes", "work/kit", "work/director", "work/style-frames",
              "work/previews", "work/renders"):
        no(os.path.join(pdir, *d.split("/")), tree=True)
    for j in range(1, n + 1):
        if j != k:
            no(os.path.join(rdir, f"p{j}"), tree=True)
    for f in ("round.json", "verdict.md", "verdict.json"):
        no(os.path.join(rdir, f))
    no(os.path.join(rdir, "sessions"), tree=True)
    no(os.path.join(pdir, STOP))
    for other in glob.glob(os.path.join(pdir, PITCHES, "round-*")):
        if os.path.normcase(os.path.abspath(other)) != os.path.normcase(os.path.abspath(rdir)):
            no(other, tree=True)
    return out


WATCHED_DIRS = ("final", "song", "work/scenes", "work/kit", "work/style-frames", "work/renders", "work/previews")
WATCHED_FILES = ("report.md", "work/plan.json", "work/concept.md", "work/live.json", "work/song-notes.md", "work/log.md",
                 "work/cast.md")


def snapshot(pdir):
    """{path: (size, mtime)} of the project's own files a pitch must not change (to say so after the round)."""
    out = {}
    for d in WATCHED_DIRS:
        for dp, dirs, files in os.walk(os.path.join(pdir, *d.split("/"))):
            dirs[:] = [x for x in dirs if x not in ("__pycache__",)]
            for f in files:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                    out[rel(p, pdir)] = (st.st_size, round(st.st_mtime, 3))
                except OSError:
                    pass
    for f in WATCHED_FILES:
        p = os.path.join(pdir, *f.split("/"))
        if os.path.isfile(p):
            st = os.stat(p)
            out[f] = (st.st_size, round(st.st_mtime, 3))
    return out


# ------------------------------------------------------------------ a pitch's files
def summary(md):
    """(title, idea) of a pitch.md: its first '# ' heading and its '**The idea:**' line (else its first paragraph)."""
    md = md or ""
    t = re.search(r"^#\s+(.+?)\s*$", md, re.M)
    title = re.sub(r"^(pitch\s*\d*\s*[:·-]\s*)", "", t.group(1), flags=re.I).strip() if t else ""
    m = re.search(r"\*\*The idea:?\*\*:?\s*([\s\S]+?)(?:\n\s*\n|\n\*\*|\n#|\Z)", md)
    if m:
        idea = m.group(1)
    else:
        body = md[t.end():] if t else md
        para = [x for x in re.split(r"\n\s*\n", body) if x.strip() and not x.lstrip().startswith(("#", "|", "```"))]
        idea = para[0] if para else ""
    return title[:200], re.sub(r"\s+", " ", idea).strip()[:600]


def pitch_files(pdir, d, since=0.0):
    """What a pitch folder has: {pitch_md, proof, style: [...], scenes: [...], plan, proof_av, critic}."""
    def there(*x):
        p = os.path.join(d, *x)
        return rel(p, pdir) if os.path.isfile(p) and os.path.getsize(p) > 0 else None
    proof = there("proof.mp4")
    if proof and os.path.getmtime(os.path.join(pdir, proof)) < since:
        proof = None                               # (left over from before the round: never so, but never trusted)
    style = sorted(rel(f, pdir) for f in glob.glob(os.path.join(d, "style", "*"))
                   if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    scenes = []
    for dp, dirs, files in os.walk(os.path.join(d, "scenes")):
        dirs[:] = sorted(x for x in dirs if x not in ("__pycache__", "node_modules") and not x.endswith(".parts"))
        scenes += [rel(os.path.join(dp, f), pdir) for f in sorted(files) if f.lower().endswith((".js", ".mjs", ".html", ".htm", ".py"))]
    return {"pitch_md": there("pitch.md"), "proof": proof, "style": style, "scenes": scenes, "plan": there("plan.json"),
            "proof_av": there("proof_av.mp4"), "critic": there("critic.json")}


# ------------------------------------------------------------------ one pitch
class Pitch:
    """One pitch of a round: its folder, its session (mv/session.py: busy/idle, agents, the running costs), the
    studio's messages to it (the time warnings, a reminder when something is missing, one push when it ends early),
    and its end."""

    def __init__(self, R, k, angle):
        self.R, self.k, self.angle = R, k, angle
        self.name = f"p{k}"
        self.dir = os.path.join(R.rdir, self.name)
        self.sdir = os.path.join(R.rdir, "sessions", self.name)
        self.status = {"pitch": f"round-{R.k}/{self.name}", "angle": angle}
        self.s = None
        self.warned, self.reminders, self.nudges, self.style_asked = set(), 0, 0, False
        self.deadline, self.grace = R.deadline, R.grace
        self.stop_reason = None
        self.ended_at = None
        self.state, self.error = "starting", ""
        self.last_ev = None                       # the last live-log line printed
        self.closing_at = None
        self.critic, self.critic_cost = None, 0.0

    # ------------------------------------------------------------------ the start
    def start(self):
        R = self.R
        os.makedirs(os.path.join(self.dir, "style"), exist_ok=True)
        os.makedirs(os.path.join(self.dir, "scenes"), exist_ok=True)
        os.makedirs(self.sdir, exist_ok=True)
        text = R.brief(self)
        with open(os.path.join(self.sdir, "brief.md"), "w", encoding="utf-8") as f:
            f.write(text)
        cmd = self.command()
        jsave(os.path.join(self.sdir, "command.json"), {"cmd": cmd, "cwd": R.pdir})
        if R.dry_run:
            self.state = "dry-run"
            return
        env = dict(os.environ, MV_PROJECT=R.pdir, PYTHONIOENCODING="utf-8", MV_RENDER_WHO=f"pitch {self.name} (round {R.k})",
                   MV_CRITIC_MODEL=R.critic_model, MV_CRITIC_EFFORT=R.critic_effort or "", MV_PITCH=f"round-{R.k}/{self.name}")
        self.s = Session(cmd, R.pdir, self.sdir, name=f"pitch {self.name}", status=self.status, env=env, started=R.started)
        self.s.start()
        self.state = "running"
        self.s.send(text, "the brief")

    def command(self):
        R = self.R
        return D.claude_args(R.pdir, R.model, R.effort, max_usd=R.max_usd, extra_deny=deny_rules(R.pdir, R.rdir, self.k, R.n),
                             settings_path=os.path.join(self.sdir, "settings.json"),
                             system=PITCH_SYSTEM.format(k=self.k, n=R.n, round=R.k, dir=rel(self.dir, R.pdir)))

    def files(self):
        return pitch_files(self.R.pdir, self.dir, self.R.started)

    def send(self, text, why):
        return self.s.send(text, why) if self.s else False

    # ------------------------------------------------------------------ every second
    def step(self, now):
        """One second of the pitch: the studio's messages when they're due, and when its turn is over (none of its
        agents running): a reminder, the push to go further, or the end. False once its session is gone."""
        s, R = self.s, self.R
        if not s or not s.alive():
            return False
        left = self.deadline - now
        if self.status.get("budget_hit") and self.stop_reason is None:
            self.stop_reason = f"its cost cap (${R.max_usd:g}) was reached"
            self.deadline, self.grace = min(self.deadline, now), 60
            self.warned.add("up")
        if self.stop_reason is None:
            self.warnings(now, left)
        if left <= -self.grace:                  # closed now; stopped with what it started if it doesn't end soon
            self.stop_reason = self.stop_reason or "its time ran out (the deadline and the grace)"
            if not s.closed:
                s.note(f"■ ending the session: {self.stop_reason}")
                s.close_input()
            if self.closing_at is None:          # (its input may have closed earlier, at the end of a turn, and the
                self.closing_at = now            # CLI kept working: a crossed message, an agent's late turn)
            elif now - self.closing_at > 20:
                s.note("■ stopped: it didn't end within 20 s of its input closing")
                kill_tree(s.proc)
            return s.alive()
        if s.turn_over(now) and not s.closed:
            s.ack()
            f = self.files()
            delivered = f["pitch_md"] and f["proof"]
            if self.stop_reason is not None:
                s.close_input()
            elif not delivered and left > 60 and self.reminders < 3:
                self.reminders += 1
                miss = [x for x, ok in (("pitch.md", f["pitch_md"]), ("proof.mp4", f["proof"]),
                                        ("the style frames in style/", f["style"])) if not ok]
                self.send(f"Your pitch isn't complete yet: {', '.join(miss)} {'is' if len(miss) == 1 else 'are'} missing in "
                          f"{rel(self.dir, R.pdir)}/, and {D.fmt_dur(left)} are left. Deliver {'it' if len(miss) == 1 else 'them'} "
                          f"(the proof over frames {R.window['a']}:{R.window['b']}), then end your turn."
                          + (" If a render keeps failing, say why in pitch.md and deliver what you have." if self.reminders == 3 else ""),
                          "something missing")
            elif delivered and not f["style"] and left > 60 and not self.style_asked:
                self.style_asked = True
                self.send(f"Your pitch has no style frames yet (style/ is empty) and {D.fmt_dur(left)} are left: render 2-3 "
                          f"at signature moments (`--still`), then end your turn.", "no style frames")
            elif delivered and f["style"] and self.nudges < 1 and left > max(4 * 60, 0.3 * R.budget_s):
                self.nudges += 1                 # (only a delivered pitch is told it is: one that isn't was reminded)
                self.send(NUDGE.format(left=D.fmt_dur(left)), "push it further")
            else:
                s.close_input()
        return True

    def warnings(self, now, left):
        R = self.R
        if "half" not in self.warned and now - R.started >= 0.5 * R.budget_s and left > 0:
            self.warned.add("half")
            self.send(f"Half of your time is gone ({D.fmt_dur(left)} left, deadline {D.hm(self.deadline)})."
                      + ("" if self.files()["proof"] else " If proof.mp4 isn't rendering yet, start it now: the render "
                         "queue is shared with the other pitches, and your render may wait behind theirs."), "half")
        if "five" not in self.warned and 0 < left <= min(5 * 60, 0.25 * R.budget_s):
            self.warned.add("five")
            self.send(f"{D.fmt_dur(left)} left: finish pitch.md and the proof you have. Nothing new.", "time running out")
        if "up" not in self.warned and left <= 0:
            self.warned.add("up")
            self.send(f"Time is up. Stop working now, and stop any agent of yours that is still running (TaskStop). Make "
                      f"sure pitch.md is written (a few lines are fine) and end. You have {D.fmt_dur(self.grace)} before the "
                      f"session is closed.", "time's up")

    def user_stop(self, now):
        if self.s and self.s.alive() and self.stop_reason is None:
            self.stop_reason = "stopped by the user"
            self.send(STOP_MSG, "stop")
        self.deadline, self.grace = min(self.deadline, now), 60

    # ------------------------------------------------------------------ the end
    def end(self, reason="the round ended"):
        if self.s:
            if self.s.alive():
                self.s.stop(self.stop_reason or reason)
            self.s.finish()
        self.ended_at = self.ended_at or time.time()
        f = self.files()
        if self.state == "dry-run":
            return
        if f["pitch_md"] and f["proof"]:
            self.state = "done"
        elif f["pitch_md"]:
            self.state, self.error = "no proof", f"no proof.mp4 ({self.why_ended()})"
        else:
            self.state = "stopped" if self.stop_reason == "stopped by the user" else "failed"
            self.error = f"no pitch.md ({self.why_ended()})"

    def why_ended(self):
        if self.stop_reason:
            return self.stop_reason
        rc = self.s.returncode if self.s else None
        if rc:
            err = ""
            try:
                err = [x for x in open(os.path.join(self.sdir, "stderr.log"), encoding="utf-8", errors="replace").read().splitlines()
                       if x.strip()][-1][:200]
            except (OSError, IndexError):
                pass
            return f"its session ended with exit {rc}" + (f": {err}" if err else "")
        return "its session ended"

    def save(self):
        try:
            with self.s.lock:
                st = dict(self.status)
                st["events"] = list(self.status.get("events") or [])
            jsave(os.path.join(self.sdir, "status.json"), st | {"updated": time.time(), "state": self.state})
        except Exception:
            pass

    def record(self):
        """Its entry in round.json."""
        R, f, st = self.R, self.files(), self.status
        title, idea = ("", "")
        if f["pitch_md"]:
            try:
                title, idea = summary(open(os.path.join(R.pdir, f["pitch_md"]), encoding="utf-8").read())
            except OSError:
                pass
        ev = [e.get("text") for e in (st.get("events") or [])[-6:]]
        c = self.critic or {}
        return {"k": self.k, "angle": self.angle, "angle_name": ANGLES[self.angle][0], "dir": rel(self.dir, R.pdir),
                "session": rel(self.sdir, R.pdir), "state": self.state, "error": self.error,
                "started": R.started, "ended": self.ended_at, "elapsed_s": round((self.ended_at or time.time()) - R.started),
                "cost_usd": round(float(st.get("cost_usd") or 0), 4), "model_usage": st.get("model_usage") or {},
                "model_used": st.get("model_used"), "agents": st.get("agents") or [], "turns": st.get("turns") or 0,
                "working": bool(st.get("working")), "budget_hit": bool(st.get("budget_hit")),
                "title": title, "idea": idea, "pitch": f["pitch_md"], "proof": f["proof"], "proof_av": f["proof_av"],
                "style": f["style"], "scenes": f["scenes"], "plan": f["plan"],
                "critic": ({k: c.get(k) for k in ("file", "scores", "verdict", "safe", "keep", "cost_usd", "model", "error")}
                           | {"problems": (c.get("problems") or [])[:3]}) if c else None,
                "critic_cost_usd": round(self.critic_cost, 4), "log": ev}


# ------------------------------------------------------------------ the round
class Round:
    """One pitch round (work/pitches/round-K/): N pitches in parallel, then the proofs with the song's audio, the critic
    on each proof, the verdict; round.json all along (the web app polls it)."""

    def __init__(self, pdir, n=3, budget="20m", model=None, effort=None, critic_model=None, critic_effort=None, proof=None,
                 seed_notes="", max_usd=None, angles=None, auto=None, dry_run=False, log=print):
        import critic
        self.pdir = os.path.abspath(pdir)
        if not os.path.exists(os.path.join(self.pdir, "song", "song-map.json")):
            raise SystemExit("no song folder yet: run ingest first")
        if not dry_run and D.run_active(self.pdir):
            raise SystemExit("a director run is going on in this project: let it finish (or `studio.py stop`) first")
        if not dry_run and round_active(self.pdir):
            raise SystemExit("a pitch round is going on in this project already")
        pj = load_pj(self.pdir)
        wanted = [x for x in re.split(r"[,\s]+", angles or "") if x] if isinstance(angles, str) else list(angles or [])
        n = int(n if n else (len(wanted) or 3))
        if wanted and len(wanted) != n:
            raise SystemExit(f"--angles names {len(wanted)} angle{'s' * (len(wanted) != 1)} for --n {n}: one per pitch")
        if not MIN_N <= n <= MAX_N:
            raise SystemExit(f"--n {n}: a pitch round has {MIN_N} to {MAX_N} pitches")
        if max_usd is not None and float(max_usd) <= 0:
            raise SystemExit("--max-usd must be more than 0 (leave it out for no cap)")
        self.n, self.angles = n, pick_angles(pj, n, wanted)
        self.budget_s = D.parse_budget(budget or "20m")
        self.model = model or (pj.get("director") or {}).get("model") or "opus"
        self.effort = effort or "high"
        cm, ce = critic.settings(pj)
        self.critic_model, self.critic_effort = critic_model or cm, critic_effort or ce
        if self.critic_model == "off":
            raise SystemExit("the pitch round needs the critic (it looks at every proof, and the verdict is its call): "
                             "--critic-model sonnet, haiku or opus")
        self.max_usd = float(max_usd) if max_usd else None
        self.seed_notes, self.auto, self.dry_run, self.log = seed_notes or "", auto, dry_run, log
        self.window = proof_window(self.pdir, proof)
        os.makedirs(os.path.join(self.pdir, PITCHES), exist_ok=True)
        self.k = 1 + max(round_numbers(self.pdir) or [0])
        self.rdir = round_dir(self.pdir, self.k)
        os.makedirs(self.rdir)
        self.started = time.time()
        self.deadline = self.started + self.budget_s
        self.grace = min(300, max(90, int(0.1 * self.budget_s)))
        self.pitches = [Pitch(self, i + 1, a) for i, a in enumerate(self.angles)]
        self.state, self.stage = ("dry-run", "dry-run") if dry_run else ("running", "pitching")
        self.stop_reason = None
        self.stop_at = None                      # the STOP the pitches saw (a newer one ends the critic and the verdict)
        self.verdict, self.warnings = None, []
        self.picked = None
        self.procs = {}                          # the critic's and the verdict's processes running now (a stop ends them)
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ the brief
    def brief(self, p):
        pj = load_pj(self.pdir)
        d = delivery(pj)
        sm = jload(os.path.join(self.pdir, "song", "song-map.json"), {}) or {}
        tools = rel(os.path.join(STUDIO, "tools"), self.pdir)
        w = self.window
        others = [f"{ANGLES[q.angle][0]} (p{q.k})" for q in self.pitches if q is not p]
        name = os.path.basename(self.pdir)
        notes = D.user_notes(pj, self.seed_notes)
        refs = D.refs_brief(self.pdir, pj).replace("work/cast.md", f"{rel(p.dir, self.pdir)}/cast.md")
        vals = {"n": self.n, "k": p.k, "angle_name": ANGLES[p.angle][0], "angle_text": ANGLES[p.angle][1],
                "others": " and ".join([", ".join(others[:-1]), others[-1]] if len(others) > 1 else others),
                "direction": ("\n" + notes + "\n") if notes else "", "dir": rel(p.dir, self.pdir), "tools": tools,
                "python": D.venv_python(self.pdir), "project": name, "fps": d["fps"], "width": d["width"], "height": d["height"],
                "frames": int(round(float(sm.get("duration") or 0) * d["fps"])), "speeds": speeds_text(self.pdir),
                "direct_budget": direct_budget_text(self.pdir, self.auto), "budget": D.fmt_dur(self.budget_s),
                "started": D.hm(self.started), "deadline": D.hm(self.deadline) + f" (in {D.fmt_dur(self.budget_s)})", "deadline_hm": D.hm(self.deadline),
                "halfway": D.hm(self.started + self.budget_s / 2), "skill": rel(D.SKILL, self.pdir),
                "anti": rel(os.path.join(os.path.dirname(D.SKILL), "anti-defaults.md"), self.pdir), "refs": refs,
                "proof_from": mmss(w["t0"]), "proof_to": mmss(w["t1"]), "proof_len": f"{w['seconds']:g}", "proof_why": w["why"],
                "proof_pos0": w["pos0"], "proof_pos1": w["pos1"], "a": w["a"], "b": w["b"],
                "proof_sections": f" ({', '.join(w['sections'])})" if w["sections"] else "",
                "proof_est": proof_estimate(self.pdir, w)}
        return D.fill(open(os.path.join(TEMPLATES, "pitch-brief.md"), encoding="utf-8").read(), vals)

    # ------------------------------------------------------------------ the record
    def costs(self):
        ps = round(sum(float(p.status.get("cost_usd") or 0) for p in self.pitches), 4)
        cr = round(sum(p.critic_cost for p in self.pitches), 4)
        vd = round(float((self.verdict or {}).get("cost_usd") or 0), 4)
        return {"pitches": ps, "critic": cr, "verdict": vd, "total": round(ps + cr + vd, 4)}

    def data(self):
        c = self.costs()
        return {"round": self.k, "n": self.n, "dir": rel(self.rdir, self.pdir), "budget": D.fmt_dur(self.budget_s),
                "budget_s": self.budget_s, "model": self.model, "effort": self.effort, "critic_model": self.critic_model,
                "critic_effort": self.critic_effort, "max_usd": self.max_usd, "seed_notes": self.seed_notes,
                "auto": self.auto, "proof": self.window,
                "angles": [{"k": p.k, "key": p.angle, "name": ANGLES[p.angle][0]} for p in self.pitches],
                "started": self.started, "deadline": self.deadline, "grace_s": self.grace, "updated": time.time(),
                "state": self.state, "stage": self.stage, "stop_reason": self.stop_reason,
                "pitches": [p.record() for p in self.pitches], "verdict": self.verdict, "picked": self.picked,
                "costs": c, "cost_usd": c["total"], "warnings": self.warnings}

    def save(self):
        with self.lock:
            d = self.data()
        path = os.path.join(self.rdir, "round.json")
        if d["picked"] is None:                  # a pick written meanwhile (direct --pitch) is never lost
            old = jload(path, None)
            if isinstance(old, dict) and old.get("picked"):
                d["picked"] = self.picked = old["picked"]
        jsave(path, d)
        return d

    def say(self, line):
        self.log(line)

    def show(self):
        """The pitches' new live-log lines, prefixed with their name (the job log shows them as they come)."""
        for p in self.pitches:
            if not p.s:
                continue
            with p.s.lock:
                evs = list(p.status.get("events") or [])
            i = next((j for j in range(len(evs) - 1, -1, -1) if evs[j] is p.last_ev), -1)
            new = evs[i + 1:]
            if new:
                p.last_ev = new[-1]
            for e in new:
                for ln in str(e.get("text") or "").splitlines():
                    if ln.strip():
                        self.say(f"{p.name} │ {ln[:200]}")

    # ------------------------------------------------------------------ the run
    def run(self):
        from studio import Status
        pdir = self.pdir
        if os.path.exists(os.path.join(pdir, STOP)):
            os.remove(os.path.join(pdir, STOP))
        steps = [f"{self.n} directors pitch at once ({D.fmt_dur(self.budget_s)})", "the proofs get the song's audio",
                 "the critic looks at each proof", "the verdict"]
        self.st = None if self.dry_run else Status(pdir, "pitch", steps)
        before = snapshot(pdir)
        w = self.window
        self.say(f"pitch round {self.k}: {self.n} pitches ({', '.join(ANGLES[a][0] for a in self.angles)}), {self.model}, "
                 f"effort {self.effort}, {D.fmt_dur(self.budget_s)} each (until {D.hm(self.deadline)})"
                 + (f", cap ${self.max_usd:g} each" if self.max_usd else "") + f"; the critic {self.critic_model}")
        self.say(f"the proof: {window_text(w)}")
        try:
            try:
                for p in self.pitches:
                    p.start()
                self.save()
                if self.dry_run:
                    self.state = self.stage = "dry-run"
                    self.save()
                    self.say(open(os.path.join(self.pitches[0].sdir, "brief.md"), encoding="utf-8").read())
                    self.say(f"(dry run: not started; the briefs and commands are in {rel(os.path.join(self.rdir, 'sessions'), pdir)}/)")
                    return self.data()
                self.pitching()
            finally:
                for p in self.pitches:
                    p.end("the round ended")
                if not self.dry_run:
                    self.save()
            changed = self.changed(before)
            if changed:
                self.warnings.append("these files outside the pitch folders changed during the round (by a pitch's command, "
                                     "which the sandbox doesn't stop the way it stops its file tools, or by you in the web "
                                     "app): " + ", ".join(changed[:8]) + (f" (+{len(changed) - 8} more)" if len(changed) > 8 else ""))
                self.say("⚠ " + self.warnings[-1])
            self.judge()
        except BaseException as ex:              # the round can't go on: said in round.json and the job's status
            if self.state not in ("done", "stopped"):
                self.state, self.stage = "failed", "done"
                self.warnings.append(f"the round stopped on an error: {type(ex).__name__}: {ex}"[:400])
                try:
                    self.save()
                except Exception:
                    pass
                if self.st:
                    self.st.done("error", str(ex)[:300] or type(ex).__name__)
            raise
        return self.data()

    def changed(self, before):
        after = snapshot(self.pdir)
        return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))

    def pitching(self):
        last_save = 0.0
        while True:
            time.sleep(1)
            now = time.time()
            try:
                if self.stop_reason is None and stop_mtime(self.pdir) is not None:
                    self.stop_reason, self.stop_at = "stopped by the user", stop_mtime(self.pdir)
                    self.say("■ the user stopped the round: each pitch wraps up")
                    for p in self.pitches:
                        p.user_stop(now)
                alive = [p for p in self.pitches if p.step(now)]
                self.show()
                if now - last_save >= 2:
                    last_save = now
                    for p in self.pitches:
                        p.save()
                    self.save()
            except Exception as ex:                  # a bug or a file in the way: noted, the round goes on
                self.trouble("a step of the round", ex)
                alive = [p for p in self.pitches if p.s and p.s.alive()]
                if now > self.deadline + self.grace + 60:
                    break
            if not alive:
                break
        for p in self.pitches:
            p.ended_at = p.ended_at or time.time()
            p.save()

    def trouble(self, what, ex):
        import traceback
        try:
            with open(os.path.join(self.rdir, "runner_errors.log"), "a", encoding="utf-8") as f:
                f.write(f"\n{time.strftime('%H:%M:%S')} {what}:\n{traceback.format_exc()}")
        except OSError:
            pass
        if time.time() - getattr(self, "t_trouble", 0) > 60:
            self.t_trouble = time.time()
            self.say(f"⚠ the studio hit an error in {what}: {type(ex).__name__}: {ex} ({rel(self.rdir, self.pdir)}/runner_errors.log); it carries on")

    def aborted(self):
        """A stop while the critic or the verdict works (newer than the one the pitches saw) ends those too."""
        m = stop_mtime(self.pdir)
        return m is not None and (self.stop_at is None or m > self.stop_at + 0.0005)

    # ------------------------------------------------------------------ after the pitches
    def judge(self):
        for p in self.pitches:
            self.say(f"{p.name} ({ANGLES[p.angle][0]}): {p.state}" + (f" ({p.error})" if p.error else "")
                     + f", ${float(p.status.get('cost_usd') or 0):.2f}")
        self.stage = "proofs"
        self.step_status(1)
        for p in self.pitches:
            if p.files()["proof"]:
                try:
                    self.proof_av(p)
                except Exception as ex:
                    p.error = (p.error + "; " if p.error else "") + f"the proof couldn't get the song's audio: {ex}"
                    self.say(f"⚠ {p.name}: {p.error}")
        self.save()
        self.stage = "critic"
        self.step_status(2)
        self.save()
        if not self.aborted():
            self.critique_all()
        self.save()
        self.stage = "verdict"
        self.step_status(3)
        self.save()
        if not self.aborted():
            try:
                self.verdict = self.make_verdict()
            except Exception as ex:
                self.verdict = {"error": f"{type(ex).__name__}: {ex}"}
                self.trouble("the verdict", ex)
        stopped = self.aborted() or self.stop_reason is not None
        if self.aborted():
            self.warnings.append("stopped by the user while the critic or the verdict worked: what was finished is kept")
        ok = any(p.state == "done" for p in self.pitches) or any(p.files()["pitch_md"] for p in self.pitches)
        self.state = "stopped" if stopped else "done" if ok else "failed"
        self.stage = "done"
        self.save()
        c = self.costs()
        v = self.verdict or {}
        if v.get("winner"):
            win = next((p for p in self.pitches if p.k == v["winner"]), None)
            self.say(f"the verdict: p{v['winner']} \"{win.record()['title'] if win else ''}\" wins ({v.get('by')})"
                     + (f"; ranking {', '.join('p' + str(x) for x in v.get('ranking') or [])}" if v.get("ranking") else ""))
        elif v.get("error"):
            self.say(f"the verdict: none ({v['error']})")
        self.say(f"pitch round {self.k} {self.state}: pitches ${c['pitches']:.2f} + the critic ${c['critic']:.2f} + the verdict "
                 f"${c['verdict']:.2f} = ${c['total']:.2f}; {rel(os.path.join(self.rdir, 'round.json'), self.pdir)}")
        if self.st:
            self.st.done("done" if self.state == "done" else "error",
                         "" if self.state == "done" else ("stopped by the user" if stopped else "no pitch was delivered"))

    def step_status(self, i):
        if self.st:
            try:
                self.st.step(i)
            except Exception:
                pass

    def proof_av(self, p):
        """proof_av.mp4: the proof's video copied (encoded only when it isn't H.264 yuv420p), the song's audio cut at
        the proof's own range (its .mv.json, else the round's window), AAC. A proof without a sidecar gets the
        window's, so the eyes and the critic place it in the song."""
        pdir = self.pdir
        src = os.path.join(pdir, p.files()["proof"])
        fps = self.window["fps"]
        side_p = src + ".mv.json"
        side = jload(side_p, None) or {}
        nfr = frames_of(src)
        pr = probe(src)
        vs = next(s for s in pr["streams"] if s["codec_type"] == "video")
        nfr = nfr or int(round(float(pr["format"].get("duration") or 0) * fps))
        if not side.get("range"):
            a = self.window["a"]
            side = dict(side, range=[a, a + nfr], fps=fps, t0=round(a / fps, 4), t1=round((a + nfr) / fps, 4),
                        note="the range written by the pitch round (the proof had no sidecar of its own)")
            jsave(side_p, side)
        a = int(side["range"][0])
        want = self.window["b"] - self.window["a"]
        if a != self.window["a"] or abs(nfr - want) > 1:
            self.warnings.append(f"{p.name}'s proof covers song frames {a}:{a + nfr}, not the window {self.window['a']}:"
                                 f"{self.window['b']}: its sound is cut at its own range")
        t0, dur = a / fps, nfr / fps
        song = song_file(pdir)
        out = os.path.join(p.dir, "proof_av.mp4")
        tmp = out + ".tmp.mp4"
        copy = vs.get("codec_name") == "h264" and vs.get("pix_fmt") == "yuv420p"
        vargs = ["-c:v", "copy"] if copy else [*h264_args("preview"), "-r", str(fps)]
        cmd = [ffmpeg(), "-v", "error", "-y", "-i", src, "-ss", f"{t0:.4f}", "-t", f"{dur:.4f}", "-i", song,
               "-map", "0:v:0", "-map", "1:a:0", *vargs, "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", tmp]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-300:] or f"ffmpeg exit {r.returncode}")
        os.replace(tmp, out)
        jsave(out + ".mv.json", dict(side, made_by="pitch round: proof + the song's audio",
                                     audio=f"the song from {t0:.3f} s for {dur:.3f} s, AAC 256k",
                                     video="stream copy" if copy else "encoded"))
        self.say(f"{p.name}: {rel(out, pdir)} (the proof with the song from {mmss(t0)} to {mmss(t0 + dur)})")

    def critique_all(self):
        """The critic on each proof (critic.py --concept pN/pitch.md --out pN/), at most CRITIC_AT_ONCE at once; a
        stop while it works ends the ones running."""
        import critic
        todo = [p for p in self.pitches if p.files()["proof"] and p.files()["pitch_md"]]
        for p in self.pitches:
            if p not in todo:
                p.critic = {"error": "no proof to look at" if p.files()["pitch_md"] else "no pitch.md"}
        running, procs = [], self.procs

        def one(p):
            f = p.files()
            w = self.window
            focus = (f"This is one of {self.n} pitches for the whole video (its angle: {ANGLES[p.angle][0]}), proven on "
                     f"the song's proof window {mmss(w['t0'])}–{mmss(w['t1'])} ({w['why']}). Judge whether the proof shows "
                     f"its pitch's idea at full strength on that moment, and whether that idea could carry the whole song.")
            cmd, env = critic.command(self.pdir, f["proof"], self.critic_model, self.critic_effort, out=rel(p.dir, self.pdir),
                                      concept=f["pitch_md"], focus=focus, pictures=f["style"][:3])
            out = ""
            try:
                proc = subprocess.Popen(cmd, cwd=STUDIO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                procs[p.k] = proc
                out = proc.communicate(timeout=1500)[0]
                c = critic.from_output(self.pdir, out) if proc.returncode == 0 else None
            except Exception as ex:
                out += f"\n({ex})"
                c = None
                if procs.get(p.k):
                    kill_tree(procs[p.k])
            if c:
                p.critic, p.critic_cost = c, float(c.get("cost_usd") or 0)
                self.say(f"{p.name}: 🔍 {critic.score_line(c.get('scores'))} · ${p.critic_cost:.2f} · {c.get('file')}")
            else:
                tail = [x for x in out.strip().splitlines() if x.strip()][-1:] or ["no output"]
                p.critic = {"error": tail[0][:300]}
                p.critic_cost = float(critic.spent(out) or 0)
                self.say(f"{p.name}: 🔍 the critic couldn't look at its proof: {tail[0][:200]}")
        queue = list(todo)
        while queue or running:
            running = [t for t in running if t.is_alive()]
            if self.aborted():
                for proc in list(procs.values()):
                    kill_tree(proc)
                queue = []
                for t in running:
                    t.join(30)
                break
            while queue and len(running) < CRITIC_AT_ONCE:
                p = queue.pop(0)
                self.say(f"{p.name}: the critic ({self.critic_model}) is looking at its proof (2-4 min)")
                t = threading.Thread(target=one, args=(p,), daemon=True)
                t.start()
                running.append(t)
            time.sleep(1)
            if int(time.time()) % 5 == 0:
                self.save()

    # ------------------------------------------------------------------ the verdict
    def candidates(self):
        return [p for p in self.pitches if p.files()["pitch_md"]]

    def make_verdict(self):
        """One call (the critic's model, read-only tools, templates/pitch-verdict.md): the ranking, the winner, why,
        the ideas worth grafting -> verdict.md + verdict.json. One pitch with a pitch.md wins without a call; when the
        answer has no readable json, the critic's scores on the proofs rank them."""
        import critic
        cands = self.candidates()
        vp = os.path.join(self.rdir, "verdict.md")
        base = {"round": self.k, "file": rel(vp, self.pdir), "model": self.critic_model, "effort": self.critic_effort,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"), "cost_usd": 0.0, "warnings": []}
        if not cands:
            return dict(base, file=None, error="no pitch delivered a pitch.md: nothing to judge")
        if len(cands) == 1:
            p = cands[0]
            v = dict(base, winner=p.k, ranking=[p.k], by="the only pitch with a pitch.md", graft=[], push=[], scores={},
                     verdict=f"p{p.k} is the only pitch that delivered.", why="The other pitches didn't deliver a pitch.md.")
            open(vp, "w", encoding="utf-8").write(f"# Verdict: pitch round {self.k}\n**Winner:** p{p.k}: the only pitch "
                                                  f"that delivered a pitch.md (no verdict call was needed).\n")
            jsave(os.path.join(self.rdir, "verdict.json"), v)
            return v
        prompt = self.verdict_prompt(cands)
        open(os.path.join(self.rdir, "verdict-prompt.md"), "w", encoding="utf-8").write(prompt)
        self.say(f"the verdict ({self.critic_model}) compares {len(cands)} pitches (2-4 min)")
        r = {"text": "", "json": {}, "cost_usd": None, "warnings": [], "returncode": None, "stderr": ""}
        err = ""
        result = {}
        done = threading.Event()

        def started(proc):                       # (a stop, or Ctrl+C, ends it: pitch_round)
            self.procs["verdict"] = proc

        def call():
            try:
                result.update(claude_print(prompt, self.pdir, model=self.critic_model, effort=self.critic_effort or None,
                                           tools=READ_ONLY, timeout=1200, max_usd=VERDICT_MAX_USD, on_start=started))
            except Exception as ex:
                result["error"] = str(ex)
            done.set()
        threading.Thread(target=call, daemon=True).start()
        saved = time.time()
        try:
            while not done.wait(1):
                if self.aborted():               # its call ended with it (its cost isn't known: it never answered)
                    if self.procs.get("verdict"):
                        kill_tree(self.procs["verdict"])
                    return dict(base, file=None, error="stopped by the user before the verdict came (its call was ended)")
                if time.time() - saved >= 5:     # round.json stays fresh while it works (2-4 min): the round is on
                    saved = time.time()
                    self.save()
        finally:
            self.procs.pop("verdict", None)
        if result.get("error"):
            err = result["error"]
        else:
            r = result
        cost = round(float(r.get("cost_usd") or 0), 4)
        md, data, warn = critic.json_block(r.get("text") or "")
        v = dict(base, cost_usd=cost, warnings=list(r.get("warnings") or []), duration_s=r.get("duration_s"))
        failed = r.get("returncode") or (r.get("json") or {}).get("is_error")
        if failed and data is None:
            err = err or (md.strip()[:300] or (r.get("stderr") or "").strip()[-300:] or f"exit {r.get('returncode')}")
        if data and not err:
            try:
                v.update(read_verdict(data, [p.k for p in cands], self.by_scores(cands)) or {})
            except Exception as ex:              # a json block of a shape not foreseen: the critic's scores rank them
                warn = f"its json block couldn't be read ({type(ex).__name__}: {ex})"
        if not v.get("winner"):
            rank = self.by_scores(cands)
            why = err or warn or "its answer named no winner"
            v.update(winner=rank[0], ranking=rank, by="the critic's scores on the proofs (the verdict couldn't be read)",
                     error=f"the verdict couldn't be read: {why}"[:400], graft=[], push=[], scores={}, verdict="", why="")
            v["warnings"].append(v["error"])
            md = (md.strip() + "\n\n" if md.strip() else f"# Verdict: pitch round {self.k}\n\n") + \
                f"(The studio: the verdict couldn't be read ({why[:200]}); the pitches are ranked by the critic's scores on " \
                f"their proofs: {', '.join('p' + str(x) for x in rank)}.)"
        open(vp, "w", encoding="utf-8").write(md.rstrip() + "\n")
        jsave(os.path.join(self.rdir, "verdict.json"), v)
        return v

    def by_scores(self, cands):
        """The pitches ranked by the critic's scores on their proofs: ambition first, then song fit, then craft; a
        pitch without a proof (or a critique) last."""
        def key(p):
            sc = (p.critic or {}).get("scores") or {}
            return (-(sc.get("ambition") or 0), -(sc.get("song_fit") or 0), -(sc.get("craft") or 0), p.k)
        return [p.k for p in sorted(cands, key=key)]

    def verdict_prompt(self, cands):
        import critic
        pj = load_pj(self.pdir)
        d = delivery(pj)
        sm = jload(os.path.join(self.pdir, "song", "song-map.json"), {}) or {}
        src, song = critic.song_brief(self.pdir)
        blocks = []
        for p in cands:
            rec, f = p.record(), p.files()
            md = open(os.path.join(self.pdir, f["pitch_md"]), encoding="utf-8").read().strip().splitlines()
            md = "\n".join(md[:140] + ([f"... ({len(md) - 140} more lines in {f['pitch_md']})"] if len(md) > 140 else []))
            c = p.critic or {}
            pics = [x for x in c.get("pictures") or [] if "strip_at" in x or "moments" in x][:3]
            crit = ""
            if c.get("file"):
                rep = open(os.path.join(self.pdir, c["file"]), encoding="utf-8").read().strip().splitlines()
                crit = (f"#### The critic on its proof: {critic.score_line(c.get('scores'))}\n" +
                        "\n".join(rep[:70] + (["... (more in " + c["file"] + ")"] if len(rep) > 70 else [])))
            else:
                crit = f"#### The critic on its proof\n({c.get('error') or 'no critique'})"
            plan = jload(os.path.join(self.pdir, f["plan"]), None) if f["plan"] else None
            nseg = len(plan.get("segments") or []) if isinstance(plan, dict) else 0
            blocks.append(
                f"### p{p.k} · {ANGLES[p.angle][0]} · \"{rec['title'] or '(no title)'}\"\n"
                f"- Its state: {p.state}" + (f" ({p.error})" if p.error else "")
                + f"; it ran {est(rec['elapsed_s'])} and cost ${rec['cost_usd']:.2f}"
                + (f"; {len(rec['agents'])} agent{'s' * (len(rec['agents']) != 1)}" if rec["agents"] else "") + "\n"
                + (f"- Its style frames (open each): {', '.join('`' + x + '`' for x in f['style'][:3])}\n" if f["style"] else
                   "- No style frames.\n")
                + (f"- Its proof: `{f['proof']}`; the critic's pictures of it (open these): {', '.join('`' + x + '`' for x in pics) or '(none)'}\n"
                   if f["proof"] else "- No proof: it didn't show its idea on the window.\n")
                + (f"- A draft plan for the whole song: `{f['plan']}` ({nseg} segments)\n" if f["plan"] else "")
                + f"\n#### Its pitch.md (`{f['pitch_md']}`)\n{md}\n\n{crit}\n")
        vals = {"n": len(cands), "window": window_text(self.window), "direct_budget": direct_budget_text(self.pdir, self.auto),
                "budget": D.fmt_dur(self.budget_s), "song": (f"(from `{src}`)\n\n" if src else "") + song,
                "notes": critic.user_notes(pj), "avoid": str(pj.get("avoid") or "").strip() or "(nothing)",
                "anti": critic.anti_defaults(), "frames": int(round(float(sm.get("duration") or 0) * d["fps"])),
                "size": f"{d['width']}×{d['height']}", "speeds": speeds_text(self.pdir), "round": self.k,
                "pitches": "\n".join(blocks)}
        return D.fill(open(os.path.join(TEMPLATES, "pitch-verdict.md"), encoding="utf-8").read(), vals)


def pnum(x):
    """A pitch's number as the verdict may give it: 2, 2.0, "2", "p2", "p2 (the wild card)" -> 2; else None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, float):
        return int(x) if x.is_integer() else None
    try:
        return int(str(x).strip().lstrip("pP").split()[0].rstrip(".,:)"))
    except (ValueError, IndexError):
        return None


def as_list(x):
    """A list the verdict may give as a list, one value, or a string ("2, 1, 3")."""
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return [y for y in re.split(r"[,;>\s]+", x) if y]
    return [] if x is None or isinstance(x, (dict, bool)) else [x]


def score(x):
    try:
        return max(1, min(10, int(round(float(x)))))
    except (TypeError, ValueError):
        return None


def read_verdict(data, ks, fallback=()):
    """The verdict's json block -> {winner, ranking, by, verdict, why, scores, graft, push}, only the pitches there
    are (ks); the ranking completed from `fallback` (the critic's order); None when it names no winner."""
    rank = []
    for x in as_list(data.get("ranking")):
        x = pnum(x)
        if x in ks and x not in rank:
            rank.append(x)
    win = pnum(data.get("winner"))
    win = win if win in ks else (rank[0] if rank else None)
    if win is None:
        return None
    rank = [win] + [x for x in rank if x != win] + [x for x in list(fallback) + list(ks) if x not in rank and x != win]
    rank = list(dict.fromkeys(rank))
    scores = {}
    for key, sc in (data.get("scores") or {}).items() if isinstance(data.get("scores"), dict) else ():
        if pnum(key) in ks and isinstance(sc, dict):
            scores[str(pnum(key))] = {s: score(sc.get(s)) for s in ("ambition", "song_fit", "buildable", "craft")}
    graft = [{"from": pnum(g.get("from")), "idea": str(g.get("idea") or "").strip(), "where": str(g.get("where") or "").strip()}
             for g in as_list(data.get("graft")) if isinstance(g, dict) and pnum(g.get("from")) in ks and str(g.get("idea") or "").strip()]
    push = data.get("push")
    push = [str(x) for x in push][:6] if isinstance(push, list) else [str(push)] if push else []
    return {"winner": win, "ranking": rank, "by": "the verdict", "verdict": str(data.get("verdict") or "").strip(),
            "why": str(data.get("why") or "").strip(), "scores": scores, "graft": graft[:8], "push": push}


def pitch_round(pdir, n=3, budget="20m", model=None, effort=None, critic_model=None, critic_effort=None, proof=None,
                seed_notes="", max_usd=None, angles=None, auto=None, dry_run=False, log=print):
    """A pitch round (studio.py pitch). auto: None, or {budget, effort, model, max_usd, ultracode}: the verdict's
    winner is built at once by a director run in the same job. Returns round.json's data (with "run": the director
    run's status when auto chained one)."""
    R = Round(pdir, n, budget, model, effort, critic_model, critic_effort, proof, seed_notes, max_usd, angles, auto, dry_run, log)
    try:
        d = R.run()
    except BaseException:                          # Ctrl+C: no pitch, critique or verdict call outlives the round
        for p in R.pitches:
            if p.s and p.s.alive():
                kill_tree(p.s.proc)
        for proc in list(R.procs.values()):
            kill_tree(proc)
        raise
    if auto and not dry_run:
        v = d.get("verdict") or {}
        if R.state == "stopped":
            log("the round was stopped: no director run")
        elif not v.get("winner"):
            log("no winner (no pitch delivered a pitch.md): no director run")
        else:
            log(f"--auto: the director builds p{v['winner']} now")
            # (the round's notes were the pitchers': the director sees them in its brief's pitch section, as with a pick
            # on the page, not as notes of its own)
            d["run"] = D.direct(pdir, budget=auto.get("budget") or "90m", effort=auto.get("effort"), model=auto.get("model"),
                                critic_model=R.critic_model, critic_effort=R.critic_effort,
                                ultracode=bool(auto.get("ultracode")), max_usd=auto.get("max_usd"),
                                pitch=f"round-{R.k}/p{v['winner']}", picked_by="auto", log=log)
    return d


# ------------------------------------------------------------------ the rounds, for the web app and the director
def load_round(pdir, k):
    return jload(os.path.join(round_dir(pdir, k), "round.json"), None)


def rounds(pdir):
    """Every round's round.json, newest first (one being written this instant is skipped)."""
    out = []
    for k in reversed(round_numbers(pdir)):
        d = load_round(pdir, k)
        if isinstance(d, dict):
            out.append(d)
    return out


def round_active(pdir):
    """The pitch round going on in this project (its round.json), or None; one whose runner stopped writing (its
    console closed, the machine slept) is over, whatever its file says."""
    for k in reversed(round_numbers(pdir)):
        d = load_round(pdir, k)
        if not isinstance(d, dict) or d.get("state") == "dry-run":      # (a dry run's briefs, written while one runs)
            continue
        if d.get("state") == "running" and time.time() - float(d.get("updated") or 0) < FRESH_S:
            return d
        return None
    return None


def parse_ref(ref):
    """'round-2/p3' (or '2/3', 'round-2/3') -> (2, 3)."""
    m = re.fullmatch(r"\s*(?:round-?)?(\d+)\s*/\s*p?(\d+)\s*", str(ref or ""))
    if not m:
        raise SystemExit(f"--pitch {ref!r}: give it as round-K/pN (e.g. round-1/p2)")
    return int(m.group(1)), int(m.group(2))


# ------------------------------------------------------------------ the promotion (studio.py direct --pitch)
SKIP_COPY = shutil.ignore_patterns("*.mp4", "*.mov", "*.part", "*.parts", "__pycache__", "node_modules", "eyes")


def same_tree(src, dst):
    """Every file under src is under dst with the same bytes (the pitch's scenes were promoted before)."""
    for dp, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", "node_modules", "eyes") and not x.endswith(".parts")]
        for f in files:
            if f.endswith((".mp4", ".mov", ".part")):
                continue
            a = os.path.join(dp, f)
            b = os.path.join(dst, os.path.relpath(a, src))
            try:
                if not os.path.isfile(b) or open(a, "rb").read() != open(b, "rb").read():
                    return False
            except OSError:
                return False
    return True


def has_files(d):
    return os.path.isdir(d) and any(files for _, _, files in os.walk(d))


def rewrite_paths(x, pitch_prefix, dest):
    """A draft plan's scene paths, moved with its scenes: the pitch's own (work/pitches/round-K/pN/scenes/...) and the
    final places it names (work/scenes/...) -> dest/..."""
    if isinstance(x, dict):
        return {k: rewrite_paths(v, pitch_prefix, dest) for k, v in x.items()}
    if isinstance(x, list):
        return [rewrite_paths(v, pitch_prefix, dest) for v in x]
    if isinstance(x, str):
        s = x.replace("\\", "/")
        if s.startswith(pitch_prefix):
            return dest + "/" + s[len(pitch_prefix):]
        if dest != "work/scenes" and s.startswith("work/scenes/"):
            return dest + "/" + s[len("work/scenes/"):]
    return x


def promote(pdir, ref, by="the user", run=None, dry=False, log=print):
    """Put a pitch in place for the director (studio.py direct --pitch round-K/pN): pitch.md -> work/concept.md (an
    existing one kept as work/concept_before_pitch_<time>.md), its scenes -> work/scenes/ (its lib/ -> work/scenes/lib/;
    an existing work/scenes/ isn't touched: the pitch's go to work/scenes/pitch-K-N/; that folder from an earlier pick of
    the same pitch, changed since, is kept as work/scenes/pitch-K-N_before_pitch_<time>/), its style frames ->
    work/style-frames/ (the ones there before kept in work/style-frames_before_pitch_<time>/), its draft plan.json ->
    work/plan.json with its scene paths moved with the scenes (an existing one kept as work/plan_before_pitch_<time>.json).
    round.json records `picked`. dry: only say what it would do. Returns what the brief needs."""
    pdir = os.path.abspath(pdir)
    K, N = parse_ref(ref)
    rd = round_dir(pdir, K)
    data = jload(os.path.join(rd, "round.json"), None)
    pd = os.path.join(rd, f"p{N}")
    if not isinstance(data, dict) or not os.path.isdir(pd):
        raise SystemExit(f"no pitch round-{K}/p{N} (work/pitches/round-{K}/p{N}/): `studio.py pitch {os.path.basename(pdir)}` first")
    if data.get("state") == "running" and time.time() - float(data.get("updated") or 0) < FRESH_S:
        raise SystemExit(f"pitch round {K} is still running: pick a pitch when it's done")
    f = pitch_files(pdir, pd)
    if not f["pitch_md"]:
        raise SystemExit(f"round-{K}/p{N} has no pitch.md: it can't be built (pick another pitch)")
    rec = next((x for x in data.get("pitches") or [] if x.get("k") == N), {}) or {}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = os.path.join(pdir, "work")
    did, notes = [], []
    md = open(os.path.join(pdir, f["pitch_md"]), encoding="utf-8").read()
    title, idea = summary(md)
    # the concept
    cp = os.path.join(work, "concept.md")
    concept_backup = None
    if os.path.isfile(cp) and open(cp, encoding="utf-8", errors="replace").read() != md:
        concept_backup = f"work/concept_before_pitch_{stamp}.md"
    # the scenes
    src_sc = os.path.join(pd, "scenes")
    scenes_dest = "work/scenes"
    already, scenes_backup = False, None
    if has_files(src_sc) and has_files(os.path.join(work, "scenes")):
        if same_tree(src_sc, os.path.join(work, "scenes")):
            already = True
        else:
            scenes_dest = f"work/scenes/pitch-{K}-{N}"
            notes.append(f"work/scenes/ had scenes already (kept as they are): the pitch's scenes are in {scenes_dest}/")
            dest = os.path.join(pdir, *scenes_dest.split("/"))
            if has_files(dest):                  # picked before: as it was then, or changed since (kept, never overwritten)
                if same_tree(src_sc, dest):
                    already = True
                else:
                    scenes_backup = f"{scenes_dest}_before_pitch_{stamp}"
                    notes.append(f"{scenes_dest}/ had this pitch's scenes from an earlier pick, changed since: they are "
                                 f"kept in {scenes_backup}/, and {scenes_dest}/ has the pitch's own again")
    # the style frames
    sf = os.path.join(work, "style-frames")
    frames_backup = None
    if f["style"] and os.path.isdir(sf):
        theirs = {os.path.basename(x) for x in f["style"]}
        mine = [x for x in os.listdir(sf) if os.path.isfile(os.path.join(sf, x))]
        if any(x not in theirs or open(os.path.join(sf, x), "rb").read() != open(os.path.join(pd, "style", x), "rb").read()
               for x in mine):
            frames_backup = f"work/style-frames_before_pitch_{stamp}"
    # the draft plan
    plan_src = os.path.join(pdir, f["plan"]) if f["plan"] else None
    plan_new, plan_backup = None, None
    pitch_prefix = f"work/pitches/round-{K}/p{N}/scenes/"
    if plan_src:
        pl = jload(plan_src, None)
        if isinstance(pl, dict):
            plan_new = rewrite_paths(pl, pitch_prefix, scenes_dest)
            if scenes_dest != "work/scenes":
                notes.append(f"work/plan.json names the pitch's scenes in {scenes_dest}/, and `plan validate` wants each "
                             f"segment's scene in its own work/scenes/<id>/: move them there (or the old scenes away) "
                             f"before you use the plan tools")
            pp = os.path.join(work, "plan.json")
            if os.path.isfile(pp) and jload(pp, None) != plan_new:
                plan_backup = f"work/plan_before_pitch_{stamp}.json"
        else:
            notes.append(f"its {f['plan']} isn't valid JSON: not used")
    if not dry:
        os.makedirs(work, exist_ok=True)
        if concept_backup:
            shutil.copyfile(cp, os.path.join(pdir, concept_backup))
        shutil.copyfile(os.path.join(pdir, f["pitch_md"]), cp)
        did.append("work/concept.md")
        if has_files(src_sc) and not already:
            if scenes_backup:
                shutil.move(os.path.join(pdir, *scenes_dest.split("/")), os.path.join(pdir, *scenes_backup.split("/")))
            shutil.copytree(src_sc, os.path.join(pdir, *scenes_dest.split("/")), ignore=SKIP_COPY, dirs_exist_ok=True)
            did.append(scenes_dest + "/")
        if f["style"]:
            if frames_backup:
                shutil.move(sf, os.path.join(pdir, frames_backup))
            os.makedirs(sf, exist_ok=True)
            for x in f["style"]:
                shutil.copyfile(os.path.join(pdir, x), os.path.join(sf, os.path.basename(x)))
            did.append("work/style-frames/")
        if plan_new is not None:
            pp = os.path.join(work, "plan.json")
            if plan_backup:
                shutil.copyfile(pp, os.path.join(pdir, plan_backup))
            jsave(pp, plan_new)
            did.append("work/plan.json")
        data["picked"] = {"k": N, "by": by, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "run": run, "title": title,
                          "scenes": scenes_dest if has_files(src_sc) else None}
        jsave(os.path.join(rd, "round.json"), data)
        log(f"pitch round-{K}/p{N} \"{title}\" in place: " + ", ".join(did)
            + "".join(f"; kept {x}" for x in (concept_backup, frames_backup, plan_backup, scenes_backup) if x))
    v = data.get("verdict") or {}
    return {"ref": f"round-{K}/p{N}", "round": K, "k": N, "n": data.get("n"), "title": title, "idea": idea,
            "angle": rec.get("angle_name") or ANGLES.get(rec.get("angle"), ("?",))[0], "by": by, "window": data.get("proof"),
            "budget": data.get("budget"), "proof": f["proof"], "proof_av": f["proof_av"],
            "scenes": scenes_dest if has_files(src_sc) else None, "lib": os.path.isdir(os.path.join(src_sc, "lib")),
            "style": [f"work/style-frames/{os.path.basename(x)}" for x in f["style"]], "plan": "work/plan.json" if plan_new is not None else None,
            "concept_backup": concept_backup, "frames_backup": frames_backup, "plan_backup": plan_backup,
            "scenes_backup": scenes_backup, "notes": notes,
            "critic": rec.get("critic") or (jload(os.path.join(pd, "critic.json"), None) or None), "verdict": v,
            "pitches": {x.get("k"): x for x in data.get("pitches") or []}, "cost_usd": data.get("cost_usd"),
            "costs": data.get("costs"), "already": already, "seed_notes": str(data.get("seed_notes") or "").strip()}


def brief_section(info):
    """The director brief's short section on the pitch it starts from: what's in place, go further, the critic's notes
    on its proof, the verdict's graft ideas. "" without a pitch."""
    if not info:
        return ""
    import critic
    w = info.get("window") or {}
    v = info.get("verdict") or {}
    c = info.get("critic") or {}
    others = info.get("pitches") or {}
    who = ("I let the studio's critic pick, and its verdict chose" if info["by"] == "auto" else "I picked")
    win = (f"**pitch {info['ref']}: \"{info['title']}\"**" if info["title"] else f"**pitch {info['ref']}**") + f" (the angle: {info['angle']})"
    place = ["its pitch is work/concept.md now" + (f" (the one before it: {info['concept_backup']})" if info["concept_backup"] else "")]
    if info["scenes"]:
        place.append(f"its scenes are in {info['scenes']}/" + (f" (shared code in {info['scenes']}/lib/)" if info["lib"] else ""))
    if info["style"]:
        place.append("its style frames in work/style-frames/" + (f" (the earlier ones: {info['frames_backup']}/)" if info["frames_backup"] else ""))
    if info["plan"]:
        place.append("its draft plan in work/plan.json" + (f" (the earlier one: {info['plan_backup']})" if info["plan_backup"] else ""))
    L = ["", "", "## The pitch I picked: go further",
         f"Before this run, {info.get('n') or 'several'} directors pitched this video at the same time, each from its own angle, each "
         f"proving its idea on the same window of the song"
         + (f" ({mmss(w['t0'])}–{mmss(w['t1'])}" + (f", around \"{str(w['moment'].get('text') or '').split(' · ')[0]}\" at "
                                                    f"{mmss(w['moment']['t'])}" if w.get("moment") else "") + ")" if w.get("t1") else "")
         + f". {who} {win}.",
         "- It's in place: " + "; ".join(place) + "."
         + (f" Its proof, with the song: {info['proof_av'] or info['proof']}." if (info["proof_av"] or info["proof"]) else "")
         + "".join(f" Note: {x}." for x in info["notes"]),
         f"- Build it, and go further than the pitch: the proof is a first draft of one window of the song, made in about "
         f"{info.get('budget') or 'twenty minutes'}, not a ceiling. Keep what makes it this song's video; push everything else "
         f"further. Rewrite work/concept.md where the idea gets stronger (the critic judges against it)."]
    if c.get("scores") or c.get("verdict"):
        safe = "; ".join(str(x) for x in (c.get("safe") or [])[:3])
        probs = critic.problems_text(c, 3)
        L.append(f"- The critic on its proof ({critic.score_line(c.get('scores'))}" + (f"; all of it in {c['file']}" if c.get("file") else "")
                 + f"): {c.get('verdict') or ''}" + (f" Where it plays safe: {safe}." if safe else "")
                 + (" Its worst problems:\n" + "\n".join("  " + x for x in probs.splitlines()) if probs else ""))
    graft = [g for g in v.get("graft") or [] if g.get("from") != info["k"]]
    if graft:
        L.append("- Ideas worth grafting from the other pitches (the verdict's; take what makes this one bolder, leave what "
                 "would dilute it):\n" + "\n".join(
                     f"  - from p{g['from']}" + (f" \"{(others.get(g['from']) or {}).get('title')}\"" if (others.get(g['from']) or {}).get("title") else "")
                     + f": {g['idea']}" + (f" ({g['where']})" if g.get("where") else "") for g in graft[:6]))
    if v.get("winner") and v["winner"] != info["k"]:
        t = (others.get(v["winner"]) or {}).get("title")
        L.append(f"- The verdict ranked p{v['winner']}" + (f" \"{t}\"" if t else "") + f" first; I picked this one. Its reasons "
                 f"are in work/pitches/round-{info['round']}/verdict.md: take what helps.")
    elif v.get("push"):
        L.append("- What the verdict says to push: " + " ".join(str(x) for x in v["push"][:3]))
    if info.get("seed_notes"):
        L.append(f"- My notes to the pitch round (written for the pitchers; context, not a rule): \"{info['seed_notes'][:600]}\"")
    L.append("You have full reign, as always: everything above and below holds.")
    return "\n".join(L)


# ------------------------------------------------------------------ the sandbox test (studio.py permtest --pitch)
def permission_test(pdir, model="haiku", log=print, timeout=600):
    """A pitch's sandbox with the real Claude Code (a few cents with Haiku): a write into its own pitch folder is
    allowed; into another pitch's folder, the project's scenes, final/ and the studio's tools refused. A scratch round
    (work/pitches/round-permtest/) is removed afterwards; the session's events are kept in work/director/permtest-pitch/."""
    pdir = os.path.abspath(pdir)
    rd = os.path.join(pdir, PITCHES, "round-permtest")
    sdir = os.path.join(pdir, "work", "director", "permtest-pitch")
    for x in ("p1", "p2"):
        os.makedirs(os.path.join(rd, x), exist_ok=True)
    made = [d for d in (os.path.join(pdir, "work", "scenes"), os.path.join(pdir, "final")) if not os.path.isdir(d)]
    probes = {"mine": os.path.join(rd, "p1", "_permtest.txt"), "other pitch": os.path.join(rd, "p2", "_permtest.txt"),
              "scenes": os.path.join(pdir, "work", "scenes", "_permtest_pitch.txt"),
              "final": os.path.join(pdir, "final", "_permtest_pitch.txt"),
              "tools": os.path.join(STUDIO, "tools", "_permtest_pitch.txt")}
    for f in probes.values():
        if os.path.exists(f):
            os.remove(f)
    cmd = D.claude_args(pdir, model, "low", extra_deny=deny_rules(pdir, rd, 1, 2), settings_path=os.path.join(sdir, "settings.json"))
    i = cmd.index("--append-system-prompt")
    del cmd[i:i + 2]                                  # without the "write only in your folder" hint: the rules must hold alone
    msg = ("This is an authorized test of this session's permission settings; just try each step even if you expect a "
           "refusal. Use the Write tool for each: 1) create " + rel(probes["other pitch"], pdir) + " containing x. 2) create "
           + rel(probes["mine"], pdir) + " containing ok. 3) create " + rel(probes["scenes"], pdir) + " containing x. 4) create "
           + rel(probes["final"], pdir) + " containing x. 5) create " + rel(probes["tools"], pdir) + " containing x. Then reply "
           "with one line per step: ALLOWED or DENIED.")
    status = {}
    s = Session(cmd, pdir, sdir, name="pitch", status=status, env=dict(os.environ, PYTHONIOENCODING="utf-8")).start()
    s.send(msg, "the permission test")
    t0, said = time.time(), 0
    try:
        while time.time() - t0 < timeout and s.alive():
            time.sleep(1)
            with s.lock:
                evs = list(status["events"])
            for e in evs[said:]:
                log("   " + str(e["text"])[:200])
            said = len(evs)
            if s.turn_over():
                break
    finally:
        s.stop("the test is done", wait=20)
        s.finish()
    there = {k: os.path.exists(f) for k, f in probes.items()}
    for f in probes.values():
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree(rd, ignore_errors=True)
    for d in made:
        try:
            os.rmdir(d)
        except OSError:
            pass
    checks = [(there["mine"], "a pitch can write in its own pitch folder"),
              (not there["other pitch"], "a pitch can't write into another pitch's folder"),
              (not there["scenes"], "a pitch can't write into the project's work/scenes/"),
              (not there["final"], "a pitch can't write into final/"),
              (not there["tools"], "a pitch can't write into the studio's tools")]
    for ok, what in checks:
        log(f"[{'PASS' if ok else 'FAIL'}] {what}")
    log(f"(the session's events: {rel(os.path.join(sdir, 'events.jsonl'), pdir)}; ${float(status.get('cost_usd') or 0):.4f})")
    return all(ok for ok, _ in checks)


# ------------------------------------------------------------------ the command line (studio.py pitch)
def main(argv=None, pdir=None):
    ap = argparse.ArgumentParser(prog="studio.py pitch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_args(ap)
    a = ap.parse_args(argv)
    if not pdir:
        from mv.common import need_project
        pdir = need_project()
    return run_args(pdir, a)


def add_args(ap):
    ap.add_argument("--n", type=int, default=None, help=f"how many pitches ({MIN_N}-{MAX_N}, default 3)")
    ap.add_argument("--budget", default="20m", help="each pitch's time (default 20m)")
    ap.add_argument("--model", help="the pitches' model (default: project.json director.model, else opus)")
    ap.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"], help="the pitches' effort (default high)")
    ap.add_argument("--critic-model", help="the critic on the proofs and the verdict (default: project.json, else sonnet)")
    ap.add_argument("--critic-effort")
    ap.add_argument("--proof", help="the proof window: a section name (\"chorus 1\") or a-b in seconds (24-44); default: the studio chooses")
    ap.add_argument("--angles", help=f"the angles, comma-separated ({', '.join(ANGLES)}); default: the studio chooses")
    ap.add_argument("--seed-notes", default="", help="notes for this round (the pitches' briefs; the director that builds the "
                    "pick sees them as the round's notes)")
    ap.add_argument("--max-usd", type=float, help="a hard cap on each pitch's session (--max-budget-usd)")
    ap.add_argument("--auto", action="store_true", help="the verdict's winner is built at once (a director run in the same job)")
    ap.add_argument("--direct-budget", default="90m", help="with --auto: the director's budget (default 90m)")
    ap.add_argument("--direct-effort", default=None, choices=["low", "medium", "high", "xhigh", "max"], help="with --auto (default max)")
    ap.add_argument("--direct-model", help="with --auto: the director's model (default: project.json director.model, else opus)")
    ap.add_argument("--direct-max-usd", type=float, help="with --auto: a cap on the director's session")
    ap.add_argument("--ultracode", action="store_true", help="with --auto: the director may orchestrate multi-agent workflows")
    ap.add_argument("--dry-run", action="store_true", help="write the round's briefs and commands; don't start Claude")


def run_args(pdir, a):
    auto = {"budget": a.direct_budget, "effort": a.direct_effort, "model": a.direct_model, "max_usd": a.direct_max_usd,
            "ultracode": a.ultracode} if a.auto else None
    if a.auto:
        D.parse_budget(a.direct_budget)
    d = pitch_round(pdir, a.n, a.budget, a.model, a.effort, a.critic_model, a.critic_effort, a.proof, a.seed_notes, a.max_usd,
                    a.angles, auto, a.dry_run, log=lambda s: print(s, flush=True))
    if d.get("state") == "dry-run":
        return 0
    return 0 if d.get("state") in ("done", "stopped") and any(p.get("pitch") for p in d.get("pitches") or []) else 1


if __name__ == "__main__":
    sys.exit(main())
