"""Claude Code from the studio: where the command is, and one headless print-mode call (the story step, the critic,
the pitch verdict). The autonomous director's long stream-json session lives in director.py.

  claude_print(prompt, cwd, model="sonnet", tools=["Read", "Glob", "Grep"], max_usd=2)
      -> {"ok", "text", "json", "cost_usd", "duration_s", "returncode", "stdout", "stderr", "cmd", "warnings"}

The call (checked against `claude --help` of Claude Code 2.1.x):
  claude -p --output-format json --permission-mode dontAsk --allowedTools <tools> --disallowedTools <the rest>
         --strict-mcp-config --no-session-persistence [--model M] [--effort E] [--max-budget-usd X]
  cwd = the project folder; the prompt goes in on stdin, never as an argument (on Windows an npm `claude.cmd` passes
  arguments through cmd.exe, which mangles newlines, quotes and parentheses).
"""
import json, os, shutil, subprocess, sys, time

READ_ONLY = ["Read", "Glob", "Grep"]
NEVER = ["Write", "Edit", "Bash", "WebFetch", "WebSearch"]     # a print-mode call reads; it never writes or fetches


def claude_bin():
    """The claude command as a list (MV_CLAUDE overrides; a .py there runs with this Python: tests use a stand-in)."""
    c = os.environ.get("MV_CLAUDE") or shutil.which("claude")
    if not c:
        return None
    return [sys.executable, c] if c.endswith(".py") else [c]


def kill_tree(proc):
    """Stop a process and what it started (an npm claude.cmd is cmd.exe -> node on Windows)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        pass


def print_cmd(model=None, effort=None, tools=("Read",), max_usd=None, args=()):
    cl = claude_bin()
    if not cl:
        raise RuntimeError("Claude Code isn't installed (https://claude.com/claude-code)")
    cmd = [*cl, "-p", "--output-format", "json", "--permission-mode", "dontAsk", "--allowedTools", *tools,
           "--disallowedTools", *[t for t in NEVER if t not in tools], "--strict-mcp-config", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if max_usd:
        cmd += ["--max-budget-usd", f"{float(max_usd):g}"]
    return cmd + list(args)


def claude_print(prompt, cwd, model=None, effort=None, tools=("Read",), timeout=1200, max_usd=None, args=(), env=None):
    """One headless print-mode call with read-only tools. The answer's text is "text" (the json "result"; the raw
    stdout when it isn't json), "json" the parsed reply ({} if none). Raises RuntimeError only when Claude Code is
    missing or doesn't answer in time; a call that ran but failed comes back with ok False and its stderr.
    A CLI that doesn't know --max-budget-usd is asked again without it (and says so in "warnings")."""
    tools = list(tools)
    warnings = []
    # a call started from inside a Claude Code session (an interactive director running the critic) is a session of
    # its own, not a nested one
    env = dict(env or os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDECODE", None)
    for attempt in (0, 1):
        cmd = print_cmd(model, effort, tools, max_usd, args)
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", env=env,
                                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            out, err = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise RuntimeError(f"Claude didn't answer within {timeout // 60} min")
        if attempt == 0 and max_usd and proc.returncode and "max-budget-usd" in (err + out) and \
                any(w in (err + out).lower() for w in ("unknown option", "unknown argument", "unrecognized")):
            warnings.append("this Claude Code doesn't know --max-budget-usd: ran without a spending cap")
            max_usd = None
            continue
        break
    try:
        res = json.loads(out)
        text = res.get("result", "") if isinstance(res, dict) else out
        res = res if isinstance(res, dict) else {}
    except Exception:
        res, text = {}, out
    return {"ok": proc.returncode == 0 and not res.get("is_error"), "text": text or "", "json": res,
            "cost_usd": res.get("total_cost_usd"), "duration_s": round(time.time() - t0, 1), "returncode": proc.returncode,
            "stdout": out, "stderr": err, "cmd": cmd, "warnings": warnings}
