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
    reusing it as final/critic_v1.md, an old Claude Code without --max-budget-usd, the lenient parser; the pipeline's
    studio side (tools/pipeline.py): validate good and bad plans, render the segments (and again from the cache), the
    cache key following a lib import, assemble a preview, the language gate, the render queue, eyes next to a segment
 6d. the session plumbing (messages sent while a turn runs join it: one result, as on the real CLI) and the
    pipeline's stages as a state machine on a simulated clock, every way out (in-process, stand-ins)
 7. the autonomous director runner end to end with a stand-in for Claude Code (tests/fake_claude.py: no usage spent;
    the critic's notes reach the director as a message and the new version gets final/critic_vN.md), then a review
    round (a note -> --revise -> the next version, the note marked done); then the pipeline end to end (--mode
    pipeline: the planner, gate 1, two builders with their critic rounds, the assembled preview, the final, the
    version delivered with every check passing) and `pipeline build` for an interactive planner
The project is projects/_smoke (internal, git-ignored); --keep leaves it for a look. Exit code 0 = everything passed.
"""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.request

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
    pipeline_check(m)
    live_check(m)
    session_check()
    pipeline_machine_check()
    builder_failures_check()

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
        pipeline_run_check(env)
    return finish(a, t_start)


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


def pipeline_check(m):
    """The pipeline's studio side (tools/pipeline.py) on a plan of three segments (copies of tests/scenes/field.js in
    their own folders, the middle one the hero, importing a shared lib) and the lyrics overlay: validate (the good
    plan; gaps, overlaps, a bad id, a missing scene, a range outside the song, a scene outside its folder), render at
    preview size and again from the cache, the cache key (a lib change renders the segment that imports it again, a
    sibling's change doesn't), assemble a preview (with the stand-in critic), the language gate (eyes next to the
    segment, the style frame shown to the critic), the render queue (a held queue makes a render wait; MV_RENDER_LOCK=0
    doesn't), the status."""
    print("== 6c. the pipeline (stand-in for Claude Code)", flush=True)
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    import pipeline
    fake = {"MV_CLAUDE": os.path.join(TESTS, "fake_claude.py")}
    sd = os.path.join(PDIR, "work", "scenes")
    for sid in ("s1", "s2", "s3", "type", "lib"):
        os.makedirs(os.path.join(sd, sid), exist_ok=True)
    for sid in ("s1", "s3"):
        shutil.copyfile(os.path.join(TESTS, "scenes", "field.js"), os.path.join(sd, sid, "scene.js"))
    open(os.path.join(sd, "s2", "scene.js"), "wb").write(b'import { LOOK } from "../lib/look.js";   // the shared language\n'
                                                         + open(os.path.join(TESTS, "scenes", "field.js"), "rb").read())
    open(os.path.join(sd, "lib", "look.js"), "wb").write(b'// the shared visual language (smoke test)\nexport const LOOK = {ink: "#e9e4d8"};\n')
    shutil.copyfile(os.path.join(TESTS, "scenes", "lyrics.html"), os.path.join(sd, "type", "type.html"))
    os.makedirs(os.path.join(PDIR, "work", "style-frames"), exist_ok=True)
    from PIL import Image
    Image.new("RGB", (320, 180), (16, 20, 24)).save(os.path.join(PDIR, "work", "style-frames", "a.png"))
    bars = [b["t"] for b in m["bars"]]
    c1, c2, dur = round(bars[len(bars) // 3], 3), round(bars[2 * len(bars) // 3], 3), m["duration"]
    seg = lambda sid, a, b, owner, status: {"id": sid, "name": f"{sid} (smoke)", "from": a, "to": b, "renderer": "web",  # noqa: E731
                                            "scene": f"work/scenes/{sid}/scene.js", "owner": owner, "brief": "the field",
                                            "enter": "the field", "exit": "the field", "moments": [], "status": status}
    good = {"stage": "planned", "hero": "s2", "lib": "work/scenes/lib/", "style_frames": ["work/style-frames/a.png"],
            "rules": "marks on a dark field; every downbeat re-deals them",
            "segments": [seg("s1", 0.0, c1, "builder", "built"), seg("s2", c1, c2, "planner", "drafted"),
                         seg("s3", c2, dur, "builder", "built")],
            "overlays": [{"id": "type", "scene": "work/scenes/type/type.html", "transparent": True, "owner": "planner",
                          "brief": "the sung lines on plates"}]}
    plan_p = os.path.join(PDIR, "work", "plan.json")
    json.dump(good, open(plan_p, "w", encoding="utf-8"), indent=1)
    r = studio("pipeline", "validate", P, check=False)
    v = pipeline.validate(PDIR)
    say("PASS" if r.returncode == 0 and v["ok"] and not v["warnings"] and "valid (stage planned)" in r.stdout else "FAIL",
        "pipeline validate: a good plan (3 segments on downbeats + an overlay)", r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr[-300:])

    def bad(fn):
        p = json.loads(json.dumps(good))
        fn(p)
        return pipeline.validate(PDIR, plan=p)
    cases = [("a gap", lambda p: p["segments"][1].update({"from": c1 + 1.0}), "a gap before it"),
             ("an overlap", lambda p: p["segments"][1].update({"from": c1 - 1.0}), "overlaps s1"),
             ("a bad id", lambda p: p["segments"][0].update({"id": "Intro 1"}), "id is 'Intro 1'"),
             ("a missing scene", lambda p: p["segments"][1].update({"scene": "work/scenes/s2/missing.js"}), "doesn't exist"),
             ("a range outside the song", lambda p: p["segments"][2].update({"to": dur + 5}), "but the song is"),
             ("a scene outside its folder", lambda p: p["segments"][0].update({"scene": "work/scenes/s3/scene.js"}), "its own folder"),
             ("an unknown hero", lambda p: p.update({"hero": "s9"}), "isn't one of the segments")]
    got = {name: bad(fn) for name, fn, _ in cases}
    miss = [name for name, _, want in cases if got[name]["ok"] or not any(want in e for e in got[name]["errors"])]
    json.dump(dict(good, segments=[good["segments"][0], dict(good["segments"][1], **{"from": c1 + 1.0}), good["segments"][2]]),
              open(plan_p, "w", encoding="utf-8"))
    r = studio("pipeline", "validate", P, check=False)
    open(plan_p, "w", encoding="utf-8").write('{"stage": "planned", "segments": [}')
    r2 = studio("pipeline", "validate", P, check=False)
    json.dump(good, open(plan_p, "w", encoding="utf-8"), indent=1)
    say("PASS" if not miss and r.returncode == 1 and "NOT valid" in r.stdout and f"Set s2.from to {c1:g}" in r.stdout
        and r2.returncode == 1 and "isn't valid JSON at line 1" in r2.stdout else "FAIL",
        "pipeline validate: bad plans are refused with what to change (" + ", ".join(n for n, _, _ in cases) + ", broken JSON)",
        f"missed: {miss}; " + " | ".join(next((e for e in got[n]["errors"]), "-")[:70] for n, _, _ in cases[:2]))
    t0 = time.time()
    r = studio("pipeline", "render", P, "--preview", check=False)
    t_render = time.time() - t0
    t0 = time.time()
    r2 = studio("pipeline", "render", P, "--preview", check=False)
    say("PASS" if r.returncode == 0 and "4 rendered, 0 unchanged" in r.stdout and r2.returncode == 0 and "0 rendered, 4 unchanged" in r2.stdout
        and all(os.path.exists(os.path.join(PDIR, "work", "segments", s, f"{s}_preview.mp4")) for s in ("s1", "s2", "s3"))
        and os.path.isdir(os.path.join(PDIR, "work", "segments", "type", "type_preview")) and time.time() - t0 < 30 else "FAIL",
        "pipeline render --preview: each segment over its own range, the overlay as a transparent pass; again: all from the cache",
        f"{t_render:.0f} s, then {time.time() - t0:.1f} s; " + (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:]))
    v = pipeline.validate(PDIR)
    its = {x["id"]: x for x in v["items"]}
    cur = lambda: {k: pipeline.current(PDIR, its[k], "preview")[0] for k in ("s1", "s2", "s3")}   # noqa: E731
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
    r = studio("pipeline", "assemble", P, "--preview", "--critic-model", "fake", check=False, env=fake)
    vid = (re.findall(r"-> (work/previews/preview_\d+\.mp4)\s*$", r.stdout, re.M) or [None])[-1]
    from mv.common import frames_of
    n = frames_of(os.path.join(PDIR, vid)) if vid else None
    stem = os.path.splitext(os.path.basename(vid or "x"))[0]
    fps = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))["format"]["fps"]
    n_song = int(round(json.load(open(os.path.join(PDIR, "song", "frames.json")))["duration"] * fps))
    say("PASS" if r.returncode == 0 and vid and n == n_song
        and "(stream copy) + 1 overlay" in r.stdout and os.path.exists(os.path.join(PDIR, "work", "eyes", stem, "index.md"))
        and os.path.exists(os.path.join(PDIR, "work", "critic", f"{stem}_01.md")) and "the joins" in
        open(os.path.join(PDIR, "work", "critic", "last-prompt.md"), encoding="utf-8").read() else "FAIL",
        "pipeline assemble --preview: segments joined (stream copy), the type layered, the song muxed; eyes; the critic on the film",
        f"{vid}: {n} frames" if vid else (r.stdout + r.stderr)[-800:])
    os.replace(s3, s3 + ".off")                    # s3 not written yet: a slate in a preview; the final says no at once
    json.dump(dict(good, segments=good["segments"][:2] + [dict(good["segments"][2], status="todo")]), open(plan_p, "w", encoding="utf-8"))
    rf = studio("pipeline", "assemble", P, "--final", check=False)
    r = studio("pipeline", "assemble", P, "--preview", "--no-critic", "--no-look", check=False)
    os.replace(s3 + ".off", s3)
    json.dump(good, open(plan_p, "w", encoding="utf-8"), indent=1)
    vid2 = (re.findall(r"-> (work/previews/preview_\d+\.mp4)\s*$", r.stdout, re.M) or [None])[-1]
    say("PASS" if rf.returncode == 1 and "no scene yet for s3" in rf.stdout and "render (" not in rf.stdout and r.returncode == 0
        and "s3 is a labelled slate" in r.stdout and vid2 and frames_of(os.path.join(PDIR, vid2)) == n_song else "FAIL",
        "a segment not built yet: a labelled slate in a preview; the final refuses before rendering anything",
        (rf.stdout.strip().splitlines() or ["-"])[-1][:120] + " | " + (r.stdout.strip().splitlines() or ["-"])[-1][:100])
    r = studio("pipeline", "gate", P, "--model", "fake", check=False, env=fake)
    st = json.load(open(os.path.join(PDIR, "work", "pipeline", "state.json"), encoding="utf-8"))
    g = (st.get("gates") or [{}])[-1]
    prompt = open(os.path.join(PDIR, "work", "critic", "last-prompt.md"), encoding="utf-8").read()
    say("PASS" if r.returncode == 0 and g.get("file") and os.path.exists(os.path.join(PDIR, g["file"])) and g.get("scores")
        and os.path.exists(os.path.join(PDIR, "work", "segments", "s2", "eyes", "s2_preview", "index.md"))
        and "`work/style-frames/a.png`" in prompt and "strong and specific enough to hand to builders" in prompt
        and "work/segments/s2/eyes/s2_preview/" in prompt else "FAIL",
        "pipeline gate: the critic on the hero's preview + the style frames; the segment's eyes next to it", f"{g.get('file')} {g.get('scores')}")
    import eyes
    scratch = os.path.join(PDIR, "cache", "_eyes")                  # (not the project's own work/pitches: no stray round)
    pv = eyes.out_dir(os.path.join(scratch, "work", "pitches", "round-1", "p2", "proof.mp4"))
    fv = eyes.out_dir(os.path.join(PDIR, "final", "music-video.mp4"))
    shutil.rmtree(scratch, ignore_errors=True)
    say("PASS" if os.path.normcase(pv) == os.path.normcase(os.path.join(scratch, "work", "pitches", "round-1", "p2", "eyes", "proof"))
        and os.path.normcase(fv) == os.path.normcase(os.path.join(PDIR, "work", "eyes", "music-video")) else "FAIL",
        "eyes out_dir: a pitch's proof keeps its eyes in its pitch folder, the final in work/eyes/", f"{pv}; {fv}")
    queue_check()
    r = studio("pipeline", "status", P, check=False)
    r2 = studio("pipeline", "build", P, check=False)
    say("PASS" if r.returncode == 0 and "render queue: free" in r.stdout and "preview ✓" in r.stdout and "gate language" in r.stdout
        and "the final render: about" in r.stdout and r2.returncode == 0 and "nothing to build" in r2.stdout else "FAIL",
        "pipeline status (segments, gates, the final's estimate, the render queue); build with every segment built: nothing to do",
        " | ".join(x for x in r.stdout.splitlines() if x.startswith(("the final", "render queue", "gate"))))


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
        w = W(d, started, "fake", "medium", most=6)
        w.done, w.n = [], n
        for i in range(rounds):
            w.poll(1000.0 + 5 * i, **kw)
            time.sleep(0.05)                        # the stub's thread ends the job
        return w
    a = watch(n=5)                                  # one slot left: it's the final's, though the preview is newer
    b = watch()                                     # the final first, then the newer preview
    c = watch(left=200)                             # 3 min left: no preview critique (its notes couldn't reach the director)
    e = watch(stopping=True)
    f = W(d, started, "fake", "medium", most=6)     # a preview critique running when the final is ready: it gives way
    f.done, f.n = [], 1
    f.job = {"key": f.key(prev), "video": "work/previews/preview_1.mp4", "proc": None}
    f.poll(1000.0)
    f.poll(1005.0)
    gave_way = f.job and f.job.get("superseded") and f.n == 0
    f.job = None
    f.poll(1010.0)
    time.sleep(0.05)
    say("PASS" if a.done == ["final/music-video.mp4"] and a.n == 6 and b.done == ["final/music-video.mp4", "work/previews/preview_1.mp4"]
        and c.done == ["final/music-video.mp4"] and e.done == [] and gave_way and f.done == ["final/music-video.mp4"] else "FAIL",
        "the critic watcher: the final first (and the reserved slot), the newer preview after it, none too late or while stopping, "
        "a running preview critique gives way to a new final", f"{a.done} {b.done} {c.done} {e.done} {f.done} gave way: {bool(gave_way)}")


def session_check():
    """The session plumbing (mv/session.py) with the stand-in, which answers like the real CLI (verified on Claude
    Code 2.1.311): messages sent while a turn runs join it (one result for all of them); a message to an idle session
    starts a turn of its own."""
    print("== 6d. the session plumbing and the pipeline's stages (stand-ins)", flush=True)
    sys.path.insert(0, os.path.join(STUDIO, "tools"))
    import mv.session as ms
    from mv.session import Session
    keep_cross = (ms.CROSS_S, ms.CROSS_SETTLE_S)
    ms.CROSS_S = 1.0                               # the first part: the joined messages went in long before the result
    d = os.path.join(PDIR, "cache", "_session")
    shutil.rmtree(d, ignore_errors=True)
    cmd = [PY, os.path.join(TESTS, "fake_claude.py"), "-p", "--input-format", "stream-json", "--output-format", "stream-json"]
    s = Session(cmd, PDIR, d, name="echo", env=dict(os.environ, FAKE_ECHO_S="2", PYTHONIOENCODING="utf-8")).start()

    def wait_turn(most=30):
        t0 = time.time()
        while time.time() - t0 < most and not s.turn_over():
            time.sleep(0.2)
        return s.turn_over()
    s.send("<!-- mv:echo --> one", "first")
    time.sleep(0.5)
    s.send("two", "second")
    s.send("three", "third")
    over1 = wait_turn()
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
    over2 = wait_turn()
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
    over3 = wait_turn()
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


class FakeSession:
    """The planner for the stage checks: the test says when it ends a turn (over()), and reads what it was sent."""

    def __init__(self):
        import threading
        self.name, self.lock, self.closed, self.busy, self.returncode = "planner", threading.RLock(), False, False, None
        self.status = {"events": [], "turns": 0, "cost_usd": 0.0, "messages_sent": [], "pid": 0}
        self.sent, self._over, self._alive, self.proc = [], False, True, None

    def send(self, text, why):
        if self.closed or not self._alive:
            return False
        self.sent.append((why, text))
        self.status["messages_sent"].append({"t": 0, "why": why})
        return True

    def over(self):
        self._over = True

    def turn_over(self, now=None, settle=2):
        return self._over

    def ack(self):
        self._over = False

    def alive(self):
        return self._alive and not self.closed

    def hung(self, now, after):
        return False

    def note(self, line):
        self.status["events"].append({"t": 0, "text": line})

    def tail(self, n=6):
        return [e["text"] for e in self.status["events"][-n:]]

    def close_input(self):
        self.closed = True

    def stop(self, reason=None, wait=20):
        self.closed = True

    def finish(self):
        pass

    def whys(self):
        return [w for w, _ in self.sent]


class FakeBuilder:
    def __init__(self, sid):
        self.id, self.name, self.state, self.error, self.critique, self.s = sid, f"builder {sid}", "working", "", None, None
        self.it = {"id": sid, "scene": f"work/scenes/{sid}/scene.js", "exists": True}


class FakeRound:
    """The builders for the stage checks: done when the test says (finish())."""

    def __init__(self, pl, ids, tasks, on_event):
        self.builders, self.tasks, self.on_event, self.stopping, self.critiques = [FakeBuilder(i) for i in ids], tasks, on_event, None, []
        self.stopped_as = None
        pl.rounds.append(self)

    def poll(self, now=None):
        pass

    @property
    def done(self):
        return all(b.state in ("built", "failed", "stopped", "skipped") for b in self.builders)

    def finish(self, state="built", why=""):
        for b in self.builders:
            b.state, b.error = state, why
            self.on_event(state, b, why)

    def stop(self, why, kind="forced"):
        self.stopping, self.stopped_as = why, kind
        for b in self.builders:
            if b.state not in ("built", "failed", "stopped", "skipped"):
                b.state = "stopped" if kind == "user" else "built"

    def sessions(self):
        return [{"name": b.name, "role": "builder", "segment": b.id, "state": b.state, "cost_usd": 0.1} for b in self.builders]

    def lines(self):
        return [f"- {b.id}: {b.state}" for b in self.builders]

    def requests(self):
        return []

    def retire(self):
        self.retired = True


class FakePipeline:
    """tools/pipeline.py for the stage checks: a scripted plan, and long steps that come back at once."""
    BUILDER_GRACE_S = 180

    def __init__(self, pdir):
        import pipeline
        self.real, self.pdir, self.rounds, self.calls, self.state = pipeline, pdir, [], [], {}
        self.plan_ok, self.stage, self.tasks, self.final_ok, self.has_plan = True, "planned", {}, True, True
        self.Builder = pipeline.Builder
        self.FILM_FOCUS, self.mmss, self.outline, self.plan_text = pipeline.FILM_FOCUS, pipeline.mmss, lambda v: "-", lambda v, p, table=True: "errors: (test)"
        self.stand_in_text = pipeline.stand_in_text
        self.critique = None

    def validate(self, pdir, plan=None, ready=False):
        items = [{"id": "s1", "kind": "segment", "owner": "builder", "status": "todo", "t0": 0.0, "t1": 20.0, "renderer": "web",
                  "scene": "work/scenes/s1/scene.js", "exists": True},
                 {"id": "s2", "kind": "segment", "owner": "planner", "status": "drafted", "t0": 20.0, "t1": 40.0, "renderer": "web",
                  "scene": "work/scenes/s2/scene.js", "exists": True}]
        if not self.has_plan:
            return {"ok": False, "errors": ["there is no work/plan.json yet: write it"], "plan": None, "items": [], "warnings": []}
        return {"ok": self.plan_ok, "errors": [] if self.plan_ok else ["segments[1] (s2): a gap before it"], "warnings": [],
                "plan": {"stage": self.stage, "segments": []}, "stage": self.stage, "hero": "s2", "items": items,
                "style_frames": [], "lib": "work/scenes/lib/"}

    def gate(self, pdir, *a, **kw):
        self.calls.append("gate")
        return {"ok": True, "errors": [], "video": "work/segments/s2/s2_preview.mp4", "critique": self.critique}

    def assemble(self, pdir, quality="preview", **kw):
        self.calls.append(f"assemble {quality}" + (" stand-ins" if kw.get("stand_ins") else ""))
        if quality == "final" and not self.final_ok and not kw.get("stand_ins"):
            return {"ok": False, "error": "the final needs every segment: s1: no scene", "renders": []}
        return {"ok": True, "video": "final/music-video.mp4" if quality == "final" else "work/previews/preview_1.mp4",
                "checks": {"flash": "PASS"}, "placeholders": [], "stand_ins": [], "critique": None}

    def run_critic(self, *a, **kw):
        return None

    def estimate_final(self, pdir, v=None):
        return 60.0, "the test"

    def estimate_rough(self, pdir):
        return 60.0, "the test"

    def load_state(self, pdir):
        return self.state

    def update_state(self, pdir, fn):
        fn(self.state)
        return self.state

    def set_stage(self, pdir, stage):
        self.stage = stage

    def set_status(self, pdir, ch):
        return True

    def read_tasks(self, pdir, v=None):
        return dict(self.tasks), []

    def consume_tasks(self, pdir, n):
        self.calls.append("tasks consumed")
        self.tasks = {}

    def outputs(self, pdir, it, q):
        return pdir, os.path.join(pdir, "nothing.mp4")

    def BuildRound(self, pdir, v, ids, tasks, *a, on_event=None, **kw):
        return FakeRound(self, ids, tasks, on_event)


def pipeline_machine_check():
    """The pipeline's stages (director.PipelineRun) driven in-process on a simulated clock, with a scripted planner and
    stand-ins for the studio's long steps: the whole way through; a plan that never validates (three tries, then
    solo); no plan at the plan stage's deadline + 5 min (solo); a builder that fails (the planner hears); the studio
    starting the final when time is short (the builders stopped); STOP while building; the planner's session ending
    (the studio finishes alone); tasks.json sending a builder back; a final that can't be assembled (the planner fixes
    it, then a neighbour's scene stands in)."""
    import director
    root = os.path.join(PDIR, "cache", "_machine")

    def make(revise=False, critic=True, budget=3600, stage="planned"):
        shutil.rmtree(root, ignore_errors=True)
        for sub in ("work/director/run-1", "final", "work/critic", "work/scenes/s1"):
            os.makedirs(os.path.join(root, *sub.split("/")), exist_ok=True)
        open(os.path.join(root, "work", "critic", "gate_01.md"), "w").write("# Critique\nthe language (test)\n")
        json.dump({"video": "work/segments/s2/s2_preview.mp4", "scores": {"ambition": 7}, "file": "work/critic/gate_01.md",
                   "model": "fake", "cost_usd": 0.01}, open(os.path.join(root, "work", "critic", "gate_01.json"), "w"))
        s = FakeSession()
        pl = FakePipeline(root)
        pl.stage = stage
        pl.critique = {"file": "work/critic/gate_01.md"} if critic else None
        t0 = time.time()
        status = {"run": 1, "state": "running", "kind": "first", "model": "fake", "effort": "max", "budget_s": budget,
                  "started": t0, "deadline": t0 + budget, "events": s.status["events"], "turns": 0, "cost_usd": 0.0,
                  "messages_sent": s.status["messages_sent"], "critic_model": "fake", "critiques": [], "critic_cost_usd": 0.0,
                  "mode": "pipeline"}
        R = director.PipelineRun(root, 1, os.path.join(root, "work", "director", "run-1"), status, s, budget, t0,
                                 critic={"model": "fake", "effort": "medium", "max": 6} if critic else None, log=lambda x: None,
                                 builders=2, builder_model="fake", builder_effort="high", revise=revise, pl=pl)
        R.inline = True
        R.begin()
        return R, s, pl, t0

    def tick(R, t, n=1):
        ok = True
        for _ in range(n):
            ok = R.step(t)
        return ok
    out = {}
    # the whole way: plan -> gate -> build -> assemble -> revise -> final
    R, s, pl, t0 = make()
    s.over()
    tick(R, t0 + 60, 2)                      # the plan validates: gate 1, the report to the planner
    s.over()
    tick(R, t0 + 120, 2)                     # its last revision of the language: the builders start
    rnd = pl.rounds[-1] if pl.rounds else None
    s.over()
    tick(R, t0 + 600)
    if rnd:
        rnd.finish("built")
    tick(R, t0 + 660, 3)                     # the planner and the builders done: assemble, the report
    pl.stage = "final"
    s.over()
    tick(R, t0 + 900, 3)                     # "final": the studio renders it, the planner gets the checks
    s.over()
    tick(R, t0 + 1000, 2)                    # delivered with most of the hour left: the nudge, back to stage 4
    nudged = s.whys()[-1] == "use the time left" and R.stage == "revise" and pl.stage == "planned"
    pl.stage = "final"
    s.over()
    tick(R, R.deadline - 600, 3)             # final again, 10 min before the end: no more nudges, the session ends
    s.over()
    tick(R, R.deadline - 590, 2)
    out["whole"] = ([x["stage"] for x in R.stages], s.whys(), pl.calls, s.closed, nudged)
    ok_whole = ([x["stage"] for x in R.stages] == ["plan", "gate", "build", "assemble", "revise", "final", "revise", "final"]
                and rnd and rnd.builders[0].id == "s1" and nudged
                and "gate 1: the critic on the language" in s.whys() and "builders started" in s.whys()
                and any(w.startswith("assemble report") for w in s.whys()) and s.whys().count("the final: fix any FAIL, write report.md") == 2
                and pl.calls == ["gate", "assemble preview", "assemble final", "assemble final"] and s.closed
                and pl.state.get("stage") == "final" and pl.state.get("costs") is not None)
    # a plan that never validates: three tries, then solo
    R, s, pl, t0 = make()
    pl.plan_ok = False
    for k in range(3):
        s.over()
        tick(R, t0 + 60 * (k + 1))
    out["invalid"] = (R.mode, s.whys())
    ok_invalid = R.mode == "solo" and s.whys()[:2] == ["plan.json isn't valid (1 of 3)", "plan.json isn't valid (2 of 3)"] \
        and s.whys()[-1] == "switching to solo" and R.critics is not None and "switching this run to solo" in s.sent[-1][1]
    # no plan at all by the plan stage's deadline + 5 min: solo
    R, s, pl, t0 = make()
    pl.has_plan = False
    tick(R, R.plan_by + 1)
    tick(R, R.plan_by + 301)
    out["late"] = (R.mode, s.whys())
    ok_late = R.mode == "solo" and "the plan stage ends" in s.whys() and s.whys()[-1] == "switching to solo"
    # a builder fails: the planner hears; then the studio starts the final when time is short
    R, s, pl, t0 = make(critic=False)
    s.over()
    tick(R, t0 + 60, 2)                      # critic off: straight from the gate to the builders
    rnd = pl.rounds[-1] if pl.rounds else None
    if rnd:
        rnd.on_event("failed", rnd.builders[0], "its session ended (exit 1)")
    told = [t for w, t in s.sent if w == "builder s1 failed"]
    tick(R, R.deadline - R.final_need() + 5)
    out["forced"] = (R.stage, s.whys(), rnd and rnd.stopped_as)
    ok_fail_forced = bool(rnd) and told and "take it over" in told[0] and R.stage == "final" and "the final starts now" in s.whys() \
        and rnd.stopped_as == "forced" and pl.calls[-1] == "assemble final"
    # STOP while building
    R, s, pl, t0 = make(critic=False)
    s.over()
    tick(R, t0 + 60, 2)
    rnd = pl.rounds[-1] if pl.rounds else None
    open(os.path.join(root, "work", "director", "STOP"), "w").write("x")
    tick(R, t0 + 100)
    s.over()
    tick(R, t0 + 102)
    out["stop"] = (R.stop_reason, s.whys(), rnd and rnd.stopped_as, s.closed)
    ok_stop = R.stop_reason == "stopped by the user" and "stop" in s.whys() and rnd and rnd.stopped_as == "user" and s.closed \
        and not any(c.startswith("assemble") for c in pl.calls)
    # the planner's session ends while it revises: the studio assembles the final alone
    R, s, pl, t0 = make(critic=False)
    s.over()
    tick(R, t0 + 60, 2)
    pl.rounds[-1].finish("built")
    s.over()
    tick(R, t0 + 300, 3)                     # assembled: now in revise
    s._alive = False
    s.returncode = 1
    still = tick(R, t0 + 400, 3)
    out["gone"] = (R.stage, pl.calls, still, R.end_reason)
    ok_gone = R.stage == "final" and pl.calls[-1].startswith("assemble final") and not still and "planner's session ended" in R.end_reason
    # tasks.json sends a builder back; a final that can't be assembled: the planner fixes it, then stand-ins
    R, s, pl, t0 = make(critic=False)
    s.over()
    tick(R, t0 + 60, 2)
    pl.rounds[-1].finish("built")
    s.over()
    tick(R, t0 + 300, 3)
    pl.tasks = {"s1": "make the stop hold longer"}
    s.over()
    tick(R, t0 + 360)
    fix_round = pl.rounds[-1] if len(pl.rounds) == 2 else None
    if fix_round:
        fix_round.finish("built")
    s.over()
    tick(R, t0 + 900, 3)                     # assembled again
    pl.stage, pl.final_ok = "final", False
    s.over()
    tick(R, t0 + 1000, 3)                    # the final can't be made: the planner is asked to fix it
    asked = "the final couldn't be assembled" in s.whys()
    s.over()
    tick(R, t0 + 1100, 3)                    # it says it did: assembled again, now with stand-ins
    out["tasks"] = (s.whys(), pl.calls, fix_round and fix_round.tasks)
    ok_tasks = bool(fix_round) and fix_round.tasks == {"s1": "make the stop hold longer"} and "tasks consumed" in pl.calls \
        and asked and pl.calls[-1] == "assemble final stand-ins" and "the final: fix any FAIL, write report.md" in s.whys() \
        and [x["stage"] for x in R.stages].count("build") == 2
    # the final fails, the planner is asked to fix it and never ends its turn: the final with stand-ins when time is short;
    # the same, but its session ends: the studio makes it at once
    fixing = {}
    for how in ("busy", "gone"):
        R, s, pl, t0 = make(critic=False)
        s.over()
        tick(R, t0 + 60, 2)
        pl.rounds[-1].finish("built")
        s.over()
        tick(R, t0 + 300, 3)
        pl.stage, pl.final_ok = "final", False
        s.over()
        tick(R, t0 + 400, 3)
        asked = R.fixing and "the final couldn't be assembled" in s.whys()
        if how == "gone":
            s._alive, s.returncode = False, 1
            tick(R, t0 + 410, 2)
        else:
            tick(R, t0 + 500, 2)
            waited = pl.calls[-1] == "assemble final"
            tick(R, R.deadline - R.final_need() + 5, 2)
        fixing[how] = (asked, pl.calls, s.whys()[-2:], how == "gone" or waited)
    out["fixing"] = fixing
    ok_fixing = all(a and c[-1] == "assemble final stand-ins" and w for a, c, _, w in fixing.values()) \
        and "the final starts now" in fixing["busy"][2]
    # the final's estimate leaves no room for the stages: a final now (before anything is built) would be the hero
    # everywhere, so the run goes solo; and at the gate's end with no time for anyone to build the rest: solo too
    R, s, pl, t0 = make(critic=False)
    pl.estimate_final = lambda pdir, v=None: (2400.0, "the test")
    s.over()
    tick(R, t0 + 60)                         # the plan validates: the gate (inline: done at once)
    tick(R, t0 + 100)                        # with the plan's estimate the final would have to start now: solo
    out["no_room"] = (R.mode, s.whys()[-1:], pl.calls)
    ok_room = R.mode == "solo" and s.whys()[-1] == "switching to solo" and "no room is left for the pipeline's stages" in s.sent[-1][1] \
        and not any(c.startswith("assemble") for c in pl.calls)
    # the early-finish nudge keeps the cut it delivered as a version before the studio renders another over it
    R, s, pl, t0 = make(critic=False)
    s.over()
    tick(R, t0 + 60, 2)
    pl.rounds[-1].finish("built")
    s.over()
    tick(R, t0 + 300, 3)
    pl.stage = "final"
    s.over()
    tick(R, t0 + 400, 3)
    open(os.path.join(root, "final", "music-video.mp4"), "wb").write(b"the delivered cut (test)")
    s.over()
    tick(R, t0 + 500, 2)                     # the nudge: back to stage 4, the cut kept as v1
    kept = json.load(open(os.path.join(root, "project.json"), encoding="utf-8")).get("versions") if os.path.exists(os.path.join(root, "project.json")) else []
    out["kept"] = (kept, s.whys()[-1:])
    ok_kept = len(kept or []) == 1 and os.path.exists(os.path.join(root, "final", "music-video_v1.mp4")) and R.stage == "revise" \
        and "kept as v1" in s.sent[-1][1]
    # a review round starts at stage 4: the plan's "final" set back; nothing changed: a reminder; "final": the final
    R, s, pl, t0 = make(revise=True, critic=False, stage="final")
    first, set_back = R.stage, pl.stage == "planned"
    s.over()
    tick(R, t0 + 60)
    reminded = s.whys()[-1:] == ["no next step"]
    pl.stage = "final"
    s.over()
    tick(R, t0 + 120, 3)
    out["revise"] = (first, set_back, s.whys(), pl.calls)
    ok_revise = first == "revise" and set_back and reminded and R.stage == "final" and pl.calls == ["assemble final"]
    ok_live = live_notes_machine(make, tick, root, out)
    shutil.rmtree(root, ignore_errors=True)
    bad = [k for k, v in (("the whole way", ok_whole), ("a review round from stage 4", ok_revise), ("never valid -> solo", ok_invalid), ("no plan by the deadline -> solo", ok_late),
                          ("a builder fails, then the final when time is short", ok_fail_forced), ("STOP while building", ok_stop),
                          ("the planner's session ends", ok_gone), ("tasks.json, a final that can't be made", ok_tasks),
                          ("the planner fixing the final: busy too long, or gone", ok_fixing), ("no room for the stages -> solo", ok_room),
                          ("the nudge keeps the delivered cut", ok_kept), ("the user's live notes", ok_live)) if not v]
    say("FAIL" if bad else "PASS", "the pipeline's stages, every way out (in-process, stand-ins): " + ", ".join(
        ["the whole way (and the early-finish nudge back to stage 4)", "never valid -> solo", "no plan by the deadline -> solo", "a builder fails", "the final when time is short",
         "STOP", "the planner gone", "tasks.json", "a final that can't be made", "a review round from stage 4",
         "the planner fixing the final too long or gone (stand-ins)", "no room for the stages (solo)", "the nudge keeps the delivered cut",
         "the user's live notes (sent where a turn is safe, held while the studio assembles and then in its report, held "
         "in the final stage, solo too)"]),
        ("failed: " + "; ".join(bad) + f" {out}")[:2500] if bad else "")


def live_notes_machine(make, tick, root, out):
    """The user's live notes during a run (director.LiveNotes, Runner.live_step), on the simulated clock: an idle
    planner gets them while builders work (the assemble then waits for its turn), with the command that marks them
    done; a note from before the run, or one done already, isn't sent; one that comes while the studio assembles is
    held and goes into the assemble report with the ones sent since; each is marked forwarded_run in review/notes.json;
    while the final renders (the planner busy writing report.md, as the studio asked) and after, one is held for the
    review round; a solo director gets them, a stopping run doesn't."""
    import director
    np = os.path.join(root, "review", "notes.json")

    def note(text, **kw):
        os.makedirs(os.path.dirname(np), exist_ok=True)
        ns = json.load(open(np, encoding="utf-8")) if os.path.exists(np) else []
        ns.append(dict({"id": int(time.time() * 1000) + len(ns), "version": "live", "t": 12.5, "text": text, "done": False,
                        "scene": "work/scenes/s1/scene.js", "timeline": "plan.json"}, **kw))
        json.dump(ns, open(np, "w", encoding="utf-8"))
    marks = lambda: {n["text"]: n.get("forwarded_run") for n in json.load(open(np, encoding="utf-8"))}   # noqa: E731
    R, s, pl, t0 = make(critic=False)
    note("from before the run", id=int((t0 - 120) * 1000))
    note("done already", done=True)
    s.over()
    tick(R, t0 + 60, 2)                      # the gate (critic off) -> the builders; the planner builds its own
    s.over()
    tick(R, t0 + 120)                        # it ended its turn: done with its own segments
    was_done = R.planner_done
    note("the marks should hit harder")
    R.live_step(t0 + 130)
    first = s.sent[-1] if s.sent else ("", "")
    m1 = marks()
    R.stage, R.job = "assemble", {"kind": "assemble", "done": False}      # the studio assembles; the planner is idle
    note("the type comes too late", t=30.0)
    n_sent = len(s.sent)
    R.live_step(t0 + 200)
    held = len(s.sent) == n_sent and marks().get("the type comes too late") is None
    R.job = None
    R.assemble_done({"ok": True, "video": "work/previews/preview_1.mp4", "checks": {}, "placeholders": []}, t0 + 210)
    report = s.sent[-1][1] if s.sent else ""
    m2 = marks()
    R.stage, R.job = "final", {"kind": "final", "done": False}            # the final renders; the planner writes report.md
    s.busy = True
    note("the end should land harder", t=40.0)
    n_sent = len(s.sent)
    R.live_step(t0 + 300)
    R.job = None                                                          # the final is in: it fixes FAILs, writes report.md
    R.live_step(t0 + 310)
    s.busy = False
    R.live_step(t0 + 320)
    held_final = len(s.sent) == n_sent and marks().get("the end should land harder") is None
    ok_pipe = (was_done and first[0] == "the user's live note (1)" and "playing your scenes live" in first[1]
               and "**the marks should hit harder**" in first[1] and "they saw work/scenes/s1/scene.js" in first[1]
               and "studio.py note-done" in first[1] and "next assemble report lists these notes again" in first[1]
               and not R.planner_done
               and m1 == {"from before the run": None, "done already": None, "the marks should hit harder": 1} and held
               and s.whys()[-1].startswith("assemble report") and "The user's live notes since the last report" in report
               and "studio.py note-done" in report
               and "**the marks should hit harder**" in report and "**the type comes too late**" in report and "(open)" in report
               and m2.get("the type comes too late") == 1 and held_final)
    # solo: a message at once; a stopping run sends none
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "work", "director", "run-1"), exist_ok=True)
    s2 = FakeSession()
    t1 = time.time()
    st = {"run": 1, "events": s2.status["events"], "messages_sent": s2.status["messages_sent"], "critiques": [], "critic_cost_usd": 0.0}
    R2 = director.Runner(root, 1, os.path.join(root, "work", "director", "run-1"), st, s2, 3600, t1, log=lambda x: None)
    note("more room before the drop")
    R2.live_step(t1 + 10)
    solo = s2.sent[-1] if s2.sent else ("", "")
    R2.stop_reason = "stopped by the user"
    note("never mind")
    R2.live_step(t1 + 20)
    ok_solo = (solo[0] == "the user's live note (1)" and "Then carry on" in solo[1] and len(s2.sent) == 1
               and marks() == {"more room before the drop": 1, "never mind": None})
    out["live"] = (first[0], m1, m2, held, held_final, s.whys()[-2:], solo[0], marks())
    return ok_pipe and ok_solo


def builder_failures_check():
    """Real builders (pipeline.BuildRound, Builder, mv/session.py) against the stand-in going wrong: one hangs (no
    events: stopped as hung), one never writes its scene (two reminders, then failed), one crashes mid-turn (failed);
    at most two at once, so the third waits its turn. Then the gate on a plan that still says planning and names no
    hero (a clear error, no crash), and the one rule for when the pipeline's stages fit (choose_mode = stage_times)."""
    import director
    import pipeline
    plan_p = os.path.join(PDIR, "work", "plan.json")
    keep_plan = open(plan_p, "rb").read()
    good = json.loads(keep_plan)
    segs = {x["id"]: x for x in good["segments"]}
    s1, s2, s3 = segs["s1"], segs["s2"], segs["s3"]
    mid = round((s1["from"] + s1["to"]) / 2, 3)
    seg = lambda sid, a, b: dict(s1, id=sid, name=f"{sid} (goes wrong)", scene=f"work/scenes/{sid}/scene.js", owner="builder",  # noqa: E731
                                 status="todo", **{"from": a, "to": b})
    test = dict(good, segments=[seg("f1", 0.0, mid), seg("f2", mid, s1["to"]), s2, seg("f3", s3["from"], s3["to"])], overlays=[])
    keep_env = {k: os.environ.get(k) for k in ("MV_CLAUDE", "FAKE_BUILDER")}
    keep_hung = pipeline.BUILDER_HUNG_S
    events, t0 = [], time.time()
    try:
        json.dump(test, open(plan_p, "w", encoding="utf-8"), indent=1)
        os.environ.update(MV_CLAUDE=os.path.join(TESTS, "fake_claude.py"), FAKE_BUILDER="f1:hang,f2:noscene,f3:crash")
        pipeline.BUILDER_HUNG_S = 4
        v = pipeline.validate(PDIR, ready=True)
        rnd = pipeline.BuildRound(PDIR, v, ["f1", "f2", "f3"], None, 2, "fake", "high", None, None, None, time.time() + 600, 91, t0,
                                  on_event=lambda state, b, why: events.append((b.id, state, why)))
        while not rnd.done and time.time() - t0 < 150:
            rnd.poll()
            time.sleep(0.5)
        rnd.retire()
        time.sleep(1)
        bs = {b.id: b for b in rnd.builders}
        first_end = min(bs["f1"].ended or 1e12, bs["f2"].ended or 1e12)
        ok_b = rnd.done and all(b.state == "failed" for b in bs.values()) and "no sign of life" in bs["f1"].error \
            and "wrote no scene" in bs["f2"].error and "session ended" in bs["f3"].error and len(events) == 3 \
            and (bs["f3"].started or 0) >= first_end - 1 and not any(b.s and b.s.alive() for b in bs.values())
        detail = f"{ {k: (b.state, b.error[:50]) for k, b in bs.items()} }; f3 started {round((bs['f3'].started or 0) - t0)} s, " \
                 f"the first end {round(first_end - t0)} s; {time.time() - t0:.0f} s"
        # the gate on a plan that says planning and has no hero: what to fix, not a crash
        json.dump(dict(good, stage="planning", hero=None), open(plan_p, "w", encoding="utf-8"), indent=1)
        loose = pipeline.validate(PDIR)
        g = pipeline.gate(PDIR, "off", log=lambda x: None)
        ok_g = loose["ok"] and not g["ok"] and any("hero" in e for e in g["errors"])
    finally:
        open(plan_p, "wb").write(keep_plan)
        pipeline.BUILDER_HUNG_S = keep_hung
        for k, x in keep_env.items():
            if x is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = x
        for sid in ("f1", "f2", "f3"):
            shutil.rmtree(os.path.join(PDIR, "work", "pipeline", "sessions", f"b91-{sid}"), ignore_errors=True)
            shutil.rmtree(os.path.join(PDIR, "work", "segments", sid), ignore_errors=True)
            shutil.rmtree(os.path.join(PDIR, "work", "scenes", sid), ignore_errors=True)
    say("PASS" if ok_b else "FAIL", "real builders that go wrong: one hangs (stopped as hung), one writes no scene (reminded, then "
        "failed), one crashes (failed); two at once, the third waits its turn", detail)
    # one rule for when the pipeline fits: auto picks it from the smallest budget whose stages fit, and not below
    est = pipeline.estimate_final(PDIR)
    b = pipeline.pipeline_from(est[0])
    below = director.choose_mode(PDIR, "auto", b - 60, 3, False)[0]
    at = director.choose_mode(PDIR, "auto", b, 3, False)[0]
    sp = (json.load(open(os.path.join(STUDIO, "tools", "speeds.json"))) if os.path.exists(os.path.join(STUDIO, "tools", "speeds.json")) else {}).get("renderers") or {}
    cyc = (sp.get("blender_cycles") or {})
    want = (cyc.get("1080p") or {}).get("s_per_frame", 0) / (cyc.get("540p_preview") or {}).get("s_per_frame", 1) if cyc else None
    got = pipeline.speed_row(sp, {"renderer": "blender", "render_args": ["--engine", "cycles"]})[2]
    measured = any((x.get("preview") or {}).get("s_per_frame") for x in (json.load(open(pipeline.renders_path(PDIR), encoding="utf-8"))
                                                                         if os.path.exists(pipeline.renders_path(PDIR)) else {}).values())
    say("PASS" if ok_g and below == "solo" and at == "pipeline" and ("preview renders" in est[1]) == measured
        and (want is None or abs((got or 0) - want) < 1e-6)
        else "FAIL", "the gate on a plan that isn't ready says what's missing; one rule for when the stages fit (auto, the brief, "
        "the web app); the final's estimate scales each preview by what a final frame costs here (Cycles ×6, not ×2.5)",
        f"gate: {g.get('errors')}; the pipeline from {b // 60} min ({below} at {b // 60 - 1}, {at} at {b // 60}); estimate {est[0]:.0f} s "
        f"({est[1]}); Cycles ratio {got}")


def pipeline_run_check(env):
    """`studio.py direct --mode pipeline` with the stand-in: the planner writes the plan and the hero, gate 1, two
    builders (each its scene, its preview, a critic round), the assembled preview with the critic's film report, the
    planner's "final", the final with the checks and the critic, the version delivered; then `pipeline build` for an
    interactive planner."""
    print("== 7b. the pipeline end to end (stand-in for Claude Code)", flush=True)
    for sid in ("s1", "s3"):                       # the builders write these
        p = os.path.join(PDIR, "work", "scenes", sid, "scene.js")
        if os.path.exists(p):
            os.remove(p)
    import threading
    from mv.live import update_notes
    runs0 = (json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8")).get("runs") or [{}])[-1].get("run") or 0
    mid = round(json.load(open(os.path.join(PDIR, "song", "song-map.json"), encoding="utf-8"))["duration"] / 2, 2)
    wrote = {}

    def user():                                    # the user plays the scenes live while the planner works, and leaves a note
        t0_ = time.time()
        while time.time() - t0_ < 600:
            cur = json.load(open(os.path.join(PDIR, "work", "director", "current.json"), encoding="utf-8")) \
                if os.path.exists(os.path.join(PDIR, "work", "director", "current.json")) else {}
            if (cur.get("run") or 0) > runs0 and cur.get("state") == "running":
                time.sleep(1)
                n = {"id": int(time.time() * 1000), "version": "live", "t": mid, "text": "the field should breathe here (live)",
                     "done": False, "at": time.strftime("%Y-%m-%d %H:%M"), "scene": "work/scenes/s2/scene.js",
                     "timeline": "plan.json"}
                update_notes(PDIR, lambda ns: ns.append(n))
                wrote.update(n)
                return
            time.sleep(0.2)
    th = threading.Thread(target=user, daemon=True)
    th.start()
    t0 = time.time()
    r = studio("direct", P, "--mode", "pipeline", "--budget", "40m", "--model", "fake", "--critic-model", "fake", "--builders", "2",
               "--no-nudge", check=False, env=env, timeout=2400)
    th.join(5)
    pj = json.load(open(os.path.join(PDIR, "project.json"), encoding="utf-8"))
    last = (pj.get("runs") or [{}])[-1]
    st = json.load(open(os.path.join(PDIR, "work", "pipeline", "state.json"), encoding="utf-8"))
    stages = [x["stage"] for x in st.get("stages") or []]
    ses = {x.get("name"): x for x in st.get("sessions") or []}
    v = next((x for x in pj.get("versions") or [] if x.get("n") == last.get("version")), {})
    rep_md = open(os.path.join(PDIR, "report.md"), encoding="utf-8").read() if os.path.exists(os.path.join(PDIR, "report.md")) else ""
    say("PASS" if r.returncode == 0 and last.get("mode") == "pipeline" and last.get("version") and stages[:6] ==
        ["plan", "gate", "build", "assemble", "revise", "final"] and v.get("checks") and all(x == "PASS" for x in v["checks"].values())
        and "pipeline's test stand-in" in rep_md and "- Pipeline: the planner" in rep_md else "FAIL",
        "studio.py direct --mode pipeline: all five stages, the final delivered, every check passes",
        f"run {last.get('run')}: v{last.get('version')} {v.get('checks')}; stages {stages}; {time.time() - t0:.0f} s; "
        f"ended: {last.get('stop_reason')}" if last else (r.stdout + r.stderr)[-1500:])
    plan = json.load(open(os.path.join(PDIR, "work", "plan.json"), encoding="utf-8"))
    status = {x["id"]: x.get("status") for x in plan.get("segments") or []}
    b1 = ses.get("builder s1") or {}
    brief = open(os.path.join(PDIR, b1.get("brief") or "x"), encoding="utf-8").read() if b1.get("brief") and os.path.exists(os.path.join(PDIR, b1["brief"])) else ""
    say("PASS" if ses.get("builder s1", {}).get("state") == "built" and ses.get("builder s3", {}).get("state") == "built"
        and (ses["builder s1"].get("critique") or {}).get("scores") and status.get("s1") == "built" and status.get("s3") == "built"
        and "<!-- mv:builder -->" in brief and "don't read the song folder" in brief and "Bar by bar" in brief
        and "`work/style-frames/a.png`" in brief and "--only s1" in brief and "Nothing is revealed in a shot's last beat" in brief
        else "FAIL", "the builders: their own sessions and narrow briefs (the song over the range as text, the style frames, "
        "the craft rules), a critic round each, built in plan.json",
        f"{ {k: x.get('state') for k, x in ses.items()} }; plan {status}; brief {len(brief)} chars")
    crs = st.get("critiques") or []
    kinds = sorted({c.get("kind") for c in crs})
    gate = (st.get("gates") or [{}])[-1]
    rs = json.load(open(os.path.join(PDIR, "work", "director", f"run-{last.get('run')}", "status.json"), encoding="utf-8"))
    sent = [x["why"] for x in rs.get("messages_sent") or []]
    say("PASS" if gate.get("scores") and {"gate", "builder", "assemble", "final"} <= set(kinds) and "gate 1: the critic on the language" in sent
        and "builders started" in sent and any(x.startswith("assemble report") for x in sent)
        and "the final: fix any FAIL, write report.md" in sent and (st.get("costs") or {}).get("critic", 0) > 0
        and os.path.exists(os.path.join(PDIR, "final", f"critic_v{last.get('version')}.md")) else "FAIL",
        "the critic at every gate (the language, each builder, the film, the final); the reports reach the planner; costs by role",
        f"critiques {kinds}; messages {sent}; costs {st.get('costs')}")
    ln = next((n for n in json.load(open(os.path.join(PDIR, "review", "notes.json"), encoding="utf-8")) if n.get("id") == wrote.get("id")), {})
    md = os.path.join(PDIR, "work", "director", f"run-{last.get('run')}", "messages.md")
    msgs = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
    own = "the user's live note (1)" in sent
    say("PASS" if wrote and ln.get("forwarded_run") == last.get("run") and "the field should breathe here (live)" in msgs
        and "segment s2 (yours, the hero)" in msgs and "The user's live notes since the last report" in msgs
        and (not own or (ln.get("done") and "test stand-in" in (ln.get("fix") or ""))) else "FAIL",
        "a live note left while the pipeline runs reaches the planner within seconds (with the song context and the segment), "
        "is marked forwarded, done by the planner, and listed in the next assemble report",
        f"{'its own message' if own else 'in the assemble report'}; forwarded_run {ln.get('forwarded_run')}, done {ln.get('done')}, "
        f"fix {ln.get('fix')!r}")
    # an interactive planner's `pipeline build`
    r = studio("pipeline", "build", P, "--only", "s1", "--builder-model", "fake", "--critic-model", "fake", "--budget", "6m",
               check=False, env=env, timeout=900)
    st = json.load(open(os.path.join(PDIR, "work", "pipeline", "state.json"), encoding="utf-8"))
    b = [x for x in st.get("sessions") or [] if x.get("name") == "builder s1"]
    hist = os.path.join(PDIR, "work", "pipeline", "history", f"state_run-{last.get('run')}.json")
    run_st = json.load(open(hist, encoding="utf-8")) if os.path.exists(hist) else {}
    say("PASS" if r.returncode == 0 and "pipeline build: done" in r.stdout and "- s1: built" in r.stdout and len(b) == 1
        and st.get("mode") == "interactive" and st.get("state") == "done" and run_st.get("run") == last.get("run")
        and run_st.get("mode") == "pipeline" and "the lib could export the plate colour" in r.stdout else "FAIL",
        "pipeline build --only s1 (an interactive planner): a builder, its critic round, the summary with its requests; "
        "its own state.json (the run's kept in history/)",
        " | ".join(x for x in r.stdout.splitlines() if x.startswith(("pipeline build", "- s1", "  s1")))[:400] or (r.stdout + r.stderr)[-800:])


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
