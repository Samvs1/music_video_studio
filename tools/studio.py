"""Music Video Studio: one entry point for every command.

  python tools/studio.py new <name> [--song file.mp3] [--title "..."]   create projects/<name>/ (song/, work/, final/, ...)
  python tools/studio.py list                                        list projects
  python tools/studio.py ingest <project> [--force] [--cpu]          song -> stems, beats, lyrics, the song folder (progress in cache/status.json)
  python tools/studio.py song <project> [--cpu] [--no-tags]          remake the song folder only (tools/song_kit.py)
  python tools/studio.py curves <project> [--fps N]                  remake song/frames.json + hits.json (e.g. after changing the fps)
  python tools/studio.py story <project> [--model M]                 Claude drafts song/song-story.md (headless, read-only)
  python tools/studio.py direct <project> --budget 90m [--effort high] [--model opus] [--seed-notes "..."] [--revise]
                                                                     the autonomous director (Claude Code headless) -> final/music-video.mp4
  python tools/studio.py clock <project>                             time left in the running director's budget
  python tools/studio.py check <project> [video]                     every check on the final (spec, flashes, text, sync, audio)
  python tools/studio.py versions <project>                          the delivered versions (final/music-video_vN.mp4)
  python tools/studio.py run <project> <tool.py> [args]              run any tool inside a project (eyes.py, songq.py, compose.py, ...)
  python tools/studio.py serve [--port 8765] [--no-browser]          the local web app
  python tools/studio.py doctor                                      check the install and say how to fix what's missing
  python tools/studio.py benchmark [--quick]                         measure render speeds -> tools/speeds.json
  python tools/studio.py smoke                                       end-to-end smoke test on a synthetic song (tests/smoke_test.py)

<project> is a folder name under projects/ (or a path). Every step runs the tools with MV_PROJECT set, so they read
and write inside that project. Started with another Python (e.g. plain `python`), it re-runs itself with the
studio's .venv, which has the libraries. Projects whose folder starts with "_" are internal (smoke tests) and hidden.
Carried over from aftermovie-studio's studio.py (structure, Status file, run, doctor, the .venv hand-over).
"""
import argparse, json, os, shutil, subprocess, sys, time, webbrowser

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(STUDIO, "tools")
PROJECTS = os.path.join(STUDIO, "projects")
# plain `python` on Windows is often the Store Python without the libraries: hand over to the studio's own venv
_VENV_PY = os.path.join(STUDIO, ".venv", "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
if __name__ == "__main__" and os.path.exists(_VENV_PY) and not os.environ.get("MV_IN_VENV") and \
        os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(os.path.abspath(_VENV_PY)):
    sys.exit(subprocess.run([_VENV_PY, os.path.abspath(__file__)] + sys.argv[1:], env=dict(os.environ, MV_IN_VENV="1")).returncode)
PY = sys.executable
sys.path.insert(0, TOOLS)
AUD_IN = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus")

DEFAULT_PROJECT = {
    "title": "",
    "song": {"file": "", "artist": "", "title": "", "language": ""},
    "about": "",            # what the song is about to the user
    "mood": "",             # mood or direction
    "ideas": "",
    "avoid": "",
    "format": {"preset": "1080p", "width": 1920, "height": 1080, "fps": 30},
    "lyrics": "",           # optional: the user's own lyrics (LRC with times, or plain text)
    "lyrics_fix": {},
    "status": {"stage": "new"},
}


# ------------------------------------------------------------------ helpers
def slugify(s):
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40] or "project"


def project_dir(name):
    p = name if os.path.isabs(name) or os.sep in name or "/" in name else os.path.join(PROJECTS, name)
    if not os.path.isfile(os.path.join(p, "project.json")):
        sys.exit(f"no such project: {name} (looked in {p})")
    return os.path.abspath(p)


def env_for(pdir):
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore", MV_PROJECT=pdir, MV_TOOLS=TOOLS,
                MV_IN_VENV="1")


def run(pdir, args, log=None, check=True):
    """Run a tool (path relative to tools/) inside the project; stream output to stdout and an optional log list."""
    cmd = [PY, "-W", "ignore", os.path.join(TOOLS, args[0])] + list(args[1:])
    p = subprocess.Popen(cmd, cwd=STUDIO, env=env_for(pdir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        line = line.rstrip()
        print(line, flush=True)
        if log is not None:
            log.append(line)
            del log[:-60]
    p.wait()
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} failed (exit {p.returncode})")
    return p.returncode


def load_project(pdir):
    p = os.path.join(pdir, "project.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def save_project(pdir, d):
    tmp = os.path.join(pdir, "project.json.tmp")
    json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(pdir, "project.json"))


class Status:
    """cache/status.json, polled by the web app."""

    def __init__(self, pdir, job, steps):
        self.path = os.path.join(pdir, "cache", "status.json")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.d = {"job": job, "steps": steps, "i": 0, "step": steps[0], "state": "running", "log": [],
                  "warnings": [], "started": time.time(), "updated": time.time(), "error": ""}
        self.write()

    def write(self):
        self.d["updated"] = time.time()
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, self.path)

    def step(self, i):
        self.d.update(i=i, step=self.d["steps"][i])
        self.write()

    def warn(self, text):
        self.d["warnings"].append(text)
        self.write()

    def done(self, state="done", error=""):
        self.d.update(state=state, error=error, i=len(self.d["steps"]))
        self.write()


def song_path(pdir):
    from mv.common import song_file
    return song_file(pdir)


# ------------------------------------------------------------------ commands
def cmd_new(a):
    slug = ("_" if a.name.startswith("_") else "") + slugify(a.name)      # "_name" = internal (tests), hidden
    pdir = os.path.join(PROJECTS, slug)
    if os.path.exists(pdir):
        sys.exit(f"project exists: {pdir}")
    for d in ("song", "cache", "work", "final", "review"):
        os.makedirs(os.path.join(pdir, d))
    pj = json.loads(json.dumps(DEFAULT_PROJECT))
    pj.update(title=a.title or a.name, created=time.strftime("%Y-%m-%d"))
    if getattr(a, "song", None):
        fn = os.path.basename(a.song)
        shutil.copyfile(a.song, os.path.join(pdir, "song", fn))
        pj["song"]["file"] = fn
    save_project(pdir, pj)
    print(pdir)
    return pdir


def cmd_list(a):
    os.makedirs(PROJECTS, exist_ok=True)
    for d in sorted(os.listdir(PROJECTS)):
        pdir = os.path.join(PROJECTS, d)
        if not d.startswith("_") and os.path.isfile(os.path.join(pdir, "project.json")):
            pj = load_project(pdir)
            s = pj.get("song") or {}
            print(f"{d:28s} {(pj.get('title') or ''):28s} {(s.get('title') or s.get('file') or '-'):30s} "
                  f"stage: {(pj.get('status') or {}).get('stage', '?')}")


def song_tags(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=artist,title", "-of", "json", path],
                           capture_output=True, text=True, encoding="utf-8")
        return {k.lower(): v for k, v in json.loads(r.stdout).get("format", {}).get("tags", {}).items()}
    except Exception:
        return {}


def cmd_ingest(a):
    pdir = project_dir(a.project)
    steps = ["separating the song into stems", "finding the lyrics", "beats, drums & sections", "aligning sung words",
             "mapping the song (melody, chords, structure, frame curves)"]
    st = Status(pdir, "ingest", steps)
    log = st.d["log"]
    try:
        path = song_path(pdir)
        if not path:
            raise RuntimeError("no song yet: put the MP3 in song/ (or upload it in the web app)")
        pj = load_project(pdir)
        sm = pj.setdefault("song", {})
        sm["file"] = os.path.basename(path)
        tags = song_tags(path)
        artist, title = sm.get("artist") or tags.get("artist", ""), sm.get("title") or tags.get("title", "")
        sm.update(artist=artist, title=title)
        save_project(pdir, pj)
        if a.force:
            for f in ("audio.json", "beat_this.json", "curves.npz"):
                if os.path.exists(os.path.join(pdir, "cache", f)):
                    os.remove(os.path.join(pdir, "cache", f))
        st.step(0)
        run(pdir, ["separate.py"] + (["--force"] if a.force else []) + (["--fallback"] if a.fallback else []), log)
        st.step(1)
        has_user = bool(str(pj.get("lyrics") or "").strip()) or any(
            os.path.exists(os.path.join(pdir, "song", f)) for f in ("lyrics.lrc", "lyrics.txt"))
        lyr = has_user
        if has_user:
            log.append("using your own lyrics (project.json / song/lyrics.*)")
        elif artist and title:
            try:
                run(pdir, ["fetch_lyrics.py", artist, title], log)
                sp_ = os.path.join(pdir, "cache", "lyrics", "search.json")
                lyr = any(c.get("syncedLyrics") for c in json.load(open(sp_, encoding="utf-8")))
                if not lyr:
                    log.append("LRCLIB has no synced lyrics for this song")
            except Exception as e:
                st.warn(f"lyrics lookup failed ({e}): LRCLIB (lrclib.net) unreachable?")
        else:
            log.append("no artist/title known: skipping the LRCLIB lookup (fill them in on the brief page)")
        if not lyr and not a.no_transcribe:
            log.append("no lyrics yet: transcribing the vocal stem with Whisper (check the text!)")
            lang = sm.get("language")
            rc = run(pdir, ["transcribe_lyrics.py"] + (["--language", lang] if lang else []), log, check=False)
            if rc:
                st.warn("Whisper transcription failed (model download from huggingface.co, or no vocals): no lyrics")
        st.step(2)
        run(pdir, ["analyze_audio.py"] + (["--fallback-beats"] if a.fallback else []), log)
        st.step(3)
        A = json.load(open(os.path.join(pdir, "cache", "audio.json"), encoding="utf-8"))
        if A.get("lyrics"):
            if run(pdir, ["align_words.py"], log, check=False):
                st.warn("forced alignment failed (MMS aligner weights from dl.fbaipublicfiles.com?): word times are estimates")
        else:
            log.append("no lyrics: nothing to align")
        st.step(4)
        run(pdir, ["song_kit.py"] + (["--cpu"] if a.cpu else []), log)
        rep = json.load(open(os.path.join(pdir, "song", "report.json"), encoding="utf-8"))
        for w in rep.get("warnings", []):
            st.warn(w)
        m = json.load(open(os.path.join(pdir, "song", "song-map.json"), encoding="utf-8"))
        pj = load_project(pdir)
        pj["song"] = dict(pj.get("song") or {}, file=os.path.basename(path), artist=artist, title=title)
        pj["analysis"] = {"duration": m["duration"], "bpm": m["tempo"]["bpm"], "key": m["key"]["name"],
                          "sections": len(m["sections"]), "lyrics_lines": len(m["lyrics"]),
                          "lyrics_source": (m.get("lyrics_meta") or {}).get("source"), "stems": rep["models"]["stems"],
                          "beats": rep["models"]["beats"], "warnings": len(rep.get("warnings", [])),
                          "at": time.strftime("%Y-%m-%d %H:%M")}
        pj.setdefault("status", {})["stage"] = "analysed"
        save_project(pdir, pj)
        st.done()
    except Exception as e:
        st.done("error", str(e))
        raise


def cmd_song(a):
    pdir = project_dir(a.project)
    st = Status(pdir, "song", ["mapping the song"])
    try:
        run(pdir, ["song_kit.py"] + (["--no-tags"] if a.no_tags else []) + (["--cpu"] if a.cpu else []), st.d["log"])
        st.done()
    except Exception as e:
        st.done("error", str(e))
        raise


def cmd_curves(a):
    pdir = project_dir(a.project)
    run(pdir, ["song_kit.py", "--curves-only"] + (["--fps", str(a.fps)] if a.fps else []))


def cmd_story(a):
    import director
    director.story(project_dir(a.project), model=a.model)


def cmd_direct(a):
    import director
    director.direct(project_dir(a.project), budget=a.budget, effort=a.effort, model=a.model, seed_notes=a.seed_notes,
                    revise=a.revise, nudge=not a.no_nudge, dry_run=a.dry_run)


def cmd_clock(a):
    import director
    director.clock(project_dir(a.project))


def cmd_check(a):
    pdir = project_dir(a.project)
    video = a.video or os.path.join(pdir, "final", "music-video.mp4")
    sys.exit(run(pdir, ["checks.py", "all", video] + a.extra, check=False))


def cmd_versions(a):
    import director
    for v in director.versions(project_dir(a.project)):
        print(f"v{v['n']}  {v.get('at', '')}  {v['file']}  {v.get('note', '')}")


def cmd_run(a):
    pdir = project_dir(a.project)
    sys.exit(subprocess.run([PY, "-W", "ignore", os.path.join(TOOLS, a.tool)] + a.args, cwd=STUDIO, env=env_for(pdir)).returncode)


def cmd_serve(a):
    sys.path.insert(0, os.path.join(STUDIO, "app"))
    import server
    url = f"http://127.0.0.1:{a.port}/"
    if not a.no_browser:
        webbrowser.open(url)
    server.serve(a.port)


def cmd_doctor(a):
    import doctor
    sys.exit(doctor.main())


def cmd_benchmark(a):
    sys.exit(subprocess.run([PY, "-W", "ignore", os.path.join(TOOLS, "benchmark.py")] + a.args, cwd=STUDIO,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8", MV_IN_VENV="1")).returncode)


def cmd_smoke(a):
    sys.exit(subprocess.run([PY, "-W", "ignore", os.path.join(STUDIO, "tests", "smoke_test.py")] + a.args, cwd=STUDIO,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8", MV_IN_VENV="1")).returncode)


def main():
    ap = argparse.ArgumentParser(description="Music Video Studio")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("new"); p.add_argument("name"); p.add_argument("--title"); p.add_argument("--song")
    sp.add_parser("list")
    p = sp.add_parser("ingest"); p.add_argument("project"); p.add_argument("--force", action="store_true")
    p.add_argument("--cpu", action="store_true"); p.add_argument("--fallback", action="store_true",
                                                                 help="skip the models (Demucs, beat_this): estimated stems and beats")
    p.add_argument("--no-transcribe", action="store_true", help="don't run Whisper when there are no lyrics")
    p = sp.add_parser("song"); p.add_argument("project"); p.add_argument("--no-tags", action="store_true"); p.add_argument("--cpu", action="store_true")
    p = sp.add_parser("curves"); p.add_argument("project"); p.add_argument("--fps", type=int)
    p = sp.add_parser("story"); p.add_argument("project"); p.add_argument("--model")
    p = sp.add_parser("direct"); p.add_argument("project"); p.add_argument("--budget", default="90m")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--model", default=None); p.add_argument("--seed-notes", default="")
    p.add_argument("--revise", action="store_true", help="address the review notes and make the next version")
    p.add_argument("--no-nudge", action="store_true", help="don't ask an early finisher to use the time left")
    p.add_argument("--dry-run", action="store_true", help="write the brief and print the command, don't launch Claude")
    p = sp.add_parser("clock"); p.add_argument("project")
    p = sp.add_parser("check"); p.add_argument("project"); p.add_argument("video", nargs="?"); p.add_argument("extra", nargs=argparse.REMAINDER)
    p = sp.add_parser("versions"); p.add_argument("project")
    p = sp.add_parser("run"); p.add_argument("project"); p.add_argument("tool"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sp.add_parser("serve"); p.add_argument("--port", type=int, default=8765); p.add_argument("--no-browser", action="store_true")
    sp.add_parser("doctor")
    p = sp.add_parser("benchmark"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sp.add_parser("smoke"); p.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
