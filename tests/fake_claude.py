"""A stand-in for the `claude` CLI, for tests that must not spend real usage (MV_CLAUDE=python tests/fake_claude.py,
or the fake_bin/claude wrapper next to it).

It speaks the same headless protocol the studio uses:
  claude --version                                                     -> a version line
  claude auth status                                                   -> {"loggedIn": true}
  claude -p --output-format json ... (the prompt on stdin)             -> the story step: a song story from the song map;
        a prompt with <!-- mv:critic --> gets a canned critique instead (markdown + a json block, its worst problem at
        the first ★★★ moment the prompt names; it says how many of the pictures it was told to open exist)
  claude -p --input-format stream-json --output-format stream-json ... -> a session: reads the first message from
        stdin, emits init / assistant / tool_use / result events, answers the studio's follow-ups, exits when stdin
        closes. Like the real CLI (verified on Claude Code 2.1.311), a turn ends with ONE result, and messages that
        arrive while it works are merged into that turn: after its work it takes every line already waiting on stdin
        and answers them in the same result. The role comes from the first message's marker:
        - none (the director brief): runs tests/mini_director.py in the project folder (cwd), writes report.md.
        - <!-- mv:planner --> (the pipeline's planner): writes work/concept.md, the lib, the hero s2 (a copy of
          tests/scenes/field.js importing the lib), the type overlay (tests/scenes/lyrics.html), a style frame and
          work/plan.json (s1 builder, s2 planner + hero, s3 builder; cuts at bars[n//3] and bars[2n//3]); renders the
          hero with `studio.py pipeline render`; to the gate: keeps the language; to "the language is locked": renders
          the overlay; to the first assemble report: sets the stage to final; to the final: writes report.md. A
          review round (the revise brief with the marker): marks the notes done, sets the stage to final. To the
          user's live notes ("playing your scenes live"): marks them done with a fix (the solo director too). Notes
          are marked done the way the brief or the message says: `studio.py note-done <p> <id> "<fix>"`, by the ids
          they give.
          FAKE_PLANNER=bad writes a plan.json that never validates (the solo fallback; to the switch it then runs the
          mini director like a solo director).
        - <!-- mv:builder -->: writes its scene (a copy of field.js) where the brief says, renders its segment preview
          with the command in the brief, and answers the critic's round (a line in its log).
          FAKE_BUILDER="f1:hang,f2:noscene,f3:crash" makes those segments' builders go wrong: hang (no events, no
          result, until it is killed), noscene (ends every turn without writing its scene), crash (exits 1 mid-turn).
        - <!-- mv:echo -->: works FAKE_ECHO_S seconds (default 2) on each turn, then answers every message of the turn
          (the session plumbing's test).
FAKE_CLAUDE_SLOW=<s> (or a file of that name in the project) sleeps that long first (to test stopping a run).
FAKE_CLAUDE_NO_BUDGET=1 refuses --max-budget-usd the way an older Claude Code would (unknown option).
FAKE_CLAUDE_CRITIC_ERROR=1 answers a critic prompt the way Claude Code reports a login or usage-limit error: exit 1,
        is_error true and the error as the "result" text (with a cost).
"""
import json, os, re, sys, time

TESTS = os.path.dirname(os.path.abspath(__file__))


def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def assistant(text=None, tool=None, inp=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": f"t{time.time_ns()}", "name": tool, "input": inp or {}})
    emit({"type": "assistant", "message": {"role": "assistant", "content": content}})


def result(turns, t0):
    emit({"type": "result", "subtype": "success", "num_turns": turns, "total_cost_usd": 0.0,
          "duration_ms": int((time.time() - t0) * 1000), "usage": {"input_tokens": 0, "output_tokens": 0}})


def story():
    m = json.load(open(os.path.join("song", "song-map.json"), encoding="utf-8"))
    rows = "\n".join(f"| {s['name']} | {s['bar0']}–{s['bar1'] - 1} | {s['t0']:.0f}–{s['t1']:.0f} s | {s['first_line']} | "
                     f"{'quiet' if s['loud'] < -25 else 'full'} | marks re-arranged on each downbeat |" for s in m["sections"])
    text = (f"# Song story: {m.get('title') or m['file']}\n\n> Drafted by the test stand-in for Claude.\n\n"
            "## What it's about\nSomeone holding on to a light through a night that keeps changing speed.\n\n"
            "## The arc, section by section\n\n| section | bars | time | in the song | feeling | what the video could do |\n"
            f"|---|---|---|---|---|---|\n{rows}\n\n## Lines that could carry a moment\n- \"hold the light\": the light holds still.\n\n"
            "## Where it peaks, where it breathes\n- The stop: everything halts.\n\n## What the user brings\n(test)\n")
    emit({"type": "result", "subtype": "success", "result": text, "total_cost_usd": 0.0})


def critic(prompt):
    """A canned critique of the video the prompt names, its worst problem at the first ★★★ moment in the prompt."""
    video = (re.search(r"## What you are looking at\s*\n`([^`]+)`", prompt) or [None, "the video"])[1]
    m = re.search(r"\|\s*(\d+):(\d+(?:\.\d+)?)\s*\|\s*[\d:.]+\s*\|\s*★★★\s*\|", prompt)
    t = round(int(m.group(1)) * 60 + float(m.group(2)), 2) if m else 10.0
    pics = re.findall(r"^- `([^`]+\.png)`", prompt, re.M)
    there = sum(os.path.exists(p) for p in pics)
    mm = f"{int(t // 60)}:{t % 60:04.1f}"
    data = {"verdict": "A field of marks re-arranged on every downbeat: the ★★★ moments should change its rules (test stand-in).",
            "scores": {"ambition": 6, "song_fit": 7, "craft": 8, "readability": 9},
            "problems": [{"severity": "high", "t0": t, "t1": round(t + 4, 2), "where": "the first ★★★ moment",
                          "what": "the marks keep the rule they had before it", "push": "make the moment change the rule set"},
                         {"severity": "low", "t0": 0.0, "t1": 4.0, "where": "intro", "what": "the first frame is busy",
                          "push": "let the intro start from nothing"}],
            "keep": ["the plates under the lyric lines"], "safe": [f"{mm} the same field before and after"],
            "anti_defaults": [], "fixed_since_last": []}
    text = (f"# Critique: {video}\n**Verdict:** {data['verdict']}\n**Scores:** ambition 6 · song fit 7 · craft 8 · "
            f"readability 9 (out of 10)\n\n## Ambition\nThe test stand-in can't see: this is a canned critique.\n\n"
            f"## Problems (worst first, at most 8)\n1. **[high] {mm}–{mm} · the first ★★★ moment** The marks keep their "
            f"rule. **Push:** make the moment change the rule set.\n\n## What works (keep it)\n- the plates under the "
            f"lyric lines\n\n## Can't tell from the pictures\n- everything (test stand-in: {there} of {len(pics)} "
            f"pictures exist)\n\n```json\n{json.dumps(data)}\n```\n")
    emit({"type": "result", "subtype": "success", "result": text, "total_cost_usd": 0.0123, "is_error": False,
          "modelUsage": {"fake-critic": {}}})


def text_of(line):
    try:
        return json.loads(line)["message"]["content"][0]["text"]
    except Exception:
        return None


def studio_cmd(*args, log=None):
    """tools/studio.py, the way a session's Bash tool runs it (stdin closed: ffmpeg would eat the studio's messages)."""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(TESTS), "tools", "studio.py"), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    tail = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()][-2:]
    if log:
        log(f"$ studio.py {' '.join(args[:4])}: exit {r.returncode}; {' | '.join(tail)[:300]}")
    return r


def edit_json(path, fn):
    d = json.load(open(path, encoding="utf-8"))
    fn(d)
    json.dump(d, open(path, "w", encoding="utf-8"), indent=1)


class Director:
    """The solo director (the director brief without a marker)."""

    def __init__(self, brief):
        self.brief = brief

    def first(self, say):
        say("Rendering with the scripted mini director.", "Bash", {"command": "python tests/mini_director.py ."})
        sys.path.insert(0, TESTS)
        import mini_director
        try:
            mini_director.run(os.getcwd(), log=lambda s: say(s.strip()))
            if "review round" in self.brief.lower():
                mark_notes_done(self.brief)
                with open("report.md", "a", encoding="utf-8") as f:
                    f.write("\n## Round 2\nAddressed every note (test stand-in).\n")
            say("Delivered final/music-video.mp4 and wrote report.md.")
        except Exception as ex:
            say(f"The mini director failed: {ex}")

    def answer(self, msg, say):
        if "playing your scenes live" in msg:
            return live_notes_done(msg, say)
        say(f"(studio said: {msg[:60]}...) Nothing more to improve in the test.")


def notes_done(text, fix, say):
    """Mark the notes a brief or a message names (by "(id N)") done with `studio.py note-done`, as the text says to (a
    text that doesn't name the command marks nothing: the test then sees the notes still open)."""
    ids = list(dict.fromkeys(re.findall(r"\(id (\d+)\)", text)))
    if "studio.py note-done" not in text:
        ids = []
    proj = os.path.basename(os.getcwd())
    for i in ids:
        say(f"Marking note {i} done.", "Bash", {"command": f'studio.py note-done {proj} {i} "{fix}"'})
        studio_cmd("note-done", proj, i, fix, log=say)
    return ids


def live_notes_done(msg, say):
    """The user's live notes the studio forwarded: each marked done with a fix, by the id the message gives."""
    ids = notes_done(msg, "the scene answers the note (test stand-in)", say)
    say(f"Took the user's live note{'s' * (len(ids) != 1)} ({len(ids)}): marked done.")


def mark_notes_done(brief, say=lambda *a, **k: None):
    notes_done(brief, "re-rendered (test stand-in)", say)


class Planner:
    """The pipeline's planner."""

    def __init__(self, brief):
        self.brief = brief
        self.project = os.path.basename(os.getcwd())
        self.assembles = 0
        self.solo = None

    def first(self, say):
        if "review round" in self.brief.lower():
            mark_notes_done(self.brief)
            with open("report.md", "a", encoding="utf-8") as f:
                f.write("\n## Round 2\nAddressed every note through the pipeline (test stand-in).\n")
            edit_json(os.path.join("work", "plan.json"), lambda d: d.update(stage="final"))
            say("Marked the notes done and set the plan's stage to final.", "Edit", {"file_path": "work/plan.json"})
            return
        say("Reading the song folder, writing the concept, the lib, the hero and the plan.", "Read", {"file_path": "song/README.md"})
        m = json.load(open(os.path.join("song", "song-map.json"), encoding="utf-8"))
        field = open(os.path.join(TESTS, "scenes", "field.js"), "rb").read()
        sd = os.path.join("work", "scenes")
        for sid in ("s2", "type", "lib"):
            os.makedirs(os.path.join(sd, sid), exist_ok=True)
        open(os.path.join(sd, "lib", "look.js"), "wb").write(b'// the shared visual language (smoke test)\nexport const LOOK = {ink: "#e9e4d8"};\n')
        open(os.path.join(sd, "s2", "scene.js"), "wb").write(b'import { LOOK } from "../lib/look.js";   // the shared language\n' + field)
        open(os.path.join(sd, "type", "type.html"), "wb").write(open(os.path.join(TESTS, "scenes", "lyrics.html"), "rb").read())
        os.makedirs(os.path.join("work", "style-frames"), exist_ok=True)
        from PIL import Image
        Image.new("RGB", (320, 180), (16, 20, 24)).save(os.path.join("work", "style-frames", "a.png"))
        open(os.path.join("work", "concept.md"), "w", encoding="utf-8").write(
            "# Concept (test stand-in)\nA field of marks re-dealt on every downbeat; the stop freezes it.\n")
        open(os.path.join("work", "song-notes.md"), "w", encoding="utf-8").write("# Song notes (test stand-in)\n")
        bars = [b["t"] for b in m["bars"]]
        c1, c2, dur = round(bars[len(bars) // 3], 3), round(bars[2 * len(bars) // 3], 3), m["duration"]
        seg = lambda sid, a, b, owner, status: {"id": sid, "name": f"{sid} (smoke)", "from": a, "to": b, "renderer": "web",  # noqa: E731
                                                "scene": f"work/scenes/{sid}/scene.js", "owner": owner, "brief": "the field, re-dealt",
                                                "enter": "the field", "exit": "the field", "moments": [], "status": status}
        plan = {"stage": "planned", "hero": "s2", "lib": "work/scenes/lib/", "style_frames": ["work/style-frames/a.png"],
                "rules": "marks on a dark field; every downbeat re-deals them",
                "segments": [seg("s1", 0.0, c1, "builder", "todo"), seg("s2", c1, c2, "planner", "drafted"),
                             seg("s3", c2, dur, "builder", "todo")],
                "overlays": [{"id": "type", "scene": "work/scenes/type/type.html", "transparent": True, "owner": "planner",
                              "brief": "the sung lines on plates"}]}
        if os.environ.get("FAKE_PLANNER") == "bad":
            plan["segments"][1]["from"] = c1 + 1.0            # a gap: never valid
        json.dump(plan, open(os.path.join("work", "plan.json"), "w", encoding="utf-8"), indent=1)
        say("Rendering the hero's preview.", "Bash", {"command": f"studio.py pipeline render {self.project} --preview --only s2"})
        studio_cmd("pipeline", "render", self.project, "--preview", "--only", "s2", log=say)
        say("plan.json says planned; the hero's preview is rendered.")

    def answer(self, msg, say):
        if self.solo:
            return self.solo.answer(msg, say)
        if "switching this run to solo" in msg:
            self.solo = Director(self.brief)
            return self.solo.first(say)
        if "playing your scenes live" in msg:
            return live_notes_done(msg, say)
        if "The language is locked" in msg:
            studio_cmd("pipeline", "render", self.project, "--preview", "--only", "type", log=say)
            say("Rendered the type overlay; s2 is drafted.")
        elif "Gate 1" in msg:
            say("The critic's notes on the language: keeping the language as it is (test stand-in).")
        elif "assembled a preview of the whole film" in msg:
            self.assembles += 1
            if self.assembles == 1:
                edit_json(os.path.join("work", "plan.json"), lambda d: d.update(stage="final"))
                say("The film holds: set the plan's stage to final.", "Edit", {"file_path": "work/plan.json"})
        elif "The final is assembled" in msg or "Time is short" in msg or "couldn't be assembled" in msg:
            open("report.md", "w", encoding="utf-8").write(
                "# Report (the pipeline's test stand-in)\n\nThree segments in one language: s1 and s3 by builders, the hero "
                "s2 by the planner; the sung lines as a transparent overlay.\n\nTimestamps to check by ear: the stop.\n")
            say("Wrote report.md.", "Write", {"file_path": "report.md"})
        else:
            say(f"(studio said: {msg[:60]}...) Noted.")


class Builder:
    """A pipeline builder."""

    def __init__(self, brief):
        self.brief = brief
        self.id = (re.search(r"--only ([a-z0-9_-]+)", brief) or [None, "s1"])[1]
        self.project = os.path.basename(os.getcwd())

    def first(self, say):
        self.mode = dict(x.split(":", 1) for x in (os.environ.get("FAKE_BUILDER") or "").split(",") if ":" in x).get(self.id)
        if self.mode == "hang":
            time.sleep(3600)                       # no events, no result: the studio's "hung" check must catch it
        if self.mode == "crash":
            os._exit(1)
        if self.mode == "noscene":
            return say("I'll write the scene later (test stand-in: it never does).")
        scene = (re.search(r"Your scene: `([^`]+)`", self.brief) or [None, f"work/scenes/{self.id}/scene.js"])[1]
        os.makedirs(os.path.dirname(scene), exist_ok=True)
        open(scene, "wb").write(open(os.path.join(TESTS, "scenes", "field.js"), "rb").read())
        say(f"Wrote {scene}; rendering the segment preview.", "Write", {"file_path": scene})
        studio_cmd("pipeline", "render", self.project, "--preview", "--only", self.id, log=say)
        d = os.path.join("work", "segments", self.id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "log.md"), "a", encoding="utf-8") as f:
            f.write(f"# {self.id} (test stand-in)\nThe field over my range.\n")
        if self.id == "s1":
            open(os.path.join(d, "requests.md"), "w", encoding="utf-8").write("- the lib could export the plate colour (test stand-in)\n")
        say("The preview is rendered.")

    def answer(self, msg, say):
        if getattr(self, "mode", None) == "noscene":
            say("Still no scene (test stand-in).")
        elif "critic" in msg:
            with open(os.path.join("work", "segments", self.id, "log.md"), "a", encoding="utf-8") as f:
                f.write("Took the critic's push (test stand-in).\n")
            say("Took the critic's push; nothing to re-render in the test.")
        else:
            say(f"(studio said: {msg[:60]}...) Noted.")


class Echo:
    """Works a little on each turn; says what it got (the session plumbing's test)."""

    def __init__(self, brief):
        self.wait = float(os.environ.get("FAKE_ECHO_S") or 2)

    def first(self, say):
        time.sleep(self.wait)
        say("echo: the first message")

    def answer(self, msg, say):
        say(f"echo: {msg[:80]}")
        time.sleep(self.wait)


def session():
    """A stream-json session: one turn per message that finds it idle; lines that come in while it works join that turn."""
    import queue, threading
    q = queue.Queue()

    def reader():
        for line in sys.stdin:
            q.put(line)
        q.put(None)
    threading.Thread(target=reader, daemon=True).start()
    t0 = time.time()
    first = q.get()
    brief = (text_of(first) or "") if first else ""
    role = Planner if "<!-- mv:planner -->" in brief else Builder if "<!-- mv:builder -->" in brief else \
        Echo if "<!-- mv:echo -->" in brief else Director
    emit({"type": "system", "subtype": "init", "model": f"fake-{role.__name__.lower()}", "tools": ["Read", "Bash"],
          "permissionMode": "dontAsk", "session_id": "fake"})
    who = role(brief)
    assistant("Reading the brief.", "Read", {"file_path": "song/README.md"})
    slow = os.environ.get("FAKE_CLAUDE_SLOW") or (open("FAKE_CLAUDE_SLOW").read().strip() if os.path.exists("FAKE_CLAUDE_SLOW") else "0")
    time.sleep(float(slow))
    say = lambda text, tool=None, inp=None: assistant(text, tool, inp)     # noqa: E731
    who.first(say)
    turns, done = 4, first is None
    while True:
        while True:                                # what came in meanwhile joins this turn
            try:
                line = q.get_nowait()
            except queue.Empty:
                break
            if line is None:
                done = True
                break
            msg = text_of(line)
            if msg:
                turns += 1
                who.answer(msg, say)
        result(turns, t0)
        if done:
            return
        line = q.get()                             # idle until the next message
        if line is None:
            return
        msg = text_of(line)
        if msg:
            turns += 1
            who.answer(msg, say)


def main():
    a = sys.argv[1:]
    if "--version" in a:
        print("0.0.0 (fake Claude Code for the studio's tests)")
    elif a[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "fake"}))
    elif "stream-json" in a:
        session()
    elif "-p" in a:
        if "--max-budget-usd" in a and os.environ.get("FAKE_CLAUDE_NO_BUDGET"):
            print("error: unknown option '--max-budget-usd'", file=sys.stderr)
            sys.exit(1)
        prompt = sys.stdin.read()                  # the prompt comes in on stdin
        if "<!-- mv:critic -->" in prompt and os.environ.get("FAKE_CLAUDE_CRITIC_ERROR"):
            emit({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login",
                  "total_cost_usd": 0.004})
            sys.exit(1)
        critic(prompt) if "<!-- mv:critic -->" in prompt else story()
    else:
        print("fake claude: nothing to do", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
