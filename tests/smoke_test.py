"""End-to-end smoke test on a synthetic song (nothing copyrighted, nothing to download but the models).

  python tests/smoke_test.py [--keep] [--size 640x360] [--fps 30] [--no-director] [--no-blender]
  (python tools/studio.py smoke ... does the same)

 1. which model downloads this machine can reach (each one that can't is reported as SKIP with its domain, and the
    pipeline uses its offline fallback instead)
 2. a synthetic song: tones, a drum pattern with a tempo change (120 -> 140 BPM) and a stop, a synthetic voice with a
    word-timed lyric (tests/make_test_song.py; the truth file says where everything is)
 3. ingest -> the song folder, checked against the truth: beats, downbeats, the stop, the tempo change, the final hit,
    key, sections, lyrics, frames.json / hits.json
 4. a scripted mini director (tests/mini_director.py): web engine (Canvas 2D + a transparent DOM type pass) and Blender
    (Workbench) -> compose (concat, layer, mux)
 5. every check on the final MP4 must pass: spec (incl. the audio identical to the song), flashes, text, sync
 6. deliver (v1), the eyes tools, songq; the critic (stand-in Claude): a critique of the final (files, parsed scores, a
    prompt with the pictures, the craft rules and the user's avoid but not the director's report), deliver --critic
    reusing it as final/critic_v1.md, an old Claude Code without --max-budget-usd, the lenient parser, the watcher
    (what it critiques, and not a cut that has or is getting a critique); the plan tools (tools/plan.py): validate
    good, old and bad plans, render the segments (and again from the cache), the cache key following a lib import,
    assemble a preview (its cut marked pending while the critic looks), the render queue, eyes next to a segment;
    Blender scenes importing shared code (work/scenes/lib/, work/kit/, a pitch's own scenes/lib/) and rendering again
    when it changes
 6d. the session plumbing (messages sent while a turn runs join it: one result, as on the real CLI; a background agent
    keeps the session working, then it starts a turn of its own; the costs as running totals; the cost cap) and the
    runner in-process (no reminder, nudge or closing while agents work; live notes; the cap; the run settings)
 6e3. Claude's usage limit, in-process: read from Claude Code's own messages (the reset in its many forms; not a
    throttle, the director's words or the cost cap); a session with the stand-in counts each refused turn once, its
    reset from the rate_limit_event, nothing spent; a --resume Claude Code doesn't have; the runner on a simulated
    clock (auto-continue: it waits with the clock stopped and carries on, resumes a session that ended or starts a
    fresh one told where the work is; without it, or a reset too far: the run ends with what was left; stopped while
    it waits); continuable and wait_for_reset
 6g. the pitch round's proof window, in-process (the studio's pick; a section; a-b in seconds or m:ss; what the song
    can't have refused with why; the Pitch round page's preview; a dry-run round on the user's section and its brief)
 7. the autonomous director runner end to end with a stand-in for Claude Code (tests/fake_claude.py: no usage spent;
    the critic's notes reach the director as a message and the new version gets final/critic_vN.md; the cost isn't
    double-counted), then a review round (a note -> --revise -> the next version, the note marked done); then a
    director that deploys an agent and uses the plan tools, with --ultracode and --max-usd (the runner waits for the
    agent, the agent in status.json and the live log, the costs per model, the critic once per cut, a live note), and
    `direct --dry-run` (the defaults: effort max, the agent tools, no Workflow, no cap); then a run Claude's usage
    limit stops (the stand-in refuses its brief, no auto-continue: it ends at once with what was left) and
    `direct --continue` (its own conversation through --resume, the studio's "carry on" first, the budget it had left)
 7c. the pitch round with the stand-in (tests/fake_claude.py's pitch and verdict roles): `pitch --n 2` (the proof
    window, each pitch's brief and sandbox: the deny rules keep it out of the other's folder and the project's files;
    proof_av.mp4 with the song's audio at the window; the critic on each proof; the verdict; the costs from the
    sessions' running totals), then `direct --pitch round-K/p2` (the promotion: the concept, the scenes with their lib,
    the style frames, the draft plan with its paths moved; backups; the brief's "go further" section; delivered), the
    promotion into an empty work/ and the same pitch picked again (nothing the director changed overwritten), a draft
    plan playing the pitch's own scenes live, and `pitch --auto` (the verdict's winner built in the same job)
 7d. a pitch's turns on a simulated clock (the reminders; the push to go further only for a delivered pitch; a session
    still running after its input closed stopped 20 s after the deadline plus the grace, and after a stop), the
    verdict's json read leniently, a pick kept by the round's next save, a dry-run round not hiding a running one
 7e. a round that goes wrong, in-process (a pitch that keeps running, one with only its pitch.md, one with nothing; the
    verdict without its json: the critic's scores rank them; round.json fresh while the verdict works; a stop during
    the verdict ends its call)
The project is projects/_smoke (internal, git-ignored); --keep leaves it for a look. Exit code 0 = everything passed.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time, urllib.request

TESTS = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(TESTS)
PY = sys.executable
P = "_smoke"
PDIR = os.path.join(STUDIO, "projects", P)
RESULTS = []


def say(verdict, what, detail=""):
    RESULTS.append((verdict, what, detail))
    print(f"[{verdict}] {what}" + (f": {detail}" if detail else ""), flush=True)


def studio(*args, check=True, env=None, timeout=3600):
    r = subprocess.run([PY, os.path.join(STUDIO, "tools", "studio.py"), *args], cwd=STUDIO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {})),
                       timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"studio.py {' '.join(args)} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    return r


MODEL_HOSTS = [
    ("Demucs weights (real stems)", "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726a21a.th", "dl.fbaipublicfiles.com"),
    ("beat_this checkpoint (neural beats)", "https://cloud.cp.jku.at/", "cloud.cp.jku.at"),
    ("MMS aligner (word alignment)", "https://dl.fbaipublicfiles.com/mms/torchaudio/ctc_alignment_mms/model.pt", "dl.fbaipublicfiles.com"),
    ("LRCLIB (synced lyrics)", "https://lrclib.net/api/search?q=test", "lrclib.net"),
    ("Hugging Face (Whisper, CLAP)", "https://huggingface.co/api/models/openai/whisper-tiny", "huggingface.co"),
]


def reachable(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "music-video-studio-smoke"})
        urllib.request.urlopen(req, timeout=12)
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500 and e.code not in (403, 407)
    except Exception:
        return False


def near(xs, t, tol):
    return any(abs(x - t) <= tol for x in xs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--size", default="640x360")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--no-director", action="store_true")
    ap.add_argument("--no-blender", action="store_true")
    a = ap.parse_args()
    t_start = time.time()
    W, H = [int(x) for x in a.size.lower().split("x")]

    print("== 1. model downloads", flush=True)
    blocked = {}
    for name, url, host in MODEL_HOSTS:
        ok = reachable(url)
        if ok:
            say("PASS", f"{name}: {host} reachable")
        else:
            blocked[name] = host
            say("SKIP", f"{name}: {host} unreachable from this machine", "the pipeline uses its offline fallback instead")

    print("== 2. synthetic song", flush=True)
    out = os.path.join(TESTS, "out")
    r = subprocess.run([PY, os.path.join(TESTS, "make_test_song.py"), out], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        sys.exit(1)
    truth = json.load(open(os.path.join(out, "test_song_truth.json")))
    say("PASS", "test song generated", r.stdout.strip().split("  ")[-1])

    print("== 3. ingest", flush=True)
    shutil.rmtree(PDIR, ignore_errors=True)
    studio("new", P, "--song", os.path.join(out, "test_song.mp3"), "--title", "Smoke test")
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    pj["format"] = {"preset": "custom", "width": W, "height": H, "fps": a.fps}
    pj["lyrics"] = open(os.path.join(out, "test_song_words.lrc"), encoding="utf-8").read()
    pj["about"] = "Holding on to a light while the night changes speed."
    pj["avoid"] = "spectrum bars"
    json.dump(pj, open(os.path.join(PDIR, "project.json"), "w", encoding="utf-8"), indent=1)
    t0 = time.time()
    r = studio("ingest", P, "--no-transcribe", check=False)
    st = json.load(open(os.path.join(PDIR, "cache", "status.json"), encoding="utf-8"))
    if r.returncode or st.get("state") != "done":
        say("FAIL", "ingest", (r.stdout + r.stderr)[-2000:])
        return finish(a, t_start)
    say("PASS", f"ingest finished in {time.time() - t0:.0f} s")
    rep = json.load(open(os.path.join(PDIR, "song", "report.json"), encoding="utf-8"))
    stems, beats_m = rep["models"]["stems"], rep["models"]["beats"]
    say("PASS" if not stems.startswith("fallback") else "SKIP", "stems with Demucs" if not stems.startswith("fallback") else
        f"Demucs separation (weights from {blocked.get('Demucs weights (real stems)', 'the network')} unavailable)",
        f"used: {stems}")
    say("PASS" if "beat_this" in beats_m else "SKIP", "beats with beat_this" if "beat_this" in beats_m else
        f"beat_this tracking (checkpoint from {blocked.get('beat_this checkpoint (neural beats)', 'the network')} unavailable)",
        f"used: {beats_m}")
    say("PASS" if rep["models"].get("pitch") else "FAIL", "pitch tracking (CREPE, offline weights)", str(rep["models"].get("pitch")))

    print("== 3b. the song folder against the truth", flush=True)
    sd = os.path.join(PDIR, "song")
    need = ["README.md", "song-map.md", "song-map.json", "lyrics.md", "song-story.md", "overview.png", "envelopes.json",
            "report.json", "frames.json", "hits.json", "stems/vocals.wav", "stems/drums.wav", "stems/bass.wav", "stems/other.wav"]
    missing = [f for f in need if not os.path.exists(os.path.join(sd, f))]
    say("FAIL" if missing else "PASS", "song folder complete", ", ".join(missing) if missing else f"{len(need)} files + pages/")
    pages = os.listdir(os.path.join(sd, "pages")) if os.path.isdir(os.path.join(sd, "pages")) else []
    say("PASS" if pages else "FAIL", "pages/ (16 bars per picture)", f"{len(pages)} pictures")
    m = json.load(open(os.path.join(sd, "song-map.json"), encoding="utf-8"))
    fb = "fallback" in beats_m
    tb, fbs = truth["beats"], m["beats"]
    rec = sum(near(fbs, t, 0.07) for t in tb) / len(tb)
    say("PASS" if rec >= (0.9 if fb else 0.95) else "FAIL", "beats", f"{rec * 100:.0f}% of the {len(tb)} true beats found within 70 ms")
    db = [b["t"] for b in m["bars"]]
    drec = sum(near(db, t, 0.07) for t in truth["downbeats"]) / len(truth["downbeats"])
    say("PASS" if drec >= 0.85 else "FAIL", "downbeats (bar 0 and every bar)", f"{drec * 100:.0f}% of {len(truth['downbeats'])} found")
    kinds = lambda k: [x["t"] for x in m["moments"] if k in x["kind"]]   # noqa: E731
    say("PASS" if near(kinds("stop"), truth["stop"]["t0"], 1.0) else "FAIL", "the stop is a ★★★ moment",
        f"truth {truth['stop']['t0']:.2f} s, found {[round(x, 2) for x in kinds('stop')]}")
    say("PASS" if near(kinds("tempo change"), truth["tempo_change"], 2.6) else "FAIL", "the tempo change (120 -> 140 BPM)",
        f"truth {truth['tempo_change']:.2f} s, found {[round(x, 2) for x in kinds('tempo change')]}")
    say("PASS" if near(kinds("ending"), truth["final_hit"], 0.5) else "FAIL", "the final hit",
        f"truth {truth['final_hit']:.2f} s, found {[round(x, 2) for x in kinds('ending')]}")
    say("PASS" if m["key"]["name"] in ("C major", "A minor") else "FAIL", "key", m["key"]["name"])
    say("PASS" if len(m["sections"]) >= 4 else "FAIL", "sections", " → ".join(s["name"] for s in m["sections"]))
    nw = sum(len(x["words"]) for x in m["lyrics"])
    wt = [w["t"] for x in m["lyrics"] for w in x["words"]]
    wok = sum(near(wt, w["t"], 0.02) for w in truth["words"]) / len(truth["words"])
    say("PASS" if len(m["lyrics"]) == len(truth["lines"]) and wok > 0.95 else "FAIL", "lyrics (word-timed LRC)",
        f"{len(m['lyrics'])} lines, {nw} words, {wok * 100:.0f}% at their true times")
    fr = json.load(open(os.path.join(sd, "frames.json")))
    n_expect = round(truth["duration"] * a.fps)
    cn = sorted(fr["curves"])
    say("PASS" if abs(fr["frames"] - n_expect) <= 1 and fr["fps"] == a.fps and all(len(v) == fr["frames"] for v in fr["curves"].values())
        else "FAIL", "frames.json", f"{fr['frames']} frames at {fr['fps']} fps (expected ~{n_expect}); curves: {', '.join(cn)}")
    hits = json.load(open(os.path.join(sd, "hits.json")))
    counts = {k: len(hits.get(k) or []) for k in ("beats", "downbeats", "kick", "snare", "hat", "bass", "vocal", "words")}
    say("PASS" if all(counts[k] > 0 for k in counts) else "FAIL", "hits.json", ", ".join(f"{k} {v}" for k, v in counts.items()))
    sig = rep.get("signals") or {}
    say("PASS" if len(sig) >= 15 and all("confidence" in v for v in sig.values()) else "FAIL", "report.json signals + confidence",
        f"{len(sig)} signals")
    r = studio("run", P, "songq.py", "at", str(truth["stop"]["t0"] + 0.5))
    say("PASS" if "bar" in r.stdout else "FAIL", "songq.py at the stop", r.stdout.strip()[:140])

    print("== 3c. plain lyrics placed on a transcription (align_lyrics.anchor; a stand-in for Whisper)", flush=True)
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    from align_lyrics import anchor
    heard = [dict(w, end=w["t"] + 0.3) for w in truth["words"]]
    heard[3]["w"], heard[10]["w"] = "fallin", "lites"                 # misheard
    del heard[20]                                                     # missed
    heard.insert(15, {"w": "yeah", "t": heard[14]["t"] + 0.6, "end": heard[14]["t"] + 0.9})    # an ad-lib
    texts = [x["text"] for x in truth["lines"]]
    res = anchor(texts[:6] + texts[7:], heard)                        # the second "hold the light" written once only
    err = max([abs(a["t"] - b["t"]) for a, b in zip(res["lines"], truth["lines"])] or [99])
    say("PASS" if [x["text"] for x in res["lines"]] == texts and err < 0.15 and res["stats"]["repeats_added"] == 1 else "FAIL",
        "lyric lines placed on heard words (mishearings, a missed word, an ad-lib, a repeat written once)",
        f"worst start {err:.2f} s off; {res['stats']}")
    res = anchor(texts[:8] + texts[9:], heard)                        # a sung line left out of the lyrics
    say("PASS" if [h["text"] for h in res["heard_only"]] == ["run run run"] and len(res["lines"]) == len(texts) - 1 else "FAIL",
        "a sung line missing from the lyrics is listed (to add)", str(res["heard_only"]))
    stuck = [{"w": w, "t": 12.25, "end": 12.27} for _ in range(10) for w in ("every", "window", "glows")]   # Whisper looping
    res = anchor(texts[:8] + texts[9:], sorted(heard + stuck, key=lambda w: w["t"]))
    say("PASS" if [h["text"] for h in res["heard_only"]] == ["run run run"] and res["stats"]["repeats_added"] == 0 else "FAIL",
        "Whisper stuck in a loop adds no copies of a line", f"{res['stats']['repeats_added']} repeats, heard only {res['heard_only']}")

    structure_check()

    print("== 4. mini director: web engine + Blender -> compose", flush=True)
    sys.path.insert(0, TESTS)
    import mini_director
    t0 = time.time()
    try:
        final = mini_director.run(PDIR, blender=not a.no_blender)
        say("PASS", f"rendered and composed in {time.time() - t0:.0f} s", os.path.relpath(final, STUDIO))
    except Exception as ex:
        say("FAIL", "mini director", str(ex)[-2500:])
        return finish(a, t_start)
    side = json.load(open(os.path.join(PDIR, "work", "renders", "field.mp4.mv.json" if a.no_blender else "field_a.mp4.mv.json")))
    say("PASS", "web engine browser", f"{side['browser']['browser']} {side['browser']['version']}, WebGL: {side['browser']['webgl']}")

    print("== 5. checks on the final", flush=True)
    r = studio("check", P, check=False)
    rep = json.load(open(os.path.join(PDIR, "work", "checks", "report.json"), encoding="utf-8"))
    for k, v in rep["verdicts"].items():
        say("PASS" if v == "PASS" else "FAIL", f"check: {k}", v + (f" ({rep['sync'].get('in_sync')}/{rep['sync'].get('events')} events in sync)"
                                                                 if k == "sync" else ""))
    if r.returncode:
        print(open(os.path.join(PDIR, "work", "checks", "report.md"), encoding="utf-8").read())

    print("== 6. deliver, eyes", flush=True)
    studio("deliver", P)
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    vs = pj.get("versions") or []
    say("PASS" if vs and os.path.exists(os.path.join(PDIR, vs[-1]["file"])) else "FAIL", "deliver -> versions",
        ", ".join(f"v{v['n']} {v['file']}" for v in vs))
    r = studio("run", P, "eyes.py", "look", "final/music-video.mp4", check=False)
    idx = os.path.join(PDIR, "work", "eyes", "music-video", "index.md")
    pics = [f for f in os.listdir(os.path.dirname(idx)) if f.endswith(".png")] if os.path.exists(idx) else []
    say("PASS" if r.returncode == 0 and os.path.exists(idx) and len(pics) >= 4 else "FAIL", "eyes.py look",
        f"{len(pics)} pictures + index.md" if pics else (r.stdout + r.stderr)[-600:])
    stop_t = truth["stop"]["t0"]
    for args in (["strip", "final/music-video.mp4", "--at", f"{stop_t:.2f}"], ["beats", "final/music-video.mp4", "--section", m["sections"][1]["name"]]):
        r = studio("run", P, "eyes.py", *args, check=False)
        say("PASS" if r.returncode == 0 and ".png" in r.stdout else "FAIL", f"eyes.py {args[0]}", r.stdout.strip().splitlines()[-1][-90:] if r.stdout.strip() else r.stderr[-300:])

    critic_check(m)
    plan_check(m)
    blender_shared_check(a.no_blender)
    live_check(m)
    session_check()
    runner_check()
    limit_check()
    proof_window_check()

    if not a.no_director:
        print("== 7. the director runner (stand-in for Claude Code)", flush=True)
        env = {"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")}
        r = studio("direct", P, "--budget", "6m", "--effort", "low", "--model", "fake", "--critic-model", "fake", check=False,
                   env=env, timeout=1800)
        runs = (json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8")).get("runs") or [])
        last = runs[-1] if runs else {}
        rep_md = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
        say("PASS" if r.returncode == 0 and last.get("version") and "Run settings (written by the studio)" in rep_md else "FAIL",
            "studio.py direct (stand-in Claude)", f"run {last.get('run')}: version v{last.get('version')}, wall {last.get('wall_s')} s, "
                                                  f"ended: {last.get('stop_reason')}" if last else (r.stdout + r.stderr)[-1500:])
        cur = json.load(open(os.path.join(PDIR, "work", "director", "current.json"), encoding="utf-8"))
        rs = json.load(open(os.path.join(PDIR, cur["dir"], "status.json"), encoding="utf-8"))
        sent = [x["why"] for x in rs.get("messages_sent", [])]
        say("PASS" if len(rs.get("events", [])) >= 3 and "the brief" in sent else "FAIL", "live progress (status.json events)",
            f"{len(rs.get('events', []))} events; studio messages: {', '.join(sent)}")
        res = [json.loads(x) for x in open(os.path.join(PDIR, cur["dir"], "events.jsonl"), encoding="utf-8")
               if x.strip() and '"result"' in x]
        res = [x for x in res if x.get("type") == "result"]
        say("PASS" if res and rs.get("cost_usd") == res[-1]["total_cost_usd"] and rs.get("turns") == sum(x["num_turns"] for x in res)
            and (len(res) == 1 or rs["cost_usd"] < round(sum(x["total_cost_usd"] for x in res), 4)) else "FAIL",
            "the run's cost is the session's latest running total (not the sum of its results); its turns add up",
            f"cost {rs.get('cost_usd')}; the results' totals {[x['total_cost_usd'] for x in res]}; turns {rs.get('turns')}")
        vn = last.get("version")
        pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
        vc = next((v.get("critic") for v in pj.get("versions") or [] if v.get("n") == vn), None) or {}
        said = [x for x in sent if x.startswith("critic:") or "critic's notes" in x]
        say("PASS" if rs.get("critiques") and said and vc.get("scores") and os.path.exists(os.path.join(PDIR, "final", f"critic_v{vn}.md"))
            and "- Critic: fake" in rep_md else "FAIL", "the critic during the run: its notes reach the director, the version keeps them",
            f"{len(rs.get('critiques') or [])} critique(s) {[c['video'] for c in rs.get('critiques') or []]}; messages {said}; "
            f"final/critic_v{vn}.md: {vc.get('scores')}; critic cost ${rs.get('critic_cost_usd')}")
        # a review round
        notes = [{"id": 1, "version": f"v{last.get('version')}", "t": round(stop_t + 0.3, 2), "text": "hold the stop longer",
                  "done": False, "at": time.strftime("%Y-%m-%d %H:%M")}]
        os.makedirs(os.path.join(PDIR, "review"), exist_ok=True)
        json.dump(notes, open(os.path.join(PDIR, "review", "notes.json"), "w", encoding="utf-8"))
        r = studio("direct", P, "--revise", "--budget", "4m", "--effort", "low", "--model", "fake", check=False, env=env, timeout=1800)
        pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
        vs = pj.get("versions") or []
        notes = json.load(open(os.path.join(PDIR, "review", "notes.json"), encoding="utf-8"))
        brief = open(os.path.join(PDIR, "work", "director", f"run-{pj['runs'][-1]['run']}", "brief.md"), encoding="utf-8").read()
        say("PASS" if r.returncode == 0 and len(vs) >= 3 and all(n.get("done") for n in notes) and "hold the stop longer" in brief
            else "FAIL", "review round (--revise)", f"versions: {', '.join('v' + str(v['n']) for v in vs)}; notes done: "
                                                     f"{all(n.get('done') for n in notes)}")
        files = sorted(f for f in os.listdir(os.path.join(PDIR, "final")) if f.endswith(".mp4"))
        say("PASS" if sum(f.startswith("music-video_v") for f in files) >= 3 else "FAIL", "earlier versions kept", ", ".join(files))
        agents_run_check(env)
        flags_check()
        continue_run_check(env)
        pitch_check(env)
        pitch_step_check()
        pitch_trouble_check()
    return finish(a, t_start)


def structure_check():
    """The sections of a song the sound can't cut (one loudness, one sound, a line sung in every bar): the lyric's
    [tags] name them; with no tags, its blank lines between stanzas still find them (the sound and the repeats name
    them); a chorus written once and sung again (Whisper's repeats) is a section of its own. No audio: a grid, lines."""
    print("== 3d. sections from the lyric's structure ([tags], stanzas)", flush=True)
    import random
    import numpy as np
    import song_kit as K
    from mv import parts
    rnd = random.Random(7)
    syl = ["ka", "lo", "mi", "re", "tu", "san", "vel", "dor", "pi", "nex", "ra", "bo", "lin", "ter", "fa", "gus"]

    def new(n):
        return [" ".join("".join(rnd.choice(syl) for _ in range(2)) for _ in range(6)) for _ in range(n)]
    pre_end, chorus = new(2), new(8)
    plan = [("Intro", new(2)), ("Verse 1", new(8)), ("Pre-Chorus", new(2) + pre_end), ("Chorus", chorus),
            ("Verse 2", new(8)), ("Pre-Chorus", new(2) + pre_end), ("Chorus", chorus), ("Bridge", new(4)),
            ("Breakdown", new(4)), ("Final Chorus", chorus), ("Outro", new(4))]
    bar, nb = 60 / 90.7 * 4, 74
    db = [1.3 + i * bar for i in range(nb)]
    flat = {"drums": [5] * nb, "bass": [5] * nb, "other": [5] * nb}

    def sections(text, written=None):
        A = {"beats": [1.3 + i * bar / 4 for i in range(nb * 4)], "downbeats": db, "beat_period": bar / 4, "lyrics": []}
        G = K.Grid(A, 1.3 + nb * bar)
        own = parts.walk(text)
        for b, x in enumerate(written or [(x, x) for x in own], start=1):        # one line a bar, never a breath
            src, ln = x
            t0 = db[b] + 0.05
            ws = ln["text"].split()
            A["lyrics"].append({"t": t0, "end": t0 + 2.2, "text": ln["text"], "words": [{"w": w, "t": t0 + 0.35 * k} for k, w in
                                enumerate(ws)], **parts.fields(src["part"], src["stanza"] if src is ln else None)})
        lines = K.lyric_lines(A, G, [])
        vf = np.array([0.8 if any(x["home"] == i for x in lines) else 0.0 for i in range(nb)])
        R, why, why_all = K.segment(G, [0] * nb, lines, vf, np.full(nb, -10.0), flat)
        if parts.tagged(lines):
            return [s["name"] for s in K.tagged_sections(G, R, why_all, [0] * nb, lines, vf, np.full(nb, -10.0))[0]]
        return [s["name"] for s in K.label_sections(R, lines, vf, np.full(nb, -10.0), True)]
    tagged = "\n\n".join(f"[{tag}]\n" + "\n".join(ls) for tag, ls in plan)
    got = sections(tagged)
    want = ["intro", "verse 1", "pre-chorus 1", "chorus 1", "verse 2", "pre-chorus 2", "chorus 2", "bridge", "breakdown",
            "final chorus", "outro"]
    say("PASS" if got == want else "FAIL", "the lyric's [tags] name the sections", " → ".join(got))
    stanzas = "\n\n".join("\n".join(ls) for _, ls in plan)
    got = sections(stanzas)
    want = ["verse 1", "pre-chorus 1", "chorus 1", "verse 2", "pre-chorus 2", "chorus 2", "bridge", "chorus 3", "outro"]
    say("PASS" if got == want else "FAIL", "no tags: the stanzas (blank lines) find the sections, the repeats name them",
        " → ".join(got))
    got = sections("\n\n".join(x for _, ls in plan for x in ls))
    say("PASS" if len(got) <= 3 else "FAIL", "a blank line after every line is no stanza break (no one-line sections)",
        " → ".join(got))
    own = parts.walk(tagged)                       # the final chorus left out of the lyric: Whisper heard it (repeats)
    written = [(x, x) for x in own[:50]] + [(x, {"text": x["text"]}) for x in own[14:22]] + [(x, x) for x in own[58:]]
    got = sections("", written)
    say("PASS" if got[-4:] == ["bridge", "breakdown", "chorus 3", "outro"] else "FAIL",
        "a chorus written once more than the lyric has it (Whisper's repeats) is a section of its own", " → ".join(got))
    w = parts.walk("[ar:Someone]\n[Verse 1]\n[00:05.00] one line\n[Male vocal] [00:07.00] two\n\n[Chorus: all]\n[00:10.00] three\n")
    say("PASS" if [(x["text"], x["part"], x["stanza"]) for x in w] == [("one line", "Verse 1", 0), ("two", "Verse 1", 0),
                                                                        ("three", "Chorus: all", 1)] else "FAIL",
        "LRC: section tags and stanzas read, LRC headers and [Male vocal] skipped", str(w))


def critic_check(m):
    """The critic with the stand-in for Claude Code: on the final (the eyes from step 6 are fresh: no new look), then
    deliver --critic (reuses that critique for v1), an old Claude Code without --max-budget-usd, and the parser."""
    print("== 6b. the critic (stand-in for Claude Code)", flush=True)
    fake = {"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")}
    cdir = os.path.join(PDIR, "work", "critic")
    r = studio("critique", P, "final/music-video.mp4", "--model", "fake", check=False, env=fake)
    c = json.load(open(os.path.join(cdir, "music-video_01.json"), encoding="utf-8")) if os.path.exists(os.path.join(cdir, "music-video_01.json")) else {}
    md = open(os.path.join(cdir, "music-video_01.md"), encoding="utf-8").read() if os.path.exists(os.path.join(cdir, "music-video_01.md")) else ""
    line = [x for x in r.stdout.splitlines() if x.startswith("critic: ")]
    first3 = next((x["t"] for x in m["moments"] if x["stars"] >= 3), None)
    seen = re.search(r"(\d+) of (\d+) pictures exist", md)
    say("PASS" if r.returncode == 0 and c.get("scores") == {"ambition": 6, "song_fit": 7, "craft": 8, "readability": 9}
        and line and line[-1].endswith("work/critic/music-video_01.md") and "```json" not in md and c.get("cost_usd") == 0.0123
        and first3 is not None and abs(c["problems"][0]["t0"] - first3) < 0.05 and seen and seen.group(1) == seen.group(2)
        and int(seen.group(2)) >= 5 else "FAIL", "studio.py critique (files, parsed scores, the worst problem at the first ★★★ moment)",
        (line[-1] if line else (r.stdout + r.stderr)[-800:]) + (f"; {seen.group(0)}" if seen else ""))
    prompt = open(os.path.join(cdir, "last-prompt.md"), encoding="utf-8").read() if os.path.exists(os.path.join(cdir, "last-prompt.md")) else ""
    say("PASS" if "<!-- mv:critic -->" in prompt and "Nothing is revealed in a shot's last beat" in prompt and "spectrum bars" in prompt
        and "a spectrum analyser or waveform bars" in prompt and "scripted mini director" not in prompt and "strip_at" in prompt
        else "FAIL", "the critic's prompt: the craft rules, the anti-defaults, the avoid, the strips; not the director's report",
        f"{len(prompt)} chars")
    r = studio("deliver", P, "--critic", check=False, env=fake)
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    v1 = (pj.get("versions") or [{}])[0]
    say("PASS" if r.returncode == 0 and (v1.get("critic") or {}).get("from") == "work/critic/music-video_01.md"
        and os.path.exists(os.path.join(PDIR, "final", "critic_v1.md")) and not os.path.exists(os.path.join(cdir, "music-video_02.json"))
        else "FAIL", "deliver --critic: the version keeps the critique of that exact cut (final/critic_v1.md, no new call)",
        str(v1.get("critic"))[:160])
    r = studio("critique", P, "final/music-video.mp4", "--model", "fake", "--no-look", "--out", "work/critic/_old-cli", check=False,
               env=dict(fake, FAKE_CLAUDE_NO_BUDGET="1"))
    log = open(os.path.join(cdir, "log.jsonl"), encoding="utf-8").read().splitlines()
    say("PASS" if r.returncode == 0 and "doesn't know --max-budget-usd" in r.stdout and os.path.exists(os.path.join(cdir, "_old-cli", "critic.json"))
        and len(log) == 1 else "FAIL", "an old Claude Code without --max-budget-usd: asked again without it; --out writes there (no log line)",
        r.stdout.strip().splitlines()[-1][-120:] if r.stdout.strip() else r.stderr[-300:])
    r = studio("critique", P, "final/music-video.mp4", "--model", "fake", "--no-look", check=False,
               env=dict(fake, FAKE_CLAUDE_CRITIC_ERROR="1"))
    log = open(os.path.join(cdir, "log.jsonl"), encoding="utf-8").read().splitlines()
    err = json.loads(log[-1]) if log else {}
    say("PASS" if r.returncode == 1 and "couldn't run" in r.stdout and "Invalid API key" in r.stdout and "$0.0040 spent" in r.stdout
        and not os.path.exists(os.path.join(cdir, "music-video_02.json")) and err.get("error") and err.get("cost_usd") == 0.004
        and not err.get("file") else "FAIL", "Claude Code's own error text (a login, a usage limit) is no critique: exit 1, nothing "
        "written but a log line with its cost", r.stdout.strip().splitlines()[-1][-160:] if r.stdout.strip() else r.stderr[-300:])
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    import critic
    _, d1, w1 = critic.parse("# Critique\nno json at all")
    _, d2, w2 = critic.parse('text\n```json\n{"scores": {"ambition": 3}}\n```\nmore\n```json\n{"scores": {"ambition": 12, "craft": "7"}, '
                             '"problems": [{"severity": "huge", "t0": "4.5"}, {"severity": "HIGH"}]}\n```')
    _, d3, w3 = critic.parse('```json\n{"scores": {broken}\n```')
    _, d4, w4 = critic.parse('```json\n{"scores": {"ambition": 3}}\n```\nthe real one:\n```json\n{broken}\n```')
    _, d5, w5 = critic.parse('report\n```JSON\n{"scores": {"craft": 5}}\n```')
    say("PASS" if d1["scores"] is None and w1 and d2["scores"] == {"ambition": 10, "song_fit": None, "craft": 7, "readability": None}
        and not w2 and d2["problems"][0]["severity"] == "medium" and d2["problems"][1]["severity"] == "high"
        and d2["problems"][0]["t0"] == 4.5 and d3["scores"] is None and "parse" in w3 and d4["scores"] is None and "parse" in w4
        and (d5["scores"] or {}).get("craft") == 5 and not w5 else "FAIL",
        "the critic's answer parsed leniently (no json, two blocks, a broken last block, ```JSON, HIGH)", f"{d2['scores']}; {w1}; {w4}; {d5['scores']}")
    watcher_check()


def plan_check(m):
    """The plan tools (tools/plan.py) on a plan of three segments (copies of tests/scenes/field.js in their own
    folders, the middle one importing the shared code in work/scenes/lib/) and the lyrics overlay: validate (the good
    plan; a plan from the stage machine, with its stage, hero, owners, statuses and briefs, reads as it is; gaps,
    overlaps, a bad id, a range outside the song, a scene outside its folder; a scene not written yet is no error),
    render at preview size and again from the cache, the cache key (a lib change renders the segment that imports it
    again, a sibling's change doesn't), assemble a preview (with the stand-in critic; its cut marked pending only
    while it works), a segment without a scene (a slate in a preview; the final refuses before rendering), the render
    queue, the status."""
    print("== 6c. the plan tools (stand-in for Claude Code)", flush=True)
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    import plan as plans
    import critic
    fake = {"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")}
    sd = os.path.join(PDIR, "work", "scenes")
    for sid in ("s1", "s2", "s3", "type", "lib"):
        os.makedirs(os.path.join(sd, sid), exist_ok=True)
    for sid in ("s1", "s3"):
        shutil.copyfile(os.path.join(TESTS, "scenes", "field.js"), os.path.join(sd, sid, "scene.js"))
    open(os.path.join(sd, "s2", "scene.js"), "wb").write(b'import { LOOK } from "../lib/look.js";   // the shared look\n'
                                                         + open(os.path.join(TESTS, "scenes", "field.js"), "rb").read())
    open(os.path.join(sd, "lib", "look.js"), "wb").write(b'// the shared look (smoke test)\nexport const LOOK = {ink: "#e9e4d8"};\n')
    shutil.copyfile(os.path.join(TESTS, "scenes", "lyrics.html"), os.path.join(sd, "type", "type.html"))
    bars = [b["t"] for b in m["bars"]]
    c1, c2, dur = round(bars[len(bars) // 3], 3), round(bars[2 * len(bars) // 3], 3), m["duration"]
    seg = lambda sid, a, b: {"id": sid, "name": f"{sid} (smoke)", "from": a, "to": b, "renderer": "web",  # noqa: E731
                             "scene": f"work/scenes/{sid}/scene.js"}
    good = {"segments": [seg("s1", 0.0, c1), seg("s2", c1, c2), seg("s3", c2, dur)],
            "overlays": [{"id": "type", "name": "the sung lines", "scene": "work/scenes/type/type.html", "transparent": True}]}
    plan_p = os.path.join(PDIR, "work", "plan.json")
    json.dump(good, open(plan_p, "w", encoding="utf-8"), indent=1)
    r = studio("plan", "validate", P, check=False)
    v = plans.validate(PDIR)
    old = dict(good, stage="planned", hero="s2", lib="work/scenes/lib/", style_frames=["work/style-frames/gone.png"],
               rules="marks on a dark field", segments=[dict(x, owner="builder", status="built", brief="b", enter="e", exit="x",
                                                             moments=["1:0 x"]) for x in good["segments"]])
    vo = plans.validate(PDIR, plan=old)
    say("PASS" if r.returncode == 0 and v["ok"] and not v["warnings"] and "plan.json: valid · 3 segments + 1 overlay" in r.stdout
        and vo["ok"] and not vo["warnings"] else "FAIL",
        "plan validate: a good plan (3 segments on downbeats + an overlay); a plan from the stage machine reads as it is",
        (r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr[-300:]) + f"; the old one: {vo['errors'] + vo['warnings']}")

    def bad(fn):
        p = json.loads(json.dumps(good))
        fn(p)
        return plans.validate(PDIR, plan=p)
    cases = [("a gap", lambda p: p["segments"][1].update({"from": c1 + 1.0}), "a gap before it"),
             ("an overlap", lambda p: p["segments"][1].update({"from": c1 - 1.0}), "overlaps s1"),
             ("a bad id", lambda p: p["segments"][0].update({"id": "Intro 1"}), "id is 'Intro 1'"),
             ("a range outside the song", lambda p: p["segments"][2].update({"to": dur + 5}), "but the song is"),
             ("a scene outside its folder", lambda p: p["segments"][0].update({"scene": "work/scenes/s3/scene.js"}), "its own folder")]
    got = {name: bad(fn) for name, fn, _ in cases}
    miss = [name for name, _, want in cases if got[name]["ok"] or not any(want in e for e in got[name]["errors"])]
    unwritten = bad(lambda p: p["segments"][1].update({"scene": "work/scenes/s2/missing.js"}))
    json.dump(dict(good, segments=[good["segments"][0], dict(good["segments"][1], **{"from": c1 + 1.0}), good["segments"][2]]),
              open(plan_p, "w", encoding="utf-8"))
    r = studio("plan", "validate", P, check=False)
    open(plan_p, "w", encoding="utf-8").write('{"segments": [}')
    r2 = studio("plan", "validate", P, check=False)
    json.dump(good, open(plan_p, "w", encoding="utf-8"), indent=1)
    say("PASS" if not miss and r.returncode == 1 and "NOT valid" in r.stdout and f"Set s2.from to {c1:g}" in r.stdout
        and r2.returncode == 1 and "isn't valid JSON at line 1" in r2.stdout and unwritten["ok"]
        and not next(x for x in unwritten["items"] if x["id"] == "s2")["exists"] else "FAIL",
        "plan validate: bad plans are refused with what to change (" + ", ".join(n for n, _, _ in cases) + ", broken JSON); "
        "a scene not written yet is no error", f"missed: {miss}; " + " | ".join(next((e for e in got[n]["errors"]), "-")[:70] for n, _, _ in cases[:2]))
    t0 = time.time()
    r = studio("plan", "render", P, "--preview", check=False)
    t_render = time.time() - t0
    t0 = time.time()
    r2 = studio("plan", "render", P, "--preview", check=False)
    say("PASS" if r.returncode == 0 and "4 rendered, 0 unchanged" in r.stdout and r2.returncode == 0 and "0 rendered, 4 unchanged" in r2.stdout
        and all(os.path.exists(os.path.join(PDIR, "work", "segments", s, f"{s}_preview.mp4")) for s in ("s1", "s2", "s3"))
        and os.path.isdir(os.path.join(PDIR, "work", "segments", "type", "type_preview")) and time.time() - t0 < 30
        and os.path.exists(plans.renders_path(PDIR)) else "FAIL",
        "plan render --preview: each segment over its own range, the overlay as a transparent pass; again: all from the cache",
        f"{t_render:.0f} s, then {time.time() - t0:.1f} s; " + (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:]))
    v = plans.validate(PDIR)
    its = {x["id"]: x for x in v["items"]}
    cur = lambda: {k: plans.current(PDIR, its[k], "preview")[0] for k in ("s1", "s2", "s3")}   # noqa: E731
    lib, s3 = os.path.join(sd, "lib", "look.js"), os.path.join(sd, "s3", "scene.js")
    keep_lib, keep_s3 = open(lib, "rb").read(), open(s3, "rb").read()
    open(lib, "ab").write(b"// the lib changed\n")
    after_lib = cur()
    open(lib, "wb").write(keep_lib)
    open(s3, "ab").write(b"// s3 changed\n")
    after_s3 = cur()
    open(s3, "wb").write(keep_s3)
    back = cur()
    say("PASS" if after_lib == {"s1": True, "s2": False, "s3": True} and after_s3 == {"s1": True, "s2": True, "s3": False}
        and all(back.values()) else "FAIL", "the render cache follows imports: a lib change renders the segment that imports "
        "it again, a sibling segment's change doesn't", f"lib changed: {after_lib}; s3 changed: {after_s3}; restored: {back}")
    r = studio("plan", "assemble", P, "--preview", "--critic-model", "fake", check=False, env=fake)
    vid = (re.findall(r"-> (work/previews/preview_\d+\.mp4)\s*$", r.stdout, re.M) or [None])[-1]
    from mv.common import frames_of
    n = frames_of(os.path.join(PDIR, vid)) if vid else None
    stem = os.path.splitext(os.path.basename(vid or "x"))[0]
    fps = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))["format"]["fps"]
    n_song = int(round(json.load(open(os.path.join(PDIR, "song", "frames.json")))["duration"] * fps))
    full = os.path.join(PDIR, vid) if vid else None
    found = critic.find(PDIR, vid, os.path.getsize(full), os.path.getmtime(full)) if vid else None
    left = glob.glob(os.path.join(PDIR, "work", "critic", "pending", "*.json"))
    say("PASS" if r.returncode == 0 and vid and n == n_song
        and "(stream copy) + 1 overlay" in r.stdout and os.path.exists(os.path.join(PDIR, "work", "eyes", stem, "index.md"))
        and found and "the joins" in open(os.path.join(PDIR, "work", "critic", "last-prompt.md"), encoding="utf-8").read()
        and not left and not critic.is_pending(PDIR, vid, os.path.getsize(full), os.path.getmtime(full)) else "FAIL",
        "plan assemble --preview: segments joined (stream copy), the type layered, the song muxed; eyes; the critic on the "
        "film (critic.find has it; no pending marker left)", f"{vid}: {n} frames; critique {found and found.get('file')}; "
        f"markers left {len(left)}" if vid else (r.stdout + r.stderr)[-800:])
    # the pending marker: there while a critique is made (this process), gone after; a dead process's is cleared
    with critic.pending(PDIR, full):
        during = critic.is_pending(PDIR, vid, os.path.getsize(full), os.path.getmtime(full))
    after = critic.is_pending(PDIR, vid, os.path.getsize(full), os.path.getmtime(full))
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    stale = os.path.join(PDIR, "work", "critic", "pending", "stale.json")
    json.dump({"video": vid, "size": os.path.getsize(full), "mtime": os.path.getmtime(full), "pid": dead.pid, "at": time.time()},
              open(stale, "w"))
    gone = not critic.is_pending(PDIR, vid, os.path.getsize(full), os.path.getmtime(full)) and not os.path.exists(stale)
    say("PASS" if during and not after and gone else "FAIL", "the critic's pending marker: there while a critique of that cut "
        "is made, gone after; a dead process's marker is cleared", f"during {during}, after {after}, a dead one cleared {gone}")
    os.replace(s3, s3 + ".off")                    # s3 not written yet: a slate in a preview; the final says no at once
    rf = studio("plan", "assemble", P, "--final", check=False)
    r = studio("plan", "assemble", P, "--preview", "--no-critic", "--no-look", check=False)
    os.replace(s3 + ".off", s3)
    vid2 = (re.findall(r"-> (work/previews/preview_\d+\.mp4)\s*$", r.stdout, re.M) or [None])[-1]
    say("PASS" if rf.returncode == 1 and "no scene yet for s3" in rf.stdout and "render (" not in rf.stdout and r.returncode == 0
        and "s3 is a labelled slate" in r.stdout and vid2 and frames_of(os.path.join(PDIR, vid2)) == n_song else "FAIL",
        "a segment not written yet: a labelled slate in a preview; the final refuses before rendering anything",
        (rf.stdout.strip().splitlines() or ["-"])[-1][:120] + " | " + (r.stdout.strip().splitlines() or ["-"])[-1][:100])
    import eyes
    scratch = os.path.join(PDIR, "cache", "_eyes")                  # (not the project's own work/pitches: no stray round)
    pv = eyes.out_dir(os.path.join(scratch, "work", "pitches", "round-1", "p2", "proof.mp4"))
    sv = eyes.out_dir(os.path.join(PDIR, "work", "segments", "s2", "s2_preview.mp4"))
    fv = eyes.out_dir(os.path.join(PDIR, "final", "music-video.mp4"))
    shutil.rmtree(scratch, ignore_errors=True)
    say("PASS" if os.path.normcase(pv) == os.path.normcase(os.path.join(scratch, "work", "pitches", "round-1", "p2", "eyes", "proof"))
        and os.path.normcase(sv) == os.path.normcase(os.path.join(PDIR, "work", "segments", "s2", "eyes", "s2_preview"))
        and os.path.normcase(fv) == os.path.normcase(os.path.join(PDIR, "work", "eyes", "music-video")) else "FAIL",
        "eyes out_dir: a segment's and a pitch's proof keep their eyes in their own folder, the final in work/eyes/", f"{sv}; {pv}; {fv}")
    queue_check()
    r = studio("plan", "status", P, check=False)
    say("PASS" if r.returncode == 0 and "render queue: free" in r.stdout and "preview ✓" in r.stdout and "the final render: about" in r.stdout
        and re.search(r"preview work/previews/preview_\d+\.mp4 .* critic ambition 6", r.stdout) else "FAIL",
        "plan status (the items' renders, the previews with the critic's scores, the final's estimate, the render queue)",
        " | ".join(x for x in r.stdout.splitlines() if x.startswith(("the final", "render queue", "preview "))))


def live_check(m):
    """Live preview (tools/mv/live.py and the runtime's live mode): the live block in mv.js changes no render (mv.js
    without it renders the same pixels); a scene played live shows exactly the renderer's frame (a stateless scene; a
    stateful one after a jump forward, a jump back and a step on); the per-frame text list doesn't grow while it plays;
    the live server serves the renderer's URL space and nothing else; the timelines the web app plays: work/live.json
    first (with what it can't play said), then the plan (the segments in their slots, the type on top), then the renders
    (the mini director's passes: field.js live over its ranges, the Blender pass as its video, the type on top)."""
    print("== 6e. live preview: the scenes in the browser with the song", flush=True)
    import io, http.client, urllib.parse
    from mv import live, web
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    fps, W, H = pj["format"]["fps"], pj["format"]["width"], pj["format"]["height"]
    out = os.path.join(PDIR, "cache", "_live")
    shutil.rmtree(out, ignore_errors=True)
    src = open(os.path.join(web.RUNTIME, "mv.js"), encoding="utf-8").read()
    a, b = src.find("// ------------------------------------------------------------------ live preview (mv:live)"), src.find("(mv:live-end)")
    rt = os.path.join(out, "runtime")
    os.makedirs(rt)
    open(os.path.join(rt, "mv.js"), "w", encoding="utf-8").write(src[:a] + src[src.index("\n", b) + 1:] if a > 0 and b > a else src)
    frames = {"field.js": [37, 452], "trail.js": [120, 30, 45]}       # trail: forward, back (a reload), a step on
    shots, keep_rt = {}, web.RUNTIME
    for tag, rdir in (("render", web.RUNTIME), ("without", rt)):
        web.RUNTIME = rdir
        try:
            for sc, fs in frames.items():
                ps = web.stills(os.path.join(TESTS, "scenes", sc), [f / fps for f in fs], os.path.join(out, tag), project=PDIR,
                                gpu="off", log=lambda x: None)
                for p in ps:
                    shots[(tag, sc, int(round(float(os.path.basename(p).rsplit("_", 1)[1][:-4]) * fps)))] = p
        finally:
            web.RUNTIME = keep_rt

    def diff(x, y):
        return max(e[1] for e in ImageChops.difference(Image.open(x).convert("RGB") if isinstance(x, str) else x,
                                                       Image.open(y).convert("RGB")).getextrema())
    same = [diff(shots[("render", sc, f)], shots[("without", sc, f)]) == 0 for sc, fs in frames.items() for f in fs]
    say("PASS" if a > 0 and b > a and len(same) == 5 and all(same) else "FAIL",
        "mv.js renders unchanged: without its live block the same frames come out pixel for pixel (a stateless and a stateful scene)",
        f"{sum(same)} of {len(same)} frames identical")
    srv = live.LiveServer(PDIR).start()

    def until(pg, js, most=30):
        t0 = time.time()
        while time.time() - t0 < most:
            try:
                if pg.evaluate(js):
                    return True
            except Exception:                    # (reloading)
                pass
            time.sleep(0.1)
        return False

    def show(pg, f):                             # the parent's clock: frame f, paused (a jump back may reload the page)
        clock = f"window.postMessage({{type: 'mv-clock', t: {f / fps}, playing: false, rate: 1, seek: true}}, '*')"
        done = f"window.MV && MV._liveState && MV._liveState().last === {f} && !MV._liveState().busy"
        until(pg, "window.__st && __st.some(s => s.ready)")
        pg.evaluate("window.__mark = 1; " + clock)
        until(pg, f"({done}) || !window.__mark")
        if not until(pg, done, 0.5):             # reloaded: it asks for its clock again (as the parent does on load)
            until(pg, "window.__st && __st.some(s => s.ready)")
            pg.evaluate(clock)
            until(pg, done)
        return Image.open(io.BytesIO(pg.screenshot())).convert("RGB")
    res, leak, catch, errs = [], {}, False, []
    try:
        with sync_playwright() as pw:
            br, _ = web.launch(pw, "off")
            ctx = br.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
            ctx.add_init_script("window.__st = []; addEventListener('message', e => { if (e.data && e.data.type === 'mv-live') __st.push(e.data); });")
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append(str(e)))
            for sc, fs in frames.items():
                pg.goto(srv.url + "/_mv/live.html?" + urllib.parse.urlencode({"scene": f"../../tests/scenes/{sc}", "scale": 1}))
                for f in fs:
                    res.append((sc, f, diff(show(pg, f), shots[("render", sc, f)])))
                catch |= bool(pg.evaluate("__st.some(s => s.stateful)"))
            pg.evaluate(f"window.postMessage({{type: 'mv-clock', t: {60 / fps}, playing: true, rate: 1}}, '*')")
            time.sleep(2.5)
            leak = pg.evaluate("MV._liveState()")
            fps_seen = max(s.get("fps") or 0 for s in pg.evaluate("__st"))
            # the clock correcting itself (a pause lands a frame behind the frame shown; a stem switch seeks back to
            # where it was): the stateful scene holds its frame, its page isn't reloaded; a real jump back still is
            pg.evaluate("window.__keep = 1")
            last0 = pg.evaluate("MV._liveState().last")
            pg.evaluate(f"window.postMessage({{type: 'mv-clock', t: {(last0 - 1) / fps}, playing: false, rate: 1}}, '*')")
            time.sleep(0.6)
            last1 = pg.evaluate("MV._liveState().last")           # (paused now: it holds)
            pg.evaluate(f"window.postMessage({{type: 'mv-clock', t: {(last1 - 2) / fps}, playing: false, rate: 1, seek: true}}, '*')")
            time.sleep(0.6)
            hold = pg.evaluate("[window.__keep === 1, MV._liveState().last, MV._liveState().ready]")
            # a stateful scene slower than real time: it skips frames to keep time (and says so), it doesn't fall behind
            open(os.path.join(out, "slow.js"), "w", encoding="utf-8").write(
                "let ctx, x = 0;\nexport default {stateful: true, setup(mv) { ctx = mv.canvas().getContext('2d'); },\n"
                "  render(f) { let z = 0; for (let k = 0; k < 2.5e7; k++) z += Math.sqrt(k); window.__z = z;\n"
                "    x = (x + 3) % f.w; ctx.fillStyle = '#000'; ctx.fillRect(0, 0, f.w, f.h); ctx.fillStyle = '#fff'; ctx.fillRect(x, 10, 10, 10); }};\n")
            pg.goto(srv.url + "/_mv/live.html?" + urllib.parse.urlencode({"scene": "cache/_live/slow.js", "scale": 0.25}))
            until(pg, "window.__st && __st.some(s => s.ready)")
            pg.evaluate(f"window.postMessage({{type: 'mv-clock', t: {30 / fps}, playing: true, rate: 1}}, '*')")
            time.sleep(4)
            slow = pg.evaluate("(() => { const s = MV._liveState(); return [s.approx, Math.round(s.t * %d) - s.last, s.last]; })()" % fps)
            br.close()
    finally:
        pass
    say("PASS" if len(res) == 5 and all(d == 0 for _, _, d in res) and catch and leak.get("texts") == 0 and leak.get("last", 0) > 60
        and fps_seen > 0 and not errs else "FAIL",
        "a scene played live shows the renderer's frame, pixel for pixel (a stateless scene; a stateful one forward, back, a step "
        "on); while it plays the per-frame text list stays empty", f"{res}; playing: frame {leak.get('last')}, texts {leak.get('texts')}, "
        f"{fps_seen} fps; {errs[:2]}")
    say("PASS" if hold[0] and hold[2] and hold[1] >= last0 and slow[0] is True and slow[1] < fps / 2 and slow[2] > 60 else "FAIL",
        "live, a stateful scene: a pause a frame back and a seek two frames back hold its frame (no reload, no restart from "
        "setup); one slower than real time skips frames to keep time (said: approx) instead of falling behind the song",
        f"hold {hold} (from frame {last0}); slow: approx {slow[0]}, {slow[1]} frames behind the clock at frame {slow[2]}")
    # what the live server serves: the renderer's URL space, no-store, nothing outside it
    def get(path, host=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=20)
        c.request("GET", path, headers={"Host": host} if host else {})
        r = c.getresponse()
        csp.append(r.getheader("Content-Security-Policy") or "")
        return r.status, r.getheader("Cache-Control"), r.getheader("Location"), r.read()
    csp = []
    abs_field = "/_mv/abs/" + urllib.parse.quote(os.path.join(TESTS, "scenes", "field.js").replace("\\", "/").lstrip("/"))
    outside = os.path.join(os.path.dirname(STUDIO), "_mv_live_outside.txt")
    probes = {"shell": get("/_mv/live.html?scene=work/scenes/s2/scene.js&scale=0.5"), "fonts": get("/_mv/fonts.css"),
              "runtime": get("/_mv/runtime/mv.js"), "scene": get("/work/scenes/s2/scene.js"), "song": get("/_mv/song/hits.json"),
              "abs in the studio": get(abs_field), "abs outside": get("/_mv/abs/" + urllib.parse.quote(outside.replace("\\", "/").lstrip("/"))),
              "..": get("/../../tools/studio.py"), "%2e%2e": get("/%2e%2e/%2e%2e/tools/studio.py"),
              "runtime ..": get("/_mv/runtime/..%2f..%2f..%2fstudio.py"), "other host": get("/project.json", "example.com"),
              "abs studio notes": get("/_mv/abs/" + urllib.parse.quote(os.path.join(STUDIO, "CLAUDE.md").replace("\\", "/").lstrip("/"))),
              "unc": get("/%5C%5C127.0.0.1%5Cshare%5Cx.js"),
              "html scene": get("/_mv/live.html?scene=work/scenes/type/type.html&transparent=1")}
    loc = probes["html scene"][2] or ""
    html = get(loc)[3].decode("utf-8", "replace") if loc else ""
    srv.stop()
    ok_srv = (probes["shell"][0] == 200 and b'"live": true' in probes["shell"][3] and all(probes[k][0] == 200 for k in ("fonts", "runtime", "scene", "song", "abs in the studio"))
              and probes["scene"][1] == "no-store" and all(probes[k][0] == 403 for k in ("abs outside", "..", "%2e%2e", "runtime ..", "other host",
                                                                                          "abs studio notes"))
              and probes["unc"][0] in (403, 404)                    # (refused by name on Windows; a plain odd name here)
              and csp and all("default-src 'self'" in x and "frame-ancestors" in x for x in csp)
              and probes["html scene"][0] == 302 and "mv-live=1" in loc and '"transparent": true' in html and '"live": true' in html
              and 'id="line"' in html)
    say("PASS" if ok_srv else "FAIL", "the live server: the renderer's URL space (shell, fonts, runtime, the scene, the song, the "
        "tests' scenes under the studio), no-store, a CSP that keeps pages off the network; outside the studio, the studio's "
        "own notes, a \\\\host\\share path, .., another host: refused; an HTML scene in its own page",
        "; ".join(f"{k} {v[0]}" for k, v in probes.items()))
    # the timelines
    st = live.state(PDIR)
    by = {tl["source"]: tl for tl in st["timelines"]}
    pl = by.get("plan.json", {}).get("layers") or []
    rn = by.get("renders", {}).get("layers") or []
    field = [x for x in rn if x.get("scene") == "../../tests/scenes/field.js"]
    blend = os.path.exists(os.path.join(PDIR, "work", "renders", "stop.mp4"))
    ok_tl = ([x["id"] for x in pl] == ["s1", "s2", "s3", "type"] and all(x["kind"] == "scene" for x in pl) and pl[-1]["transparent"]
             and pl[0]["slots"][0][0] == 0 and pl[2]["slots"][0][1] == st["n"] and len(field) == 1 and len(field[0]["slots"]) == (2 if blend else 1)
             and (not blend or any(x["kind"] == "video" and x["video"] == "work/renders/stop.mp4" for x in rn))
             and rn[-1].get("scene", "").endswith("lyrics.html") and rn[-1]["transparent"])
    sc = {x["path"] for x in st["scenes"]}
    sec = m["sections"][1]
    secs = [x for x in m["sections"] if x["name"] != sec["name"]]
    s2_ = secs[-1] if len(secs) > 1 else None             # a section that isn't next to the first one
    json.dump({"layers": [{"scene": "../../tests/scenes/trail.js", "section": sec["name"]}, {"scene": "work/scenes/nope.js"},
                          {"scene": "work/scenes/s1/scene.js", "section": "no such section"},
                          {"scene": "work/scenes/s2/scene.js", "section": [sec["name"]] + ([s2_["name"]] if s2_ else []), "mode": ["odd"]},
                          {"scene": "work/scenes/type/type.html", "transparent": True}]},
              open(os.path.join(PDIR, "work", "live.json"), "w", encoding="utf-8"))
    st2 = live.state(PDIR)
    os.remove(os.path.join(PDIR, "work", "live.json"))
    lj = st2["timelines"][0]
    fr = lambda x: [int(round(x["t0"] * fps)), int(round(x["t1"] * fps))]      # noqa: E731
    want2 = [fr(sec)] + ([fr(s2_)] if s2_ else [])
    ok_lj = (lj["source"] == "live.json" and [x.get("scene") for x in lj["layers"]] == ["../../tests/scenes/trail.js", "work/scenes/s2/scene.js",
                                                                                       "work/scenes/type/type.html"]
             and lj["layers"][0]["slots"] == [fr(sec)] and any("nope.js" in w for w in lj["warnings"])
             and any("no such section" in w and "left out" in w for w in lj["warnings"])
             and (lj["layers"][1]["slots"] == want2 or (len(want2) == 2 and want2[0][1] == want2[1][0])) and lj["layers"][1]["mode"] == "normal")
    say("PASS" if ok_tl and ok_lj and {"work/scenes/s1/scene.js", "work/scenes/type/type.html", "../../tests/scenes/field.js"} <= sc
        and "work/scenes/lib/look.js" not in sc else "FAIL",
        "the timelines: work/live.json first (what it can't play said), the plan (segments in their slots, the type on top), the "
        "renders (a scene live over its ranges, the Blender pass as its video, the type on top); the scenes, the lib left out",
        f"plan {[x['id'] for x in pl]}; renders {[(x['kind'], x.get('scene') or x.get('video'), len(x['slots'])) for x in rn]}; "
        f"live.json {[(x.get('scene'), x['slots']) for x in lj['layers']]} {lj['warnings']}; scenes {sorted(sc)[:6]}")
    # live notes left while no run was going: a first run's brief has them (with their ids and how to mark them done)
    import director
    np_ = os.path.join(PDIR, "review", "notes.json")
    keep = open(np_, encoding="utf-8").read() if os.path.exists(np_) else None
    try:
        live.update_notes(PDIR, lambda ns: ns + [{"id": 1700000000123, "version": "live", "t": 12.5, "text": "the marks should breathe (live)",
                                                   "done": False, "scene": "work/scenes/s1/scene.js", "timeline": "plan.json"}])
        br_ = director.build_brief(PDIR, 3600, started=time.time(), deadline=time.time() + 3600)
        done = subprocess.run([PY, os.path.join(STUDIO, "tools", "studio.py"), "note-done", P, "1700000000123", "the marks breathe now"],
                              capture_output=True, text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        nt = next((x for x in json.load(open(np_, encoding="utf-8")) if x.get("id") == 1700000000123), {})
    finally:
        if keep is None:
            os.remove(np_)
        else:
            open(np_, "w", encoding="utf-8").write(keep)
    say("PASS" if "My notes from playing the scenes live" in br_ and "**the marks should breathe (live)** (id 1700000000123)" in br_
        and "studio.py note-done" in br_ and done.returncode == 0 and nt.get("done") is True and nt.get("fix") == "the marks breathe now"
        else "FAIL", "live notes left while no run was going are in a first run's brief (with the song context and their ids); "
        "studio.py note-done marks one done with its fix", f"{done.stdout.strip() or done.stderr.strip()}; {nt}")
    shutil.rmtree(out, ignore_errors=True)

def queue_check():
    """The render queue with its own lock file: while the test holds the queue a render waits (and says for whom),
    a render with MV_RENDER_LOCK=0 runs anyway; a lock left by a process that is gone is taken over at once."""
    import threading
    from mv import renderlock
    lockf = os.path.join(PDIR, "cache", "_test-render.lock")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MV_RENDER_LOCK_FILE=lockf)
    scene = os.path.relpath(os.path.join(TESTS, "scenes", "field.js"), PDIR)
    cmd = lambda out: [PY, os.path.join(STUDIO, "tools", "studio.py"), "run", P, "web-engine/render.py", scene, "--frames", "0:20",  # noqa: E731
                       "--preview", "--fresh", "--out", f"work/renders/{out}.mp4"]
    was = os.environ.get("MV_RENDER_LOCK_FILE")
    os.environ["MV_RENDER_LOCK_FILE"] = lockf
    lines, waited, a_waits, b = [], False, False, None
    try:
        with renderlock.render_queue("holding it for the test", who="the smoke test", log=lambda s: None):
            a = subprocess.Popen(cmd("_queue_a"), cwd=STUDIO, env=dict(env, MV_RENDER_WHO="render A"), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            th = threading.Thread(target=lambda: [lines.append(x) for x in a.stdout], daemon=True)
            th.start()
            t0 = time.time()
            while time.time() - t0 < 120 and a.poll() is None and not waited:
                waited = any("waiting for the render queue: the smoke test" in x for x in lines)
                time.sleep(0.3)
            b = subprocess.run(cmd("_queue_b"), cwd=STUDIO, env=dict(env, MV_RENDER_LOCK="0"), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=600)
            a_waits = a.poll() is None
        a.wait(timeout=600)
        th.join(10)
        dead = subprocess.Popen([PY, "-c", "pass"])
        dead.wait()
        open(lockf, "w").write(json.dumps({"pid": dead.pid, "who": "a killed render", "started": time.time(), "token": "t"}))
        said = []
        with renderlock.render_queue("after a crash", who="the smoke test", log=said.append):
            pass
    finally:
        if was is None:
            os.environ.pop("MV_RENDER_LOCK_FILE", None)
        else:
            os.environ["MV_RENDER_LOCK_FILE"] = was
    out = "".join(lines)
    say("PASS" if waited and a_waits and a.returncode == 0 and "my turn after" in out and b and b.returncode == 0
        and "waiting for the render queue" not in b.stdout and any("stale lock from a killed render" in x for x in said)
        and not os.path.exists(lockf) else "FAIL",
        "the render queue: a render waits while the queue is held (and says for whom), MV_RENDER_LOCK=0 doesn't wait, a "
        "dead holder's lock is taken over", " | ".join(x.strip() for x in lines if "queue" in x)[:240] + f"; {said}")


def watcher_check():
    """The runner's critic watcher choosing what to critique (its job stubbed: no critic.py runs): a ready final goes
    first and keeps the reserved last slot, a newer preview comes after it, no preview critique too late in the run or
    while it stops, and a preview critique still running gives way to a new final."""
    import director

    class W(director.CriticWatcher):
        def _run(self, job):
            self.done.append(job["video"])
            self.job = None
    d = os.path.join(PDIR, "cache", "_watcher")
    for sub in ("final", os.path.join("work", "previews")):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    fin, prev = os.path.join(d, "final", "music-video.mp4"), os.path.join(d, "work", "previews", "preview_1.mp4")
    started = time.time() - 1000
    for f, dt in ((fin, 10), (prev, 20)):
        open(f, "wb").write(b"x" * 100)
        os.utime(f, (started + dt, started + dt))

    def watch(n=0, rounds=4, **kw):
        w = W(d, started, "fake", "medium", most=6, settle=0)
        w.done, w.n = [], n
        for i in range(rounds):
            w.poll(1000.0 + 5 * i, **kw)
            time.sleep(0.05)                        # the stub's thread ends the job
        return w
    a = watch(n=5)                                  # one slot left: it's the final's, though the preview is newer
    b = watch()                                     # the final first, then the newer preview
    c = watch(left=200)                             # 3 min left: no preview critique (its notes couldn't reach the director)
    e = watch(stopping=True)
    f = W(d, started, "fake", "medium", most=6, settle=0)     # a preview critique running when the final is ready: it gives way
    f.done, f.n = [], 1
    f.job = {"key": f.key(prev), "video": "work/previews/preview_1.mp4", "proc": None}
    f.poll(1000.0)
    f.poll(1005.0)
    gave_way = f.job and f.job.get("superseded") and f.n == 0
    f.job = None
    f.poll(1010.0)
    time.sleep(0.05)
    passed = (a.passed(), b.passed(), c.passed(), a.passed())
    say("PASS" if a.done == ["final/music-video.mp4"] and a.n == 6 and b.done == ["final/music-video.mp4", "work/previews/preview_1.mp4"]
        and c.done == ["final/music-video.mp4"] and e.done == [] and gave_way and f.done == ["final/music-video.mp4"]
        and passed == (["work/previews/preview_1.mp4"], [], [], []) else "FAIL",
        "the critic watcher: the final first (and the reserved slot), the newer preview after it, none too late or while stopping, "
        "a running preview critique gives way to a new final; a preview passed over because the critiques are used up is "
        "handed to the runner once (it tells the director)",
        f"{a.done} {b.done} {c.done} {e.done} {f.done} gave way: {bool(gave_way)}; passed over {passed}")
    # a cut that has a critique already (`plan assemble` made one) isn't critiqued again; one that is being made
    # (its pending marker) waits, and is critiqued after all when the other one gave up without a critique
    import critic
    p2, p3 = os.path.join(d, "work", "previews", "preview_2.mp4"), os.path.join(d, "work", "previews", "preview_3.mp4")
    for f, dt in ((p2, 30), (p3, 40)):
        open(f, "wb").write(b"y" * 120)
        os.utime(f, (started + dt, started + dt))
    os.makedirs(os.path.join(d, "work", "critic"), exist_ok=True)
    json.dump({"video": "work/previews/preview_2.mp4", "size": 120, "mtime": started + 30, "scores": {"ambition": 5},
               "file": "work/critic/preview_2_01.md"}, open(os.path.join(d, "work", "critic", "preview_2_01.json"), "w"))
    g = W(d, started, "fake", "medium", most=6, settle=0)
    g.done, g.handled = [], {g.key(fin), g.key(prev)}
    with critic.pending(d, p3):
        for i in range(3):
            g.poll(2000.0 + 5 * i)
            time.sleep(0.05)
        while_pending = list(g.done)
    for i in range(3, 5):
        g.poll(2000.0 + 5 * i)
        time.sleep(0.05)
    seen = g.take()
    say("PASS" if while_pending == [] and g.done == ["work/previews/preview_3.mp4"] and g.key(p2) in g.handled
        and [(x.get("video"), x.get("seen")) for x in seen] == [("work/previews/preview_2.mp4", True)] else "FAIL",
        "the critic watcher: a cut with a critique already (plan assemble's) isn't critiqued again but goes to the runner "
        "for the run's record (marked seen); one being made waits for it (and is critiqued after all when that one left "
        "none)", f"while pending {while_pending}; after {g.done}; handed on {[(x.get('video'), x.get('seen')) for x in seen]}")
    # an older preview still waiting isn't critiqued after a newer cut has its critique (or is getting one) elsewhere
    json.dump({"video": "work/previews/preview_3.mp4", "size": 120, "mtime": started + 40, "scores": {"ambition": 6},
               "file": "work/critic/preview_3_01.md"}, open(os.path.join(d, "work", "critic", "preview_3_01.json"), "w"))
    h = W(d, started, "fake", "medium", most=6, settle=0)
    h.done, h.handled = [], {h.key(fin)}
    for i in range(3):
        h.poll(3000.0 + 5 * i)
        time.sleep(0.05)
    k = W(d, started, "fake", "medium", most=6, settle=0)     # the same with the newer one still being critiqued
    k.done, k.handled = [], {k.key(fin), k.key(p2), k.key(p3)}
    p4 = os.path.join(d, "work", "previews", "preview_4.mp4")
    open(p4, "wb").write(b"z" * 130)
    os.utime(p4, (started + 50, started + 50))
    with critic.pending(d, p4):
        for i in range(3):
            k.poll(4000.0 + 5 * i)
            time.sleep(0.05)
    say("PASS" if h.done == [] and h.key(prev) in h.handled and k.done == [] and k.key(prev) in k.handled else "FAIL",
        "the critic watcher: an older preview still waiting is passed over once a newer cut has a critique, or is getting "
        "one, elsewhere", f"done {h.done} / {k.done}; passed over {h.key(prev) in h.handled} / {k.key(prev) in k.handled}")
    # the final is rewritten in place while the director iterates: it waits until the director's turn is over or it
    # has held still for `settle` s (a rewrite restarts the wait), or at once near the run's end; newer() names the
    # cuts written after one (for the "these notes are on an older cut" line)
    d2 = os.path.join(PDIR, "cache", "_watcher_settle")
    shutil.rmtree(d2, ignore_errors=True)
    os.makedirs(os.path.join(d2, "final"))
    os.makedirs(os.path.join(d2, "work", "previews"))
    fin2 = os.path.join(d2, "final", "music-video.mp4")
    open(fin2, "wb").write(b"x" * 100)
    os.utime(fin2, (started + 10, started + 10))

    def settle_watch(times, rewrite_at=None, **kw):
        w = W(d2, started, "fake", "medium", most=6, settle=180)
        w.done, got = [], []
        for t in times:
            if rewrite_at is not None and t == rewrite_at:
                open(fin2, "wb").write(b"x" * 110)
                os.utime(fin2, (started + 20, started + 20))
            w.poll(float(t), **kw)
            time.sleep(0.05)
            got.append(len(w.done))
        return w, got
    _, early = settle_watch([1000, 1005, 1010, 1100])                # still settling: nothing yet
    _, held = settle_watch([1000, 1005, 1190])                       # held still 3 min: critiqued
    open(fin2, "wb").write(b"x" * 100)
    os.utime(fin2, (started + 10, started + 10))
    _, rewritten = settle_watch([1000, 1005, 1100, 1190, 1300], rewrite_at=1100)   # the rewrite at 1100 restarts the wait
    _, idle = settle_watch([1000, 1005], idle=True)                  # the director's turn is over: at once
    _, late = settle_watch([1000, 1005], left=400)                   # near the end: at once
    w = W(d2, started, "fake", "medium", most=6, settle=180)
    k_old = (w.key(fin2)[0], 100, started + 10)
    newer = w.newer(k_old)
    say("PASS" if early == [0, 0, 0, 0] and held == [0, 0, 1] and rewritten == [0, 0, 0, 0, 1] and idle == [0, 1]
        and late == [0, 1] and newer == ["final/music-video.mp4"] and w.newer(w.key(fin2)) == [] else "FAIL",
        "the critic watcher: a final rewritten in place is critiqued once the director's turn is over or it has held "
        "still for the settle time (a rewrite restarts it), at once near the end; newer() names the cuts written since",
        f"early {early} held {held} rewritten {rewritten} idle {idle} late {late} newer {newer}")


def session_check():
    """The session plumbing (mv/session.py) with the stand-in, which answers like the real CLI (verified on Claude
    Code 2.1.311-2.1.312): messages sent while a turn runs join it (one result for all of them); a message to an idle
    session starts a turn of its own; a background agent keeps the session working after its turn ended (no turn is
    over then, and its progress is a sign of life), then the session starts a turn of its own; every turn starts with
    an init but only the first is "session started"; the cost is the latest result's running total (not a sum), the
    turns add up; a result over the --max-budget-usd cap says so."""
    print("== 6d. the session plumbing (stand-ins)", flush=True)
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    import mv.session as ms
    from mv.session import Session
    keep_cross = (ms.CROSS_S, ms.CROSS_SETTLE_S)
    ms.CROSS_S = 1.0                               # the first part: the joined messages went in long before the result
    d = os.path.join(PDIR, "cache", "_session")
    shutil.rmtree(d, ignore_errors=True)
    cmd = [PY, os.path.join(TESTS, "fake_claude.py"), "-p", "--input-format", "stream-json", "--output-format", "stream-json"]
    s = Session(cmd, PDIR, d, name="echo", env=dict(os.environ, FAKE_ECHO_S="2", PYTHONIOENCODING="utf-8")).start()

    def wait_turn(s, most=30):
        t0 = time.time()
        while time.time() - t0 < most and not s.turn_over():
            time.sleep(0.2)
        return s.turn_over()
    s.send("<!-- mv:echo --> one", "first")
    time.sleep(0.5)
    s.send("two", "second")
    s.send("three", "third")
    over1 = wait_turn(s)
    n1 = len(s.results)
    time.sleep(2.5)
    n1b = len(s.results)
    joined = [m.get("joined") for m in s.status["messages_sent"]]
    texts = " ".join(e["text"] for e in s.status["events"])
    s.ack()
    time.sleep(3)                                  # idle for a while (the studio renders, the critic looks): not "quiet"
    s.send("four", "fourth")
    busy = s.busy
    fresh = s.quiet() < 1 and not s.hung(time.time(), 2)
    over2 = wait_turn(s)
    n2 = len(s.results)
    s.ack()
    ms.CROSS_S, ms.CROSS_SETTLE_S = 10.0, 6.0      # a message that went in just before the result: wait for its own turn
    s.send("five", "fifth")
    time.sleep(0.5)
    s.send("six", "sixth (crosses the result)")
    t_res = time.time()
    while time.time() - t_res < 20 and len(s.results) < 3:
        time.sleep(0.1)
    t_res = time.time()
    early = s.turn_over(t_res + 3)
    over3 = wait_turn(s)
    late = time.time() - t_res >= 5
    ms.CROSS_S, ms.CROSS_SETTLE_S = keep_cross
    s.ack()
    s.stop("the test is done", wait=10)
    s.finish()
    say("PASS" if over1 and n1 == 1 and n1b == 1 and joined[:3] == [None, True, True] and "echo: two" in texts
        and "echo: three" in texts and busy and fresh and over2 and n2 == 2 and not early and over3 and late and not s.alive() else "FAIL",
        "the session: messages sent while it works join the running turn (one result); a message to an idle session starts one; "
        "an idle wait before a message isn't 'no sign of life'; a message just before a result waits for a turn of its own",
        f"results after the first turn {n1} (then {n1b}), joined {joined}, after the fourth {n2}; quiet at once {fresh}; "
        f"crossed: over at +3 s {early}, then {over3} after {late and '>= 5 s'}; alive {s.alive()}")
    # a background agent: the turn ends while it works; the session is working, no turn is over, its progress is a sign
    # of life; then the turn the session starts by itself; the costs as running totals
    d2 = os.path.join(PDIR, "cache", "_session_agent")
    shutil.rmtree(d2, ignore_errors=True)
    s = Session(cmd, PDIR, d2, name="echo", env=dict(os.environ, FAKE_ECHO_S="1", FAKE_AGENT_S="5", PYTHONIOENCODING="utf-8")).start()
    s.send("<!-- mv:echo-agent --> go", "first")
    t0 = time.time()
    while time.time() - t0 < 30 and not s.results:
        time.sleep(0.1)
    time.sleep(2.5)                                # the turn is 2.5 s over, its agent still works
    mid = {"results": len(s.results), "busy": s.busy, "working": s.working, "over": s.turn_over(), "hung": s.hung(time.time(), 0.9),
           "agents": [(a["description"], a["model"], a["state"]) for a in s.status["agents"]]}
    over = wait_turn(s, 40)
    st = s.status
    evs = [e["text"] for e in st["events"]]
    s.stop("the test is done", wait=10)
    s.finish()
    raw = [json.loads(x) for x in open(os.path.join(d2, "events.jsonl"), encoding="utf-8") if x.strip()]
    res = [e for e in raw if e.get("type") == "result"]
    inits = sum(1 for e in raw if e.get("type") == "system" and e.get("subtype") == "init")
    a = (st.get("agents") or [{}])[0]
    ok_agent = (mid["results"] == 1 and not mid["busy"] and mid["working"] and not mid["over"] and not mid["hung"]
                and mid["agents"] == [("echo agent", "haiku", "running")] and over and len(res) == 2 and inits == 2
                and sum("session started" in x for x in evs) == 1 and any("started a turn of its own (its agent finished)" in x for x in evs)
                and any(x.startswith("🤖 agent: echo agent (haiku) started") for x in evs)
                and any(x.startswith("🤖 agent: echo agent (haiku) done in") for x in evs)
                and a.get("state") == "done" and a.get("ended") is not None and a["ended"] >= a["started"]
                and st["cost_usd"] == res[-1]["total_cost_usd"] < sum(r["total_cost_usd"] for r in res)
                and st["turns"] == sum(r["num_turns"] for r in res)
                and set(st["model_usage"]) == {"fake-echo", "claude-haiku-fake"})
    say("PASS" if ok_agent else "FAIL", "a background agent: the turn ends while it works (working, no turn over, its progress a "
        "sign of life), then the session's own turn; the agent in status and the live log; 'session started' once; the cost "
        "is the latest running total, the turns add up, the costs per model", f"mid {mid}; {len(res)} results, {inits} inits; "
        f"cost {st['cost_usd']} (results {[r['total_cost_usd'] for r in res]}); turns {st['turns']}; agent {a}")
    # the cost cap: the turn that goes past it says so
    d3 = os.path.join(PDIR, "cache", "_session_cap")
    shutil.rmtree(d3, ignore_errors=True)
    s = Session(cmd + ["--max-budget-usd", "0.015"], PDIR, d3, name="echo", env=dict(os.environ, FAKE_ECHO_S="0.2",
                                                                                    PYTHONIOENCODING="utf-8")).start()
    s.send("<!-- mv:echo --> one", "first")
    first_hit = wait_turn(s) and s.status.get("budget_hit")
    s.ack()
    s.send("two", "second")
    wait_turn(s)
    s.stop("the test is done", wait=10)
    s.finish()
    say("PASS" if not first_hit and s.status.get("budget_hit") and any("cost cap" in e["text"] for e in s.status["events"]) else "FAIL",
        "the cost cap (--max-budget-usd): the result that goes past it marks the session (budget_hit)",
        f"after the first turn {first_hit}, after the second {s.status.get('budget_hit')}; ${s.status.get('cost_usd')}")


class FakeSession:
    """The director for the runner's checks: the test says when it ends a turn (over()), and reads what it was sent."""

    def __init__(self):
        import threading
        self.name, self.lock, self.closed, self.busy, self.returncode = "director", threading.RLock(), False, False, None
        self.status = {"events": [], "turns": 0, "cost_usd": 0.0, "messages_sent": [], "pid": 0, "agents": [], "model_usage": {}}
        self.sent, self._over, self._alive, self.proc, self.working = [], False, True, None, False
        self.inits = 1                           # (a session the runner starts itself: it has begun its first turn)

    def start(self):
        return self

    def send(self, text, why):
        if self.closed or not self._alive:
            return False
        self.sent.append((why, text))
        self.status["messages_sent"].append({"t": 0, "why": why})
        return True

    def over(self):
        self._over = True

    def turn_over(self, now=None, settle=2):
        return self._over and not self.working

    def ack(self):
        self._over = False

    def alive(self):
        return self._alive and not self.closed

    def hung(self, now, after):
        return False

    def note(self, line):
        self.status["events"].append({"t": 0, "text": line})

    def close_input(self):
        self.closed = True

    def stop(self, reason=None, wait=20):
        self.closed = True

    def finish(self):
        pass

    def whys(self):
        return [w for w, _ in self.sent]


def runner_check():
    """The runner (director.Runner) in-process on a simulated clock with a scripted director: while its agents work
    (the session working after its turn ended) no reminder, no nudge and no closing; when they're done and the turn is
    over, the reminder comes; the user's live notes reach it at once (with the command that marks them done) and are
    marked forwarded, a note from before the run or one done already isn't sent, a stopping run sends none; the cost
    cap reached ends the run with no more messages and the session closed once its turn is over; report.md's run
    settings: the costs per model (the director's, the agents', Claude Code's own small calls unlabelled; under half a
    cent as <$0.01: director.cost_rows, as the web app's run view shows them), the total, the agents, ultracode, the
    cap."""
    print("== 6e2. the runner (in-process, a scripted director)", flush=True)
    import director
    root = os.path.join(PDIR, "cache", "_runner")

    def make(budget=3600, **st):
        shutil.rmtree(root, ignore_errors=True)
        for sub in ("work/director/run-1", "final", "review"):
            os.makedirs(os.path.join(root, *sub.split("/")), exist_ok=True)
        s = FakeSession()
        t0 = time.time()
        status = dict({"run": 1, "state": "running", "kind": "first", "model": "fake", "effort": "max", "budget_s": budget,
                       "started": t0, "deadline": t0 + budget, "critiques": [], "critic_cost_usd": 0.0, "critic_model": "off"},
                      **st) | {"events": s.status["events"], "messages_sent": s.status["messages_sent"], "agents": s.status["agents"]}
        s.status = status
        return director.Runner(root, 1, os.path.join(root, "work", "director", "run-1"), status, s, budget, t0, log=lambda x: None), s, t0
    out = {}
    # agents at work: nothing, then the reminder
    R, s, t0 = make()
    s.over()
    s.working = True                         # its turn ended; an agent of its still works
    for k in range(5):
        R.step(t0 + 60 + k)
    held = (s.whys(), s.closed)
    s.working = False
    R.step(t0 + 70)
    out["agents"] = (held, s.whys())
    ok_agents = held == ([], False) and s.whys() == ["no final yet"] and not s.closed
    # the user's live notes: a message at once; one from before the run, or done already, isn't sent; a stopping run sends none
    np = os.path.join(root, "review", "notes.json")

    def note(text, **kw):
        ns = json.load(open(np, encoding="utf-8")) if os.path.exists(np) else []
        ns.append(dict({"id": int(time.time() * 1000) + len(ns), "version": "live", "t": 12.5, "text": text, "done": False,
                        "scene": "work/scenes/s1/scene.js", "timeline": "plan.json"}, **kw))
        json.dump(ns, open(np, "w", encoding="utf-8"))
    marks = lambda: {n["text"]: n.get("forwarded_run") for n in json.load(open(np, encoding="utf-8"))}   # noqa: E731
    R, s, t0 = make()
    note("from before the run", id=int((t0 - 120) * 1000))
    note("done already", done=True)
    s.working = True                         # (its agents working doesn't hold a note back: it starts a turn)
    note("more room before the drop")
    R.live_step(t0 + 10)
    solo = s.sent[-1] if s.sent else ("", "")
    R.stop_reason = "stopped by the user"
    note("never mind")
    R.live_step(t0 + 20)
    out["live"] = (solo[0], marks())
    ok_live = (solo[0] == "the user's live note (1)" and "Then carry on" in solo[1] and "they saw work/scenes/s1/scene.js" in solo[1]
               and "studio.py note-done" in solo[1] and len(s.sent) == 1
               and marks() == {"from before the run": None, "done already": None, "more room before the drop": 1, "never mind": None})
    # the cost cap reached: the run ends (no more messages; closed when its turn is over)
    R, s, t0 = make(max_usd=5.0)
    R.step(t0 + 30)
    s.status["budget_hit"] = True
    alive1 = R.step(t0 + 31)
    sent_cap = list(s.whys())
    s.over()
    R.step(t0 + 32)
    out["cap"] = (R.stop_reason, sent_cap, s.closed, alive1)
    ok_cap = R.stop_reason == "the cost cap ($5) was reached" and sent_cap == [] and s.closed and R.deadline <= t0 + 31
    # report.md's run settings
    rs = {"run": 3, "kind": "first", "model": "opus", "model_used": "claude-opus-fake", "effort": "max", "ultracode": True,
          "max_usd": 5.0, "budget_hit": True, "budget_s": 3600, "started": time.time(), "wall_s": 1800, "stop_reason": "the cost cap ($5) was reached",
          "claude_version": "test", "turns": 12, "cost_usd": 4.22,
          "model_usage": {"claude-opus-fake": {"cost_usd": 4.1}, "claude-haiku-fake": {"cost_usd": 0.12},
                          "claude-quick-fake": {"cost_usd": 0.003}},
          "agents": [{"description": "build s3", "model": "haiku", "state": "done", "started": 100, "ended": 340}],
          "critic_model": "sonnet", "critic_effort": "medium", "critic_cost_usd": 0.4, "critiques": [{}, {}]}
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    open(os.path.join(root, "report.md"), "w", encoding="utf-8").write("# Report\nThe director's own words.\n")
    director.append_settings(root, rs, None)
    rep = open(os.path.join(root, "report.md"), encoding="utf-8").read()
    out["report"] = [x for x in rep.splitlines() if x.startswith("- ")]
    ok_rep = ("The director's own words." in rep and "ultracode on; cost cap $5 (reached: the run ended there)" in rep
              and "- Costs per model: opus-fake $4.10 (the director), haiku-fake $0.12 (agents), quick-fake <$0.01 · the critic "
                  "sonnet $0.40 · total $4.62" in rep
              and "- Agents: 1 (haiku 1): build s3 (haiku, done in 4 min)" in rep and "cost reported $4.22 (the director's session, its agents included)" in rep
              and [(r["name"], r["who"]) for r in director.cost_rows(rs)] == [("opus-fake", "the director"), ("haiku-fake", "agents"),
                                                                             ("quick-fake", "")]
              and director.total_cost(rs) == 4.62)
    shutil.rmtree(root, ignore_errors=True)
    bad = [k for k, v in (("agents at work", ok_agents), ("live notes", ok_live), ("the cost cap", ok_cap), ("the run settings", ok_rep)) if not v]
    say("FAIL" if bad else "PASS", "the runner: no reminder, nudge or closing while the director's agents work (then the reminder); "
        "the user's live notes at once (marked forwarded; old, done ones and a stopping run's not sent); the cost cap ends the "
        "run without more messages; report.md's run settings (costs per model, the agents, ultracode, the cap)",
        ("failed: " + "; ".join(bad) + f" {out}")[:2500] if bad else "")


def limit_check():
    """Claude's usage limit, in-process: reading it (mv/session.py reset_at and limit_of on the shapes Claude Code
    uses; not a temporary throttle, not the director's own words, not the cost cap); a real Session with the stand-in
    (one hit per refused turn, its reset from the rate_limit_event, nothing spent on it, "close to its limit" once; a
    --resume Claude Code doesn't have ends before its first turn); the runner on a simulated clock: with
    auto_continue it pauses (nothing sent, no reminder, no live note, the clock stopped), carries on at the reset with
    the deadline moved by the wait, and a session that ended meanwhile is resumed (--resume) or, when that can't be,
    started fresh with a brief that says where the work is; no reset time: it tries again later; a reset too far away
    or no auto_continue: the run ends with what was left (cut_off); stopped while it waits: it ends;
    continuable() and wait_for_reset() for `direct --continue`."""
    print("== 6e3. Claude's usage limit (in-process)", flush=True)
    import datetime as dt
    import director
    from mv.session import Session, reset_at, limit_of
    bad, out = [], {}
    now = time.time()
    # reading it
    r = {k: reset_at(k, now) for k in ("Claude AI usage limit reached|1762178400", "resets in 2h 15m", "resets 3pm",
                                       "resets 18:05Z", "resets 3pm (Europe/Brussels)", "resets Mon 10am", "resets Nov 4, 10am",
                                       "no time here", "resets Feb 30, 1am")}
    loc = lambda t: dt.datetime.fromtimestamp(t)                     # noqa: E731
    try:
        from zoneinfo import ZoneInfo
        bxl = dt.datetime.fromtimestamp(r["resets 3pm (Europe/Brussels)"], ZoneInfo("Europe/Brussels"))
    except Exception:                                                # (no tz database: the machine's own time)
        bxl = loc(r["resets 3pm (Europe/Brussels)"])
    utc = dt.datetime.fromtimestamp(r["resets 18:05Z"], dt.timezone.utc)
    ok_read = (r["Claude AI usage limit reached|1762178400"] == 1762178400 and r["resets in 2h 15m"] == now + 8100
               and (loc(r["resets 3pm"]).hour, loc(r["resets 3pm"]).minute) == (15, 0) and -60 < r["resets 3pm"] - now <= 86400
               and (utc.hour, utc.minute) == (18, 5) and (bxl.hour, bxl.minute) == (15, 0)
               and loc(r["resets Mon 10am"]).weekday() == 0 and loc(r["resets Mon 10am"]).hour == 10
               and -60 < r["resets Mon 10am"] - now <= 7 * 86400
               and (loc(r["resets Nov 4, 10am"]).month, loc(r["resets Nov 4, 10am"]).day) == (11, 4)
               and r["no time here"] is None and r["resets Feb 30, 1am"] is None)
    syn = lambda text, **kw: dict({"type": "assistant", "parent_tool_use_id": None,           # noqa: E731
                                   "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}}, **kw)
    hit = limit_of(syn("You've hit your session limit · resets 3pm", error="rate_limit"), None, now)
    typed = limit_of(syn("Usage limit reached", api_error="usage_limit_reached",
                         api_error_params={"rate_limit_info": {"resetsAt": int(now) + 900, "rateLimitType": "seven_day"}}), None, now)
    by_event = limit_of(syn("You've hit your session limit · resets 3pm", error="rate_limit"),
                        {"resets_at": now + 1234, "kind": "five_hour", "t": now - 5}, now)
    old = limit_of({"type": "result", "is_error": True, "result": "Claude AI usage limit reached|1762178400"}, None, now)
    nots = [limit_of(syn("API Error: Server is temporarily limiting requests (not your usage limit)", error="rate_limit"), None, now),
            limit_of({"type": "assistant", "message": {"model": "opus", "content": [{"type": "text", "text": "I'll keep it under the usage limit"}]}}, None, now),
            limit_of({"type": "result", "is_error": False, "result": "You've hit your session limit"}, None, now),
            limit_of({"type": "result", "is_error": True, "subtype": "error_max_budget_usd", "result": "Budget limit reached"}, None, now),
            limit_of(syn("Credit balance is too low", error="billing_error"), None, now),
            limit_of(syn("You've hit your limit", api_error="model_requires_usage_credits"), None, now)]
    ok_of = (hit and hit["resets_at"] == r["resets 3pm"] and typed and typed["resets_at"] == int(now) + 900
             and typed["kind"] == "seven_day" and by_event and by_event["resets_at"] == now + 1234 and by_event["kind"] == "five_hour"
             and old and old["resets_at"] == 1762178400 and nots == [None] * len(nots))
    out["read"] = (r, hit, typed, by_event, old, nots)
    if not ok_read:
        bad.append("reset_at")
    if not ok_of:
        bad.append("limit_of")
    # a real Session with the stand-in: the 2nd message is refused, it resets 2 s later
    cmd = [PY, os.path.join(TESTS, "fake_claude.py"), "-p", "--input-format", "stream-json", "--output-format", "stream-json"]
    d = os.path.join(PDIR, "cache", "_session_limit")
    shutil.rmtree(d, ignore_errors=True)
    s = Session(cmd, PDIR, d, name="echo", env=dict(os.environ, FAKE_ECHO_S="0.2", FAKE_LIMIT="2:2", FAKE_LIMIT_WARN="1",
                                                     PYTHONIOENCODING="utf-8")).start()

    def wait(most=20):
        t0 = time.time()
        while time.time() - t0 < most and not s.turn_over():
            time.sleep(0.1)
        s.ack()
    s.send("<!-- mv:echo --> one", "first")
    wait()
    cost1 = s.status["cost_usd"]
    t_hit = time.time()
    s.send("two", "second")
    wait()
    hits2, lim, cost2 = s.status["limit_hits"], dict(s.status.get("limit") or {}), s.status["cost_usd"]
    time.sleep(2.3)
    s.send("three", "third")
    wait()
    s.stop("the test is done", wait=10)
    s.finish()
    evs = [e["text"] for e in s.status["events"]]
    ok_sess = (hits2 == 1 and s.status["limit_hits"] == 1 and lim.get("kind") == "five_hour"
               and 0 < (lim.get("resets_at") or 0) - t_hit <= 3 and cost2 == cost1 and s.status["cost_usd"] > cost2
               and sum("⛔ Claude's usage limit stopped the echo" in x for x in evs) == 1
               and sum("close to its limit (90% of the 5-hour limit" in x for x in evs) == 1 and any("echo: three" in x for x in evs))
    d2 = os.path.join(PDIR, "cache", "_session_resume_bad")
    shutil.rmtree(d2, ignore_errors=True)
    s = Session(cmd + ["--resume", "nope"], PDIR, d2, name="director", env=dict(os.environ, FAKE_RESUME_BAD="1", PYTHONIOENCODING="utf-8")).start()
    s.send("carry on", "continuing")
    t0 = time.time()
    while time.time() - t0 < 10 and s.alive():
        time.sleep(0.1)
    s.finish()
    err = open(os.path.join(d2, "stderr.log"), encoding="utf-8").read()
    ok_bad = not s.alive() and s.inits == 0 and "No conversation found with session ID: nope" in err
    out["session"] = (hits2, lim, cost1, cost2, s.status.get("cost_usd"), [x for x in evs if "limit" in x])
    if not ok_sess:
        bad.append("a session with the stand-in")
    if not ok_bad:
        bad.append("a --resume that Claude Code doesn't have")
    # the runner on a simulated clock
    root = os.path.join(PDIR, "cache", "_runner_limit")

    def make(budget=3600, auto=True, factory=None, fallback=None):
        shutil.rmtree(root, ignore_errors=True)
        for sub in ("work/director/run-1", "final", "review"):
            os.makedirs(os.path.join(root, *sub.split("/")), exist_ok=True)
        s = FakeSession()
        t0 = time.time()
        status = {"run": 1, "state": "running", "kind": "first", "model": "fake", "effort": "max", "budget_s": budget,
                  "started": t0, "deadline": t0 + budget, "critiques": [], "critic_cost_usd": 0.0, "critic_model": "off",
                  "events": s.status["events"], "messages_sent": s.status["messages_sent"], "agents": s.status["agents"],
                  "limit_hits": 0, "session_id": "sid-1"}
        s.status = status
        R = director.Runner(root, 1, os.path.join(root, "work", "director", "run-1"), status, s, budget, t0, log=lambda x: None,
                            auto_continue=auto, new_session=factory, fallback=fallback)
        return R, s, t0

    def limit(R, t, resets_at):
        R.status["limit_hits"] += 1
        R.status["limit"] = {"text": "You've hit your session limit · resets 3pm", "resets_at": resets_at, "kind": "five_hour", "at": t}
    # 1. auto_continue: it waits (nothing sent, no reminder, no live note), then carries on with the clock moved
    R, s, t0 = make()
    limit(R, t0 + 100, t0 + 600)
    s.over()                                      # (the turn the limit ended)
    a1 = R.step(t0 + 100)
    st1 = (R.status["state"], dict(R.status.get("paused") or {}))
    np = os.path.join(root, "review", "notes.json")
    json.dump([{"id": int((t0 + 1) * 1000), "version": "live", "t": 3.0, "text": "a note while it waits", "done": False}],
              open(np, "w", encoding="utf-8"))
    R.live_step(t0 + 300)
    a2 = R.step(t0 + 400)
    held = list(s.whys())
    t_on = t0 + 600 + director.RESET_MARGIN_S + 1
    a3 = R.step(t_on)
    moved = R.deadline - (t0 + 3600)
    ok1 = (a1 and a2 and a3 and st1[0] == "paused" and st1[1].get("until") == t0 + 600 + director.RESET_MARGIN_S
           and st1[1].get("left_s") == 3500 and held == [] and s.whys() == ["the usage limit reset: carry on"]
           and "you still have 58 min" in s.sent[-1][1] and "deadline is now" in s.sent[-1][1]
           and abs(moved - (t_on - (t0 + 100))) < 1 and R.status["state"] == "running" and "paused" not in R.status
           and len(R.status["pauses"]) == 1 and R.status["pauses"][0]["end"] == "carried on"
           and abs(R.status["paused_s"] - (t_on - t0 - 100)) <= 1 and abs(R.active(t_on) - 100) < 1)
    R.live_step(t_on + 10)                        # (the note left while it waited goes in now)
    ok1 = ok1 and s.whys()[-1] == "the user's live note (1)"
    out["auto"] = (st1, held, s.whys(), moved, R.status.get("pauses"))
    if not ok1:
        bad.append("auto_continue: wait, then carry on")
    # 2. no reset time given: it tries again after RETRY_S; still refused: it waits again
    R, s, t0 = make()
    limit(R, t0 + 50, None)
    R.step(t0 + 50)
    p2 = dict(R.status.get("paused") or {})
    R.step(t0 + 50 + director.RETRY_S + 1)
    sent2 = list(s.whys())
    limit(R, t0 + 60 + director.RETRY_S, None)
    R.step(t0 + 60 + director.RETRY_S)
    ok2 = (p2.get("until") == t0 + 50 + director.RETRY_S and sent2 == ["the usage limit reset: carry on"]
           and R.status["state"] == "paused" and len(R.status["pauses"]) == 1)
    out["retry"] = (p2, sent2, R.status["state"])
    if not ok2:
        bad.append("no reset time: try again later")
    # 3. without auto_continue: the run ends there, with what was left
    R, s, t0 = make(auto=False)
    limit(R, t0 + 100, t0 + 600)
    s.over()
    R.step(t0 + 100)
    R.step(t0 + 101)
    cut3 = dict(R.cut or {})
    ok3 = ("Claude's usage limit was reached" in (R.stop_reason or "") and cut3.get("why") == "limit" and cut3.get("left_s") == 3500
           and cut3.get("resets_at") == t0 + 600 and s.whys() == [] and s.closed and R.status["state"] == "finishing")
    out["no auto"] = (R.stop_reason, cut3, s.whys(), s.closed)
    if not ok3:
        bad.append("without auto_continue: the run ends")
    # 4. auto_continue, but the reset is days away: it ends too
    R, s, t0 = make()
    limit(R, t0 + 100, t0 + 3 * 86400)
    R.step(t0 + 100)
    ok4 = R.pause is None and "too far to wait" in (R.stop_reason or "") and (R.cut or {}).get("why") == "limit"
    # 5. stopped by the user while it waits: it ends
    R, s, t0 = make()
    limit(R, t0 + 100, t0 + 600)
    R.step(t0 + 100)
    open(os.path.join(root, "work", "director", "STOP"), "w").write("x")
    a5 = R.step(t0 + 200)
    ok5 = a5 is False and R.stop_reason == "stopped by the user" and s.whys() == [] and (R.cut or {}).get("left_s") == 3500
    out["far, stop"] = (R.stop_reason, ok4, a5)
    if not ok4:
        bad.append("a reset too far away")
    if not ok5:
        bad.append("stopped while it waits")
    # 6. its session ended meanwhile: resumed (--resume sid-1); one Claude Code can't resume: a fresh session
    made = []

    def factory(good):
        def f(rid=None):
            x = FakeSession()
            x.status = R6.status
            x.inits = 1 if (good or rid is None) else 0
            x._alive = good or rid is None
            made.append((rid, x))
            return x
        return f
    for good in (True, False):
        made.clear()
        R6, s, t0 = make(factory=factory(good), fallback=lambda what, left, dl: f"FRESH BRIEF: This carries on {what}.")
        limit(R6, t0 + 100, t0 + 600)
        R6.step(t0 + 100)
        s._alive = False                          # (its session ended while it waited)
        R6.step(t0 + 700)
        new = made[-1][1] if made else None
        if good:
            ok6 = ([m[0] for m in made] == ["sid-1"] and R6.s is new and new.whys() == ["the usage limit reset: carry on"]
                   and "Claude's usage limit stopped you" in new.sent[0][1])
        else:
            ok6 = ([m[0] for m in made] == ["sid-1", None] and R6.s is new
                   and new.whys() == ["the brief (a fresh session: the conversation couldn't be resumed)"]
                   and "FRESH BRIEF: This carries on this run (run 1)" in new.sent[0][1]
                   and "FRESH BRIEF" in open(os.path.join(root, "work", "director", "run-1", "brief.md"), encoding="utf-8").read())
        out[f"relaunch {'good' if good else 'bad'}"] = ([m[0] for m in made], new.whys() if new else None)
        if not ok6:
            bad.append("its session resumed" if good else "a fresh session when it can't be resumed")
    # 7. continuable() and wait_for_reset(): the last run that started, what it had left, a reset still to come
    pd = os.path.join(PDIR, "cache", "_continue")
    shutil.rmtree(pd, ignore_errors=True)
    for k in (1, 2, 3):
        os.makedirs(os.path.join(pd, "work", "director", f"run-{k}"))
    t = time.time()
    json.dump({"run": 1, "state": "done", "kind": "first", "budget_s": 5400, "started": t - 4000, "wall_s": 3000, "left_s": 2400,
               "stop_reason": "Claude's usage limit was reached", "session_id": "sid-a", "cost_usd": 3.0,
               "cut_off": {"why": "limit", "at": t - 1000, "left_s": 2400, "resets_at": t + 900}, "result": "no final"},
              open(os.path.join(pd, "work", "director", "run-1", "status.json"), "w"))
    json.dump({"run": 2, "state": "stopped", "result": "not started", "kind": "continue"},
              open(os.path.join(pd, "work", "director", "run-2", "status.json"), "w"))
    json.dump({"run": 3, "state": "dry-run"}, open(os.path.join(pd, "work", "director", "run-3", "status.json"), "w"))
    json.dump({"run": 2, "state": "stopped", "dir": "work/director/run-2", "updated": t},
              open(os.path.join(pd, "work", "director", "current.json"), "w"))
    c = director.continuable(pd) or {}
    ok7 = (c.get("run") == 1 and c.get("left_s") == 2400 and c.get("budget_s") == 2400 and c.get("resets_at") == t + 900
           and c.get("limit") and c.get("cut") and c.get("resumable") and c.get("session_id") == "sid-a" and c.get("conv_cost_usd") == 3.0)
    # a run whose runner was cut off while it waited: what its clock said last, its reset
    json.dump({"run": 1, "state": "paused", "kind": "first", "budget_s": 3600, "started": t - 2000, "elapsed_s": 2000, "active_s": 1500,
               "updated": t - 600, "paused": {"since": t - 1100, "resets_at": t + 300, "until": t + 360, "left_s": 2100}},
              open(os.path.join(pd, "work", "director", "run-1", "status.json"), "w"))
    c2 = director.continuable(pd) or {}
    ok7 = ok7 and c2.get("left_s") == 2100 and c2.get("resets_at") == t + 300 and c2.get("limit") and c2.get("cut") \
        and "runner stopped" in (c2.get("why") or "") and not c2.get("resumable")
    st = {"run": 4, "budget": "40 min", "state": "waiting", "events": []}
    rd = os.path.join(pd, "work", "director", "run-4")
    os.makedirs(rd)
    t0 = time.time()
    w1 = director.wait_for_reset(pd, rd, st, t0 + 1.2, log=lambda x: None)
    waited = time.time() - t0
    cur = json.load(open(os.path.join(pd, "work", "director", "current.json"), encoding="utf-8"))
    open(os.path.join(pd, "work", "director", "STOP"), "w").write("x")
    t0 = time.time()
    w2 = director.wait_for_reset(pd, rd, st, t0 + 60, log=lambda x: None)
    ok7 = ok7 and w1 is True and 1.1 <= waited < 4 and cur.get("state") == "waiting" and cur.get("dir") == "work/director/run-4" \
        and w2 is False and time.time() - t0 < 5 and "waiting for Claude's usage limit to reset" in st["events"][0]["text"]
    out["continue"] = (c, c2, w1, round(waited, 1), cur.get("state"), w2)
    shutil.rmtree(pd, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)
    if not ok7:
        bad.append("continuable / wait_for_reset")
    say("FAIL" if bad else "PASS", "Claude's usage limit: read from Claude Code's own messages (its reset too; not a throttle, not the "
        "director's words); a session counts each refused turn once; the runner waits with the clock stopped and carries on "
        "(the same session, a resumed one, or a fresh one told where the work is), or ends the run with what was left; "
        "continuable / wait_for_reset", ("failed: " + "; ".join(bad) + f" {out}")[:3000] if bad else "")


def blender_shared_check(no_blender):
    """A Blender scene imports shared code: its own folder, work/scenes/lib/ and work/kit/ are on its sys.path (a kit
    module named like a studio tool, models.py, is the scene's own: the studio's tools aren't on that path); its
    render (the plan tools' render_item: run.py, then compose encode) comes back from the cache while nothing changed,
    and renders again from scratch when the kit, the lib or its own folder change (run.py's key; plan.current says
    stale)."""
    print("== 6f. Blender: shared code (work/scenes/lib/, work/kit/) and its cache", flush=True)
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    from blender.run import find_blender
    if no_blender or not find_blender():
        return say("SKIP", "Blender importing shared code", "--no-blender" if no_blender else "no Blender on this machine")
    import plan as plans
    files = {"work/kit/models.py": "def size():\n    return 1.2\n",
             "work/scenes/lib/blook.py": "COLOR = (0.9, 0.35, 0.2, 1.0)\n",
             "work/scenes/bshared/helper.py": "def height():\n    return 0.0\n",
             "work/scenes/bshared/scene.py": (
                 "import bpy\nimport blook                    # work/scenes/lib/\nfrom models import size        # work/kit/ (tools/models.py has the same name)\n"
                 "from helper import height       # its own folder\n\n\ndef build(mv):\n"
                 "    bpy.ops.mesh.primitive_cube_add(size=size(), location=(0, 0, height()))\n"
                 "    m = bpy.data.materials.new('m')\n    m.diffuse_color = blook.COLOR\n    bpy.context.active_object.data.materials.append(m)\n"
                 "    bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))\n"
                 "    bpy.ops.object.camera_add(location=(0, -6, 2), rotation=(1.3, 0, 0))\n    mv.scene.camera = bpy.context.active_object\n")}
    for f, txt in files.items():
        os.makedirs(os.path.dirname(os.path.join(PDIR, f)), exist_ok=True)
        open(os.path.join(PDIR, f), "w", encoding="utf-8").write(txt)
    fps = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))["format"]["fps"]
    it = {"id": "bshared", "kind": "segment", "name": "", "renderer": "blender", "scene": "work/scenes/bshared/scene.py",
          "scene_abs": os.path.join(PDIR, "work", "scenes", "bshared", "scene.py"), "exists": True, "a": 0, "b": 3, "t0": 0.0,
          "t1": 3 / fps, "render_args": ["--engine", "workbench"]}
    logf = os.path.join(PDIR, "work", "segments", "bshared", "render_preview.log")

    def run():
        r = plans.render_item(PDIR, it, "preview", log=lambda x: None)
        txt = open(logf, encoding="utf-8").read() if os.path.exists(logf) and r["state"] != "cached" else ""
        return r["state"], ("0 frames already there" in txt), r.get("error")
    first = run()
    cur0 = plans.current(PDIR, it, "preview")[0]
    again = run()
    steps = {}
    for f, more in (("work/kit/models.py", "# the kit changed\n"), ("work/scenes/lib/blook.py", "# the lib changed\n"),
                    ("work/scenes/bshared/helper.py", "# its folder changed\n")):
        time.sleep(1.1)                        # (a newer mtime than the render's)
        open(os.path.join(PDIR, f), "a", encoding="utf-8").write(more)
        stale = plans.current(PDIR, it, "preview")
        steps[f.split("/")[1] + "/" + f.split("/")[2]] = (stale[0], stale[1], run())
    ok = (first[0] == "rendered" and first[1] and cur0 and again[0] == "cached"
          and all(not x[0] and "shared code" in x[1] and x[2][0] == "rendered" and x[2][1] for x in steps.values()))
    say("PASS" if ok else "FAIL", "Blender scenes import shared code (their own folder, work/scenes/lib/, work/kit/, a kit models.py over the studio's); the render "
        "is cached while nothing changed, and made again from scratch when the kit, the lib or the scene's folder change",
        f"first {first}; current {cur0}; again {again}; {steps}")
    for f in files:
        os.remove(os.path.join(PDIR, f))
    for dd in ("work/kit", "work/scenes/bshared"):
        shutil.rmtree(os.path.join(PDIR, dd), ignore_errors=True)
    # a pitch's Blender scene imports the lib next to its folder (the pitch's scenes/lib/), and its cache key follows it
    from mv.common import scene_inputs
    rd0 = os.path.join(PDIR, "work", "pitches", "round-0")
    pd = os.path.join(rd0, "p1", "scenes")
    for sub in ("lib", "bproof"):
        os.makedirs(os.path.join(pd, sub), exist_ok=True)
    open(os.path.join(pd, "lib", "plook.py"), "w", encoding="utf-8").write("COLOR = (0.2, 0.5, 0.9, 1.0)\n")
    open(os.path.join(pd, "bproof", "scene.py"), "w", encoding="utf-8").write(
        "import bpy\nimport plook                    # the pitch's scenes/lib/\n\n\ndef build(mv):\n"
        "    bpy.ops.mesh.primitive_cube_add(size=1.0)\n    m = bpy.data.materials.new('m')\n    m.diffuse_color = plook.COLOR\n"
        "    bpy.context.active_object.data.materials.append(m)\n"
        "    bpy.ops.object.camera_add(location=(0, -6, 2), rotation=(1.3, 0, 0))\n    mv.scene.camera = bpy.context.active_object\n")
    sc = os.path.join(pd, "bproof", "scene.py")
    ins = [os.path.relpath(x, PDIR).replace("\\", "/") for x in scene_inputs(sc, PDIR)]
    r = studio("run", P, "blender/run.py", "work/pitches/round-0/p1/scenes/bproof/scene.py", "--still", "0.1", "--engine", "workbench",
               "--out", "work/pitches/round-0/p1/style", check=False)
    pics = glob.glob(os.path.join(rd0, "p1", "style", "*.png"))
    say("PASS" if r.returncode == 0 and pics and "work/pitches/round-0/p1/scenes/lib/plook.py" in ins else "FAIL",
        "a pitch's Blender scene imports its own scenes/lib/ (the lib next to its folder is on its path, and in its cache key)",
        f"exit {r.returncode}; {len(pics)} still(s); inputs {ins}" + ("" if r.returncode == 0 else (r.stdout + r.stderr)[-800:]))
    shutil.rmtree(rd0, ignore_errors=True)


def agents_run_check(env):
    """`studio.py direct` with a stand-in director that deploys an agent (FAKE_DIRECTOR=agents) and uses the plan tools,
    with --ultracode and --max-usd: its turn ends while the agent builds s3, and the runner sends it no reminder or nudge
    and doesn't close the session; the session's own turn assembles and delivers; the agent in status.json and the live
    log; the cost the latest running total (not a sum), the costs per model and the agents in report.md; the critic
    looks at the assembled preview and the final once (plan assemble's critiques; the watcher doesn't look again; the
    version reuses the final's); a live note left while the run goes reaches the director and is marked done."""
    print("== 7b. a director that deploys an agent (stand-in for Claude Code)", flush=True)
    import threading
    from mv.live import update_notes
    flag = os.path.join(PDIR, "FAKE_DIRECTOR")
    open(flag, "w").write("agents")
    heard = os.path.join(PDIR, "work", "_fake_agent_heard.txt")
    if os.path.exists(heard):
        os.remove(heard)
    runs0 = (json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8")).get("runs") or [{}])[-1].get("run") or 0
    bars = [b["t"] for b in json.load(open(os.path.join(PDIR, "song", "song-map.json"), encoding="utf-8"))["bars"]]
    t_s2 = round((bars[len(bars) // 3] + bars[2 * len(bars) // 3]) / 2, 2)     # inside s2 (the stand-in's plan)
    wrote = {}

    def user():                                    # the user plays the scenes live while the director works, and leaves a note
        t0_ = time.time()
        while time.time() - t0_ < 600:
            cur = json.load(open(os.path.join(PDIR, "work", "director", "current.json"), encoding="utf-8")) \
                if os.path.exists(os.path.join(PDIR, "work", "director", "current.json")) else {}
            if (cur.get("run") or 0) > runs0 and cur.get("state") == "running" and any(a.get("state") == "running" for a in cur.get("agents") or []):
                n = {"id": int(time.time() * 1000), "version": "live", "t": t_s2, "text": "the field should breathe here (live)",
                     "done": False, "at": time.strftime("%Y-%m-%d %H:%M"), "scene": "work/scenes/s2/scene.js", "timeline": "plan.json"}
                update_notes(PDIR, lambda ns: ns.append(n))
                wrote.update(n)
                return
            time.sleep(0.2)
    th = threading.Thread(target=user, daemon=True)
    th.start()
    t0 = time.time()
    try:
        r = studio("direct", P, "--budget", "30m", "--model", "fake", "--critic-model", "fake", "--ultracode", "--max-usd", "50",
                   "--no-nudge", check=False, env=dict(env, FAKE_AGENT_S="12"), timeout=2400)
    finally:
        os.remove(flag)
    th.join(5)
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    last = (pj.get("runs") or [{}])[-1]
    rdir = os.path.join(PDIR, "work", "director", f"run-{last.get('run')}")
    rs = json.load(open(os.path.join(rdir, "status.json"), encoding="utf-8"))
    v = next((x for x in pj.get("versions") or [] if x.get("n") == last.get("version")), {})
    rep_md = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
    ag = (rs.get("agents") or [{}])[0]
    evs = [e["text"] for e in rs.get("events") or []]
    during = [m["why"] for m in rs.get("messages_sent") or [] if ag.get("started") is not None and ag.get("ended") is not None
              and ag["started"] <= m["t"] <= ag["ended"]]
    say("PASS" if r.returncode == 0 and last.get("version") and v.get("checks") and all(x == "PASS" for x in v["checks"].values())
        and "the test stand-in with an agent" in rep_md and ag.get("description") == "build s3" and ag.get("model") == "haiku"
        and ag.get("state") == "done" and ag["ended"] - ag["started"] >= 10
        and not [w for w in during if w in ("no final yet",) or w.startswith("use the time left")]
        and any(x.startswith("🤖 agent: build s3 (haiku) started") for x in evs) and any(x.startswith("🤖 agent: build s3 (haiku) done in") for x in evs)
        and any("↳ build s3" in x for x in evs) and any("started a turn of its own (its agent finished)" in x for x in evs) else "FAIL",
        "a director that deploys an agent: its turn ends while the agent works, the runner waits (no reminder, no nudge, the "
        "session open), the session's own turn delivers; the agent in status.json and the live log",
        f"run {last.get('run')}: v{last.get('version')} {v.get('checks')}; agent {ag}; messages while it worked {during}; "
        f"{time.time() - t0:.0f} s; ended: {last.get('stop_reason')}")
    raw = [json.loads(x) for x in open(os.path.join(rdir, "events.jsonl"), encoding="utf-8") if x.strip()]
    res = [e for e in raw if e.get("type") == "result"]
    mu = rs.get("model_usage") or {}
    cmd = json.load(open(os.path.join(rdir, "command.json"), encoding="utf-8"))["cmd"]
    tools = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
    brief = open(os.path.join(rdir, "brief.md"), encoding="utf-8").read()
    say("PASS" if res and rs.get("cost_usd") == res[-1]["total_cost_usd"] and len(res) >= 2
        and rs["cost_usd"] < round(sum(x["total_cost_usd"] for x in res), 4) and rs.get("turns") == sum(x["num_turns"] for x in res)
        and set(mu) >= {"fake-agentdirector", "claude-haiku-fake"} and "(agents)" in rep_md and "(the director)" in rep_md
        and "- Agents: 1 (haiku 1): build s3 (haiku, done" in rep_md and "ultracode on; cost cap $50 (not reached)" in rep_md
        and {"Agent", "TaskStop", "Workflow"} <= set(tools) and "SendMessage" not in tools and cmd[cmd.index("--max-budget-usd") + 1] == "50"
        and cmd[cmd.index("--effort") + 1] == "max" and "Ultracode is on for this run" in brief and "## You have full reign" in brief
        and last.get("costs") and last.get("agents") == 1 and last.get("ultracode") is True else "FAIL",
        "the costs: the session's latest running total (not a sum of its results), the turns added up, the costs per model "
        "(the director's, the agent's) and the agents in report.md; --ultracode (Workflow, the brief's line) and --max-usd on "
        "the command line; effort max by default",
        f"cost {rs.get('cost_usd')} (results {[x['total_cost_usd'] for x in res]}); turns {rs.get('turns')}; models {mu and list(mu)}; "
        f"{next((x for x in rep_md.splitlines() if x.startswith('- Costs per model')), '-')}; tools {tools}")
    cr = [c for c in rs.get("critiques") or [] if c.get("video", "").startswith("work/previews/")]
    fin = [c for c in rs.get("critiques") or [] if c.get("video") == "final/music-video.mp4"]
    log = [json.loads(x) for x in open(os.path.join(PDIR, "work", "critic", "log.jsonl"), encoding="utf-8") if x.strip()]
    t_run = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rs["started"]))
    mine = [d for d in log if str(d.get("at") or "") >= t_run]
    vc = (v.get("critic") or {})
    say("PASS" if len(cr) == 1 and cr[0].get("used") == "the director ran it" and len(fin) == 1 and len(mine) == 2
        and vc.get("from") == fin[0].get("file") and os.path.exists(os.path.join(PDIR, "final", f"critic_v{last.get('version')}.md"))
        and not [m for m in rs.get("messages_sent") or [] if m["why"].startswith("critic:")]
        and any(x.startswith("🔍 critic on work/previews/") for x in evs) else "FAIL",
        "the critic looks once at each cut: plan assemble's critiques of the preview and the final (the watcher doesn't look "
        "again, no second message, but they reach the run's record and live log while it runs), and the version reuses the "
        "final's",
        f"previews {[(c.get('video'), c.get('used')) for c in cr]}; final {[(c.get('file'), c.get('used')) for c in fin]}; "
        f"{len(mine)} critic calls; v{last.get('version')} critic from {vc.get('from')}")
    ln = next((n for n in json.load(open(os.path.join(PDIR, "review", "notes.json"), encoding="utf-8")) if n.get("id") == wrote.get("id")), {})
    md = os.path.join(rdir, "messages.md")
    msgs = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
    say("PASS" if wrote and ln.get("forwarded_run") == last.get("run") and "the field should breathe here (live)" in msgs
        and "segment s2 (s2 (smoke))" in msgs and ln.get("done") and "test stand-in" in (ln.get("fix") or "") else "FAIL",
        "a live note left while the agent works reaches the director within seconds (the song context, the plan's segment), "
        "is marked forwarded and done", f"forwarded_run {ln.get('forwarded_run')}, done {ln.get('done')}, fix {ln.get('fix')!r}; "
        f"heard while the agent worked: {open(heard).read().strip() if os.path.exists(heard) else '-'}")


def continue_run_check(env):
    """Claude's usage limit stops a run and it doesn't wait (the stand-in refuses its brief: FAKE_LIMIT=1:-1;
    --no-auto-continue): the run ends at once, no reminder sent into a session that can't answer, cut_off says the
    limit and what was left, report.md says so; then `direct --continue`: the same conversation (--resume), its first
    message the studio's "carry on" (how run N ended, the new clock), the budget what was left (at least 15 min), kind
    continue, delivered, its costs its own."""
    print("== 7f. a run Claude's usage limit stops, then Continue the last run (stand-in)", flush=True)
    dd = os.path.join(PDIR, "work", "director")

    def last():
        cur = json.load(open(os.path.join(dd, "current.json"), encoding="utf-8"))
        rd = os.path.join(PDIR, cur["dir"])
        return rd, json.load(open(os.path.join(rd, "status.json"), encoding="utf-8"))
    t0 = time.time()
    r = studio("direct", P, "--budget", "6m", "--effort", "low", "--model", "fake", "--critic-model", "off",
               "--no-auto-continue", check=False, env=dict(env, FAKE_LIMIT="1:-1"), timeout=900)
    wall = time.time() - t0
    rd, st = last()
    rep = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
    cut = st.get("cut_off") or {}
    ok1 = (r.returncode == 0 and cut.get("why") == "limit" and st.get("result") == "no final" and 300 <= (st.get("left_s") or 0) <= 360
           and "Claude's usage limit was reached" in (st.get("stop_reason") or "") and st.get("limit_hits") == 1
           and [m["why"] for m in st.get("messages_sent") or []] == ["the brief"] and "of the budget left" in rep
           and "Continue by itself when the usage limit resets: off (the limit stopped this run)" in rep and wall < 300)
    say("PASS" if ok1 else "FAIL", "Claude's usage limit stops a run that doesn't wait for it: it ends at once (no reminder into a "
        "session that can't answer), with what was left (cut_off), and report.md says so",
        f"wall {wall:.0f} s; {st.get('stop_reason')}; cut_off {cut}; left {st.get('left_s')}; messages "
        f"{[m['why'] for m in st.get('messages_sent') or []]}" + ("" if ok1 else f"; {(r.stdout + r.stderr)[-800:]}"))
    n1 = st.get("run")
    r = studio("direct", P, "--continue", "--effort", "low", "--model", "fake", "--critic-model", "off", check=False, env=env, timeout=1800)
    rd, st = last()
    cmd = json.load(open(os.path.join(rd, "command.json"), encoding="utf-8"))
    msgs = open(os.path.join(rd, "messages.md"), encoding="utf-8").read() if os.path.exists(os.path.join(rd, "messages.md")) else ""
    res = [json.loads(x) for x in open(os.path.join(rd, "events.jsonl"), encoding="utf-8") if x.strip() and '"result"' in x]
    res = [x for x in res if x.get("type") == "result"]
    co = st.get("continues") or {}
    c0 = cmd.get("cmd") or []
    rep = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
    ok2 = (r.returncode == 0 and st.get("kind") == "continue" and co.get("run") == n1 and co.get("resumed") is True
           and "--resume" in c0 and c0[c0.index("--resume") + 1] == "fake" and len(cmd.get("launches") or []) == 1
           and st.get("budget_s") == 900 and st.get("version") and f"(continuing run {n1})" in msgs
           and f"Your run {n1} ended at" in msgs and "Claude's usage limit was reached" in msgs and "the deadline is **" in msgs
           and res and st.get("cost_usd") == res[-1]["total_cost_usd"] and f"- Continues run {n1} (its conversation, resumed" in rep)
    say("PASS" if ok2 else "FAIL", "Continue the last run (direct --continue): its own conversation (--resume), the studio's "
        "'carry on' first (how it ended, the new clock), the budget it had left (at least 15 min), delivered; its costs its own",
        f"run {st.get('run')} ({st.get('kind')}): continues {co}; budget {st.get('budget_s')} s; version v{st.get('version')}; "
        f"resume {c0[c0.index('--resume') + 1] if '--resume' in c0 else None}; cost {st.get('cost_usd')}"
        + ("" if ok2 else f"; {(r.stdout + r.stderr)[-800:]}"))


def flags_check():
    """`studio.py direct --dry-run`: the command line and the brief without starting Claude: effort max and the agent
    tools by default, no Workflow and no ultracode line without --ultracode, no cap without --max-usd."""
    r = studio("direct", P, "--dry-run", "--model", "fake", "--budget", "20m", check=False, env={"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")})
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    n = max(int(x.split("-")[1]) for x in os.listdir(os.path.join(PDIR, "work", "director")) if re.fullmatch(r"run-\d+", x))
    rdir = os.path.join(PDIR, "work", "director", f"run-{n}")
    cmd = json.load(open(os.path.join(rdir, "command.json"), encoding="utf-8"))["cmd"]
    brief = open(os.path.join(rdir, "brief.md"), encoding="utf-8").read()
    st = json.load(open(os.path.join(rdir, "status.json"), encoding="utf-8"))
    tools = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
    say("PASS" if r.returncode == 0 and st.get("state") == "dry-run" and cmd[cmd.index("--effort") + 1] == "max"
        and {"Agent", "TaskStop"} <= set(tools) and "Workflow" not in tools and "SendMessage" not in tools and "--max-budget-usd" not in cmd
        and "## You have full reign" in brief and "Ultracode is on" not in brief and "nothing here requires agents" in brief
        and not (pj.get("runs") or [{}])[-1].get("run") == n else "FAIL",
        "direct --dry-run: effort max and the agent tools by default; without --ultracode no Workflow and no opt-in line; "
        "without --max-usd no cap; the brief's 'You have full reign'", f"tools {tools}; effort {cmd[cmd.index('--effort') + 1]}")
    r2 = studio("direct", P, "--dry-run", "--model", "fake", "--budget", "20m", "--critic-model", "off", check=False,
                env={"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")})
    reign = r2.stdout.split("## You have full reign", 1)[-1]
    say("PASS" if r2.returncode == 0 and "the looking is yours" in reign and "studio.py critique" not in reign
        and "the looking is the critic's" not in reign.lower() else "FAIL",
        "direct --dry-run with the critic off: the brief leaves the looking to the director, not to a critic it would "
        "have to call", reign[:300])
    # a run going on (one started from a terminal: its runner writes current.json every second): a second `direct`
    # (e.g. Build this one) and a pitch round are refused before anything is moved, archived or started
    cp = os.path.join(PDIR, "work", "director", "current.json")
    keep = open(cp, encoding="utf-8").read() if os.path.exists(cp) else None
    stop = os.path.join(PDIR, "work", "director", "STOP")
    vs0 = len(json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8")).get("versions") or [])
    runs0 = sorted(x for x in os.listdir(os.path.join(PDIR, "work", "director")) if re.fullmatch(r"run-\d+", x))
    fake = {"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")}
    try:
        json.dump({"run": 99, "state": "running", "started": time.time() - 60, "deadline": time.time() + 3000, "budget_s": 3600,
                   "updated": time.time()}, open(cp, "w", encoding="utf-8"))
        open(stop, "w").write("")                           # (the running one's STOP: not the second one's to delete)
        r3 = studio("direct", P, "--budget", "5m", "--model", "fake", "--critic-model", "off", check=False, env=fake)
        r4 = studio("pitch", P, "--n", "2", "--budget", "3m", "--model", "fake", "--critic-model", "fake", check=False, env=fake)
        stop_kept = os.path.exists(stop)
    finally:
        if keep is None:
            os.remove(cp)
        else:
            open(cp, "w", encoding="utf-8").write(keep)
        if os.path.exists(stop):
            os.remove(stop)
    vs1 = len(json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8")).get("versions") or [])
    runs1 = sorted(x for x in os.listdir(os.path.join(PDIR, "work", "director")) if re.fullmatch(r"run-\d+", x))
    say("PASS" if r3.returncode and "a director run is going on" in r3.stderr and r4.returncode
        and "a director run is going on" in r4.stderr and stop_kept and vs1 == vs0 and runs1 == runs0 else "FAIL",
        "a director run going on: a second `direct` and a pitch round are refused, nothing archived, no run folder, its STOP "
        "file left alone", f"direct: {r3.returncode} {r3.stderr.strip()[-120:]}; pitch: {r4.returncode} {r4.stderr.strip()[-120:]}; "
        f"versions {vs0} -> {vs1}; runs {len(runs0)} -> {len(runs1)}; STOP kept {stop_kept}")


def proof_window_check():
    """The part of the song a pitch round proves its ideas on (in-process, no Claude): the studio's pick; a section by
    name in any case (the same rule around its start); a-b in seconds or m:ss (the same window either way, on its
    frames); what the song can't have refused with why (BadWindow, no flag in the message: the web app shows it as it
    is; the command line puts --proof in front, before any round folder exists); the Pitch round page's preview (the
    render time; the queue's note only when the proofs crowd the budget); a dry-run round on a section: round.json says
    the user chose it, the brief says so in the user's voice ("the section I chose")."""
    print("== 6g. the proof window: the studio's pick or the user's (in-process)", flush=True)
    import pitch as pitches
    from mv.song import Song
    S = Song(PDIR)
    dur, bad, rd = S.duration, [], None
    mm = lambda t: f"{int(t // 60)}:{t % 60:05.2f}"
    auto = pitches.proof_window(PDIR)
    fps = auto["fps"]
    sec = S.sections[min(2, len(S.sections) - 1)]
    w_sec = pitches.proof_window(PDIR, sec["name"].upper())
    rule = pitches.window_around(S, float(sec["t0"]), fps, int(round(dur * fps)))
    if auto.get("by") != "studio" or auto.get("spec") is not None or "the user chose" in auto["why"]:
        bad.append(f"the studio's pick: {auto.get('by')} {auto.get('spec')} {auto['why']!r}")
    if (w_sec["a"], w_sec["b"]) != rule[:2] or w_sec["by"] != "user" or f"{sec['name']}, the section the user chose" not in w_sec["why"]:
        bad.append(f"section {sec['name']}: {w_sec['a']}:{w_sec['b']} vs the rule {rule[:2]}; {w_sec['by']} {w_sec['why']!r}")
    x, y = round(dur * 0.3, 2), round(dur * 0.3 + 15.5, 2)
    w1, w2 = pitches.proof_window(PDIR, f"{x}-{y}"), pitches.proof_window(PDIR, f"{mm(x)} – {mm(y)}")
    if (w1["a"], w1["b"]) != (w2["a"], w2["b"]) or (w1["a"], w1["b"]) != (int(round(x * fps)), int(round(y * fps))) or w2["by"] != "user" \
            or w2["why"] != "the part of the song the user chose":
        bad.append(f"{x}-{y} {w1['a']}:{w1['b']} vs {mm(x)}-{mm(y)} {w2['a']}:{w2['b']} ({w2['by']}, {w2['why']!r})")
    for spec, want in ((f"{dur - 5:.1f}-{dur - 20:.1f}", "the start first"), (f"0-{dur + 5:.0f}", "inside the song"), ("0-1", "at least 2 s"),
                       ("0:75-1:20", "m:ss"), ("no-such-part", "no section 'no-such-part'")):
        try:
            pitches.proof_window(PDIR, spec)
            bad.append(f"{spec}: taken")
        except pitches.BadWindow as e:
            if want not in str(e) or "--proof" in str(e):
                bad.append(f"{spec}: {e}")
    before = pitches.round_numbers(PDIR)
    try:
        pitches.Round(PDIR, n=2, budget="3m", model="fake", effort="low", critic_model="fake", proof="no-such-part", dry_run=True,
                      log=lambda s: None)
        bad.append("a round on a section the song doesn't have: started")
    except SystemExit as e:
        if not str(e).startswith("--proof no-such-part: no section") or pitches.round_numbers(PDIR) != before:
            bad.append(f"the command line's refusal: {e}; rounds {before} -> {pitches.round_numbers(PDIR)}")
    roomy, tight = pitches.window_preview(PDIR, None, 2, "60m"), pitches.window_preview(PDIR, f"0-{dur:.2f}", 5, "2m")
    if roomy["note"] or not roomy["estimate"] or roomy["window"] != auto or not tight["note"] or tight["all_s"] < 5 * tight["proof_s"] - 5:
        bad.append(f"the preview: roomy {roomy['estimate']} {roomy['note']!r}; tight {tight['estimate']} {tight['all_s']} {tight['note'][:60]!r}")
    was = os.environ.get("MV_CLAUDE")
    os.environ["MV_CLAUDE"] = os.path.join(TESTS, "fake_claude.py")
    try:
        d = pitches.pitch_round(PDIR, 2, "3m", "fake", "low", "fake", None, sec["name"], dry_run=True, log=lambda s: None)
        rd = os.path.join(PDIR, *d["dir"].split("/"))
        brief = open(os.path.join(rd, "sessions", "p1", "brief.md"), encoding="utf-8").read()
        if (d["proof"]["by"], d["proof"]["spec"], d["proof"]["a"], d["proof"]["b"]) != ("user", sec["name"], *rule[:2]) \
                or f"{sec['name']}, the section I chose" not in brief or "the user chose" in brief or f"--frames {rule[0]}:{rule[1]}" not in brief:
            bad.append(f"the dry-run round: {d['proof']}; brief: {[ln for ln in brief.splitlines() if 'proves itself' in ln][:1]}")
    finally:
        if was is None:
            os.environ.pop("MV_CLAUDE", None)
        else:
            os.environ["MV_CLAUDE"] = was
        if rd:
            shutil.rmtree(rd, ignore_errors=True)
    say("PASS" if not bad else "FAIL", "the proof window: the studio's pick; a section by name (any case, the rule around its "
        "start); a-b in seconds or m:ss (the same frames); what the song can't have refused with why (no flag in the message; "
        "the command line's --proof refusal before any round folder); the page's preview (render time, the queue's note when "
        "tight); a round on the user's section: round.json says so, the brief in the user's voice",
        "; ".join(bad)[:2500] if bad else f"studio {auto['t0']}-{auto['t1']}; {sec['name']} {w_sec['t0']}-{w_sec['t1']}; "
                                          f"preview {roomy['estimate']} per proof")


def pitch_check(env):
    """The pitch round with the stand-in for Claude Code (its <!-- mv:pitch --> and <!-- mv:verdict --> roles): two
    pitches at once, then the proofs with the song's audio, the critic on each, the verdict, the costs; the sandbox on
    each pitch's command line; then `direct --pitch` promotes the winner over the agents run's work (backups) and
    delivers; a promotion into an empty work/ in-process; and `pitch --auto` builds the winner in the same job."""
    print("== 7c. the pitch round (stand-in for Claude Code)", flush=True)
    import numpy as np
    import pitch as pitches
    import director
    from mv.common import decode_audio, probe, song_file
    from mv.song import Song
    t0 = time.time()
    envlog = os.path.join(PDIR, "work", "_fake_env.jsonl")
    if os.path.exists(envlog):
        os.remove(envlog)
    # started the way an interactive director would start it, from its own Bash: the session's markers in the
    # environment, which no session or call the round starts may inherit (it would take itself for a nested child)
    marked = dict(env, CLAUDECODE="1", CLAUDE_CODE_CHILD_SESSION="1", CLAUDE_CODE_SESSION_ID="s-test", CLAUDE_EFFORT="max",
                  CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH="1", FAKE_ENV_LOG=envlog)
    r = studio("pitch", P, "--n", "2", "--budget", "3m", "--model", "fake", "--critic-model", "fake", check=False, env=marked,
               timeout=1800)
    seen = [json.loads(x) for x in open(envlog, encoding="utf-8")] if os.path.exists(envlog) else []
    say("PASS" if {x["mode"] for x in seen} == {"session", "print"} and not any(x["markers"] for x in seen) else "FAIL",
        "started from inside a Claude Code session, the round's sessions and print calls (the critic, the verdict) are "
        "sessions of their own: none gets CLAUDECODE, CLAUDE_CODE_CHILD_SESSION, ...",
        f"{len(seen)} calls: {sorted({(x['mode'], tuple(x['markers'])) for x in seen})}")
    K = max(pitches.round_numbers(PDIR) or [0])
    rd = os.path.join(PDIR, "work", "pitches", f"round-{K}")
    R = json.load(open(os.path.join(rd, "round.json"), encoding="utf-8")) if K else {}
    ps, w = R.get("pitches") or [], R.get("proof") or {}
    S = Song(PDIR)
    fps = w.get("fps") or 30
    on_db = bool(w) and min(abs(int(round(d * fps)) - w["a"]) for d in S.downbeats) <= 1 or w.get("a") == 0
    say("PASS" if r.returncode == 0 and R.get("state") == "done" and len(ps) == 2 and all(p["state"] == "done" for p in ps)
        and all(p.get("pitch") and p.get("proof") and p.get("proof_av") and len(p.get("style") or []) == 2 for p in ps)
        and w and 12 <= w["seconds"] <= 24.05 and on_db and 0 <= w["t0"] and w["t1"] <= S.duration + 0.05
        and ps[0]["angle"] != ps[1]["angle"] and ps[0]["agents"] and ps[0]["agents"][0]["state"] == "done"
        and not R.get("warnings") else "FAIL",
        "studio.py pitch --n 2: both pitches delivered (pitch.md, style frames, proof.mp4), each its own angle, one with an "
        "agent; the proof window 12-24 s on a downbeat, inside the song",
        f"round {K}: {R.get('state')}; {[(p['k'], p['angle'], p['state'], p.get('error')) for p in ps]}; warnings {R.get('warnings')}; window "
        f"{w.get('t0')}-{w.get('t1')} ({w.get('seconds')} s): {w.get('why')}; {time.time() - t0:.0f} s"
        + ("" if r.returncode == 0 else (r.stdout + r.stderr)[-1500:]))
    # proof_av.mp4: the proof's video, the song's audio at the window
    sr, au = 48000, []
    for p in ps:
        if not p.get("proof_av"):
            au.append(None)
            continue
        av = os.path.join(PDIR, p["proof_av"])
        st = {x["codec_type"]: x for x in probe(av)["streams"]}
        a_ = decode_audio(av, sr=sr, mono=True)
        b_ = decode_audio(song_file(PDIR), sr=sr, mono=True, start=w["t0"], dur=w["seconds"])
        c_ = decode_audio(song_file(PDIR), sr=sr, mono=True, start=w["t0"] + 1.0, dur=w["seconds"])
        m = min(len(a_), len(b_), len(c_))
        x, y, z = a_[:m].astype(np.float64), b_[:m].astype(np.float64), c_[:m].astype(np.float64)
        nfft = 1 << int(np.ceil(np.log2(2 * m)))
        cc = np.fft.irfft(np.fft.rfft(x, nfft) * np.conj(np.fft.rfft(y, nfft)), nfft)
        lim = sr // 20
        k = int(np.argmax(np.r_[cc[:lim], cc[-lim:]]))
        lag = k if k < lim else k - 2 * lim
        xs, ys = (x[lag:], y[:m - lag]) if lag >= 0 else (x[:m + lag], y[-lag:])
        corr = float(np.dot(xs, ys) / (np.linalg.norm(xs) * np.linalg.norm(ys) + 1e-12))
        other = float(np.dot(x, z) / (np.linalg.norm(x) * np.linalg.norm(z) + 1e-12))
        frames = int(probe(os.path.join(PDIR, p["proof"]))["streams"][0].get("nb_frames") or 0)
        au.append((st.get("video", {}).get("codec_name"), st.get("audio", {}).get("codec_name"), round(len(a_) / sr, 3),
                   lag, round(corr, 4), round(other, 3), frames))
    say("PASS" if au and all(x and x[0] == "h264" and x[1] == "aac" and abs(x[2] - w["seconds"]) <= 0.06 and abs(x[3]) <= sr // 1000
                             and x[4] > 0.98 and x[5] < x[4] - 0.1 and abs(x[6] - (w["b"] - w["a"])) <= 1 for x in au) else "FAIL",
        "proof_av.mp4: the proof's video with the song's audio at the window (AAC, the window's length, no offset, the song "
        "itself, not another stretch of it)", f"(video, audio, seconds, lag, correlation, vs 1 s later, proof frames) {au}; "
        f"the window {w.get('seconds')} s")
    # the critic on each proof, the verdict, the costs
    crit = [json.load(open(os.path.join(rd, f"p{p['k']}", "critic.json"), encoding="utf-8"))
            if os.path.exists(os.path.join(rd, f"p{p['k']}", "critic.json")) else {} for p in ps]
    cprompt = open(os.path.join(rd, "p1", "prompt.md"), encoding="utf-8").read() if os.path.exists(os.path.join(rd, "p1", "prompt.md")) else ""
    v = R.get("verdict") or {}
    vprompt = open(os.path.join(rd, "verdict-prompt.md"), encoding="utf-8").read() if os.path.exists(os.path.join(rd, "verdict-prompt.md")) else ""
    vmd = open(os.path.join(rd, "verdict.md"), encoding="utf-8").read() if os.path.exists(os.path.join(rd, "verdict.md")) else ""
    say("PASS" if all(c.get("scores") for c in crit) and f"(from `work/pitches/round-{K}/p1/pitch.md`)" in cprompt
        and "a pitch's proof clip" in cprompt and "style frame" in cprompt and v.get("winner") == 2 and v.get("ranking") == [2, 1]
        and v.get("graft") and v["graft"][0]["from"] == 1 and "# Verdict" in vmd and "<!-- mv:verdict -->" in vprompt
        and "Ambition, highest of all" in vprompt and "Never reward the safe option" in vprompt
        and vprompt.count("#### Its pitch.md") == 2 and "/style/" in vprompt and "## This machine" in vprompt else "FAIL",
        "the critic on each proof (against its pitch.md, with its style frames) and the verdict (ambition highest, never the "
        "safe option; every pitch.md, the style frames and the critiques in its prompt): a ranking, the winner, ideas to graft",
        f"critiques {[c.get('scores') for c in crit]}; verdict {v.get('winner')} {v.get('ranking')} graft {v.get('graft')}")
    costs_ok, got = True, []
    for p in ps:
        evs = os.path.join(PDIR, p["session"], "events.jsonl")
        res = [json.loads(x) for x in open(evs, encoding="utf-8") if '"result"' in x] if os.path.exists(evs) else []
        res = [x for x in res if x.get("type") == "result"]
        got.append((p["k"], p["cost_usd"], [x["total_cost_usd"] for x in res], p["critic_cost_usd"]))
        costs_ok &= bool(res) and p["cost_usd"] == res[-1]["total_cost_usd"] and p["critic_cost_usd"] == 0.0123
    c = R.get("costs") or {}
    costs_ok &= c.get("total") == round(sum(p["cost_usd"] for p in ps) + sum(p["critic_cost_usd"] for p in ps) + v.get("cost_usd", 0), 4) \
        and c.get("verdict") == 0.0211 and R.get("cost_usd") == c.get("total") and any(len(x[2]) > 1 for x in got)
    say("PASS" if costs_ok else "FAIL", "the round's costs: each pitch's session from its latest running total (not a sum), "
        "the critic's, the verdict's, the round's total", f"{got}; {c}")
    # each pitch's sandbox (its command line) and its brief
    s1 = os.path.join(rd, "sessions", "p1")
    deny = (json.load(open(os.path.join(s1, "settings.json"), encoding="utf-8")).get("permissions") or {}).get("deny") or []
    cmd = json.load(open(os.path.join(s1, "command.json"), encoding="utf-8"))["cmd"]
    rp = director.rule_path
    want = [f"{t}({rp(os.path.join(rd, 'p2'))}/**)" for t in ("Edit", "Write")] + \
        [f"Write({rp(os.path.join(PDIR, *x.split('/')))})" for x in ("work/concept.md", "work/plan.json", "project.json", "report.md")] + \
        [f"Write({rp(os.path.join(PDIR, *x.split('/')))}/**)" for x in ("final", "work/scenes", "work/kit", "song", "review")] + \
        [f"Write({rp(os.path.join(rd, 'sessions'))}/**)", f"Write({rp(os.path.join(rd, 'round.json'))})",
         f"Write({rp(os.path.join(STUDIO, 'tools'))}/**)"]
    mine = rp(os.path.join(rd, "p1"))
    brief = open(os.path.join(s1, "brief.md"), encoding="utf-8").read()
    tools = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
    miss = [x for x in want if x not in deny]
    say("PASS" if not miss and not [x for x in deny if mine in x] and cmd[cmd.index("--settings") + 1] == os.path.join(s1, "settings.json")
        and "Agent" in tools and "Write" in tools and "work/pitches/round-" in cmd[cmd.index("--append-system-prompt") + 1]
        and "<!-- mv:pitch -->" in brief and f"--frames {w.get('a')}:{w.get('b')}" in brief and "unmistakably different" in brief
        and "The safe option never wins" in brief and "the wild card (p2)" in brief and "**the story**" in brief
        and f"work/pitches/round-{K}/p1/" in brief and "## Be honest about what it takes" in brief else "FAIL",
        "each pitch's sandbox (its own settings file): writes into the other pitch's folder, the project's own files, the "
        "round's files and the studio's tools refused, its own folder not; its brief: its angle and the others', the proof "
        "window as --frames, bold and unmistakably different, honest about the render time",
        f"missing deny rules {miss}; rules on its own folder {[x for x in deny if mine in x]}")
    # the draft plan plays the pitch's own scenes live (it names their final place, work/scenes/<id>/, where the project
    # has a scene of that name from the agents run)
    from mv import live
    tl = next((t for t in live.state(PDIR)["timelines"] if t["source"] == f"pitch:round-{K}/p2"), {})
    own = [x.get("scene") for x in tl.get("layers") or []]
    clash = os.path.exists(os.path.join(PDIR, "work", "scenes", "s1", "scene.js"))
    say("PASS" if own and all(str(x).startswith(f"work/pitches/round-{K}/p2/scenes/") for x in own) else "FAIL",
        "a pitch's draft plan plays its own scenes live, not the project's of the same name", f"{own}; work/scenes/s1 there: {clash}")
    # direct --pitch: the promotion over the agents run's work (work/concept.md, work/scenes/, work/plan.json there)
    cp, pp = os.path.join(PDIR, "work", "concept.md"), os.path.join(PDIR, "work", "plan.json")
    old_concept = open(cp, encoding="utf-8").read() if os.path.exists(cp) else None
    old_plan = open(pp, encoding="utf-8").read() if os.path.exists(pp) else None
    old_s1 = open(os.path.join(PDIR, "work", "scenes", "s1", "scene.js"), "rb").read() \
        if os.path.exists(os.path.join(PDIR, "work", "scenes", "s1", "scene.js")) else None
    r = studio("direct", P, "--pitch", f"round-{K}/p2", "--budget", "4m", "--effort", "low", "--model", "fake",
               "--critic-model", "off", check=False, env=env, timeout=1800)
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    last = (pj.get("runs") or [{}])[-1]
    rdir = os.path.join(PDIR, "work", "director", f"run-{last.get('run')}")
    rb = open(os.path.join(rdir, "brief.md"), encoding="utf-8").read() if os.path.exists(os.path.join(rdir, "brief.md")) else ""
    R2 = json.load(open(os.path.join(rd, "round.json"), encoding="utf-8"))
    dest = os.path.join(PDIR, "work", "scenes", f"pitch-{K}-2")
    plan = json.load(open(pp, encoding="utf-8")) if os.path.exists(pp) else {}
    backs = {k: sorted(glob.glob(os.path.join(PDIR, "work", pat))) for k, pat in (("concept", "concept_before_pitch_*.md"),
                                                                                  ("plan", "plan_before_pitch_*.json"))}
    rep_md = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
    p2md = open(os.path.join(rd, "p2", "pitch.md"), encoding="utf-8").read()
    ok = (r.returncode == 0 and last.get("version") and (last.get("pitch") or {}).get("ref") == f"round-{K}/p2"
          and open(cp, encoding="utf-8").read() == p2md and old_concept is not None and backs["concept"]
          and open(backs["concept"][-1], encoding="utf-8").read() == old_concept
          and old_plan is not None and backs["plan"] and open(backs["plan"][-1], encoding="utf-8").read() == old_plan
          and [x.get("scene") for x in plan.get("segments") or []] == [f"work/scenes/pitch-{K}-2/s1/scene.js", f"work/scenes/pitch-{K}-2/s2/scene.js"]
          and all(os.path.exists(os.path.join(dest, *x.split("/"))) for x in ("lib/look.js", "proof/scene.js", "s1/scene.js", "s2/scene.js"))
          and (old_s1 is None or open(os.path.join(PDIR, "work", "scenes", "s1", "scene.js"), "rb").read() == old_s1)
          and sorted(os.listdir(os.path.join(PDIR, "work", "style-frames"))) == sorted(os.path.basename(x) for x in R["pitches"][1]["style"])
          and (R2.get("picked") or {}).get("k") == 2 and R2["picked"].get("by") == "the user" and R2["picked"].get("run") == last.get("run")
          and "## The pitch I picked: go further" in rb and R["pitches"][1]["title"] in rb and "go further than the pitch" in rb
          and "Ideas worth grafting from the other pitches" in rb and "from p1" in rb and f"work/scenes/pitch-{K}-2/" in rb
          and "You have full reign" in rb and f"Started from pitch round-{K}/p2" in rep_md and "picked by the user" in rep_md)
    say("PASS" if ok else "FAIL", "direct --pitch: the pitch put in place before the run (its pitch as work/concept.md, the old "
        "one kept; its scenes and lib next to the existing ones in work/scenes/pitch-K-N/; its style frames; its draft plan "
        "with the paths moved, the old plan kept), round.json's picked, the brief's 'go further' section with the critic's "
        "notes and the graft ideas; delivered",
        f"run {last.get('run')}: v{last.get('version')}, pitch {last.get('pitch')}; backups {backs}; plan "
        f"{[x.get('scene') for x in plan.get('segments') or []]}; picked {R2.get('picked')}"
        + ("" if r.returncode == 0 else (r.stdout + r.stderr)[-1500:]))
    # the promotion into a project with no work yet: everything lands in its place (no backups)
    scratch = os.path.join(PDIR, "cache", "_promote")
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(os.path.join(scratch, "work", "pitches"))
    shutil.copytree(rd, os.path.join(scratch, "work", "pitches", f"round-{K}"))
    info = pitches.promote(scratch, f"round-{K}/p2", log=lambda x: None)
    pl = json.load(open(os.path.join(scratch, "work", "plan.json"), encoding="utf-8"))
    ok = (info["scenes"] == "work/scenes" and not info["concept_backup"] and not info["plan_backup"] and not info["notes"]
          and [x["scene"] for x in pl["segments"]] == ["work/scenes/s1/scene.js", "work/scenes/s2/scene.js"]
          and all(os.path.exists(os.path.join(scratch, "work", "scenes", *x.split("/"))) for x in ("lib/look.js", "proof/scene.js", "s1/scene.js"))
          and not glob.glob(os.path.join(scratch, "work", "scenes", "**", "*.mp4"), recursive=True)
          and open(os.path.join(scratch, "work", "concept.md"), encoding="utf-8").read() == p2md)
    # the same pitch picked again after the director changed its scenes: nothing it changed is overwritten
    rd_ = lambda f: open(f, encoding="utf-8").read() if os.path.exists(f) else ""      # noqa: E731
    proof_js = os.path.join(scratch, "work", "scenes", "proof", "scene.js")
    open(proof_js, "a", encoding="utf-8").write("\n// the director's change\n")
    i2 = pitches.promote(scratch, f"round-{K}/p2", log=lambda x: None)        # -> work/scenes/pitch-K-2/ (work/scenes/ changed)
    mine = os.path.join(scratch, "work", "scenes", f"pitch-{K}-2", "proof", "scene.js")
    open(mine, "a", encoding="utf-8").write("\n// the director's change there\n")
    i3 = pitches.promote(scratch, f"round-{K}/p2", log=lambda x: None)        # again: that folder kept, the pitch's own back
    i4 = pitches.promote(scratch, f"round-{K}/p2", log=lambda x: None)        # and again, unchanged: nothing copied
    back = os.path.join(scratch, *str(i3.get("scenes_backup") or "x").split("/"), "proof", "scene.js")
    again = (i2["scenes"], i2["scenes_backup"], i3["scenes_backup"], i4["already"], i4["scenes_backup"])
    ok_again = (i2["scenes"] == f"work/scenes/pitch-{K}-2" and not i2["scenes_backup"] and "director's change" in rd_(proof_js)
                and i3["scenes_backup"] and "change there" in rd_(back) and "change there" not in rd_(mine) and rd_(mine)
                and i4["already"] and not i4["scenes_backup"])
    shutil.rmtree(scratch, ignore_errors=True)
    say("PASS" if ok else "FAIL", "the promotion into a project with no work yet: the scenes and their lib in work/scenes/, "
        "the plan's paths as the pitch named them, the concept; no backups", f"{ {k: info[k] for k in ('scenes', 'concept_backup', 'plan_backup', 'notes')} }")
    say("PASS" if ok_again else "FAIL", "the same pitch picked again after the director changed its scenes: work/scenes/ kept "
        "(the pitch's go to work/scenes/pitch-K-N/), then that folder kept as pitch-K-N_before_pitch_<time>/ when it changed; "
        "unchanged, nothing copied", f"{again}")
    # pitch --auto: the verdict's winner built in the same job
    runs0 = last.get("run")
    r = studio("pitch", P, "--n", "2", "--budget", "3m", "--model", "fake", "--critic-model", "fake", "--auto",
               "--direct-budget", "4m", "--direct-effort", "low", "--direct-model", "fake", check=False, env=env, timeout=2400)
    K2 = max(pitches.round_numbers(PDIR))
    R3 = json.load(open(os.path.join(PDIR, "work", "pitches", f"round-{K2}", "round.json"), encoding="utf-8"))
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    last = (pj.get("runs") or [{}])[-1]
    st = json.load(open(os.path.join(PDIR, "work", "director", f"run-{last.get('run')}", "status.json"), encoding="utf-8")) \
        if last.get("run") else {}
    rep_md = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
    deny2 = (json.load(open(os.path.join(PDIR, "work", "pitches", f"round-{K2}", "sessions", "p1", "settings.json"),
                            encoding="utf-8")).get("permissions") or {}).get("deny") or []
    pt = st.get("pitch") or {}
    say("PASS" if r.returncode == 0 and K2 == K + 1 and R3.get("state") == "done" and last.get("run") != runs0
        and last.get("version") and pt.get("ref") == f"round-{K2}/p2" and pt.get("by") == "auto" and pt.get("chained")
        and pt.get("round_cost_usd") == R3.get("cost_usd") and (R3.get("picked") or {}).get("by") == "auto"
        and (R3.get("picked") or {}).get("run") == last.get("run") and "picked by the round's verdict" in rep_md
        and "the round and this run together" in rep_md and f"Write({rp(rd)}/**)" in deny2 else "FAIL",
        "pitch --auto: the verdict's winner is built by a director run in the same job (its run settings name the pitch, "
        "the round's cost next to the run's); a later round's pitches can't write into an earlier round",
        f"round {K2}: {R3.get('state')}, picked {R3.get('picked')}; run {last.get('run')} v{last.get('version')} pitch {pt}"
        + ("" if r.returncode == 0 else (r.stdout + r.stderr)[-1500:]))


def pitch_step_check():
    """A pitch's turns on a simulated clock (pitch.Pitch.step with a scripted session): one that delivers nothing gets
    the studio's reminders (three, then its session is closed), never "your pitch is delivered"; a delivered one with
    its style frames gets the push to go further once; one without style frames a reminder, not the push; one that
    keeps running after its input closed (at the end of a turn) is stopped 20 s after the deadline plus the grace, and
    after the user's stop too. And: the verdict's json read leniently (a ranking as one number or a string, a winner
    as 2.0); a pick written while the round saves is kept; a dry-run round doesn't hide the round running before it."""
    print("== 7d. a pitch's turns, the verdict's json, the round's file (in-process)", flush=True)
    import threading, types
    import pitch as pitches
    from mv.common import jsave
    root = os.path.join(PDIR, "cache", "_pitchstep")
    shutil.rmtree(root, ignore_errors=True)
    rd = os.path.join(root, "work", "pitches", "round-1")
    t0 = time.time()
    R = types.SimpleNamespace(pdir=root, rdir=rd, k=1, n=5, deadline=t0 + 1200, grace=120, window={"a": 0, "b": 600},
                              budget_s=1200, started=t0 - 1, max_usd=None,
                              queue={"start_by": t0 + 600, "proof_s": 60, "all_s": 300, "note": ""})

    class Linger(FakeSession):               # its input closed, the process still there
        def alive(self):
            return self.proc.poll() is None

    def make(k, files=(), session=None):
        p = pitches.Pitch(R, k, "story")
        for f in files:
            fp = os.path.join(p.dir, *f.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "wb").write(b"x")
        p.s, p.state = session or FakeSession(), "running"
        return p

    def turns(p, n):
        for i in range(n):
            p.s.over()
            p.step(t0 + 60 + 60 * i)
        return p.s.whys(), p.s.closed
    out, procs = {}, []
    try:
        out["nothing"] = turns(make(1), 4)
        out["delivered"] = turns(make(2, ("pitch.md", "proof.mp4", "style/a.png")), 2)
        out["no style"] = turns(make(3, ("pitch.md", "proof.mp4")), 2)
        for k, stop in ((4, False), (5, True)):
            proc = subprocess.Popen([PY, "-c", "import time; time.sleep(300)"])
            procs.append(proc)
            s = Linger()
            s.proc, s.closed = proc, True
            p = make(k, session=s)
            at = t0 + 100
            if stop:
                p.user_stop(at)
            end = p.deadline + p.grace
            out["stopped" if stop else "lingers"] = (p.step(end + 1), p.step(end + 10), p.step(end + 22), proc.poll() is not None,
                                                     p.stop_reason, [e["text"] for e in s.status["events"]][-1:])
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
    # the render queue: a pitch without a proof by its start-by time is told once to start it (in the background);
    # one with a proof isn't; the start-by time leaves room for N proofs one after another, said when the round is tight
    pa, pb = make(6), make(7, ("proof.mp4",))
    for p_ in (pa, pb):
        p_.warnings(t0 + 605, 595)
        p_.warnings(t0 + 610, 590)
    q1, q2, q3 = (pitches.queue_plan(3, 267, 0, 1200, 120), pitches.queue_plan(2, 60, 0, 180, 90),
                  pitches.queue_plan(2, 30, 0, 1200, 120))
    out["queue"] = (pa.s.whys().count("start the proof"), "start the proof" in pb.s.whys(),
                    round(q1["start_by"]), bool(q1["note"]), round(q2["start_by"]), bool(q2["note"]), round(q3["start_by"]), bool(q3["note"]))
    say("PASS" if out["queue"] == (1, False, 459, True, 90, True, 600, False) else "FAIL",
        "the render queue in a round: a pitch without a proof at its start-by time is told once to start it, one with a "
        "proof isn't; the start-by time leaves the N proofs room before the deadline plus the grace (never later than "
        "half-way), and a round whose proofs take over 40% of the budget says so", str(out["queue"]))
    ok_turns = (out["nothing"] == (["something missing"] * 3, True) and out["delivered"] == (["push it further"], True)
                and out["no style"] == (["no style frames"], True)
                and out["lingers"][:4] == (True, True, False, True) and "time ran out" in out["lingers"][4]
                and "didn't end within 20 s" in str(out["lingers"][5])
                and out["stopped"][:4] == (True, True, False, True) and out["stopped"][4] == "stopped by the user")
    # the verdict's json, read leniently
    v1 = pitches.read_verdict({"winner": 2.0, "ranking": 2}, [1, 2], [1, 2])
    v2 = pitches.read_verdict({"winner": "p3", "ranking": "3, 1 > 2", "graft": "from p1", "scores": []}, [1, 2, 3], [1, 2, 3])
    v3 = pitches.read_verdict({"ranking": {"a": 1}}, [1, 2], [1, 2])
    out["verdict"] = (v1 and (v1["winner"], v1["ranking"]), v2 and (v2["winner"], v2["ranking"], v2["graft"]), v3,
                      pitches.pnum(2.5), pitches.pnum(True))
    ok_verdict = out["verdict"] == ((2, [2, 1]), (3, [3, 1, 2], []), None, None, None)
    # a pick written while the round runs is kept by its next save
    os.makedirs(rd, exist_ok=True)
    jsave(os.path.join(rd, "round.json"), {"round": 1, "picked": {"k": 2, "by": "the user"}})
    fake_round = types.SimpleNamespace(lock=threading.Lock(), data=lambda: {"round": 1, "state": "running", "picked": None},
                                       rdir=rd, picked=None)
    saved = pitches.Round.save(fake_round)
    out["picked"] = (saved.get("picked"), pitches.load_round(root, 1).get("picked"))
    ok_picked = out["picked"] == ({"k": 2, "by": "the user"}, {"k": 2, "by": "the user"})
    # a dry-run round (its briefs written while one runs) doesn't hide the round running before it
    jsave(os.path.join(rd, "round.json"), {"round": 1, "state": "running", "updated": time.time()})
    jsave(os.path.join(root, "work", "pitches", "round-2", "round.json"), {"round": 2, "state": "dry-run", "updated": time.time()})
    act = pitches.round_active(root)
    jsave(os.path.join(rd, "round.json"), {"round": 1, "state": "running", "updated": time.time() - pitches.FRESH_S - 5})
    out["active"] = ((act or {}).get("round"), pitches.round_active(root))
    ok_active = out["active"] == (1, None)
    shutil.rmtree(root, ignore_errors=True)
    bad = [k for k, v in (("the turns", ok_turns), ("the verdict's json", ok_verdict), ("the pick kept", ok_picked),
                          ("round_active", ok_active)) if not v]
    say("FAIL" if bad else "PASS", "a pitch's turns: nothing delivered -> three reminders, then closed (never the push); delivered -> "
        "the push once; no style frames -> a reminder; a session still running after its input closed is stopped 20 s after "
        "the deadline plus the grace, and after a stop; the verdict's json read leniently; a pick kept by the round's next "
        "save; a dry-run round doesn't hide a running one", ("failed: " + "; ".join(bad) + f" {out}")[:2500] if bad else "")


def pitch_trouble_check():
    """A round where the pitches go wrong, in-process with the stand-in (FAKE_PITCH) and a short clock (25 s, a 10 s
    grace): one keeps running after its input closed (the studio stops it 20 s after the deadline plus the grace and the
    round goes on), one delivers only its pitch.md, one nothing; the verdict answers without its json (the critic's
    scores rank the two with a pitch.md, and verdict.md says so); round.json stays fresh while a slow verdict works."""
    print("== 7e. a round that goes wrong (in-process, the stand-in)", flush=True)
    import threading
    import pitch as pitches
    from mv.claude import kill_tree
    keys = ("MV_CLAUDE", "FAKE_PITCH", "FAKE_VERDICT", "FAKE_VERDICT_S")
    was = {k: os.environ.get(k) for k in keys}
    os.environ.update(MV_CLAUDE=os.path.join(TESTS, "fake_claude.py"), FAKE_PITCH="1:linger,2:md,3:fail", FAKE_VERDICT="nojson",
                      FAKE_VERDICT_S="12")
    samples, forced, R, d, took, err = [], [], None, {}, 0.0, ""
    stop = threading.Event()
    try:
        R = pitches.Round(PDIR, n=3, budget="2m", model="fake", effort="low", critic_model="fake", log=lambda x: None)
        R.budget_s, R.deadline, R.grace = 25, R.started + 25, 10     # (each pitch's first turn ends, and is closed, at once)
        for p in R.pitches:
            p.deadline, p.grace = R.deadline, R.grace

        def watch():
            t = time.time()
            while not stop.wait(1):
                x = pitches.load_round(PDIR, R.k) or {}
                samples.append((round(time.time() - t), x.get("stage"), round(time.time() - float(x.get("updated") or 0), 1)))
                if time.time() - t > 240 and not forced:       # a pitch nobody stops: the test mustn't hang on it
                    forced.append(True)
                    for p in R.pitches:
                        if p.s and p.s.alive():
                            kill_tree(p.s.proc)
        threading.Thread(target=watch, daemon=True).start()
        t0 = time.time()
        d = R.run()
        took = time.time() - t0
    except BaseException as ex:
        err = f"{type(ex).__name__}: {ex}"
    finally:
        stop.set()
        for k, v in was.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for p in (R.pitches if R else []):
            if p.s and p.s.alive():
                kill_tree(p.s.proc)
    # a stop while the verdict works ends its call (no claude -p left running on its own)
    seen, quick, vv = [], None, {}
    if R and not err:
        orig = pitches.claude_print

        def spy(*a, **k):
            then = k.get("on_start")
            k["on_start"] = lambda proc: (seen.append(proc), then and then(proc))
            return orig(*a, **k)
        pitches.claude_print = spy
        os.environ.update(MV_CLAUDE=os.path.join(TESTS, "fake_claude.py"), FAKE_VERDICT_S="60")
        try:
            R.stop_at = None
            threading.Timer(4, pitches.request_stop, args=(PDIR,)).start()
            t1 = time.time()
            vv = R.make_verdict()
            quick = round(time.time() - t1, 1)
        finally:
            pitches.claude_print = orig
            for k, x in was.items():
                if x is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = x
            if os.path.exists(os.path.join(PDIR, pitches.STOP)):
                os.remove(os.path.join(PDIR, pitches.STOP))
    ps = {p["k"]: p for p in d.get("pitches") or []}
    v = d.get("verdict") or {}
    rd = R.rdir if R else ""
    vmd = open(os.path.join(rd, "verdict.md"), encoding="utf-8").read() if rd and os.path.exists(os.path.join(rd, "verdict.md")) else ""
    ev1 = [e.get("text") for e in (R.pitches[0].status.get("events") or [])] if R else []
    during = [x for x in samples if x[1] == "verdict"]
    ok = (not err and not forced and took < 25 + 10 + 20 + 50 and d.get("state") == "done"
          and (ps.get(1) or {}).get("state") == "no proof" and "time ran out" in (ps.get(1) or {}).get("error", "")
          and any("didn't end within 20 s" in str(x) for x in ev1)
          and (ps.get(2) or {}).get("state") == "no proof" and (ps.get(3) or {}).get("state") == "failed"
          and "no pitch.md" in (ps.get(3) or {}).get("error", "")
          and ((ps.get(1) or {}).get("critic") or {}).get("error") == "no proof to look at"
          and v.get("winner") == 1 and v.get("ranking") == [1, 2] and "the critic's scores" in str(v.get("by"))
          and "there was no readable json block" in str(v.get("error")) and "the verdict couldn't be read" in vmd
          and len(during) >= 3 and max(x[2] for x in during) < 8 and not R.procs
          and quick is not None and quick < 15 and seen and seen[0].poll() is not None and "its call was ended" in str(vv.get("error")))
    say("PASS" if ok else "FAIL", "a round that goes wrong: a pitch still running after its input closed is stopped (the round "
        "ends by the deadline plus the grace plus 20 s, then judges), one with only its pitch.md, one with nothing (their "
        "states and errors); the verdict without its json falls back to the critic's scores; round.json stays fresh while "
        "the verdict works; a stop during the verdict ends its call",
        f"{err}; {took:.0f} s; stopped verdict after {quick} s (its process ended: {bool(seen) and seen[0].poll() is not None}, {vv.get('error')}){' (a pitch had to be killed by the test)' if forced else ''}; state {d.get('state')}; "
        f"{[(k, p.get('state'), p.get('error')) for k, p in ps.items()]}; verdict {v.get('winner')} {v.get('ranking')} "
        f"{v.get('by')} {v.get('error')}; during the verdict (t, stage, age of round.json) {during}; p1's log {ev1[-3:]}")


def finish(a, t_start):
    bad = [r for r in RESULTS if r[0] == "FAIL"]
    skipped = [r for r in RESULTS if r[0] == "SKIP"]
    print(f"\n== smoke test: {sum(r[0] == 'PASS' for r in RESULTS)} passed, {len(bad)} failed, {len(skipped)} skipped "
          f"in {time.time() - t_start:.0f} s")
    for r in skipped:
        print(f"   SKIPPED: {r[1]}")
    for r in bad:
        print(f"   FAILED: {r[1]}: {r[2][:300]}")
    if not a.keep and not bad:
        shutil.rmtree(PDIR, ignore_errors=True)
    elif bad:
        print(f"   (kept {PDIR} for a look)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
