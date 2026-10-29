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
    reusing it as final/critic_v1.md, an old Claude Code without --max-budget-usd, the lenient parser
 7. the autonomous director runner end to end with a stand-in for Claude Code (tests/fake_claude.py: no usage spent;
    the critic's notes reach the director as a message and the new version gets final/critic_vN.md), then a review
    round (a note -> --revise -> the next version, the note marked done)
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
