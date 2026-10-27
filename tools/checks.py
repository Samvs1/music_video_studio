"""Checks on a rendered video. Every check prints PASS / WARN / FAIL lines and writes work/checks/<name>/report.md.

  python tools/studio.py run <p> checks.py spec  VIDEO     delivery spec + the audio is the song file, unchanged and in sync
  python tools/studio.py run <p> checks.py flash VIDEO     photosensitivity: general and red flashes
  python tools/studio.py run <p> checks.py text  VIDEO     reading time, contrast, title-safe area, clipping, lyric timing
  python tools/studio.py run <p> checks.py all   VIDEO     all three + the sync check (eyes.py sync); exit 1 on any FAIL
  (studio.py check <p> runs `all` on final/music-video.mp4)

spec: H.264 (yuv420p) + AAC in MP4; the delivery width, height and a constant fps; the video decodes to the end with no
  errors; the frame count is the song's length to the frame (round(duration * fps)); both streams start at 0; the
  audio, decoded, is the song file's audio (cross-correlation lag 0, level within 0.1 dB, same length); no black or
  frozen stretches that weren't declared (scenes declare them with MV.intent('black'|'frozen', t0, t1, why), or list
  them in work/intent.json: {"black": [[t0, t1, "why"]], "frozen": [...]}; black before the music starts (at least
  0.5 s) and after it ends (at least 1 s) is allowed without a declaration).
flash: after ITU-R BT.1702 / Ofcom and WCAG 2.x. A flash = a pair of opposing changes in relative luminance of 10% or
  more where the darker state is below 0.80; a red flash = an opposing pair of changes in (R-G-B)x320 of more than 20
  involving saturated red (R/(R+G+B) >= 0.8). FAIL: more than 3 flashes within any one second over more than 25% of
  the screen (the broadcast limit). WARN: the same over more than 10% (about the WCAG area), or 2-3 flashes a second
  sustained for more than 5 s. An automated approximation of those guidelines, not a certified Harding test.
text: uses the on-screen text recorded by the renderers (VIDEO.mv.json sidecars, merged by compose) plus
  work/text.json. Roles (data-mv-text="role" / MV.text(s, {role})): text, title, credit (every check); lyric (a sung
  line: also its timing); lyric-word (one word at a time: timing, no reading time); label (changing counters,
  timecodes: no reading time or size rule); deco (type as texture: not checked). Reading time 0.8 s + 55 ms per character (1.4-3 s; word-by-word lyric type is exempt per word),
  contrast >= 3:1 against what is behind it, inside the title-safe area (90%), never clipped, lyric text appearing on
  the beat or 8th at or just before the sung word (never after), at least 34 px tall at 1080p.
Carried over from aftermovie-studio's qa.py and am/checks.py (reading time, contrast, audio), now EDL-free.
"""
import argparse, json, os, re, subprocess, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.common import ROOT, WORK, ffmpeg, ffprobe, probe, delivery, decode_audio, audio_duration, song_file, jload, jsave, need_project  # noqa: E402

FAIL, WARN, PASS = "FAIL", "WARN", "PASS"


class Report:
    def __init__(self, name, video):
        self.name, self.video, self.items = name, video, []

    def add(self, verdict, what, detail=""):
        self.items.append({"verdict": verdict, "what": what, "detail": detail})
        print(f"  [{verdict}] {what}" + (f": {detail}" if detail else ""), flush=True)

    @property
    def verdict(self):
        v = [i["verdict"] for i in self.items]
        return FAIL if FAIL in v else WARN if WARN in v else PASS

    def write(self, extra_md=""):
        d = os.path.join(WORK, "checks", self.name)
        os.makedirs(d, exist_ok=True)
        L = [f"# {self.name} check: {os.path.basename(self.video)} — {self.verdict}", ""]
        L += [f"- **{i['verdict']}** {i['what']}" + (f": {i['detail']}" if i["detail"] else "") for i in self.items]
        open(os.path.join(d, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n" + extra_md)
        jsave(os.path.join(d, "report.json"), {"verdict": self.verdict, "items": self.items, "video": self.video})
        return d


def intents(video):
    side = jload(video + ".mv.json", {}) or {}
    out = {"black": [], "frozen": []}
    for it in side.get("intents", []):
        out.setdefault(it.get("kind", "black"), []).append((float(it["t0"]), float(it["t1"]), it.get("why", "")))
    wi = jload(os.path.join(WORK, "intent.json"), {}) or {}
    for k in ("black", "frozen"):
        for x in wi.get(k, []):
            out[k].append((float(x[0]), float(x[1]), x[2] if len(x) > 2 else ""))
    return out


def covered(t0, t1, ranges, slack=0.1):
    return any(a - slack <= t0 and t1 <= b + slack for a, b, _ in ranges)


# ------------------------------------------------------------------ spec
def check_spec(video, song=None):
    R = Report("spec", video)
    d = delivery()
    fps = d["fps"]
    song = song or song_file()
    pr = probe(video)
    fmt = pr["format"]
    vs = [s for s in pr["streams"] if s["codec_type"] == "video"]
    aus = [s for s in pr["streams"] if s["codec_type"] == "audio"]
    R.add(PASS if "mp4" in fmt.get("format_name", "") else FAIL, "container", fmt.get("format_name"))
    if len(vs) != 1:
        R.add(FAIL, "video stream", f"{len(vs)} video streams")
        return R
    v = vs[0]
    R.add(PASS if v["codec_name"] == "h264" else FAIL, "video codec", f"{v['codec_name']} {v.get('profile', '')}")
    R.add(PASS if v.get("pix_fmt") == "yuv420p" else FAIL, "pixel format", v.get("pix_fmt"))
    R.add(PASS if (int(v["width"]), int(v["height"])) == (d["width"], d["height"]) else FAIL, "resolution",
          f"{v['width']}x{v['height']} (want {d['width']}x{d['height']})")
    rf, af = v.get("r_frame_rate", "0/1"), v.get("avg_frame_rate", "0/1")

    def rate(r):
        a, b = (r.split("/") + ["1"])[:2]
        return float(a) / float(b) if float(b) else 0
    R.add(PASS if abs(rate(rf) - fps) < 0.01 and abs(rate(af) - fps) < 0.01 else FAIL, "frame rate",
          f"r {rf}, avg {af} (want constant {fps})")
    if len(aus) != 1:
        R.add(FAIL, "audio stream", f"{len(aus)} audio streams (want 1 AAC)")
    else:
        a = aus[0]
        R.add(PASS if a["codec_name"] == "aac" else FAIL, "audio codec", f"{a['codec_name']} {a.get('sample_rate')} Hz {a.get('channels')} ch")
    # decode everything: plays to the end?
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", video, "-map", "0", "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    errs = [x for x in r.stderr.splitlines() if x.strip()]
    R.add(PASS if r.returncode == 0 and not errs else FAIL, "decodes to the end", "no errors" if not errs else errs[0][:200])
    n = count_frames(video)
    dur = audio_duration(song) if song else None
    want = int(round(dur * fps)) if dur else None
    if want is not None:
        R.add(PASS if n == want else FAIL, "length", f"{n} frames = {n / fps:.3f} s; the song is {dur:.3f} s = {want} frames at {fps} fps")
    st_v = float(v.get("start_time", 0) or 0)
    st_a = float(aus[0].get("start_time", 0) or 0) if aus else 0
    R.add(PASS if abs(st_v) < 0.001 and abs(st_a) < 0.001 else FAIL, "streams start at 0", f"video {st_v:.4f} s, audio {st_a:.4f} s")
    if song and aus:
        audio_identity(R, video, song, fps)
    # black / frozen stretches
    it = intents(video)
    total = n / fps
    sm = jload(os.path.join(ROOT, "song", "song-map.json"), {}) or {}
    mus = sm.get("music") or {}
    pre = max(0.5, float(mus.get("start", 0)) + 0.25)            # black before the music starts is fine
    post = max(1.0, total - float(mus.get("end", total)) + 0.25)  # ... and after it ends
    # black = (almost) nothing on screen; frozen = (almost) no pixel changes: a dark, sparse or slow design is neither
    for kind, flt, key in (("black", "blackdetect=d=0.4:pix_th=0.05:pic_th=0.999", "black_start"),
                           ("frozen", "freezedetect=n=0.0002:d=2", "freeze_start")):
        r = subprocess.run([ffmpeg(), "-v", "info", "-i", video, "-map", "0:v", "-vf", flt, "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        spans = []
        if kind == "black":
            for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr):
                spans.append((float(m.group(1)), float(m.group(2))))
        else:
            starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", r.stderr)]
            ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", r.stderr)]
            spans = [(s0, ends[i] if i < len(ends) else total) for i, s0 in enumerate(starts)]
        bad = []
        for s0, s1 in spans:
            if kind == "black" and ((s0 <= 0.01 and s1 <= pre) or (s1 >= total - 0.05 and s1 - s0 <= post)):
                continue
            if covered(s0, s1, it[kind]):
                continue
            bad.append(f"{s0:.2f}-{s1:.2f} s")
        R.add(FAIL if bad else PASS, f"no undeclared {kind} stretches",
              ", ".join(bad[:8]) if bad else f"{len(spans)} found, all declared or at the ends" if spans else "none")
    return R


def count_frames(video):
    r = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                        "stream=nb_read_frames", "-of", "csv=p=0", video], capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except Exception:
        return -1


def audio_identity(R, video, song, fps):
    """The video's audio is the song's audio: same length, no offset, no level change."""
    sr = 48000
    a = decode_audio(video, sr=sr, mono=True)
    b = decode_audio(song, sr=sr, mono=True)
    R.add(PASS if abs(len(a) - len(b)) <= sr / fps else FAIL, "audio length",
          f"{len(a) / sr:.4f} s in the video vs {len(b) / sr:.4f} s in the song file")
    seg = int(min(len(a), len(b), 60 * sr))
    if seg < sr:
        R.add(WARN, "audio identity", "too short to compare")
        return
    start = int(np.argmax(np.convolve(np.abs(b[:seg]), np.ones(sr // 10), "valid"))) if seg > sr else 0
    lo = max(0, start - sr * 5)
    x = a[lo:lo + min(seg, 10 * sr)].astype(np.float64)
    y = b[lo:lo + min(seg, 10 * sr)].astype(np.float64)
    m = min(len(x), len(y))
    x, y = x[:m], y[:m]
    nfft = 1 << int(np.ceil(np.log2(2 * m)))
    c = np.fft.irfft(np.fft.rfft(x, nfft) * np.conj(np.fft.rfft(y, nfft)), nfft)
    lim = sr // 2
    cand = np.r_[c[:lim], c[-lim:]]
    k = int(np.argmax(cand))
    lag = k if k < lim else k - 2 * lim
    corr = float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-12))
    R.add(PASS if abs(lag) <= sr // 1000 else FAIL, "no audio offset", f"lag {lag} samples ({lag / sr * 1000:+.2f} ms) at 48 kHz")
    ra = np.sqrt(np.mean(a[:min(len(a), len(b))].astype(np.float64) ** 2))
    rb = np.sqrt(np.mean(b[:min(len(a), len(b))].astype(np.float64) ** 2))
    gain = 20 * np.log10((ra + 1e-12) / (rb + 1e-12))
    R.add(PASS if abs(gain) < 0.1 else FAIL, "no level change", f"{gain:+.3f} dB")
    R.add(PASS if corr > 0.98 else WARN if corr > 0.9 else FAIL, "same audio", f"correlation {corr:.4f} with the song file")


# ------------------------------------------------------------------ photosensitivity
def lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def transitions(series, thresh, cond):
    """series [frames, cells]; returns count of opposing transitions per (frame, cell) as an int8 array."""
    n, c = series.shape
    ext = series[0].copy()
    dirn = np.zeros(c, np.int8)
    out = np.zeros((n, c), np.int8)
    for f in range(1, n):
        y = series[f]
        up = (y - ext >= thresh) & (dirn <= 0) & cond(ext, y)
        dn = (ext - y >= thresh) & (dirn >= 0) & cond(ext, y)
        out[f] = up | dn
        dirn = np.where(up, 1, np.where(dn, -1, dirn)).astype(np.int8)
        ext = np.where(up | dn, y, np.where(dirn > 0, np.maximum(ext, y), np.where(dirn < 0, np.minimum(ext, y), ext)))
    return out


def windows(trans, fps):
    """Area share of cells with more than 3 flashes (>= 7 transitions) in each 1 s window, and with >= 4 transitions."""
    n = len(trans)
    cum = np.vstack([np.zeros((1, trans.shape[1]), np.int32), np.cumsum(trans, axis=0, dtype=np.int32)])
    w = int(round(fps))
    if n < w:
        w = n
    cnt = cum[w:] - cum[:-w] if n >= w else cum[-1:] - cum[:1]
    return (cnt >= 7).mean(axis=1), (cnt >= 4).mean(axis=1)


def check_flash(video):
    R = Report("flash", video)
    from eyes import Video
    V = Video(video, delivery()["fps"])
    fps = V.fps
    gw, gh = (16, 9) if V.w >= V.h else (9, 16)          # areas of the screen (0.7% each), not pixels: a thin moving line is not a flash
    L, RED, SAT = [], [], []
    for blk in V.stream(gw, gh):
        x = lin(blk.astype(np.float32) / 255.0).reshape(len(blk), -1, 3)
        L.append(x @ np.array([0.2126, 0.7152, 0.0722], np.float32))
        r, g, b = x[..., 0], x[..., 1], x[..., 2]
        RED.append(np.maximum(0, (r - g - b) * 320))
        SAT.append(r / np.maximum(r + g + b, 1e-6) >= 0.8)
    if not L:
        R.add(FAIL, "decode", "no frames")
        return R
    L, RED, SAT = np.concatenate(L), np.concatenate(RED), np.concatenate(SAT)
    tg = transitions(L, 0.10, lambda a, b: np.minimum(a, b) < 0.80)
    sat_any = SAT
    tr = transitions(RED, 20.0, lambda a, b: np.ones_like(a, bool))
    tr = tr & (sat_any | np.r_[sat_any[:1], sat_any[:-1]])
    out = {}
    for kind, t in (("general", tg), ("red", tr)):
        a7, a4 = windows(t, fps)
        out[kind] = (a7, a4)

        def spans(mask):
            idx = np.flatnonzero(mask)
            if not len(idx):
                return []
            res, s0, prev = [], idx[0], idx[0]
            for i in idx[1:]:
                if i > prev + 1:
                    res.append((s0, prev))
                    s0 = i
                prev = i
            res.append((s0, prev))
            return [f"{(V.f0 + a) / fps:.2f}-{(V.f0 + b + fps) / fps:.2f} s" for a, b in res]
        f25, f10 = spans(a7 > 0.25), spans(a7 > 0.10)
        R.add(FAIL if f25 else PASS, f"{kind} flashes: no more than 3 a second over 25% of the screen",
              ", ".join(f25[:6]) if f25 else f"max area {float(a7.max()) * 100:.1f}%")
        if not f25:
            R.add(WARN if f10 else PASS, f"{kind} flashes: no more than 3 a second over 10% of the screen (WCAG area)",
                  ", ".join(f10[:6]) if f10 else "ok")
        sustained = a4 > 0.25
        run, best = 0, 0
        for s in sustained:
            run = run + 1 if s else 0
            best = max(best, run)
        R.add(WARN if best > 5 * fps else PASS, f"{kind} flashing at 2-3 a second never sustained over 5 s",
              f"longest {best / fps:.1f} s")
    rate = out["general"][0]
    jsave(os.path.join(WORK, "checks", "flash", "curve.json"), {"fps": fps, "f0": V.f0,
                                                               "area_over_3_per_s": [round(float(x), 3) for x in rate[::max(1, fps // 10)]]})
    return R


# ------------------------------------------------------------------ text
def need(text):
    """Reading time for a line of text: ~0.8 s + 55 ms per character, 1.4..3 s (aftermovie-studio's rule)."""
    n = len(text.replace("\n", " "))
    return min(3.0, max(1.4, 0.8 + 0.055 * n))


def rel_lum(rgb):
    return lin(rgb) @ np.array([0.2126, 0.7152, 0.0722])


def parse_color(c):
    if not c:
        return None
    m = re.match(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)", str(c))
    if m:
        return np.array([float(m.group(i)) / 255 for i in (1, 2, 3)])
    m = re.match(r"#?([0-9a-fA-F]{6})$", str(c).strip())
    if m:
        h = m.group(1)
        return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])
    return None


def contrast_at(img, box, color):
    """WCAG contrast of the text against what is behind it, in a box of an RGB float image (0..1)."""
    from scipy.ndimage import binary_dilation
    H, W, _ = img.shape
    x, y, w, h = [int(round(v)) for v in box]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    pad = max(4, int(0.15 * (y1 - y0)))
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    reg = img[Y0:Y1, X0:X1]
    lum = rel_lum(reg)
    col = parse_color(color)
    inner = np.zeros(lum.shape, bool)
    inner[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = True
    if col is not None:
        mask = (np.abs(reg - col).max(-1) < 0.12) & inner
    else:
        mask = None
    if mask is None or mask.sum() < 8:
        v = lum[inner]
        thr = otsu(v)
        hi, lo = v[v >= thr], v[v < thr]
        if not len(hi) or not len(lo):
            return 1.0
        small = hi if len(hi) < len(lo) else lo                    # the text is the smaller class
        mask = inner & ((lum >= thr) if small is hi else (lum < thr))
    ring = binary_dilation(mask, iterations=3) & ~binary_dilation(mask, iterations=1)
    if ring.sum() < 8:
        return None
    lt = float(np.median(lum[mask])) if col is None else float(rel_lum(col[None])[0])
    lb = float(np.median(lum[ring]))
    return (max(lt, lb) + 0.05) / (min(lt, lb) + 0.05)


def otsu(v):
    hist, edges = np.histogram(v, bins=64, range=(0, 1))
    w = np.cumsum(hist)
    mu = np.cumsum(hist * (edges[:-1] + edges[1:]) / 2)
    tot = mu[-1]
    best, thr = -1, 0.5
    for i in range(1, 63):
        w0, w1 = w[i], w[-1] - w[i]
        if w0 == 0 or w1 == 0:
            continue
        m0, m1 = mu[i] / w0, (tot - mu[i]) / w1
        b = w0 * w1 * (m0 - m1) ** 2
        if b > best:
            best, thr = b, edges[i + 1]
    return thr


def check_text(video):
    R = Report("text", video)
    from eyes import Video
    from mv.song import Song
    d = delivery()
    side = jload(video + ".mv.json", {}) or {}
    texts = list(side.get("texts", []))
    for t in jload(os.path.join(WORK, "text.json"), []) or []:
        texts.append(dict(t, boxes=t.get("boxes") or [[int(round(t["t0"] * d["fps"]))] + list(t["box"])]))
    if not texts:
        R.add(PASS, "on-screen text", "none recorded (no text in the video, or not declared: see checks.py --help)")
        return R
    V = Video(video, d["fps"])
    fps = V.fps

    def scale(t):
        sp = t.get("space") or [side.get("w") or d["width"], side.get("h") or d["height"]]
        return V.w / max(1, sp[0]), V.h / max(1, sp[1]), 1080 / max(1, min(sp))
    song = Song(ROOT)
    beat_s = song.beat_s
    lines = [(re.sub(r"[^\w ]+", "", x["text"].lower()).strip(), x) for x in song.lines]
    from difflib import SequenceMatcher
    short, low, unsafe, clipped, late, small = [], [], [], [], [], []
    need_frames = {}
    for i, t in enumerate(texts):
        mid = (t["t0"] + t["t1"]) / 2
        bx = min(t.get("boxes") or [[0] + t["box"]], key=lambda b: abs(b[0] / fps - mid))
        need_frames[i] = (V.local(mid), bx[1:])
    ims = V.frames([f for f, _ in need_frames.values()], V.w)
    title_safe = (0.05 * V.w, 0.05 * V.h, 0.95 * V.w, 0.95 * V.h)
    for i, t in enumerate(texts):
        txt = t["text"].strip()
        role = t.get("role", "text")
        if role == "deco":                                  # type used as texture: not meant to be read
            continue
        sx, sy, s1080 = scale(t)
        dur = t["t1"] - t["t0"]
        at_end = t["t1"] >= V.duration + V.f0 / fps - 1.5 / fps
        if role not in ("lyric-word", "word", "kinetic", "label") and not at_end and dur < need(txt) - 1e-3:
            short.append(f"{t['t0']:.2f}s \"{txt[:30]}\" {dur:.3f}s < {need(txt):.3f}s")
        f, bx = need_frames[i]
        if f in ims:
            img = np.asarray(ims[f], dtype=np.float32) / 255
            box = [bx[0] * sx, bx[1] * sy, bx[2] * sx, bx[3] * sy]
            c = contrast_at(img, box, t.get("color"))
            if c is not None and c < 3.0:
                low.append(f"{t['t0']:.2f}s \"{txt[:30]}\" {c:.1f}:1")
        for b in t.get("boxes") or [[0] + t["box"]]:
            x, y, w, h = b[1] * sx, b[2] * sy, b[3] * sx, b[4] * sy
            if x < -0.5 or y < -0.5 or x + w > V.w + 0.5 or y + h > V.h + 0.5:
                clipped.append(f"{t['t0']:.2f}s \"{txt[:30]}\" runs off the frame")
                break
            if x < title_safe[0] or y < title_safe[1] or x + w > title_safe[2] or y + h > title_safe[3]:
                unsafe.append(f"{t['t0']:.2f}s \"{txt[:30]}\"")
                break
        if t.get("overflow"):
            clipped.append(f"{t['t0']:.2f}s \"{txt[:30]}\" overflows its box")
        if t.get("size") and t["size"] * s1080 < 34 and role not in ("label",):
            small.append(f"{t['t0']:.2f}s \"{txt[:30]}\" {t['size']:.0f}px")
        if role in ("lyric", "lyric-word", "word", "kinetic"):
            norm = re.sub(r"[^\w ]+", "", txt.lower()).strip()
            onset = None
            if role == "lyric":                             # the matching line sung nearest to when the text appears
                cands = [L for L in lines if SequenceMatcher(None, norm, L[0]).ratio() >= 0.6]
                if cands:
                    onset = min(cands, key=lambda L: abs(L[1]["t"] - t["t0"]))[1]["t"]
            else:
                cands = [w for w in song.words if re.sub(r"[^\w]+", "", w[1].lower()) == re.sub(r"[^\w]+", "", norm)]
                if cands:
                    onset = min(cands, key=lambda w: abs(w[0] - t["t0"]))[0]
            if onset is not None:
                early = onset - t["t0"]
                if early < -0.5 / fps - 1e-3:
                    late.append(f"{t['t0']:.2f}s \"{txt[:30]}\" appears {-early * 1000:.0f} ms AFTER it is sung ({onset:.2f}s)")
                elif early > beat_s / 2 + 1.0 / fps + 1e-3 and role != "lyric":
                    late.append(f"{t['t0']:.2f}s \"{txt[:30]}\" appears {early:.2f}s before it is sung (more than an 8th)")
                elif role == "lyric" and early > beat_s * 1.0 + 1.0 / fps:
                    late.append(f"{t['t0']:.2f}s \"{txt[:30]}\" appears {early:.2f}s before the line is sung (more than a beat)")
    n = len(texts)
    R.add(FAIL if short else PASS, "reading time", "; ".join(short[:6]) if short else f"{n} texts")
    R.add(FAIL if low else PASS, "contrast >= 3:1", "; ".join(low[:6]) if low else f"{n} texts")
    R.add(FAIL if clipped else PASS, "no clipped text", "; ".join(clipped[:6]) if clipped else "ok")
    R.add(WARN if unsafe else PASS, "inside the title-safe area (90%)", "; ".join(unsafe[:6]) if unsafe else "ok")
    R.add(WARN if small else PASS, "at least 34 px tall at 1080p", "; ".join(small[:6]) if small else "ok")
    R.add(FAIL if late else PASS, "lyric text on (or just before) the sung word", "; ".join(late[:6]) if late else "ok")
    return R


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["spec", "flash", "text", "all"])
    ap.add_argument("video")
    ap.add_argument("--no-sync", action="store_true")
    a = ap.parse_args()
    need_project()
    video = a.video if os.path.isabs(a.video) else os.path.join(ROOT, a.video)
    if not os.path.exists(video):
        sys.exit(f"no video at {video}")
    results = []
    for name, fn in (("spec", check_spec), ("flash", check_flash), ("text", check_text)):
        if a.cmd in (name, "all"):
            print(f"== {name}", flush=True)
            R = fn(video)
            R.write()
            results.append(R)
    sync = None
    if a.cmd == "all" and not a.no_sync:
        print("== sync", flush=True)
        import eyes
        from mv.song import Song
        V = eyes.Video(video, delivery()["fps"])
        sync = eyes.cmd_sync(V, Song(ROOT), argparse.Namespace())
    if a.cmd == "all":
        L = [f"# Checks: {os.path.relpath(video, ROOT)}", ""]
        for R in results:
            L.append(f"## {R.name}: {R.verdict}")
            L += [f"- **{i['verdict']}** {i['what']}" + (f": {i['detail']}" if i["detail"] else "") for i in R.items]
            L.append("")
        if sync:
            L += [f"## sync: {sync['in_sync']}/{sync['events']} visual events within one frame of the music "
                  f"({(sync['share'] or 0) * 100:.0f}%)", "", "| section | events | in sync | median offset ms |", "|---|---|---|---|"]
            L += [f"| {s['section']} | {s['events']} | {s['in_sync']} | {s['median_offset_ms']} |" for s in sync["sections"]]
            L.append("")
        L += ["QA can't hear: phrase-level musical awkwardness needs a human ear. Listen at the ★★★ moments and wherever "
              "a section or the energy changes."]
        os.makedirs(os.path.join(WORK, "checks"), exist_ok=True)
        open(os.path.join(WORK, "checks", "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
        verdicts = {R.name: R.verdict for R in results}
        if sync:
            verdicts["sync"] = PASS if (sync["share"] or 0) >= 0.9 or not sync["events"] else WARN
        jsave(os.path.join(WORK, "checks", "report.json"), {"video": os.path.relpath(video, ROOT), "verdicts": verdicts,
                                                            "sync": {k: v for k, v in (sync or {}).items() if k != "rows"}})
        print(f"\nchecks: " + ", ".join(f"{k} {v}" for k, v in verdicts.items()) + f" -> {os.path.join(WORK, 'checks', 'report.md')}")
    sys.exit(1 if any(R.verdict == FAIL for R in results) else 0)


if __name__ == "__main__":
    main()
