"""A stand-in for the `claude` CLI, for tests that must not spend real usage (MV_CLAUDE=python tests/fake_claude.py,
or the fake_bin/claude wrapper next to it).

It speaks the same headless protocol the studio uses:
  claude --version                                                     -> a version line
  claude auth status                                                   -> {"loggedIn": true}
  claude -p "<prompt>" --output-format json ...                        -> the story step: a song story from the song map
  claude -p --input-format stream-json --output-format stream-json ... -> the director: reads the brief from stdin,
        emits init / assistant / tool_use / result events, runs tests/mini_director.py in the project folder (cwd),
        writes report.md, answers the studio's follow-up messages, exits when stdin closes.
FAKE_CLAUDE_SLOW=<s> (or a file of that name in the project) sleeps that long first (to test stopping a run).
"""
import json, os, sys, time

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


def director():
    t0 = time.time()
    first = sys.stdin.readline()
    brief = json.loads(first)["message"]["content"][0]["text"] if first.strip() else ""
    emit({"type": "system", "subtype": "init", "model": "fake-director", "tools": ["Read", "Bash"], "permissionMode": "dontAsk",
          "session_id": "fake"})
    assistant("Reading the brief and the song folder.", "Read", {"file_path": "song/README.md"})
    slow = os.environ.get("FAKE_CLAUDE_SLOW") or (open("FAKE_CLAUDE_SLOW").read().strip() if os.path.exists("FAKE_CLAUDE_SLOW") else "0")
    time.sleep(float(slow))
    assistant("Rendering with the scripted mini director.", "Bash", {"command": "python tests/mini_director.py ."})
    sys.path.insert(0, TESTS)
    import mini_director
    try:
        mini_director.run(os.getcwd(), log=lambda s: assistant(s.strip()))
        if "review round" in brief.lower():
            p = os.path.join("review", "notes.json")
            notes = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
            for n in notes:
                if not n.get("done"):
                    n.update(done=True, fix="re-rendered (test stand-in)")
            json.dump(notes, open(p, "w", encoding="utf-8"), indent=1)
            with open("report.md", "a", encoding="utf-8") as f:
                f.write("\n## Round 2\nAddressed every note (test stand-in).\n")
        assistant("Delivered final/music-video.mp4 and wrote report.md.")
    except Exception as ex:
        assistant(f"The mini director failed: {ex}")
    result(4, t0)
    turns = 4
    for line in sys.stdin:                         # the studio's follow-ups (nudges, time warnings) until stdin closes
        try:
            msg = json.loads(line)["message"]["content"][0]["text"]
        except Exception:
            continue
        turns += 1
        assistant(f"(studio said: {msg[:60]}...) Nothing more to improve in the test.")
        result(turns, t0)


def main():
    a = sys.argv[1:]
    if "--version" in a:
        print("0.0.0 (fake Claude Code for the studio's tests)")
    elif a[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "fake"}))
    elif "stream-json" in a:
        director()
    elif "-p" in a:
        sys.stdin.read()                           # the prompt comes in on stdin
        story()
    else:
        print("fake claude: nothing to do", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
