"""One long Claude Code session in stream-json, run by the studio: the autonomous director (and the pitch round's
directors).

  s = Session(cmd, cwd, rdir, name="director", status=status_dict, env=env).start()
  s.send(text, "the brief")             a user message: to an idle session it starts a turn, to a busy one it joins it
  s.turn_over(now)                      it ended a turn the runner hasn't dealt with yet, and nothing of its own is
                                        still going on (an agent, a background command): decide, then s.ack()
  s.working                             busy, or its agents / background commands are still running
  s.hung(now, 20 * 60)                  working, and no sign of life for that long
  s.stop("why")                         close its input (it finishes and exits); after a grace, stop it with its children

How a session answers (verified on the real CLI, Claude Code 2.1.311, haiku, stream-json in and out): a message sent
to an idle session starts a turn, which ends with one `result` event. Messages sent while that turn runs are merged
into it: three messages, the second and third sent while a 20 s Bash command ran, gave ONE result whose text answered
all three. So a session is busy from the moment a message goes to an idle session until the next `result`; a message
sent while it is busy joins the running turn and gets no result of its own. A turn counts as over once the result is
2 s old with nothing after it (a message that crossed the result on its way starts a turn of its own: then it is busy
again). When a message went in while it was busy just before its result (within CROSS_S), the runner waits
CROSS_SETTLE_S instead: the CLI may have merged it into the turn that ended, or start a new turn for it, and that
turn's first event can take a while at high effort.
Agents and background commands (verified the same way): the Agent tool with run_in_background: true runs an agent in
the background ("Async agent launched"; without it, on 2.1.312, in the foreground: the turn waits for it); the session
may end its turn while it runs, and when it finishes the session starts a turn of its own,
with no user message (a new system/init, its assistant events, a result). The stream says what runs:
system/background_tasks_changed (the current list of tasks: [] when none), task_started (task_id, tool_use_id,
description, subagent_type, task_type), task_progress (usage, last_tool_name), task_updated, task_notification (the
end, with its status); an agent's own assistant events carry parent_tool_use_id (the Agent call's id), and a command
an agent runs is a task of its own (owned_by_subagent). So while tasks run the session is *working* even when it is
idle: no turn of it is over then, and after the last one ends the runner waits up to SELF_TURN_S for the turn the
session starts for it (seen within a second of the empty background_tasks_changed). A system/init starts every turn
(a message's and its own): only the first is the session starting. The `result` events' total_cost_usd and modelUsage
are the session's running totals, not the turn's (results gave 0.00735, 0.00820, 0.00863): the session's cost is its
latest result's; num_turns is the turn's own (2, 1, 1), so they add up.
"No sign of life" (hung) counts from the last event (an agent's progress counts) or from the message that made it
busy, whichever is later: the time it sat idle waiting for the studio (a render, the critic) doesn't count.
The command (claude -p --input-format stream-json --output-format stream-json --verbose ...) comes from the caller
(director.claude_args: the sandbox). rdir gets events.jsonl (every event, raw), stderr.log and messages.md (every
message the studio sent, in full). The caller's status dict gets events (the live log), turns, cost_usd,
model_usage {model: {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd}}, agents
[{id, description, model, subagent_type, started, ended, state, tools, last}] (seconds from the session's start),
background (tasks running now), budget_hit (a result said the --max-budget-usd cap was reached), busy, working.
"""
import json, os, re, subprocess, threading, time

from .claude import clean_env, kill_tree

CROSS_S = 10.0                  # a message sent while busy this close before the result may have crossed it
CROSS_SETTLE_S = 30.0           # then wait this long for the turn it may start, before the turn counts as over
SELF_TURN_S = 90.0              # after its last agent ended while it was idle: wait this long for its own turn
AGENT_LINE_S = 20.0             # an agent's own tool calls: at most one line in the live log per agent this often
AGENT_TOOLS = ("Agent", "Task")                  # the agent tool (the init event lists it as Task)
LAUNCHES = AGENT_TOOLS + ("Workflow",)           # what starts agents (Workflow: ultracode's multi-agent workflows)


def tool_arg(c):
    """A tool call's argument, in short."""
    inp = c.get("input") or {}
    if c.get("name") == "TaskCreate":                # its own to-do list (TaskCreate / TaskUpdate)
        return str(inp.get("subject") or "")
    if c.get("name") == "TaskUpdate":
        return f"#{inp.get('taskId') or '?'} {inp.get('status') or ''}".strip()
    if c.get("name") == "SendMessage":
        return f"to {inp.get('to') or '?'}: " + (str(inp.get("message") or "").strip().splitlines() or [""])[0]
    if c.get("name") in AGENT_TOOLS:
        return f"{inp.get('description') or ''}" + (f" ({inp['model']})" if inp.get("model") else "")
    return str(inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("path") or inp.get("description") or "")


def summarize(ev):
    """One short line (or None) for the web app's live log. (A session's own log, Session._reader, says a later init
    and the agents' lifecycle in its own way.)"""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"session started · model {ev.get('model')} · {len(ev.get('tools') or [])} tools · permission {ev.get('permissionMode')}"
    if t == "assistant":
        out = []
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append("» " + re.sub(r"\s+", " ", c["text"]).strip()[:400])
            elif c.get("type") == "tool_use":
                out.append(f"⚙ {c.get('name')}: {tool_arg(c)[:220]}")
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
                f"${(ev.get('total_cost_usd') or 0):.2f} so far, {round((ev.get('duration_ms') or 0) / 60000, 1)} min")
    return None


def user_msg(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"


def model_usage(mu):
    """A result's modelUsage (the session's running totals per model) in the studio's words."""
    out = {}
    for m, u in (mu or {}).items():
        if isinstance(u, dict):
            out[m] = {"input_tokens": u.get("inputTokens") or 0, "output_tokens": u.get("outputTokens") or 0,
                      "cache_read_tokens": u.get("cacheReadInputTokens") or 0,
                      "cache_write_tokens": u.get("cacheCreationInputTokens") or 0,
                      "cost_usd": round(float(u.get("costUSD") or 0), 4)}
    return out


def dur(sec):
    sec = int(max(0, sec))
    return f"{sec} s" if sec < 90 else f"{round(sec / 60)} min"


class Session:
    """A stream-json Claude Code process and what is known about it. `status` is the dict the caller saves (the run's
    status.json): this keeps the keys the module doc lists in it."""

    def __init__(self, cmd, cwd, rdir, name="director", status=None, env=None, started=None, keep=300):
        self.cmd, self.cwd, self.rdir, self.name, self.env = cmd, cwd, rdir, name, env
        self.status = status if status is not None else {}
        for k, v in (("events", []), ("turns", 0), ("cost_usd", 0.0), ("messages_sent", []), ("pid", None), ("agents", []),
                     ("model_usage", {}), ("background", 0)):
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
        self.inits = 0
        self.tasks = {}                         # its background tasks running now (background_tasks_changed), by id
        self.task_end_at = None                 # when the last of them ended: it starts a turn of its own for that
        self.agents = {}                        # every agent it started: task id -> its entry in status["agents"]
        self.launches = {}                      # Agent tool calls, by tool_use id: what it asked for (model, ...)
        self.by_call = {}                       # an agent's tool_use id -> its task id (its own events carry it)
        self.said = {}                          # task id -> [when its last line went into the live log, calls since]
        self.t_start = None
        self.proc = None
        self.closed = False                     # its input (stdin)
        self.raw = self.errlog = None

    # ------------------------------------------------------------------ the process
    def start(self):
        os.makedirs(self.rdir, exist_ok=True)
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     env=clean_env(self.env),           # a session of its own, not a nested one
                                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
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

    @property
    def working(self):
        """Busy, or something of its own is still going on: an agent or a background command running, or the turn
        it starts when the last of them ended (not come yet)."""
        with self.lock:
            return self.busy or bool(self.tasks) or self.awaiting_own_turn()

    def awaiting_own_turn(self, now=None):
        return self.task_end_at is not None and (self.idle_at is None or self.task_end_at > self.idle_at) and \
            (now or time.time()) - self.task_end_at < SELF_TURN_S

    def note(self, line):
        with self.lock:
            self.status["events"].append({"t": round(time.time() - self.started), "text": line})
            del self.status["events"][:-self.keep]

    def rel_t(self, t=None):
        return round((t or time.time()) - self.started)

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
            if not isinstance(ev, dict):
                continue
            with self.lock:
                now = time.time()
                self.last_event = now
                self.status["last_event"] = now
                try:
                    self._event(ev, now)
                except Exception as ex:          # an event of a shape not seen before: noted, the session goes on
                    self.note(f"(the studio couldn't read an event: {type(ex).__name__}: {ex})")
                self.status["busy"] = self.busy
                self.status["working"] = self.busy or bool(self.tasks)
        with self.lock:
            self.busy = False
            self.tasks = {}
            for a in self.agents.values():
                if a["state"] == "running":
                    a.update(state="stopped", ended=self.rel_t())
            self.status.update(busy=False, working=False, background=0)

    def _event(self, ev, now):
        t, sub = ev.get("type"), ev.get("subtype")
        parent = ev.get("parent_tool_use_id")
        if t == "system":
            if sub == "init":
                self.inits += 1
                if self.inits == 1:
                    self.note(summarize(ev))
                    self.status["session_id"] = ev.get("session_id")
                    self.status["model_used"] = ev.get("model")
                    self.status["tools"] = ev.get("tools") or []
                elif not self.busy:              # a turn of its own (an agent or a background command finished)
                    self.own_turn(now)
            elif sub == "background_tasks_changed":
                had = bool(self.tasks)
                self.tasks = {str(x.get("task_id")): x for x in ev.get("tasks") or [] if isinstance(x, dict)}
                self.status["background"] = len(self.tasks)
                if had and not self.tasks:
                    self.task_end_at = now
            elif sub == "task_started":
                self.task_started(ev, now)
            elif sub == "task_progress":
                a = self.agents.get(str(ev.get("task_id")))
                if a:
                    a["last"] = ev.get("last_tool_name") or a.get("last")
            elif sub == "task_updated":
                a = self.agents.get(str(ev.get("task_id")))
                st = ((ev.get("patch") or {}).get("status") or "")
                if a and st and st not in ("running", "pending"):
                    self.task_ended(a, st, now)
            elif sub == "task_notification":
                a = self.agents.get(str(ev.get("task_id")))
                if a:
                    self.task_ended(a, ev.get("status") or "completed", now)
            return
        if parent:                              # an agent's own work: a short line under it, now and then
            if t == "assistant":
                self.agent_event(ev, parent, now)
            return
        s = summarize(ev)
        if s:
            self.note(s)
        if t == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use" and c.get("name") in LAUNCHES:
                    self.launches[c.get("id")] = dict(c.get("input") or {}, tool=c.get("name"))
        if t == "result":
            self.status["turns"] = int(self.status.get("turns") or 0) + int(ev.get("num_turns") or 0)
            if isinstance(ev.get("total_cost_usd"), (int, float)):      # the session's running total
                self.status["cost_usd"] = round(max(float(self.status.get("cost_usd") or 0), ev["total_cost_usd"]), 4)
            if ev.get("modelUsage"):
                self.status["model_usage"] = model_usage(ev["modelUsage"])
            self.status["usage"] = ev.get("usage")
            if "budget" in str(sub or ""):
                self.status["budget_hit"] = True
                self.note("■ the session reached its cost cap (--max-budget-usd)")
            self.results.append(ev)
            self.busy, self.idle_at = False, now
        elif t in ("assistant", "user") and not self.busy:
            self.own_turn(now)

    def own_turn(self, now):
        """It started a turn of its own: for its background tasks that finished (said in the live log), or for a
        message that crossed its last result."""
        if self.task_end_at and not self.tasks and now - self.task_end_at < SELF_TURN_S:
            agent = any(a["ended"] is not None and self.rel_t(now) - a["ended"] <= SELF_TURN_S for a in self.agents.values())
            self.note(f"↻ the {self.name} started a turn of its own ({'its agent' if agent else 'its background command'} finished)")
        self.busy, self.busy_since = True, now
        self.task_end_at = None

    def task_started(self, ev, now):
        tid = str(ev.get("task_id"))
        kind = str(ev.get("task_type") or "")
        call = ev.get("tool_use_id")
        asked = self.launches.get(call) or {}
        if ev.get("owned_by_subagent"):          # an agent's own command: its line comes with the agent's events
            return
        flow = "workflow" in kind or asked.get("tool") == "Workflow"
        if "agent" not in kind and not flow and not ev.get("subagent_type") and not asked:
            if ev.get("is_backgrounded"):
                self.note(f"⏳ in the background: {str(ev.get('description') or kind or 'a task')[:120]}")
            return
        a = {"id": tid, "description": str(ev.get("description") or asked.get("description") or "an agent")[:200],
             "model": asked.get("model") or ("a workflow" if flow else self.status.get("model_used") or "the director's"),
             "subagent_type": ev.get("subagent_type") or asked.get("subagent_type") or ("workflow" if flow else None),
             "started": self.rel_t(now), "ended": None,
             "state": "running", "tools": 0, "last": None}
        self.agents[tid] = a
        self.said[tid] = [0.0, 0]
        if call:
            self.by_call[call] = tid
        self.status["agents"].append(a)
        self.note(f"🤖 agent: {a['description']} ({a['model']}) started")

    def task_ended(self, a, state, now):
        if a["state"] != "running":
            return
        ok = str(state).lower() in ("completed", "complete", "done", "success")
        a.update(state="done" if ok else str(state).lower(), ended=self.rel_t(now))
        self.note(f"🤖 agent: {a['description']} ({a['model']}) {'done' if ok else a['state']} in {dur(a['ended'] - a['started'])}"
                  + (f", {a['tools']} tool calls" if a["tools"] else ""))

    def agent_event(self, ev, parent, now):
        tid = self.by_call.get(parent, "")
        a = self.agents.get(tid)
        calls = [c for c in (ev.get("message") or {}).get("content") or [] if c.get("type") == "tool_use"]
        if not a or not calls:
            return
        a["tools"] += len(calls)
        said = self.said[tid]                   # [when its last line went in, the calls since]
        if now - said[0] < AGENT_LINE_S:
            said[1] += len(calls)
            return
        c = calls[-1]
        more = said[1] + len(calls) - 1
        said[0], said[1] = now, 0
        self.note(f"    ↳ {a['description'][:40]}: ⚙ {c.get('name')}: {tool_arg(c)[:100]}" + (f" (+{more} more)" if more else ""))

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
        with self.lock:                         # busy before it goes in: the turn's first event (its init) may come
            joined, was = self.busy, (self.busy_since, self.joined_at)      # at once, and isn't a turn of its own then
            now = time.time()
            if joined:
                self.joined_at = now
            else:
                self.busy_since = now
            self.busy = True
            self.status["busy"] = self.status["working"] = True
        try:                                    # (not under the lock: the reader takes it for every event)
            self.proc.stdin.write(user_msg(text))
            self.proc.stdin.flush()
        except Exception as ex:
            with self.lock:                     # it never went in
                self.busy_since, self.joined_at = was
                if not joined:
                    self.busy = False
                self.status["busy"], self.status["working"] = self.busy, self.busy or bool(self.tasks)
            self.note(f"(could not message the {self.name}: {ex})")
            return False
        try:
            with open(os.path.join(self.rdir, "messages.md"), "a", encoding="utf-8") as f:
                f.write(f"\n## {time.strftime('%H:%M:%S')} studio → {self.name} ({why})\n\n{text.strip()}\n")
        except OSError:
            pass
        with self.lock:
            self.status["messages_sent"].append({"t": round(now - self.started), "why": why} | ({"joined": True} if joined else {}))
            self.note(f"✉ studio → {self.name} ({why})")
        return True

    def turn_over(self, now=None, settle=2.0):
        """It ended a turn the runner hasn't dealt with yet: idle, a result came, nothing since for `settle` s (or
        CROSS_SETTLE_S when a message went in, or an agent of its ended, just before that result: it may start a turn
        of its own), and nothing of its own still going on (no agent or background command running; after the last
        one ended, the turn it starts for that has come and gone, or SELF_TURN_S passed without one)."""
        now = now or time.time()
        with self.lock:
            if self.busy or len(self.results) <= self.acked or self.tasks:
                return False
            if self.idle_at is None:
                return True
            if self.awaiting_own_turn(now):
                return False
            crossed = any(x is not None and 0 <= self.idle_at - x < CROSS_S for x in (self.joined_at, self.task_end_at))
            return now - self.idle_at >= (max(settle, CROSS_SETTLE_S) if crossed else settle)

    def ack(self):
        with self.lock:
            self.acked = len(self.results)

    def last_result(self):
        with self.lock:
            return self.results[-1] if self.results else None

    def quiet(self, now=None):
        """Seconds since its last sign of life: its last event (an agent's progress too), or the message that made it
        busy (whichever is later), or its start."""
        return (now or time.time()) - max(self.last_event or 0, self.busy_since or 0, self.t_start or self.started)

    def hung(self, now, after):
        """Working (busy, or its agents running) and no sign of life for `after` seconds: a long command makes no
        events while it runs, so `after` is generous (a hung session is the only thing this catches)."""
        return self.alive() and self.working and self.quiet(now) > after

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
