"""A stand-in for the `claude` CLI, for tests that must not spend real usage (MV_CLAUDE=python tests/fake_claude.py,
or the fake_bin/claude wrapper next to it).

It speaks the same headless protocol the studio uses:
  claude --version                                                     -> a version line
  claude auth status                                                   -> {"loggedIn": true}
  claude -p --output-format json ... (the prompt on stdin)             -> the story step: a song story from the song map;
        a prompt with <!-- mv:critic --> gets a canned critique instead (markdown + a json block, its worst problem at
        the first ★★★ moment the prompt names; it says how many of the pictures it was told to open exist); one with
        <!-- mv:verdict --> (the pitch round's verdict) a canned verdict: p2 wins when there is a p2, graft ideas from
        the others, the pictures it was told to open counted
  claude -p --input-format stream-json --output-format stream-json ... -> a session: reads the first message from
        stdin, emits init / assistant / tool_use / result events, answers the studio's follow-ups, exits when stdin
        closes. Like the real CLI (verified on Claude Code 2.1.311-2.1.312): every turn starts with a system/init and
        ends with ONE result; messages that arrive while it works are merged into that turn (after its work it takes
        every line already waiting on stdin and answers them in the same result); num_turns is the turn's own;
        total_cost_usd and modelUsage are the session's running totals (each turn adds $0.01, an agent $0.004 on its
        model). An agent runs in the background: the Agent tool_use and its "Async agent launched" result,
        background_tasks_changed with the task, task_started, task_progress every half second, its own tool_use
        events (parent_tool_use_id), task_updated and task_notification (completed), background_tasks_changed [];
        then the session starts a turn of its own (init, assistant, result) with no user message. With
        --max-budget-usd, the turn that takes the total past it ends with subtype error_max_budget_usd, and every
        turn after that does nothing but say so.
        The role comes from the first message (and FAKE_DIRECTOR):
        - the director brief: runs tests/mini_director.py in the project folder (cwd), writes report.md; a review round
          marks its notes done. To the user's live notes ("playing your scenes live") it marks them done with a fix.
          Notes are marked done the way the brief or the message says: `studio.py note-done <p> <id> "<fix>"`, by the
          ids they give.
        - FAKE_DIRECTOR=agents (the environment, or a file of that name in the project holding "agents"): a director
          that uses the plan tools and deploys one agent: it writes work/concept.md, the shared code
          (work/scenes/lib/look.js), s1 and s2 (copies of tests/scenes/field.js importing the lib), the type overlay
          (tests/scenes/lyrics.html) and work/plan.json (s1, s2, s3 + type; cuts at bars[n//3] and bars[2n//3]), and
          starts an agent (model haiku, "build s3") that writes s3 and renders its preview with `studio.py plan
          render` (FAKE_AGENT_S seconds of progress first, default 6); its turn ends while the agent works. In the turn
          the session starts when the agent is done it runs `studio.py plan assemble --preview` (the critic on the
          film), `plan assemble --final` and `studio.py check`, and writes report.md. A message that comes while the
          agent works is noted in work/_fake_agent_heard.txt (the test sees what reached it then).
        - <!-- mv:pitch -->: a pitch of the pitch round, in the pitch folder its brief names: pitch.md, a shared lib
          and the proof scene (copies of tests/scenes/field.js importing it), two style frames (render.py --still), the
          proof over the brief's --frames A:B at preview size (through the render queue); the second pitch also a
          draft plan.json for the whole song (its scenes named where they'll live once picked: work/scenes/<id>/), the
          first also a short background agent (haiku) that writes notes into its folder. To the studio's push to go
          further it answers that this is its best. FAKE_PITCH="1:linger,2:md,3:fail" plays pitches that go wrong:
          md writes only its pitch.md; fail writes nothing (its render "failed"); linger writes only its pitch.md and
          keeps running after its input is closed (a CLI still at work: the studio has to stop it).
        - <!-- mv:echo -->: works FAKE_ECHO_S seconds (default 2) on each turn, then answers every message of the turn
          (the session plumbing's test). <!-- mv:echo-agent --> also starts a background agent in its first turn
          (FAKE_AGENT_S seconds, default 4) and says "echo: the agent finished" in the turn of its own after it.
FAKE_CLAUDE_SLOW=<s> (or a file of that name in the project) sleeps that long first (to test stopping a run).
FAKE_CLAUDE_NO_BUDGET=1 refuses --max-budget-usd the way an older Claude Code would (unknown option).
FAKE_CLAUDE_CRITIC_ERROR=1 answers a critic prompt the way Claude Code reports a login or usage-limit error: exit 1,
        is_error true and the error as the "result" text (with a cost).
FAKE_VERDICT=nojson answers the verdict without its json block (the studio falls back to the critic's scores);
        FAKE_VERDICT_S=<s> takes that long first (round.json must stay fresh meanwhile).
FAKE_ENV_LOG=<file>: each call appends a line {"mode", "markers"}: which of a Claude Code session's own environment
        variables (CLAUDECODE, CLAUDE_CODE_CHILD_SESSION, ...) it was started with (the studio starts none as a child).
"""
import json, os, re, sys, threading, time

TESTS = os.path.dirname(os.path.abspath(__file__))
OUT = threading.Lock()                         # the session's events come from its turns and its agents' threads


def emit(ev):
    with OUT:
        sys.stdout.write(json.dumps(ev) + "\n")
        sys.stdout.flush()


def assistant(text=None, tool=None, inp=None, parent=None, tid=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": tid or f"t{time.time_ns()}", "name": tool, "input": inp or {}})
    emit({"type": "assistant", "message": {"role": "assistant", "content": content}, "parent_tool_use_id": parent})


class Costs:
    """The session's running totals, as a real result reports them: $0.01 a turn on the session's model, $0.004 an
    agent on its own."""

    def __init__(self, model, cap=None):
        self.by, self.model, self.cap = {}, model, cap
        self.lock = threading.Lock()

    def add(self, model, usd):
        with self.lock:
            self.by[model] = round(self.by.get(model, 0.0) + usd, 4)

    def total(self):
        with self.lock:
            return round(sum(self.by.values()), 4)

    def over(self):
        return self.cap is not None and self.total() > self.cap

    def usage(self):
        with self.lock:
            return {m: {"inputTokens": 10, "outputTokens": 100, "cacheReadInputTokens": 1000, "cacheCreationInputTokens": 500,
                        "costUSD": c} for m, c in self.by.items()}


def result(turns, t0, costs, capped=False):
    emit({"type": "result", "subtype": "error_max_budget_usd" if capped else "success", "num_turns": turns,
          "total_cost_usd": costs.total(), "modelUsage": costs.usage(), "is_error": capped,
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


def verdict(prompt):
    """A canned verdict of a pitch round: p2 wins when there is one (the last pitch otherwise), a graft idea from each
    of the others; it says how many of the pictures it was told to open exist."""
    time.sleep(float(os.environ.get("FAKE_VERDICT_S") or 0))
    ks = [int(x) for x in re.findall(r"^### p(\d+) ·", prompt, re.M)] or [1]
    titles = dict((int(k), t) for k, t in re.findall(r'^### p(\d+) · [^\n]*? · "([^"\n]*)"', prompt, re.M))
    win = 2 if 2 in ks else ks[-1]
    pics = re.findall(r"`([^`]+\.png)`", prompt)
    there = sum(os.path.exists(p) for p in pics)
    rank = [win] + [k for k in ks if k != win]
    data = {"winner": win, "ranking": rank, "verdict": f"p{win} is the boldest pitch that can be made well (test stand-in).",
            "why": "Its proof changes the rules on the ★★★ moment (test stand-in).",
            "scores": {str(k): {"ambition": 8 if k == win else 6, "song_fit": 7, "buildable": 8, "craft": 7} for k in ks},
            "graft": [{"from": k, "idea": f"p{k}'s freeze on the stop (test stand-in)", "where": "the stop"} for k in ks if k != win],
            "push": ["let the chorus break the field's rule completely (test stand-in)"]}
    text = (f"# Verdict: pitch round (test stand-in)\n**Winner:** p{win}, \"{titles.get(win, '')}\": the boldest that can be "
            f"made well.\n\n## Ranking\n" + "\n".join(f"{i}. **p{k} \"{titles.get(k, '')}\"**: ambition "
                                                     f"{data['scores'][str(k)]['ambition']} · song fit 7 · buildable 8 · craft 7."
                                                     for i, k in enumerate(rank, 1))
            + "\n\n## Ideas worth grafting into the winner\n" + "\n".join(f"- from p{g['from']}: {g['idea']}" for g in data["graft"])
            + f"\n\n## What the winner's director should push\n- the chorus (test stand-in: {there} of {len(pics)} pictures "
            f"exist)\n" + ("" if os.environ.get("FAKE_VERDICT") == "nojson" else f"\n```json\n{json.dumps(data)}\n```\n"))
    emit({"type": "result", "subtype": "success", "result": text, "total_cost_usd": 0.0211, "is_error": False,
          "modelUsage": {"fake-verdict": {}}})


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


class Agent(threading.Thread):
    """A background agent of the session: its lifecycle events while `work(say)` runs, then the session's own turn."""

    def __init__(self, q, costs, description, model, work=None, seconds=6.0):
        super().__init__(daemon=True)
        self.q, self.costs, self.description, self.model, self.work, self.seconds = q, costs, description, model, work, seconds
        self.id = f"a{time.time_ns() % 10**12}"
        self.call = f"toolu_{self.id}"
        self.done = False

    def launch(self):
        assistant(f"Starting an agent: {self.description}.", "Agent", {"description": self.description, "model": self.model,
                  "run_in_background": True, "prompt": "(test stand-in)"}, tid=self.call)
        task = {"task_id": self.id, "task_type": "local_agent", "subagent_type": "general-purpose", "description": self.description}
        emit({"type": "system", "subtype": "background_tasks_changed", "tasks": [task]})
        emit({"type": "system", "subtype": "task_started", "task_id": self.id, "tool_use_id": self.call,
              "description": self.description, "subagent_type": "general-purpose", "is_backgrounded": True,
              "task_type": "local_agent"})
        emit({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": self.call,
              "content": [{"type": "text", "text": f"Async agent launched successfully.\nagentId: {self.id}"}]}]}})
        self.start()

    def run(self):
        t0, k = time.time(), 0
        while time.time() - t0 < self.seconds:
            k += 1
            emit({"type": "system", "subtype": "task_progress", "task_id": self.id, "tool_use_id": self.call,
                  "usage": {"total_tokens": 1000 * k, "tool_uses": k}, "last_tool_name": "Read"})
            time.sleep(0.5)
        say = lambda text, tool=None, inp=None: assistant(text, tool, inp, parent=self.call)     # noqa: E731
        if self.work:
            self.work(say)
        self.costs.add(self.model_id(), 0.004)
        emit({"type": "system", "subtype": "task_updated", "task_id": self.id, "patch": {"status": "completed"}})
        emit({"type": "system", "subtype": "task_notification", "task_id": self.id, "tool_use_id": self.call,
              "status": "completed", "summary": "done (test stand-in)"})
        emit({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
        self.done = True
        self.q.put(("own", self))

    def model_id(self):
        return f"claude-{self.model}-fake"


class Director:
    """The director brief, played by the scripted mini director."""

    def __init__(self, brief, q, costs):
        self.brief, self.q, self.costs = brief, q, costs

    def running(self):
        return False

    def first(self, say):
        say("Rendering with the scripted mini director.", "Bash", {"command": "python tests/mini_director.py ."})
        sys.path.insert(0, TESTS)
        import mini_director
        try:
            mini_director.run(os.getcwd(), log=lambda s: say(s.strip()))
            if "review round" in self.brief.lower():
                notes_done(self.brief, "re-rendered (test stand-in)", say)
                with open("report.md", "a", encoding="utf-8") as f:
                    f.write("\n## Round 2\nAddressed every note (test stand-in).\n")
            say("Delivered final/music-video.mp4 and wrote report.md.")
        except Exception as ex:
            say(f"The mini director failed: {ex}")

    def answer(self, msg, say):
        if "playing your scenes live" in msg:
            return live_notes_done(msg, say)
        say(f"(studio said: {msg[:60]}...) Nothing more to improve in the test.")

    def own(self, agent, say):
        say(f"The agent ({agent.description}) finished.")


class AgentDirector(Director):
    """A director that uses the plan tools and deploys one agent for s3 (FAKE_DIRECTOR=agents)."""

    def __init__(self, brief, q, costs):
        super().__init__(brief, q, costs)
        self.project = os.path.basename(os.getcwd())
        self.agent = None

    def running(self):
        return bool(self.agent and not self.agent.done)

    def first(self, say):
        say("Reading the song folder; the concept, the shared look, s1, s2, the type and the plan.", "Read",
            {"file_path": "song/README.md"})
        m = json.load(open(os.path.join("song", "song-map.json"), encoding="utf-8"))
        field = open(os.path.join(TESTS, "scenes", "field.js"), "rb").read()
        sd = os.path.join("work", "scenes")
        for sid in ("s1", "s2", "s3", "type", "lib"):
            os.makedirs(os.path.join(sd, sid), exist_ok=True)
        open(os.path.join(sd, "lib", "look.js"), "wb").write(b'// the shared look (smoke test)\nexport const LOOK = {ink: "#e9e4d8"};\n')
        for sid in ("s1", "s2"):
            open(os.path.join(sd, sid, "scene.js"), "wb").write(b'import { LOOK } from "../lib/look.js";   // the shared look\n' + field)
        if os.path.exists(os.path.join(sd, "s3", "scene.js")):
            os.remove(os.path.join(sd, "s3", "scene.js"))      # the agent writes it
        open(os.path.join(sd, "type", "type.html"), "wb").write(open(os.path.join(TESTS, "scenes", "lyrics.html"), "rb").read())
        open(os.path.join("work", "concept.md"), "w", encoding="utf-8").write(
            "# Concept (test stand-in)\nA field of marks re-dealt on every downbeat; the stop freezes it.\n")
        open(os.path.join("work", "song-notes.md"), "w", encoding="utf-8").write("# Song notes (test stand-in)\n")
        bars = [b["t"] for b in m["bars"]]
        c1, c2, dur = round(bars[len(bars) // 3], 3), round(bars[2 * len(bars) // 3], 3), m["duration"]
        seg = lambda sid, a, b: {"id": sid, "name": f"{sid} (smoke)", "from": a, "to": b, "renderer": "web",  # noqa: E731
                                 "scene": f"work/scenes/{sid}/scene.js"}
        plan = {"segments": [seg("s1", 0.0, c1), seg("s2", c1, c2), seg("s3", c2, dur)],
                "overlays": [{"id": "type", "name": "the sung lines", "scene": "work/scenes/type/type.html", "transparent": True}]}
        json.dump(plan, open(os.path.join("work", "plan.json"), "w", encoding="utf-8"), indent=1)
        say("Wrote work/plan.json.", "Write", {"file_path": "work/plan.json"})
        self.agent = Agent(self.q, self.costs, "build s3", "haiku", work=self.build_s3,
                           seconds=float(os.environ.get("FAKE_AGENT_S") or 6))
        self.agent.launch()
        say("An agent builds s3 in the background; I'll assemble the film when it's done.")

    def build_s3(self, say):
        """The agent's own work: s3's scene and its preview (through the render queue, like any render)."""
        scene = os.path.join("work", "scenes", "s3", "scene.js")
        say("Writing s3.", "Write", {"file_path": scene})
        open(scene, "wb").write(b'import { LOOK } from "../lib/look.js";   // the shared look\n'
                                + open(os.path.join(TESTS, "scenes", "field.js"), "rb").read())
        say("Rendering s3's preview.", "Bash", {"command": f"studio.py plan render {self.project} --preview --only s3"})
        studio_cmd("plan", "render", self.project, "--preview", "--only", "s3")

    def own(self, agent, say):
        say("The agent built s3: assembling the film.", "Bash", {"command": f"studio.py plan assemble {self.project} --preview"})
        studio_cmd("plan", "assemble", self.project, "--preview", log=say)
        say("The film holds: the final.", "Bash", {"command": f"studio.py plan assemble {self.project} --final"})
        studio_cmd("plan", "assemble", self.project, "--final", log=say)
        studio_cmd("check", self.project, log=say)
        open("report.md", "w", encoding="utf-8").write(
            "# Report (the test stand-in with an agent)\n\nThree segments in one shared look: s1 and s2 by the director, s3 by "
            "an agent (haiku); the sung lines as a transparent overlay; assembled with the plan tools.\n\nTimestamps to "
            "check by ear: the stop.\n")
        say("Delivered final/music-video.mp4 and wrote report.md.", "Write", {"file_path": "report.md"})

    def answer(self, msg, say):
        if self.running():
            with open(os.path.join("work", "_fake_agent_heard.txt"), "a", encoding="utf-8") as f:
                f.write(msg.splitlines()[0][:200] + "\n")
        super().answer(msg, say)


class Pitch:
    """A pitch of the pitch round (<!-- mv:pitch -->), in the pitch folder its brief names."""

    def __init__(self, brief, q, costs):
        self.brief, self.q, self.costs = brief, q, costs
        m = re.search(r"work/pitches/round-\d+/p(\d+)", brief)
        self.dir, self.k = (m.group(0), int(m.group(1))) if m else ("work/pitches/round-0/p0", 0)
        fr = re.search(r"--frames (\d+):(\d+)", brief)
        self.a, self.b = (int(fr.group(1)), int(fr.group(2))) if fr else (0, 60)
        an = re.search(r"your angle is \*\*(.+?)\*\*", brief)
        self.angle = an.group(1) if an else "?"
        self.project = os.path.basename(os.getcwd())
        self.agent = None
        modes = dict(x.split(":", 1) for x in (os.environ.get("FAKE_PITCH") or "").split(",") if ":" in x)
        self.mode = modes.get(str(self.k), "full")
        self.lingers = self.mode == "linger"         # it keeps running after its input is closed

    def running(self):
        return bool(self.agent and not self.agent.done)

    def first(self, say):
        d, k = self.dir, self.k
        if self.mode in ("md", "linger", "fail"):
            os.makedirs(d, exist_ok=True)
            if self.mode != "fail":
                open(os.path.join(d, "pitch.md"), "w", encoding="utf-8").write(
                    f"# Only a pitch ({self.mode}, pitch {k})\n\n**The idea:** A pitch without its proof (test stand-in, "
                    f"{self.mode}).\n")
                say("Wrote pitch.md; the proof render failed.", "Write", {"file_path": f"{d}/pitch.md"})
            else:
                say("The proof render failed; nothing to deliver.")
            return
        say(f"Pitch {k} ({self.angle}): reading the song, then the proof over frames {self.a}:{self.b}.", "Read",
            {"file_path": "song/README.md"})
        field = open(os.path.join(TESTS, "scenes", "field.js"), "rb").read()
        imp = b'import { LOOK } from "../lib/look.js";   // the pitch\'s shared look\n'
        for sub in ("lib", "proof", "style"):
            os.makedirs(os.path.join(d, "scenes" if sub != "style" else "", sub), exist_ok=True)
        open(os.path.join(d, "scenes", "lib", "look.js"), "wb").write(
            f'// the shared look of pitch {k} (smoke test)\nexport const LOOK = {{ink: "#e9e4d8", pitch: {k}}};\n'.encode())
        open(os.path.join(d, "scenes", "proof", "scene.js"), "wb").write(imp + field)
        pj = json.load(open("project.json", encoding="utf-8"))
        fps = int((pj.get("format") or {}).get("fps") or 30)
        m = json.load(open(os.path.join("song", "song-map.json"), encoding="utf-8"))
        if k == 2:                                     # a draft plan for the whole song
            bars = [b["t"] for b in m["bars"]]
            c1 = round(bars[len(bars) // 2], 3)
            for sid in ("s1", "s2"):
                os.makedirs(os.path.join(d, "scenes", sid), exist_ok=True)
                open(os.path.join(d, "scenes", sid, "scene.js"), "wb").write(imp + field)
            plan = {"segments": [{"id": "s1", "name": "s1 (pitch 2)", "from": 0.0, "to": c1, "renderer": "web",
                                  "scene": "work/scenes/s1/scene.js"},
                                 {"id": "s2", "name": "s2 (pitch 2)", "from": c1, "to": m["duration"], "renderer": "web",
                                  "scene": "work/scenes/s2/scene.js"}]}
            json.dump(plan, open(os.path.join(d, "plan.json"), "w", encoding="utf-8"), indent=1)
            say("Wrote a draft plan.", "Write", {"file_path": f"{d}/plan.json"})
        open(os.path.join(d, "pitch.md"), "w", encoding="utf-8").write(
            f"# The field, dealt from {self.angle} (pitch {k})\n\n**The idea:** A field of marks re-dealt on every downbeat, "
            f"seen from {self.angle}; the stop freezes it mid-deal. Test stand-in, pitch {k}.\n\n**Why this song:** its "
            f"downbeats are its spine.\n\n## The visual language\nSquare marks, one ink (#e9e4d8) on four grounds.\n\n"
            f"## Signature moments\n- 12:0 the chorus: the field re-deals itself.\n\n## Section plan\n| time | bars | music "
            f"| picture |\n|---|---|---|---|\n| 0:00 | 0-4 | intro | the field |\n\n## Unlike the defaults\nNo spectrum bars.\n\n"
            f"## How it gets made\nThe web engine (2D canvas): about a minute here.\n\n## Risks\nNone (test stand-in).\n")
        say("Wrote pitch.md.", "Write", {"file_path": f"{d}/pitch.md"})
        t1, t2 = round((self.a + (self.b - self.a) / 3) / fps, 2), round((self.a + 2 * (self.b - self.a) / 3) / fps, 2)
        say("Two style frames.", "Bash", {"command": f"studio.py run {self.project} web-engine/render.py ... --still {t1},{t2}"})
        studio_cmd("run", self.project, "web-engine/render.py", f"{d}/scenes/proof/scene.js", "--still", f"{t1},{t2}",
                   "--out", f"{d}/style", log=say)
        say("The proof.", "Bash", {"command": f"studio.py run {self.project} web-engine/render.py ... --frames {self.a}:{self.b}"})
        studio_cmd("run", self.project, "web-engine/render.py", f"{d}/scenes/proof/scene.js", "--frames", f"{self.a}:{self.b}",
                   "--preview", "--out", f"{d}/proof.mp4", log=say)
        if k == 1:
            self.agent = Agent(self.q, self.costs, "pitch notes", "haiku", work=self.notes, seconds=2.0)
            self.agent.launch()
        say(f"Pitch {k} is delivered.")

    def notes(self, say):
        say("Writing the notes.", "Write", {"file_path": f"{self.dir}/notes-agent.md"})
        open(os.path.join(self.dir, "notes-agent.md"), "w", encoding="utf-8").write("# Notes (the pitch's agent, test stand-in)\n")

    def answer(self, msg, say):
        say("This is my best: ending." if "push it further" in msg or "Before you end" in msg else f"(studio said: {msg[:60]})")

    def own(self, agent, say):
        say("The agent's notes are in.")


class Echo:
    """Works a little on each turn; says what it got (the session plumbing's test). <!-- mv:echo-agent --> also starts
    a background agent in its first turn."""

    def __init__(self, brief, q, costs):
        self.wait = float(os.environ.get("FAKE_ECHO_S") or 2)
        self.agent = Agent(q, costs, "echo agent", "haiku", seconds=float(os.environ.get("FAKE_AGENT_S") or 4)) \
            if "<!-- mv:echo-agent -->" in brief else None

    def running(self):
        return bool(self.agent and not self.agent.done)

    def first(self, say):
        time.sleep(self.wait)
        if self.agent:
            self.agent.launch()
        say("echo: the first message")

    def answer(self, msg, say):
        say(f"echo: {msg[:80]}")
        time.sleep(self.wait)

    def own(self, agent, say):
        say("echo: the agent finished")


def director_kind():
    k = os.environ.get("FAKE_DIRECTOR") or (open("FAKE_DIRECTOR").read().strip() if os.path.exists("FAKE_DIRECTOR") else "")
    return AgentDirector if k == "agents" else Director


def session(argv):
    """A stream-json session: one turn per message that finds it idle; lines that come in while it works join that
    turn; an agent that finishes while it is idle starts a turn of its own."""
    import queue
    q = queue.Queue()

    def reader():
        for line in sys.stdin:
            q.put(line)
        q.put(None)
    threading.Thread(target=reader, daemon=True).start()
    first = q.get()
    brief = (text_of(first) or "") if first else ""
    role = Echo if "<!-- mv:echo" in brief else Pitch if "<!-- mv:pitch -->" in brief else director_kind()
    model = f"fake-{role.__name__.lower()}"
    cap = float(argv[argv.index("--max-budget-usd") + 1]) if "--max-budget-usd" in argv else None
    costs = Costs(model, cap)
    init = {"type": "system", "subtype": "init", "model": model, "tools": ["Task", "Read", "Bash", "TaskStop"],
            "permissionMode": "dontAsk", "session_id": "fake"}
    emit(init)
    t0 = time.time()
    who = role(brief, q, costs)
    say = lambda text, tool=None, inp=None: assistant(text, tool, inp)     # noqa: E731
    assistant("Reading the brief.", "Read", {"file_path": "song/README.md"})
    slow = os.environ.get("FAKE_CLAUDE_SLOW") or (open("FAKE_CLAUDE_SLOW").read().strip() if os.path.exists("FAKE_CLAUDE_SLOW") else "0")
    time.sleep(float(slow))
    who.first(say)
    turns, done, capped = 2, first is None, False

    def end():                                     # stdin closed: it exits (a lingering one keeps "working")
        if getattr(who, "lingers", False):
            time.sleep(600)

    def handle(item):
        nonlocal turns, done
        if item is None:
            done = True
        elif isinstance(item, tuple):                  # an agent finished while this turn ran
            turns += 1
            who.own(item[1], say)
        else:
            msg = text_of(item)
            if msg:
                turns += 1
                say("(the cost cap was reached: nothing more)") if capped else who.answer(msg, say)
    while True:
        while True:                                # what came in meanwhile joins this turn
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            handle(item)
        costs.add(model, 0.01)
        capped = capped or costs.over()
        result(turns, t0, costs, capped)
        if done:
            return end()
        item = q.get()                             # idle until the next message, or one of its agents finishing
        if item is None:
            return end()
        t0, turns = time.time(), 0
        emit(init)                                 # every turn starts with an init (the real CLI's way)
        handle(item)


MARKERS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED",
           "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH")


def main():
    a = sys.argv[1:]
    if os.environ.get("FAKE_ENV_LOG") and ("-p" in a):
        with open(os.environ["FAKE_ENV_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"mode": "session" if "stream-json" in a else "print",
                                "markers": [k for k in MARKERS if k in os.environ]}) + "\n")
    if "--version" in a:
        print("0.0.0 (fake Claude Code for the studio's tests)")
    elif a[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "fake"}))
    elif "stream-json" in a:
        session(a)
    elif "-p" in a:
        if "--max-budget-usd" in a and os.environ.get("FAKE_CLAUDE_NO_BUDGET"):
            print("error: unknown option '--max-budget-usd'", file=sys.stderr)
            sys.exit(1)
        prompt = sys.stdin.read()                  # the prompt comes in on stdin
        if "<!-- mv:critic -->" in prompt and os.environ.get("FAKE_CLAUDE_CRITIC_ERROR"):
            emit({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login",
                  "total_cost_usd": 0.004})
            sys.exit(1)
        critic(prompt) if "<!-- mv:critic -->" in prompt else verdict(prompt) if "<!-- mv:verdict -->" in prompt else story()
    else:
        print("fake claude: nothing to do", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
