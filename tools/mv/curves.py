"""Frame-accurate song data for animation: 200 Hz curves, their exact per-frame resampling, and hit lists.

The song kit (tools/song_kit.py) calls compute() once per ingest and caches the 200 Hz curves in cache/curves.npz;
frames_json() turns them into song/frames.json for any fps, and hits_json() writes song/hits.json.

Timing rule (the same everywhere in the studio):
  - an event at song time t belongs to frame round(t * fps)
  - frame k covers song time [(k - 0.5) / fps, (k + 0.5) / fps); a curve's value for frame k is the mean of the 200 Hz
    curve over exactly that slot ("transient" takes the max, so a hit always shows at full strength in its frame)
  - grid values (bar, beat position, phases, section) are sampled at the frame's own time k / fps
Video time = song time: the video always uses the whole song from 0, unchanged.
"""
import numpy as np
import librosa
import scipy.signal as sps

from . import listen as L

RATE = 200                       # curve samples per second
SR = 24000
HOP = SR // RATE                 # 120 samples
BANDS = (("sub", 20, 60), ("low", 60, 250), ("lowmid", 250, 2000), ("highmid", 2000, 6000), ("high", 6000, 11500))

INFO = {   # name: (what it is, unit / range, how sure we are of it)
    "loud": ("perceived loudness, momentary (400 ms, EBU R128 K-weighted), mapped 0..1 from 24 LU below the song's "
             "integrated loudness up to 6 LU above it", "0..1", "high: a direct measurement of the file"),
    "lufs": ("momentary loudness", "LUFS (about -60 .. 0)", "high"),
    "level": ("fast level of the mix (50 ms RMS): reacts to every hit, unlike 'loud'", "0..1 (36 dB range)", "high"),
    "vocals": ("vocal stem level against its own loud parts", "0..1", "stems"),
    "drums": ("drum stem level", "0..1", "stems"),
    "bass": ("bass stem level", "0..1", "stems"),
    "other": ("everything else (guitars, keys, synths, strings) level", "0..1", "stems"),
    "sub": ("energy 20-60 Hz (kick body, sub bass)", "0..1 (40 dB range)", "high: a measurement"),
    "low": ("energy 60-250 Hz (bass, kick, low toms)", "0..1", "high"),
    "lowmid": ("energy 250-2000 Hz (voice body, guitars, keys)", "0..1", "high"),
    "highmid": ("energy 2-6 kHz (presence, snare crack, consonants)", "0..1", "high"),
    "high": ("energy 6-11.5 kHz (hats, cymbals, air)", "0..1", "high"),
    "transient": ("onset strength of the mix: how hard something new starts (max over the frame's slot)",
                  "0..1 (song's 99.5th percentile = 1)", "high: a measurement; not every peak is a musical event"),
    "bright": ("spectral centroid, log-mapped 400 Hz..6 kHz", "0..1", "high: a measurement; brighter = more treble"),
    "centroid_hz": ("spectral centroid", "Hz", "high"),
    "width": ("stereo width: side energy / total energy, smoothed 100 ms (0 = mono, 1 = all side)", "0..1",
              "high for stereo files; always 0 for mono files"),
    "voice_midi": ("vocal melody note being sung (MIDI number; 0 = not singing a tracked note)", "MIDI 0 or ~40..90",
                   "medium: CREPE pitch on the vocal stem; backing vocals and bleed add stray notes"),
    "bass_midi": ("bass note (MIDI; 0 = none)", "MIDI 0 or ~24..60", "medium: CREPE on the bass stem"),
}


def _peaks_list(env, rate, height, dist_s, prom=0.05):
    pk, _ = sps.find_peaks(env, height=height, distance=max(1, int(dist_s * rate)), prominence=prom)
    m = env[pk].max() if len(pk) else 1.0
    return [{"t": round(float(p / rate), 3), "s": round(float(min(1.0, env[p] / (m + 1e-9))), 3)} for p in pk]


def _db01(x, span, ref=None):
    db = 20 * np.log10(np.asarray(x, dtype=np.float64) + 1e-9)
    ref = np.percentile(db, 95) if ref is None else ref
    return np.clip((db - (ref - span)) / span, 0, 1)


def _rms(y, win):
    r = librosa.feature.rms(y=y, frame_length=win, hop_length=HOP, center=True)[0]
    return r


def _fit(x, n):
    x = np.asarray(x, dtype=np.float64)
    return np.pad(x, (0, max(0, n - len(x))), mode="edge")[:n] if len(x) else np.zeros(n)


def compute(mix_path, stems_dir, lufs_ref, vnotes=(), bnotes=(), stems_reliable=True):
    """{name: 200 Hz float array} + {'hats': [...], 'other_onsets': [...]} for the song."""
    y2, _ = librosa.load(mix_path, sr=SR, mono=False)
    if y2.ndim == 1:
        y2 = np.stack([y2, y2])
    mono = y2.mean(0)
    n = 1 + len(mono) // HOP
    out = {}
    # loudness, momentary, at 200 Hz (K-weighted mean square over 400 ms)
    z = y2.astype(np.float64)
    for kind, g, q, fc in (("high_shelf", 4.0, 1 / np.sqrt(2), 1500.0), ("high_pass", 0.0, 0.5, 38.0)):
        b, a = L._biquad(kind, g, q, fc, SR)
        z = sps.lfilter(b, a, z, axis=-1)
    p = (z ** 2).sum(axis=0)
    c = np.concatenate([[0.0], np.cumsum(p)])
    centers = np.arange(n) * HOP
    h = int(0.2 * SR)
    lo, hi = np.clip(centers - h, 0, len(p)), np.clip(centers + h, 0, len(p))
    lufs = -0.691 + 10 * np.log10((c[hi] - c[lo]) / np.maximum(hi - lo, 1) + 1e-12)
    out["lufs"] = lufs
    out["loud"] = np.clip((lufs - (lufs_ref - 24)) / 30, 0, 1)
    del z, p, c
    out["level"] = _fit(_db01(_rms(mono, 1200), 36), n)
    # stems
    for k in ("vocals", "drums", "bass", "other"):
        try:
            s, _ = librosa.load(f"{stems_dir}/{k}.wav", sr=SR, mono=True)
            out[k] = _fit(_db01(_rms(s, 1024), 30), n)
        except Exception:
            out[k] = np.zeros(n)
    # spectrum: bands, centroid, transients
    S = np.abs(librosa.stft(mono, n_fft=2048, hop_length=HOP, center=True))
    f = librosa.fft_frequencies(sr=SR, n_fft=2048)
    for name, a, b in BANDS:
        e = np.sqrt((S[(f >= a) & (f < b)] ** 2).sum(axis=0))
        out[name] = _fit(_db01(e, 40), n)
    cen = librosa.feature.spectral_centroid(S=S, sr=SR)[0]
    quiet = out["level"] < 0.05
    cen = np.where(quiet, np.nan, cen)
    cen = np.nan_to_num(np.interp(np.arange(len(cen)), np.flatnonzero(~np.isnan(cen)), cen[~np.isnan(cen)])) \
        if (~np.isnan(cen)).any() else np.zeros(len(cen))
    out["centroid_hz"] = _fit(cen, n)
    out["bright"] = np.clip((np.log2(np.maximum(out["centroid_hz"], 1)) - np.log2(400)) / (np.log2(6000) - np.log2(400)), 0, 1)
    mel = librosa.power_to_db(librosa.feature.melspectrogram(S=S ** 2, sr=SR, n_mels=96))
    fl = np.maximum(0, np.diff(mel, axis=1, prepend=mel[:, :1])).mean(axis=0)
    out["transient"] = _fit(np.clip(fl / (np.percentile(fl, 99.5) + 1e-9), 0, 1), n)
    # stereo width
    Ls = np.abs(librosa.stft(y2[0], n_fft=2048, hop_length=HOP)) ** 2
    Rs = np.abs(librosa.stft(y2[1], n_fft=2048, hop_length=HOP)) ** 2
    Ms = np.abs(librosa.stft((y2[0] + y2[1]) / 2, n_fft=2048, hop_length=HOP)) ** 2
    Ss = np.abs(librosa.stft((y2[0] - y2[1]) / 2, n_fft=2048, hop_length=HOP)) ** 2
    side, tot = Ss.sum(0), Ss.sum(0) + Ms.sum(0)
    k20 = 20
    side, tot = np.convolve(side, np.ones(k20) / k20, "same"), np.convolve(tot, np.ones(k20) / k20, "same")
    w = np.where(tot > 1e-6 * tot.max(), side / (tot + 1e-12), 0.0)
    out["width"] = _fit(np.clip(w, 0, 1), n)
    del Ls, Rs, Ms, Ss, S, mel
    # melody / bass line as step curves
    t = np.arange(n) / RATE
    for key, notes in (("voice_midi", vnotes), ("bass_midi", bnotes)):
        v = np.zeros(n)
        for nt in notes:
            if key == "voice_midi" and nt.get("in_lyric") is False:
                continue
            v[(t >= nt["t"]) & (t < nt["end"])] = nt["midi"]
        out[key] = v
    # extra hit lists: hats (drum stem, top band), other-stem onsets
    extra = {"hats": [], "other_onsets": []}
    try:
        d, _ = librosa.load(f"{stems_dir}/drums.wav", sr=SR, mono=True)
        Sd = np.abs(librosa.stft(d, n_fft=1024, hop_length=HOP))
        fd = librosa.fft_frequencies(sr=SR, n_fft=1024)
        band = np.log1p(20 * Sd[(fd >= 6000) & (fd < 11500)])
        hf = np.maximum(0, np.diff(band, axis=1, prepend=band[:, :1])).sum(0)
        hf = hf / (np.percentile(hf, 99.5) + 1e-9)
        extra["hats"] = _peaks_list(hf, RATE, 0.25, 0.07)
        o, _ = librosa.load(f"{stems_dir}/other.wav", sr=SR, mono=True)
        oe = librosa.onset.onset_strength(y=o, sr=SR, hop_length=HOP)
        oe = oe / (np.percentile(oe, 99.5) + 1e-9)
        extra["other_onsets"] = _peaks_list(oe, RATE, 0.3, 0.08)
    except Exception as ex:
        print("   hats / other onsets skipped:", ex)
    return {k: v.astype(np.float32) for k, v in out.items()}, extra


# ------------------------------------------------------------------ exact per-frame resampling
def n_frames(duration, fps):
    return int(round(duration * fps))


def resample(x, fps, n, rate=RATE, agg="mean"):
    """Per-frame values of a curve sampled at `rate` (sample i = the value at time i / rate, covering
    [(i - 0.5) / rate, (i + 0.5) / rate)). Frame k = mean (or max) over [(k - 0.5) / fps, (k + 0.5) / fps)."""
    x = np.asarray(x, dtype=np.float64)
    edges = (np.arange(n + 1) - 0.5) / fps * rate + 0.5         # in sample units, sample i spans [i, i + 1)
    edges = np.clip(edges, 0, len(x))
    if agg == "max":
        out = np.empty(n)
        for k in range(n):
            a, b = int(np.floor(edges[k])), int(np.ceil(edges[k + 1]))
            out[k] = x[min(a, len(x) - 1):max(b, a + 1)].max()
        return out
    C = np.r_[0.0, np.cumsum(x)]

    def integ(u):
        i = np.minimum(np.floor(u).astype(int), len(x) - 1)
        return C[i] + (u - i) * x[i]
    w = edges[1:] - edges[:-1]
    v = (integ(edges[1:]) - integ(edges[:-1])) / np.maximum(w, 1e-9)
    return np.where(w > 1e-9, v, x[np.minimum(edges[:-1].astype(int), len(x) - 1)])


def grid_per_frame(beats, downbeats, sections, n, fps):
    """bar, beat position in the bar (bar:beat notation), beat / bar phase (0..1) and section index at k / fps."""
    t = np.arange(n) / fps
    B = np.asarray(beats, dtype=float)
    D = np.asarray(downbeats, dtype=float)
    per = float(np.median(np.diff(B))) if len(B) > 1 else 0.5
    j = np.searchsorted(B, t + 1e-6, "right") - 1
    jc = np.clip(j, 0, len(B) - 2) if len(B) > 1 else np.zeros_like(j)
    nxt = np.where(j + 1 < len(B), B[np.minimum(j + 1, len(B) - 1)], B[-1] + per) if len(B) else t
    cur = np.where(j >= 0, B[np.maximum(j, 0)], B[0] - per) if len(B) else t
    beat_phase = np.clip((t - cur) / np.maximum(nxt - cur, 1e-6), 0, 0.9999)
    i = np.searchsorted(D, t + 1e-6, "right") - 1
    bar = np.where((t >= (B[0] - per if len(B) else 0)) & (t <= (B[-1] + per if len(B) else 0)), np.maximum(i, -1), -1)
    dbi = np.array([int(np.argmin(np.abs(B - d))) for d in D]) if len(D) else np.array([0])
    beat_pos = np.zeros(n)
    bar_phase = np.zeros(n)
    for k in range(n):
        if bar[k] < 0 or i[k] < 0:
            continue
        bi = int(i[k])
        beat_pos[k] = (j[k] - dbi[bi]) + beat_phase[k]
        end = D[bi + 1] if bi + 1 < len(D) else D[bi] + 4 * per
        bar_phase[k] = np.clip((t[k] - D[bi]) / max(end - D[bi], 1e-6), 0, 0.9999)
    sec = np.full(n, -1)
    for si, s in enumerate(sections):
        sec[(t >= s["t0"]) & (t < s["t1"])] = si
    del jc
    return {"bar": bar.astype(int).tolist(), "beat": np.round(beat_pos, 3).tolist(),
            "beat_phase": np.round(beat_phase, 3).tolist(), "bar_phase": np.round(bar_phase, 3).tolist(),
            "section": sec.astype(int).tolist()}


def frames_json(curves, duration, fps, beats, downbeats, sections, confidence):
    n = n_frames(duration, fps)
    out = {"fps": fps, "frames": n, "duration": round(float(duration), 4),
           "rule": "an event at song time t is in frame round(t * fps); frame k covers [(k-0.5)/fps, (k+0.5)/fps) and each "
                   "curve value is the mean over that slot (transient: the max); grid values are taken at k / fps",
           "curves": {}, "grid": grid_per_frame(beats, downbeats, sections, n, fps), "info": {}}
    for name, x in curves.items():
        v = resample(x, fps, n, agg="max" if name == "transient" else "mean")
        digits = 1 if name in ("lufs", "centroid_hz") else 0 if name.endswith("_midi") else 3
        out["curves"][name] = [round(float(a), digits) if digits else int(round(float(a))) for a in v]
        what, unit, conf = INFO.get(name, (name, "", ""))
        out["info"][name] = {"what": what, "unit": unit, "confidence": confidence.get(name, conf)}
    return out


def hits_json(A, model, extra, fps, G):
    """Every hit a renderer may want, with its time, frame and a 0..1 strength."""
    def fr(t):
        return int(round(t * fps))

    def norm_list(xs, key="s"):
        m = max([x.get(key, 1.0) for x in xs] or [1.0])
        return [{"t": round(x["t"], 3), "f": fr(x["t"]), "s": round(min(1.0, x.get(key, 1.0) / (m + 1e-9)), 3)} for x in xs]
    beats = []
    dset = {round(d, 3) for d in G.db}
    for j, t in enumerate(G.beats):
        p = G.pos(float(t))
        beats.append({"t": round(float(t), 3), "f": fr(t), "bar": p[0] if p else None,
                      "beat": round(p[1]) if p else None, "down": round(float(t), 3) in dset})
    words, lines = [], []
    for x in model["lyrics"]:
        lines.append({"t": x["t"], "f": fr(x["t"]), "end": x["end"], "text": x["text"], "i": x["i"]})
        for w in x["words"]:
            words.append({"t": round(float(w["t"]), 3), "f": fr(w["t"]), "end": round(float(w.get("t_end", w["t"] + 0.3)), 3),
                          "w": w["w"], "line": x["i"], "note": w.get("note"), "aligned": "fa" in w})
    return {
        "fps": fps, "rule": "frame = round(t * fps); s = strength 0..1 within its own list",
        "beats": beats, "downbeats": [{"t": round(float(t), 3), "f": fr(t), "bar": i} for i, t in enumerate(G.db)],
        "kick": norm_list(A.get("kicks", [])), "snare": norm_list(A.get("snares", [])),
        "hat": norm_list(extra.get("hats", [])), "crash": norm_list(A.get("cymbals", [])),
        "bass": norm_list(A.get("bass_hits", [])), "other": norm_list(extra.get("other_onsets", [])),
        "vocal": norm_list(A.get("vocal_onsets", [])), "onset": norm_list(A.get("onsets", [])),
        "words": words, "lines": lines,
        "notes": {k: [{"t": nt["t"], "f": fr(nt["t"]), "end": nt["end"], "midi": nt["midi"], "name": nt["name"]}
                      for nt in model["notes"][k] if nt.get("in_lyric", True) or k == "bass"] for k in ("vocal", "bass")},
        "chords": [dict(c, f=fr(c["t"])) for c in model["chords"]],
        "sections": [{"t": s["t0"], "f": fr(s["t0"]), "end": s["t1"], "name": s["name"], "label": s["label"]} for s in model["sections"]],
        "moments": [{"t": m["t"], "f": fr(m["t"]), "stars": m["stars"], "kind": m["kind"], "text": m["text"]} for m in model["moments"]],
    }
