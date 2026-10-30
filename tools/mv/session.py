"""One long Claude Code session in stream-json, run by the studio: the autonomous director (solo mode), the
pipeline's planner and its builders (and the pitch round's directors).

  s = Session(cmd, cwd, rdir, name="planner", status=status_dict, env=env).start()
  s.send(text, "the brief")             a user message: to an idle session it starts a turn, to a busy one it joins it
  s.turn_over(now)                      it ended a turn the runner hasn't dealt with yet: decide, then s.ack()
  s.hung(now, 20 * 60)                  busy, and no sign of life for that long
  s.stop("why")                         close its input (it finishes and exits); after a grace, stop it with its children

How a session answers (verified on the real CLI, Claude Code 2.1.311, haiku, stream-json in and out): a message sent
to an idle session starts a turn, which ends with one `result` event. Messages sent while that turn runs are merged
into it: three messages, the second and third sent while a 20 s Bash command ran, gave ONE result whose text answered
all three. So a session is busy from the moment a message goes to an idle session until the next `result`; a message
sent while it is busy joins the running turn and gets no result of its own. A session may also start a turn of its
own (a background command it started finishing): assistant or tool events while it is idle make it busy until that
turn's result. Other events seen in the real stream: system init, task_started, task_notification, task_summary,
thinking_tokens, post_turn_summary. A turn counts as over once the result is 2 s old with nothing after it (a message
that crossed the result on its way starts a turn of its own: then it is busy again). When a message went in while it
was busy just before its result (within CROSS_S), the runner waits CROSS_SETTLE_S instead: the CLI may have merged it
into the turn that ended, or start a new turn for it, and that turn's first event can take a while at high effort.
"No sign of life" (hung) counts from the last event or from the message that made it busy, whichever is later: the
time it sat idle waiting for the studio (a render, the critic) doesn't count.
The command (claude -p --input-format stream-json --output-format stream-json --verbose ...) comes from the caller
(director.claude_args: the sandbox). rdir gets events.jsonl (every event, raw), stderr.log and messages.md (every
message the studio sent, in full).
"""
import json, os, re, subprocess, threading, time

from .claude import kill_tree

CROSS_S = 10.0                  # a message sent while busy this close before the result may have crossed it
CROSS_SETTLE_S = 30.0           # then wait this long for the turn it may start, before the turn counts as over


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


class Session:
    """A stream-json Claude Code process and what is known about it. `status` is the dict the caller saves (the run's
    status.json for the director or the planner; a builder's own): this keeps events (the live log, the last `keep`),
    turns, cost_usd, messages_sent, pid, last_event, usage, session_id, model_used and busy in it."""

    def __init__(self, cmd, cwd, rdir, name="director", status=None, env=None, started=None, keep=300):
        self.cmd, self.cwd, self.rdir, self.name, self.env = cmd, cwd, rdir, name, env
        self.status = status if status is not None else {}
        for k, v in (("events", []), ("turns", 0), ("cost_usd", 0.0), ("messages_sent", []), ("pid", None)):
            self.status.setdefault(k, v)
        self.started = started or time.time()
        self.keep = keep
        self.lock = threading.RLock()
        self.results = []
        self.acked = 0                          # results the runner has dealt with
        self.busy = False
        self.idle_at = None                     # when the last turn ended
        self.last_event = None
        self.busy_since = None                  # when a message made it busy (an idle wait before it isn't "quiet")
        self.joined_at = None                   # when the last message went in while it was busy
        self.t_start = None
        self.proc = None
        self.closed = False                     # its input (stdin)
        self.raw = self.errlog = None

    # ------------------------------------------------------------------ the process
    def start(self):
        os.makedirs(self.rdir, exist_ok=True)
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     env=self.env, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        self.t_start = time.time()
        self.status["pid"] = self.proc.pid
        self.raw = open(os.path.join(self.rdir, "events.jsonl"), "w", encoding="utf-8")
        self.errlog = open(os.path.join(self.rdir, "stderr.log"), "w", encoding="utf-8")
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._errreader, daemon=True).start()
        return self

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self):
        return self.proc.returncode if self.proc else None

    def note(self, line):
        with self.lock:
            self.status["events"].append({"t": round(time.time() - self.started), "text": line})
            del self.status["events"][:-self.keep]

    def _reader(self):
        for line in self.proc.stdout:
            try:
                self.raw.write(line)
                self.raw.flush()
            except (OSError, ValueError):
                pass
            try:
                ev = json.loads(line)
            except Exception:
                continue
            with self.lock:
                now = time.time()
                self.last_event = now
                self.status["last_event"] = now
                s = summarize(ev)
                if s:
                    self.note(s)
                t = ev.get("type")
                if t == "result":
                    self.status["turns"] += ev.get("num_turns") or 0
                    self.status["cost_usd"] = round(self.status["cost_usd"] + (ev.get("total_cost_usd") or 0), 4)
                    self.status["usage"] = ev.get("usage")
                    self.results.append(ev)
                    self.busy, self.idle_at = False, now
                elif t in ("assistant", "user") and not self.busy:
                    self.busy = True                # a turn of its own (e.g. a background command finished)
                if t == "system" and ev.get("subtype") == "init":
                    self.status["session_id"] = ev.get("session_id")
                    self.status["model_used"] = ev.get("model")
                self.status["busy"] = self.busy
        with self.lock:
            self.busy = False
            self.status["busy"] = False

    def _errreader(self):
        for line in self.proc.stderr:
            try:
                self.errlog.write(line)
                self.errlog.flush()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------ talking to it
    def send(self, text, why):
        """A user message. To an idle session it starts a turn; to a busy one it joins the running turn (no result
        of its own). False when it couldn't be sent (the input is closed, the process is gone)."""
        if self.proc is None or self.closed:
            self.note(f"(could not message the {self.name}: its input is closed) ({why})")
            return False
        try:
            self.proc.stdin.write(user_msg(text))
            self.proc.stdin.flush()
        except Exception as ex:
            self.note(f"(could not message the {self.name}: {ex})")
            return False
        try:
            with open(os.path.join(self.rdir, "messages.md"), "a", encoding="utf-8") as f:
                f.write(f"\n## {time.strftime('%H:%M:%S')} studio → {self.name} ({why})\n\n{text.strip()}\n")
        except OSError:
            pass
        with self.lock:
            joined = self.busy
            if joined:
                self.joined_at = time.time()
            else:
                self.busy_since = time.time()
            self.busy = True
            self.status["busy"] = True
            self.status["messages_sent"].append({"t": round(time.time() - self.started), "why": why}
                                                | ({"joined": True} if joined else {}))
            self.note(f"✉ studio → {self.name} ({why})")
        return True

    def turn_over(self, now=None, settle=2.0):
        """It ended a turn the runner hasn't dealt with yet: idle, a result came, nothing since for `settle` s (or
        CROSS_SETTLE_S when a message went in just before that result: it may start a turn of its own)."""
        now = now or time.time()
        with self.lock:
            if self.busy or len(self.results) <= self.acked:
                return False
            if self.idle_at is None:
                return True
            crossed = self.joined_at is not None and 0 <= self.idle_at - self.joined_at < CROSS_S
            return now - self.idle_at >= (max(settle, CROSS_SETTLE_S) if crossed else settle)

    def ack(self):
        with self.lock:
            self.acked = len(self.results)

    def last_result(self):
        with self.lock:
            return self.results[-1] if self.results else None

    def quiet(self, now=None):
        """Seconds since its last sign of life: its last event, or the message that made it busy (whichever is later),
        or its start."""
        return (now or time.time()) - max(self.last_event or 0, self.busy_since or 0, self.t_start or self.started)

    def hung(self, now, after):
        """Busy and no sign of life for `after` seconds: a long command makes no events while it runs, so `after`
        is generous (a hung session is the only thing this catches)."""
        return self.alive() and self.busy and self.quiet(now) > after

    # ------------------------------------------------------------------ the end
    def close_input(self):
        if not self.closed:
            self.closed = True
            try:
                self.proc.stdin.close()
            except Exception:
                pass

    def stop(self, reason=None, wait=20):
        """End it: close its input (it finishes the turn and exits), and after `wait` s stop it with what it started
        (taskkill /T on Windows, the process and its children elsewhere): a child left running keeps spending."""
        if self.proc is not None and self.proc.poll() is None:
            if reason:
                self.note(f"■ ending the session: {reason}")
            self.close_input()
            try:
                self.proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                kill_tree(self.proc)
        self.close_input()

    def finish(self):
        """Close its files (after stop)."""
        time.sleep(0.5)
        for f in (self.raw, self.errlog):
            try:
                f and f.close()
            except Exception:
                pass

    def tail(self, n=6):
        with self.lock:
            return [e["text"] for e in self.status["events"][-n:]]
