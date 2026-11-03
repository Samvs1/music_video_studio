"""Music Video Studio: one entry point for every command.

  python tools/studio.py new <name> [--song file.mp3] [--title "..."]   create projects/<name>/ (song/, work/, final/, ...)
  python tools/studio.py list                                        list projects
  python tools/studio.py ingest <project> [--force] [--cpu]          song -> stems, beats, lyrics, the song folder (progress in cache/status.json)
  python tools/studio.py song <project> [--cpu] [--no-tags]          remake the song folder only (tools/song_kit.py)
  python tools/studio.py curves <project> [--fps N]                  remake song/frames.json + hits.json (e.g. after changing the fps)
  python tools/studio.py story <project> [--model M]                 Claude drafts song/song-story.md (headless, read-only)
  python tools/studio.py align-lyrics <project> [--in lines.json]    the lyric lines placed where Whisper hears them
                                                                     (cache/lyrics/aligned.json; the web app's timing editor)
  python tools/studio.py direct <project> --budget 90m [--model opus] [--effort max] [--seed-notes "..."] [--revise]
                         [--ultracode] [--max-usd N] [--critic-model sonnet|haiku|opus|off] [--critic-effort medium]
                         [--critic-max 6] [--pitch round-K/pN] [--continue] [--auto-continue | --no-auto-continue]
                                                                     the autonomous run (Claude Code headless) -> final/music-video.mp4:
                                                                     one director with full reign (agents if it wants them;
                                                                     --ultracode: multi-agent workflows too; --max-usd: a cap)
                                                                     and the critic on its previews and the final
                                                                     (--pitch: it starts from that pitch, put in place first;
                                                                     --continue: carries on the last run in its own conversation,
                                                                     with what it had left; --auto-continue: when Claude's usage
                                                                     limit stops it, it waits for the reset and carries on)
  python tools/studio.py pitch <project> [--n 3] [--budget 20m] [--model M] [--effort high] [--critic-model sonnet]
                         [--critic-effort E] [--proof "chorus 1" | --proof 24-44] [--angles story,wild,...]
                         [--seed-notes "..."] [--max-usd N] [--dry-run]
                         [--auto [--direct-budget 90m] [--direct-effort max] [--direct-model M] [--direct-max-usd N]
                          [--ultracode]]
                                                                     the pitch round, optional (tools/pitch.py): several directors
                                                                     pitch at once from different angles, each proving its idea on
                                                                     the same window of the song; the critic on each proof, one
                                                                     verdict (ambition first); then direct --pitch round-K/pN
                                                                     builds the one you pick (--auto: the verdict's, at once)
  python tools/studio.py pitch-pick <project> round-K/pN [--dry-run]  put a picked pitch in place for an interactive
                                                                     director (/musicvideo), as direct --pitch does first,
                                                                     and print what's where, the critic's notes, the grafts
  python tools/studio.py critique <project> <video | vN> [--model sonnet] [--effort E] [--focus "..."] [--max-usd 2]
                         [--pictures a.png] [--concept PATH] [--out DIR] [--no-look]
                                                                     the critic: a cheaper model with fresh eyes looks at a video
                                                                     and reports back (work/critic/; vN also final/critic_vN.md)
  python tools/studio.py plan validate|status|render|assemble <project> [...]
                                                                     the plan tools (tools/plan.py), optional: a scene list in
                                                                     work/plan.json rendered segment by segment (cached), the
                                                                     previews and the final assembled in one command
  python tools/studio.py clock <project>                             time left in the running director's budget
  python tools/studio.py check <project> [video]                     every check on the final (spec, flashes, text, sync, audio)
  python tools/studio.py versions <project>                          the delivered versions (final/music-video_vN.mp4)
  python tools/studio.py deliver <project> [--critic [--critic-model M]]
                                                                     archive final/music-video.mp4 as the next version + checks
                                                                     (--critic: and the critic's notes on it)
  python tools/studio.py stop <project>                              ask a running autonomous director (or pitch round) to stop cleanly
  python tools/studio.py note-done <project> <id> ["what changed"] [--undo]
                                                                     mark the user's note done with a one-line fix
                                                                     (review/notes.json, safely: the web app writes it too)
  python tools/studio.py permtest <project> [--pitch] [--model haiku]
                                                                     check the director's sandbox (and an agent's; --pitch: a pitch's)
                                                                     with the real Claude Code (cents)
  python tools/studio.py run <project> <tool.py> [args]              run any tool inside a project (eyes.py, songq.py, compose.py, ...)
  python tools/studio.py serve [--port 8765] [--no-browser]          the local web app
  python tools/studio.py doctor                                      check the install and say how to fix what's missing
  python tools/studio.py benchmark [--quick]                         measure render speeds -> tools/speeds.json
  python tools/studio.py smoke                                       end-to-end smoke test on a synthetic song (tests/smoke_test.py)

Each command's --help lists all its options. <project> is a folder name under projects/ (or a path). Every step runs
the tools with MV_PROJECT set, so they read and write inside that project. Started with another Python (e.g. plain
`python`), it re-runs itself with the studio's .venv, which has the libraries. Projects whose folder starts with "_"
are internal (smoke tests) and hidden.
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
    "refs": [],             # optional: reference pictures in refs/ (who's in the song): [{id, file, name, note, added}]
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
    """cache/status.json, polled by the web app. Bookkeeping: a write that fails (on Windows, the file held open by a
    read a little too long) is said on stderr and never stops the job (a pitch round, a run's archive)."""

    def __init__(self, pdir, job, steps):
        self.path = os.path.join(pdir, "cache", "status.json")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.d = {"job": job, "steps": steps, "i": 0, "step": steps[0], "state": "running", "log": [],
                  "warnings": [], "started": time.time(), "updated": time.time(), "error": ""}
        self.write()

    def write(self):
        from mv.common import jsave              # (the writer's own temporary file; tried again while it's being read)
        self.d["updated"] = time.time()
        try:
            jsave(self.path, self.d, indent=None)
        except OSError as ex:
            print(f"(cache/status.json couldn't be written: {ex})", file=sys.stderr, flush=True)

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


def user_lyrics_kind(pdir, pj):
    """'lrc' (line times given), 'plain' (text only), 'partly' (timed in the web app's editor, but not every line set
    by hand: Whisper places the others) or None: the user's own lyrics, as analyze_audio.py reads them."""
    import re
    txt = pj.get("lyrics")
    txt = (txt.get("text") if isinstance(txt, dict) else txt) or ""
    for fn in ("lyrics.lrc", "lyrics.txt"):
        if not txt.strip() and os.path.exists(os.path.join(pdir, "song", fn)):
            txt = open(os.path.join(pdir, "song", fn), encoding="utf-8").read()
    if not txt.strip():
        return None
    stamps = re.findall(r"\[\d+:\d+(?:\.\d+)?\]", txt)
    if len(stamps) < 3:
        return "plain"
    return "partly" if pj.get("lyrics_synced") and len(pj.get("lyrics_set") or []) < len(stamps) else "lrc"


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
        kind = user_lyrics_kind(pdir, pj)
        lyr = has_user = kind is not None
        if has_user:
            log.append("using your own lyrics (project.json / song/lyrics.*)")
            if kind in ("plain", "partly") and not a.no_transcribe:   # lines without a time of the user's: Whisper places them
                log.append("your lyrics have no times: Whisper listens to the vocals to place them" if kind == "plain" else
                           "some lyric lines were never set by hand: Whisper listens to the vocals to place them")
                lang = sm.get("language")
                if run(pdir, ["transcribe_lyrics.py"] + (["--language", lang] if lang else []) + (["--force"] if a.force else []),
                       log, check=False):
                    st.warn("Whisper couldn't run (model download from huggingface.co?): your lyric lines are placed by "
                            "estimate; fix them on The song page (Fix the timing)")
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


def cmd_align_lyrics(a):
    pdir = project_dir(a.project)
    st = Status(pdir, "align", ["Whisper listens to the vocals", "matching your lines to what it heard"])
    try:
        lang = (load_project(pdir).get("song") or {}).get("language")
        if run(pdir, ["transcribe_lyrics.py"] + (["--language", lang] if lang else []), st.d["log"], check=False):
            raise RuntimeError("Whisper couldn't run (the model downloads from huggingface.co on first use): see the log")
        st.step(1)
        run(pdir, ["align_lyrics.py"] + (["--in", a.inp] if a.inp else []), st.d["log"])
        st.done()
    except Exception as e:
        st.done("error", str(e))
        raise


def cmd_direct(a):
    import director
    director.direct(project_dir(a.project), budget=a.budget, effort=a.effort, model=a.model, seed_notes=a.seed_notes,
                    revise=a.revise, nudge=not a.no_nudge, dry_run=a.dry_run, critic_model=a.critic_model,
                    critic_effort=a.critic_effort, critic_max=a.critic_max, ultracode=a.ultracode, max_usd=a.max_usd,
                    pitch=a.pitch, auto_continue=a.auto_continue, cont=a.cont)


def cmd_pitch(a):
    import pitch
    sys.exit(pitch.run_args(project_dir(a.project), a))


def cmd_critique(a):
    import re
    import director, pitch
    pdir = project_dir(a.project)
    # a director run or a pitch round owns cache/status.json while it works (it, or one of its agents, may run this
    # itself): the web app's job shows that one then
    busy = director.run_active(pdir) or pitch.round_active(pdir)
    st = None if busy else Status(pdir, "critique", [f"the critic looks at {a.video}"])
    extra = (["--focus", a.focus] if a.focus else []) + (["--max-usd", str(a.max_usd)] if a.max_usd else []) + \
        (["--no-look"] if a.no_look else []) + (["--concept", a.concept] if a.concept else []) + \
        (["--pictures", a.pictures] if a.pictures else [])
    try:
        if re.fullmatch(r"v\d+", a.video):                    # a delivered version: also final/critic_vN.md
            v = next((x for x in director.versions(pdir) if f"v{x['n']}" == a.video), None)
            if not v:
                raise RuntimeError(f"no version {a.video} (studio.py versions {a.project})")
            ok = director.critique_version(pdir, v, a.model, a.effort, force=True, extra=extra)[0] is not None
        else:
            for flag, x in (("--model", a.model), ("--effort", a.effort), ("--out", a.out)):
                if x:
                    extra += [flag, x]
            ok = run(pdir, ["critic.py", a.video] + extra, check=False) == 0
    except Exception as e:
        if st:
            st.done("error", str(e))
        sys.exit(f"critique: {e}")
    if st:
        st.done("done" if ok else "error", "" if ok else "the critic couldn't run: see the log")
    sys.exit(0 if ok else 1)


def cmd_plan(a):
    import plan
    sys.exit(plan.main([a.sub] + a.args, project_dir(a.project)))


def cmd_clock(a):
    import director
    director.clock(project_dir(a.project))


def cmd_check(a):
    pdir = project_dir(a.project)
    video = a.video or os.path.join(pdir, "final", "music-video.mp4")
    sys.exit(run(pdir, ["checks.py", "all", video] + a.extra, check=False))


def cmd_deliver(a):
    import director
    pdir = project_dir(a.project)
    v = director.deliver(pdir)
    if v and a.critic:
        director.critique_version(pdir, v, a.critic_model)


def cmd_stop(a):
    import director
    director.request_stop(project_dir(a.project))
    print("asked the running director (or pitch round) to stop (it wraps up within a minute or two)")


def cmd_note_done(a):
    from mv.live import note_done
    n = note_done(project_dir(a.project), a.id, a.fix, a.undo)
    if not n:
        sys.exit(f"no note with id {a.id} in review/notes.json")
    t = float(n.get("t") or 0)
    print(f"note {n.get('id')} at {int(t // 60)}:{t % 60:04.1f} ({str(n.get('text') or '')[:60]}): "
          + ("open again" if a.undo else "done" + (f": {n['fix']}" if n.get("fix") else "")))


def cmd_permtest(a):
    import director
    if a.pitch:
        import pitch
        sys.exit(0 if pitch.permission_test(project_dir(a.project), model=a.model) else 1)
    sys.exit(0 if director.permission_test(project_dir(a.project), model=a.model) else 1)


def cmd_pitch_pick(a):
    """A picked pitch put in place for an interactive director (/musicvideo): pitch.promote, as `direct --pitch` does
    before an autonomous run, then what that run's brief would say about it (what's where, the critic on its proof, the
    verdict's ideas worth grafting)."""
    import director, pitch
    pdir = project_dir(a.project)
    if director.run_active(pdir) or pitch.round_active(pdir):
        sys.exit("a director run or a pitch round is going on in this project: let it finish (or `studio.py stop`) first")
    info = pitch.promote(pdir, a.ref, by="the user", dry=a.dry_run)
    print(pitch.brief_section(info).strip())


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
    p = sp.add_parser("align-lyrics"); p.add_argument("project"); p.add_argument("--in", dest="inp")
    efforts = ["low", "medium", "high", "xhigh", "max"]
    p = sp.add_parser("direct"); p.add_argument("project")
    p.add_argument("--budget", default=None, help="the time budget, e.g. 90m, 1h30m (default 90m; --continue: what the last run had left)")
    p.add_argument("--effort", default=None, choices=efforts, help="the director's (default max)")
    p.add_argument("--model", default=None, help="the director's (default: project.json director.model, else opus)")
    p.add_argument("--ultracode", action="store_true",
                   help="the director may orchestrate multi-agent workflows (the Workflow tool): more tokens")
    p.add_argument("--max-usd", type=float, help="a hard cap on the director's session, its agents included (--max-budget-usd)")
    p.add_argument("--seed-notes", default="")
    p.add_argument("--revise", action="store_true", help="address the review notes and make the next version")
    p.add_argument("--no-nudge", action="store_true", help="don't ask an early finisher to use the time left")
    p.add_argument("--dry-run", action="store_true", help="write the brief and print the command, don't launch Claude")
    p.add_argument("--critic-model", help="the critic's model (default: project.json director.critic_model, else sonnet); off = no critic")
    p.add_argument("--critic-effort", help="the critic's effort (default medium)")
    p.add_argument("--critic-max", type=int, default=6, help="automatic critiques per run (default 6)")
    p.add_argument("--pitch", help="start from a pitch of a pitch round: round-K/pN (studio.py pitch)")
    p.add_argument("--continue", dest="cont", action="store_true",
                   help="carry on the last run (cut off by Claude's usage limit, stopped, ...): its own conversation "
                        "(claude --resume), the budget it had left unless --budget says; it waits for a limit still to reset")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--auto-continue", dest="auto_continue", action="store_const", const=True, default=None,
                   help="when Claude's usage limit stops the director, wait for the reset (the clock stopped) and carry on "
                        "(default: the Direct page's choice, project.json director.auto_continue)")
    g.add_argument("--no-auto-continue", dest="auto_continue", action="store_const", const=False,
                   help="when Claude's usage limit stops the director, end the run there (continue it later)")
    p = sp.add_parser("pitch", help="the pitch round (tools/pitch.py)"); p.add_argument("project")
    import pitch as pitch_
    pitch_.add_args(p)
    p = sp.add_parser("pitch-pick", help="put a pitch in place for an interactive director (what direct --pitch does first)")
    p.add_argument("project"); p.add_argument("ref", help="round-K/pN")
    p.add_argument("--dry-run", action="store_true", help="only say what it would put where")
    p = sp.add_parser("critique"); p.add_argument("project"); p.add_argument("video", help="a video in the project, or vN")
    p.add_argument("--model"); p.add_argument("--effort"); p.add_argument("--focus", default="")
    p.add_argument("--concept"); p.add_argument("--out"); p.add_argument("--max-usd", type=float)
    p.add_argument("--no-look", action="store_true"); p.add_argument("--pictures", help="more stills to open, comma-separated")
    p = sp.add_parser("plan"); p.add_argument("sub", choices=["validate", "status", "render", "assemble"])
    p.add_argument("project"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sp.add_parser("clock"); p.add_argument("project")
    p = sp.add_parser("check"); p.add_argument("project"); p.add_argument("video", nargs="?"); p.add_argument("extra", nargs=argparse.REMAINDER)
    p = sp.add_parser("versions"); p.add_argument("project")
    p = sp.add_parser("deliver"); p.add_argument("project"); p.add_argument("--critic", action="store_true")
    p.add_argument("--critic-model")
    p = sp.add_parser("permtest"); p.add_argument("project"); p.add_argument("--model", default="haiku")
    p.add_argument("--pitch", action="store_true", help="a pitch's sandbox (its own folder writable, the rest refused)")
    p = sp.add_parser("stop"); p.add_argument("project")
    p = sp.add_parser("note-done"); p.add_argument("project"); p.add_argument("id"); p.add_argument("fix", nargs="?", default="")
    p.add_argument("--undo", action="store_true", help="open the note again")
    p = sp.add_parser("run"); p.add_argument("project"); p.add_argument("tool"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sp.add_parser("serve"); p.add_argument("--port", type=int, default=8765); p.add_argument("--no-browser", action="store_true")
    sp.add_parser("doctor")
    p = sp.add_parser("benchmark"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sp.add_parser("smoke"); p.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
