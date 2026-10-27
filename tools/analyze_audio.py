"""Music analysis of the project's song (carried over from aftermovie-studio: stems + neural downbeats).

  python tools/studio.py run <project> analyze_audio.py [--fallback-beats]

Inputs: the song in song/, the stems in song/stems/ (separate.py), beat_this beats/downbeats (computed and cached
here), lyrics: the user's own (project.json "lyrics" or song/lyrics.lrc / lyrics.txt) first, else LRCLIB
(cache/lyrics/search.json), else a Whisper transcription (cache/lyrics/transcribed.json).

Writes cache/audio.json with:
  - beats: constant grid for click-steady songs (fit to the drum stem, phase checked against the kick drum and
    beat_this), otherwise beat_this beats snapped to drum onsets and smoothed
  - downbeats/bars (beat_this downbeat votes)
  - kick / snare / cymbal / bass-hit / generic onset events with strengths
  - energy curve (per 0.1 s, per beat, per bar), sections (bar-level novelty)
  - vocal activity + vocal onsets from the vocal stem
  - lyric lines aligned to this exact file, with estimated word times (align_words.py refines them)

Offline fallback (new): when the beat_this checkpoint can't be loaded (first use downloads it from cloud.cp.jku.at)
the beats come from librosa's dynamic-programming tracker with a time-varying tempo (so tempo changes survive),
and the downbeats from the beat phase where kick, bass and harmony changes agree. Less reliable on live or
syncopated music; "beats" in report.json says which tracker ran.
"""
import argparse, json, os, re, sys
import numpy as np
import librosa
import scipy.signal as sps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.common import CACHE, SONG, STEMS, song_file, project, need_project  # noqa: E402

SR = 22050
HOP = 128  # 5.8 ms frames
DT = HOP / SR
BEAT_INFO = {}          # which tracker ran (for report.json)
FALLBACK_BEATS = False


def norm(x):
    return x / (np.percentile(x, 99.7) + 1e-9)


def flux(S, freqs, lo, hi):
    band = S[(freqs >= lo) & (freqs < hi)]
    lg = np.log1p(band * 20)
    f = np.maximum(0, np.diff(lg, axis=1, prepend=lg[:, :1])).sum(axis=0)
    return norm(f)


def peaks(env, height, distance_s, prominence=0.08):
    pk, _ = sps.find_peaks(env, height=height, distance=max(1, int(distance_s / DT)), prominence=prominence)
    return [{"t": round(float(p * DT), 3), "s": round(float(min(env[p], 1.5)), 3)} for p in pk]


def fold(env, period, nb=600, mask=None):
    t = np.arange(len(env)) * DT
    w = env ** 2 if mask is None else (env ** 2) * mask
    h = np.bincount(((t % period) / period * nb).astype(int) % nb, weights=w, minlength=nb)
    h = np.convolve(np.r_[h[-4:], h, h[:4]], np.ones(7) / 7, "same")[4:-4]
    k = int(np.argmax(h))
    return k / nb * period, float(h[k] / (h.mean() + 1e-9))


def fit_constant_grid(env, bpm0):
    best = None
    for bpm in np.arange(bpm0 * 0.985, bpm0 * 1.015, 0.002):
        ph, sc = fold(env, 60 / bpm)
        if best is None or sc > best[0]:
            best = (sc, bpm, ph)
    sc, bpm, ph = best
    P = 60 / bpm
    t = np.arange(len(env)) * DT
    devs = []
    for a in np.arange(0, t[-1] - 20, 10):
        m = ((t >= a) & (t < a + 20)).astype(float)
        p2, s2 = fold(env, P, mask=m)
        dv = (p2 - ph + P / 2) % P - P / 2
        if s2 > 6.5:  # ignore windows without a clear drum pulse (e.g. drumless intros)
            devs.append(dv)
    drift = float(np.max(np.abs(devs))) if devs else 1.0
    return bpm, ph, drift


def syllables(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n)


WORD_TAG = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>\s*([^<]*)")


def parse_lrc(text):
    """[(line time, text)] from LRC; "[00:12.00][01:30.00] line" = sung twice. Enhanced LRC word tags
    ("<00:12.40> word") are dropped from the text here; lrc_words() reads them."""
    out = []
    for line in text.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        words = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", re.sub(r"\[[^\]]*\]", "", line))).strip()
        for m, s_ in stamps:
            out.append((int(m) * 60 + float(s_), words))
    return sorted(out)


def lrc_words(text):
    """{line time: [(word, time)]} for lines with enhanced-LRC word tags (word-timed lyrics, e.g. from a lyrics app)."""
    out = {}
    for line in text.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        tags = WORD_TAG.findall(re.sub(r"\[[^\]]*\]", "", line))
        if stamps and tags:
            ws = []
            for m, s_, chunk in tags:
                t = int(m) * 60 + float(s_)
                for k, w in enumerate(chunk.split()):
                    ws.append((w, t + 0.001 * k))
            if ws:                                   # a line with only an end tag (lrc_ends) has no word times
                for m, s_ in stamps[:1]:
                    out[round(int(m) * 60 + float(s_), 3)] = ws
    return out


def lrc_ends(text):
    """{line time: end time} for lines that end in a time tag with no word after it: "[00:12.00] hold the light
    <00:14.10>" = the line stops at 14.10 s (the web app's lyric timing editor writes them; word-timed lines too)."""
    out = {}
    for line in text.splitlines():
        stamps = [int(m) * 60 + float(s_) for m, s_ in re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)]
        e = re.search(r"<(\d+):(\d+(?:\.\d+)?)>\s*$", re.sub(r"\[[^\]]*\]", "", line))
        if stamps and e:
            end = int(e.group(1)) * 60 + float(e.group(2))
            if end > stamps[0] + 0.05:
                for t in stamps:
                    out[round(t, 3)] = t + end - stamps[0]
    return out


# ------------------------------------------------------------------ beats
def beat_this_cached(y, drums):
    path = os.path.join(CACHE, "beat_this.json")
    c = json.load(open(path)) if os.path.exists(path) else {}
    if c.get("beats") and c.get("tracker"):
        BEAT_INFO.update(tracker=c["tracker"])
        return np.array(c["beats"]), np.array(c["downbeats"])
    tracker = None
    if not FALLBACK_BEATS:
        try:
            from beat_this.inference import Audio2Beats
            from mv import device as D
            print("   beat_this (first use downloads its checkpoint, ~80 MB)", flush=True)
            a2b = Audio2Beats(checkpoint_path="final0", device=D.pick(1.0), dbn=False)
            b, d = a2b(y, SR)
            del a2b
            D.release()
            tracker = "beat_this final0"
        except Exception as ex:
            print(f"   beat_this unavailable ({type(ex).__name__}: {str(ex)[:160]})", flush=True)
            print("   WARNING: using the fallback beat tracker (librosa, time-varying tempo)", flush=True)
    if tracker is None:
        b, d = fallback_beats(y, drums)
        tracker = "fallback (librosa dynamic-tempo tracker + downbeat phase vote)"
    BEAT_INFO.update(tracker=tracker)
    json.dump({"beats": np.round(b, 4).tolist(), "downbeats": np.round(d, 4).tolist(), "tracker": tracker}, open(path, "w"))
    return np.array(b), np.array(d)


def fallback_beats(y, drums):
    """Beats + downbeats without a model. Tempo is estimated per ~8 s window (tempo changes survive), beats by
    librosa's DP tracker on the drum stem's onsets (the mix's when there are no drums); the downbeat is the phase (of 4)
    where kicks, bass hits and chord changes land most, decided per run of beats between stops."""
    hop = 256
    src = drums if np.sqrt(np.mean(drums ** 2)) > 0.1 * np.sqrt(np.mean(y ** 2)) else y
    oenv = librosa.onset.onset_strength(y=src, sr=SR, hop_length=hop, aggregate=np.median)
    oenv = oenv + 0.5 * librosa.onset.onset_strength(y=y, sr=SR, hop_length=hop)
    tempo = librosa.feature.tempo(onset_envelope=oenv, sr=SR, hop_length=hop, aggregate=None, ac_size=6.0)
    g = float(np.median(tempo))
    tempo = np.where(np.abs(np.log2(tempo / g)) > 0.75, g, tempo)            # no octave jumps
    tempo = sps.medfilt(tempo, 2 * int(2.0 * SR / hop) + 1)
    _, bt = librosa.beat.beat_track(onset_envelope=oenv, sr=SR, hop_length=hop, bpm=tempo, units="time", tightness=120,
                                    trim=False)
    bt = np.asarray(bt, dtype=float)
    act = np.convolve(oenv, np.ones(int(SR / hop)) / int(SR / hop), "same")
    alive = act[np.clip((bt * SR / hop).astype(int), 0, len(act) - 1)] > 0.05 * act.max()
    if alive.any():                                             # drop beats before the music starts / after it ends
        i0, i1 = np.argmax(alive), len(alive) - np.argmax(alive[::-1])
        bt = bt[max(0, i0 - 1):i1]
    # downbeat phase from what lands on beats
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    fr = librosa.fft_frequencies(sr=SR, n_fft=2048)
    low = flux_s(S, fr, 30, 160)
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=SR)
    bf = np.clip((bt * SR / hop).astype(int), 0, S.shape[1] - 1)
    at = np.array([low[max(0, f - 3):f + 4].max() for f in bf])
    cs = librosa.util.sync(chroma, bf, aggregate=np.mean, pad=False)
    cn = np.r_[0, np.linalg.norm(np.diff(librosa.util.normalize(cs, axis=0), axis=1), axis=0)][:len(bt)]
    cn = np.pad(cn, (0, len(bt) - len(cn)))
    ev = at / (at.max() + 1e-9) + cn / (cn.max() + 1e-9)
    per = np.median(np.diff(bt)) if len(bt) > 1 else 0.5
    runs, s0 = [], 0
    for i in range(1, len(bt) + 1):
        if i == len(bt) or bt[i] - bt[i - 1] > 1.6 * per:
            runs.append((s0, i))
            s0 = i
    db = []
    for a, b in runs:
        if b - a < 4:
            continue
        sc = [ev[a + p:b:4].mean() for p in range(4)]
        p = int(np.argmax(sc))
        db += list(bt[a + p:b:4])
    return bt, np.array(db)


def flux_s(S, freqs, lo, hi):
    band = S[(freqs >= lo) & (freqs < hi)]
    lg = np.log1p(band * 20)
    return np.maximum(0, np.diff(lg, axis=1, prepend=lg[:, :1])).sum(axis=0)


def load_stem(name, n):
    p = os.path.join(STEMS, f"{name}.wav")
    s, _ = librosa.load(p, sr=SR, mono=True)
    return np.pad(s, (0, max(0, n - len(s))))[:n]


# ------------------------------------------------------------------ the user's own lyrics
def user_lyrics():
    """(kind, text) from project.json "lyrics" or song/lyrics.lrc / song/lyrics.txt; kind = 'lrc' | 'plain' | None."""
    pj = project()
    txt = pj.get("lyrics")
    txt = (txt.get("text") if isinstance(txt, dict) else txt) or ""
    if not txt.strip():
        for fn in ("lyrics.lrc", "lyrics.txt"):
            p = os.path.join(SONG, fn)
            if os.path.exists(p):
                txt = open(p, encoding="utf-8").read()
                break
    if not txt.strip():
        return None, ""
    return ("lrc" if len(parse_lrc(txt)) >= 3 else "plain"), txt


def time_plain_lines(lines, voc_act, dur):
    """Rough times for untimed lyric lines: spread them over the stretches where the vocal stem sings, in proportion
    to their syllables. align_words.py (forced alignment) then places every word properly."""
    on = voc_act > 0.45
    t = np.arange(len(on)) * DT
    sung = t[on]
    if len(sung) < 10:
        sung = t[(t > 0.05 * dur) & (t < 0.95 * dur)]
    syl = np.array([sum(syllables(w) for w in L.split()) for L in lines], dtype=float)
    cum = np.r_[0, np.cumsum(syl)] / max(1.0, syl.sum())
    idx = np.clip((cum * (len(sung) - 1)).astype(int), 0, len(sung) - 1)
    return [(float(sung[idx[i]]), L) for i, L in enumerate(lines)]


def analyze():
    path = song_file()
    print(f"== {os.path.basename(path)}")
    y, _ = librosa.load(path, sr=SR, mono=True)
    n = len(y)
    dur = n / SR
    drums, bass, vocals, other = (load_stem(s, n) for s in ("drums", "bass", "vocals", "other"))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)

    def spec(sig):
        return np.abs(librosa.stft(sig, n_fft=2048, hop_length=HOP))

    Sd, Sb, Sv, Sm = spec(drums), spec(bass), spec(vocals), spec(y)
    nf = Sm.shape[1]
    times = np.arange(nf) * DT

    kick = flux(Sd, freqs, 25, 150)
    snare = flux(Sd, freqs, 180, 2500) * 0.5 + flux(Sd, freqs, 2500, 6000) * 0.5
    snare = norm(snare)
    cym = flux(Sd, freqs, 7000, 11000)
    drum_env = norm(kick + snare)
    bass_env = flux(Sb, freqs, 30, 400)
    mix_env = norm(librosa.onset.onset_strength(S=librosa.amplitude_to_db(Sm, ref=np.max), sr=SR, hop_length=HOP))

    # ---------------- beats
    bt_b, bt_d = beat_this_cached(y, drums)
    bpm0 = 60 / np.median(np.diff(bt_b))
    bpm, ph, drift = fit_constant_grid(drum_env, bpm0)
    P = 60 / bpm
    constant = drift < 0.022
    if constant and len(bt_b) > 16:
        # the drum fold only scores windows that pulse at the fitted period, so a section at another tempo can hide
        # from it: every tracked beat must also sit on the grid, at the grid's tempo (or half / double of it)
        P_ = 60 / bpm
        dev = (bt_b - ph) % P_
        dev = np.minimum(dev, P_ - dev)
        ibi = sps.medfilt(np.diff(bt_b), 7)
        ratio = ibi / P_
        off = ~((np.abs(ratio - 1) < 0.04) | (np.abs(ratio - 0.5) < 0.03) | (np.abs(ratio - 2) < 0.08))
        if np.mean(dev > 0.07) > 0.15 or np.mean(off) > 0.08:
            constant = False
            print(f"   the tracked beats leave the {bpm:.1f} BPM grid ({np.mean(off) * 100:.0f}% at another tempo): tempo-following")
    print(f"   grid fit {bpm:.3f} bpm, max window drift {drift*1000:.0f} ms -> {'constant grid' if constant else 'tempo-following'}")
    if constant:
        # the fold peak can sit on the off-beat; pick the phase the kick drum and beat_this agree with
        kph, _ = fold(kick, P)
        cands = [ph, (ph + P / 2) % P]
        bt_phase = float(np.angle(np.mean(np.exp(2j * np.pi * (bt_b % P) / P))) / (2 * np.pi) * P) % P

        def cdist(a, b):
            return abs((a - b + P / 2) % P - P / 2)
        score = [cdist(c, kph) + cdist(c, bt_phase) for c in cands]
        ph = cands[int(np.argmin(score))]
        print(f"   phase: fold {cands[0]:.3f}/{cands[1]:.3f}, kick {kph:.3f}, beat_this {bt_phase:.3f} -> {ph:.3f}")
        first = bt_b[0] - 0.1
        last = min(bt_b[-1] + 0.1, dur)
        k0 = int(np.ceil((first - ph) / P))
        k1 = int(np.floor((last - ph) / P))
        beats = np.array([ph + k * P for k in range(k0, k1 + 1)])
    else:
        # snap each neural beat to the strongest drum onset within +-35 ms, then smooth jitter
        snapped = []
        for b in bt_b:
            f = int(round(b / DT))
            w = int(0.035 / DT)
            seg = drum_env[max(0, f - w):f + w + 1]
            if len(seg) and seg.max() > 0.2:
                snapped.append((max(0, f - w) + int(np.argmax(seg))) * DT)
            else:
                snapped.append(b)
        snapped = np.array(snapped)
        resid = snapped - sps.savgol_filter(snapped, 9, 2)
        beats = np.where(np.abs(resid) < 0.03, snapped, sps.savgol_filter(snapped, 9, 2))
    # downbeats: follow beat_this bar positions (handles irregular bars), fill gaps in 4-beat steps
    bi = np.array([int(np.argmin(np.abs(beats - d))) for d in bt_d])
    ok = np.abs(beats[bi] - bt_d) < 0.09
    votes = np.bincount(bi[ok] % 4, minlength=4)
    dphase = int(np.argmax(votes))
    anchors = sorted(set(bi[ok].tolist()))
    # drop isolated anchors that disagree with both neighbours (1-beat 'bars' are tracker noise)
    clean = []
    for j, a in enumerate(anchors):
        prev_ok = j > 0 and (a - anchors[j - 1]) % 4 == 0
        next_ok = j + 1 < len(anchors) and (anchors[j + 1] - a) % 4 == 0
        if prev_ok or next_ok or len(anchors) < 3:
            clean.append(a)
    db_idx = []
    for j, a in enumerate(clean):
        db_idx.append(a)
        nxt = clean[j + 1] if j + 1 < len(clean) else len(beats)
        k = a + 4
        while k <= nxt - 3:
            db_idx.append(k)
            k += 4
    k = clean[0] - 4 if clean else dphase
    while k >= 0:
        db_idx.insert(0, k)
        k -= 4
    db_idx = sorted(set(db_idx))
    downbeats = beats[db_idx]
    irregular = [round(float(beats[a]), 3) for a, b in zip(db_idx[:-1], db_idx[1:]) if b - a != 4]
    print(f"   downbeat votes {votes.tolist()}; irregular bars at {irregular}")

    # ---------------- energy
    rms = librosa.feature.rms(S=Sm, frame_length=2048, hop_length=HOP)[0]
    e = np.clip((librosa.amplitude_to_db(rms, ref=np.max) + 40) / 40, 0, 1)
    e_s = sps.savgol_filter(e, 201, 2)
    bfr = (beats / DT).astype(int)
    beat_energy = [round(float(e[max(0, f - 10):f + 60].mean()), 3) for f in bfr]
    dfr = (downbeats / DT).astype(int)
    bar_energy = [round(float(e[a:b].mean()), 3) for a, b in zip(dfr, list(dfr[1:]) + [nf])]

    def at(env, f, w=6):
        return float(env[max(0, f - w):f + w + 1].max())
    beat_kick = [round(at(kick, f), 3) for f in bfr]
    beat_snare = [round(at(snare, f), 3) for f in bfr]
    beat_bass = [round(at(bass_env, f), 3) for f in bfr]

    # ---------------- sections (bar novelty on chroma + mfcc + energy)
    chroma = librosa.feature.chroma_stft(S=Sm ** 2, sr=SR)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(librosa.feature.melspectrogram(S=Sm ** 2, sr=SR)), n_mfcc=13)
    voc_rms = librosa.feature.rms(S=Sv, frame_length=2048, hop_length=HOP)[0]
    voc_act = np.clip((librosa.amplitude_to_db(voc_rms, ref=np.max) + 35) / 35, 0, 1)
    feat = np.vstack([librosa.util.normalize(chroma, axis=0), mfcc / 50.0, e[None] * 3, voc_act[None] * 2])
    bars = librosa.util.sync(feat, dfr, aggregate=np.mean)[:, 1:][:, :len(bar_energy)]
    R = librosa.segment.recurrence_matrix(bars, mode="affinity", sym=True, width=2)
    R = librosa.segment.path_enhance(R, 3)
    nb = R.shape[0]
    K = 4
    ker = np.kron(np.array([[1, -1], [-1, 1]]), np.ones((K, K)))
    g = sps.windows.gaussian(2 * K, K / 1.5)
    ker *= np.outer(g, g)
    pad = np.pad(R, K)
    nov = np.array([np.sum(pad[i:i + 2 * K, i:i + 2 * K] * ker) for i in range(nb)])
    be = np.array(bar_energy[:nb])
    ej = np.abs(np.r_[0, np.diff(be)])
    nov2 = nov / (np.abs(nov).max() + 1e-9) + 1.5 * ej / (ej.max() + 1e-9)
    pk, _ = sps.find_peaks(nov2, distance=4, height=np.percentile(nov2, 60))
    bounds = sorted(set([0] + pk.tolist() + [nb]))
    sections = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        en = float(downbeats[b]) if b < len(downbeats) else dur
        sections.append({"start": round(float(downbeats[a]), 3), "end": round(en, 3), "bars": b - a,
                         "energy": round(float(be[a:b].mean()), 3),
                         "vocal": round(float(voc_act[dfr[a]:(dfr[b] if b < len(dfr) else nf)].mean()), 3)})

    # ---------------- vocals
    v_on = flux(Sv, freqs, 150, 4000)
    v_on = v_on * (voc_act > 0.35)
    vocal_onsets = peaks(v_on, 0.18, 0.09, 0.08)

    # ---------------- lyrics: the user's own first, then LRCLIB's synced lyrics, then a Whisper transcription
    lyrics, meta, best = [], None, None
    ukind, utext = user_lyrics()
    cands = []
    if ukind == "lrc":
        cands.append({"id": "user", "syncedLyrics": utext, "albumName": None, "user": True})
    lp = os.path.join(CACHE, "lyrics", "search.json")
    if ukind is None and os.path.exists(lp):
        cands += [c for c in json.load(open(lp, encoding="utf-8")) if c.get("syncedLyrics")]
    if cands:
        on_frames = np.zeros(nf)
        for v in vocal_onsets:
            on_frames[min(nf - 1, int(v["t"] / DT))] = v["s"]
        on_s = np.convolve(on_frames, np.ones(int(0.12 / DT)), "same")
        for c in cands[:15]:
            lines = [(t, s) for t, s in parse_lrc(c["syncedLyrics"]) if s]
            if len(lines) < (3 if c.get("user") else 6):
                continue
            if c.get("user") and project().get("lyrics_synced"):      # timed on this very file (the web app's editor)
                best = (0.0, 0.0, c, lines)
                break
            lt = np.array([t for t, _ in lines])

            def score(o):
                fr = ((lt + o) / DT).astype(int)
                okf = (fr > 80) & (fr < nf - 200)
                if okf.sum() < 0.8 * len(fr):
                    return -1.0
                # onset at the timestamp, singing right after, (relative) silence just before
                return float(np.mean([on_s[f - 10:f + 25].max() + 0.6 * voc_act[f:f + 120].mean() - 0.3 * voc_act[f - 60:f - 15].mean()
                                      for f in fr[okf]]))
            # music videos open with up to a minute of film before the song: search wide (coarse), then refine
            coarse = np.arange(-60, 60, 0.05)
            csc = np.nan_to_num(np.array([score(o) for o in coarse]), nan=-1.0)
            tops = coarse[np.argsort(csc)[::-1][:3]]
            offs = np.unique(np.round(np.concatenate([np.arange(o - 0.1, o + 0.1, 0.01) for o in tops]), 2))
            sc = np.nan_to_num(np.array([score(o) for o in offs]), nan=-1.0)
            j = int(np.argmax(sc))
            allsc = np.r_[csc, sc]                               # z-score against the whole search
            pos = allsc[allsc > 0]
            z = (sc[j] - np.median(pos)) / (np.std(pos) + 1e-9) if len(pos) else 0.0
            off = float(offs[j])
            if c.get("user") and score(0.0) >= 0.9 * sc[j]:     # the user's LRC was made for this file: keep it as is
                off = 0.0
            if c.get("user") or best is None or z > best[0]:
                best = (z, off, c, lines)
            if c.get("user"):
                break
        if best is None:          # no usable synced lyrics: carry on without
            print("   lyrics: no synced lyrics among the candidates (transcribe_lyrics.py can transcribe them)")
    if best is not None:
        z, off, c, lines = best
        meta = {"source": ("user (LRC, timed in the studio)" if project().get("lyrics_synced") else "user (LRC)")
                if c.get("user") else "lrclib", "id": c["id"], "album": c.get("albumName"),
                "offset": round(off, 3), "match_z": round(float(z), 2)}
        print(f"   lyrics: {meta['source']} {c['id']} offset {off:+.2f}s z={z:.1f}")
        on_t = np.array([v["t"] for v in vocal_onsets])
        on_sv = np.array([v["s"] for v in vocal_onsets])
        timed = lrc_words(c["syncedLyrics"]) if c.get("user") else {}
        ends = lrc_ends(c["syncedLyrics"]) if c.get("user") else {}
        if timed:
            meta["source"] = "user (LRC with word times)"
        for i, (t, s) in enumerate(lines):
            ue = ends.get(round(t, 3))                     # the user said when the line stops: kept as the line's end
            mine = {"end_src": "user"} if ue else {}
            if round(t, 3) in timed:                       # the user gave every word's time: keep them exactly
                ws = timed[round(t, 3)]
                t1 = ue + off if ue else lines[i + 1][0] + off if i + 1 < len(lines) else min(dur, ws[-1][1] + off + 1.0)
                lyrics.append({"t": round(ws[0][1] + off, 3), "end": round(float(t1), 3), "text": s, **mine,
                               "words": [{"w": w, "t": round(wt_ + off, 3), "fa": 1.0, "src": "user"} for w, wt_ in ws]})
                continue
            t0 = t + off
            near = np.where(np.abs(on_t - t0) < 0.3)[0]
            if len(near):
                t0 = float(on_t[near[np.argmax(on_sv[near] - np.abs(on_t[near] - t0))]])
            t1 = ue + off if ue else lines[i + 1][0] + off if i + 1 < len(lines) else min(dur, t0 + 5)
            words = s.split()
            syl = [syllables(w) for w in words]
            cand = on_t[(on_t >= t0 - 0.01) & (on_t < t1 - 0.15)]
            wt = []
            if len(cand) >= len(words):
                # walk syllables through the onsets in order; word time = onset of its first syllable
                k = 0
                total = sum(syl)
                scale = len(cand) / max(total, 1)
                for w, sy in zip(words, syl):
                    wt.append(round(float(cand[min(int(round(k * scale)), len(cand) - 1)]), 3))
                    k += sy
            else:
                # not enough onsets: spread words by syllables over the sung part of the line (all of it when its end is known)
                vend = max(t0 + 0.2, t1 - 0.15) if ue else t0 + max(0.4, min(t1 - t0, 0.32 * sum(syl) + 0.3))
                acc = 0
                for sy in syl:
                    wt.append(round(t0 + (vend - t0) * acc / max(sum(syl), 1), 3))
                    acc += sy
            lyrics.append({"t": round(t0, 3), "end": round(float(t1), 3), "text": s, **mine,
                           "words": [{"w": w, "t": tt} for w, tt in zip(words, wt)]})
    if best is None and ukind == "plain":           # the user's text without times: rough times, align_words places the words
        plain = [x.strip() for x in utext.splitlines() if x.strip() and not x.strip().startswith(("[", "#"))]
        timed = time_plain_lines(plain, voc_act, dur)
        for i, (t0, text) in enumerate(timed):
            t1 = timed[i + 1][0] if i + 1 < len(timed) else min(dur, t0 + 5)
            words = text.split()
            syl = [syllables(w) for w in words]
            acc, wt = 0, []
            for sy in syl:
                wt.append(round(t0 + (t1 - t0) * 0.8 * acc / max(sum(syl), 1), 3))
                acc += sy
            lyrics.append({"t": round(t0, 3), "end": round(float(t1), 3), "text": text,
                           "words": [{"w": w, "t": tt} for w, tt in zip(words, wt)]})
        meta = {"source": "user (plain text, timed by estimate)", "match_z": None}
        print(f"   lyrics: {len(lyrics)} lines of the user's text, timed roughly (forced alignment places the words)")
    tp = os.path.join(CACHE, "lyrics", "transcribed.json")
    if best is None and not lyrics and os.path.exists(tp):      # no synced LRC: the Whisper transcription (transcribe_lyrics.py)
        tr = json.load(open(tp, encoding="utf-8"))
        lyrics = [{"t": x["t"], "end": x["end"], "text": x["text"], "words": [{"w": w["w"], "t": w["t"]} for w in x["words"]]}
                  for x in tr["lines"]]
        meta = {"source": f"transcribed ({tr.get('model')})", "match_z": None}
        print(f"   lyrics: {len(lyrics)} transcribed lines ({tr.get('model')}): check them, they can contain mishearings")
    if lyrics:
        from fix_lyrics import apply_text                 # project.json lyrics_fix.text: corrected words, before alignment
        for line in apply_text(lyrics, project().get("lyrics_fix")):
            print("   lyrics_fix:", line)

    # ---------------- events
    kicks = peaks(kick, 0.3, 0.15)
    snares = peaks(snare, 0.3, 0.15)
    cymbals = peaks(cym, 0.45, 0.4, 0.2)
    bass_hits = peaks(bass_env, 0.35, 0.2)
    onsets = peaks(mix_env, 0.2, 0.08)
    step = int(round(0.1 / DT))
    data = {
        "file": os.path.basename(path), "duration": round(dur, 3), "beat_tracker": BEAT_INFO.get("tracker"),
        "tempo": round(bpm if constant else 60 / np.median(np.diff(beats)), 3),
        "beat_period": round(P if constant else float(np.median(np.diff(beats))), 5),
        "grid": {"constant": bool(constant), "drift_ms": round(drift * 1000, 1)},
        "downbeat_votes": votes.tolist(), "irregular_bars": irregular,
        "beats": [round(float(b), 3) for b in beats],
        "beat_energy": beat_energy, "beat_kick": beat_kick, "beat_snare": beat_snare, "beat_bass": beat_bass,
        "downbeats": [round(float(d), 3) for d in downbeats], "bar_energy": bar_energy,
        "sections": sections,
        "kicks": kicks, "snares": snares, "cymbals": cymbals, "bass_hits": bass_hits, "onsets": onsets,
        "vocal_onsets": vocal_onsets,
        "curve": {"t": [round(float(t), 2) for t in times[::step]], "energy": [round(float(v), 3) for v in e_s[::step]],
                  "vocal": [round(float(v), 3) for v in sps.savgol_filter(voc_act, 41, 2)[::step]]},
        "lyrics_meta": meta, "lyrics": lyrics,
    }
    json.dump(data, open(os.path.join(CACHE, "audio.json"), "w"), indent=0)
    print(f"   {len(beats)} beats, {len(downbeats)} bars, {len(sections)} sections, {len(lyrics)} lyric lines, "
          f"{len(kicks)} kicks, {len(snares)} snares, {len(cymbals)} cymbals, {len(bass_hits)} bass hits")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback-beats", action="store_true", help="skip beat_this, use the librosa tracker")
    a_ = ap.parse_args()
    need_project()
    FALLBACK_BEATS = a_.fallback_beats
    os.makedirs(CACHE, exist_ok=True)
    analyze()
