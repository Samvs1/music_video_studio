"""The director's eyes: it can't watch the video or hear the song, so these turn a rendered video into pictures and
numbers laid against the song. Output goes to work/eyes/<video name>/ (PNG + markdown), paths printed.

  python tools/studio.py run <p> eyes.py sheet  VIDEO [--times 1.2,30.5 | --every 4 | --section "chorus 1" | --moments 3 | --range 20 40 --n 24]
      contact sheet; every tile's margin: timecode, frame, bar:beat, section, chord, the lyric being sung, hits on it
  python tools/studio.py run <p> eyes.py strip  VIDEO --at 61.25 [--n 8]           consecutive frames around a moment
  python tools/studio.py run <p> eyes.py strip  VIDEO --range 60 64 [--n 12]       evenly spaced frames over a stretch
  python tools/studio.py run <p> eyes.py beats  VIDEO --section "chorus 1"  [--bars 16 24]
      beat sheet: for every downbeat, the frame just before it and the frame on it, side by side
  python tools/studio.py run <p> eyes.py motion VIDEO          the video's motion / brightness against loudness + stems, per section
  python tools/studio.py run <p> eyes.py sync   VIDEO          hard visual changes vs musical events (within 1 frame), drift per section
  python tools/studio.py run <p> eyes.py look   VIDEO          all of the above in one go + index.md (start here)

VIDEO is a path relative to the project (a preview at any size works). It must start at song time 0 unless its
.mv.json sidecar says where it sits (a section render: range [a, b) in song frames).
Sync check carried over from aftermovie-studio's qa.py, without the EDL: events are found in the rendered pixels.
"""
import argparse, json, os, subprocess, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.common import ROOT, WORK, ffmpeg, probe, font, jload, jsave, delivery, need_project  # noqa: E402
from mv.song import Song  # noqa: E402


def mmss(t, frames_fps=None):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


class Video:
    def __init__(self, path, fps=None):
        self.path = path
        pr = probe(path)
        v = next(s for s in pr["streams"] if s["codec_type"] == "video")
        self.w, self.h = int(v["width"]), int(v["height"])
        a, b = (v.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
        self.fps = fps or round(float(a) / float(b or 1))
        self.duration = float(pr["format"].get("duration") or 0)
        side = jload(path + ".mv.json", None) or {}
        self.f0 = (side.get("range") or [0])[0]           # song frame of the video's first frame
        self.side = side

    def song_t(self, i):
        return (self.f0 + i) / self.fps

    def local(self, t):
        return int(round(t * self.fps)) - self.f0

    def stream(self, w, h, chunk=256):
        """Yield uint8 frames [k, h, w, 3] in chunks, decoded at w x h."""
        cmd = [ffmpeg(), "-v", "error", "-i", self.path, "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        fs = w * h * 3
        while True:
            buf = p.stdout.read(fs * chunk)
            if not buf:
                break
            n = len(buf) // fs
            yield np.frombuffer(buf[:n * fs], np.uint8).reshape(n, h, w, 3)
        p.wait()

    def frames(self, idx, tw):
        """{local frame index: PIL image tw wide} for a set of frame indices (one decode pass with a select filter)."""
        from PIL import Image
        idx = sorted({i for i in idx if i >= 0})
        th = int(round(tw * self.h / self.w / 2)) * 2
        out = {}
        if not idx:
            return out
        for k in range(0, len(idx), 60):
            part = idx[k:k + 60]
            sel = "+".join(f"eq(n\\,{i})" for i in part)
            cmd = [ffmpeg(), "-v", "error", "-i", self.path, "-vf", f"select='{sel}',scale={tw}:{th}:flags=lanczos",
                   "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            raw = subprocess.run(cmd, capture_output=True).stdout
            fs = tw * th * 3
            for j, i in enumerate(part):
                if (j + 1) * fs <= len(raw):
                    out[i] = Image.frombytes("RGB", (tw, th), raw[j * fs:(j + 1) * fs])
        return out


def out_dir(video):
    d = os.path.join(WORK, "eyes", os.path.splitext(os.path.basename(video.rstrip("/\\")))[0])
    os.makedirs(d, exist_ok=True)
    return d


def hits_on(song, t, fps):
    """Short labels for what lands on the frame at t (within half a frame)."""
    tags = []
    for kind, lab in (("downbeats", "DOWN"), ("kick", "kick"), ("snare", "snare"), ("crash", "crash")):
        try:
            hs = song.hits(kind)
        except Exception:
            continue
        if any(abs(h["t"] - t) <= 0.5 / fps + 1e-6 for h in hs):
            tags.append(lab)
    for m in song.moments(2):
        if abs(m["t"] - t) <= 0.5 / fps + 1e-6:
            tags.append("★" * m["stars"])
    return tags


def margin(song, t, fps, extra=""):
    d = song.at(t)
    import bisect
    j = bisect.bisect_left(song.beats, t - 0.5 / fps)
    if j < len(song.beats) and abs(song.beats[j] - t) <= 0.5 / fps + 1e-6:     # a beat lands in this frame's slot
        p = song.pos(song.beats[j] + 1e-4)
        if p:
            d["bar"], d["beat"] = p[0], float(round(p[1]))
    l1 = f"{mmss(t)}  f{int(round(t * fps))}  " + (f"bar {d['bar']}:{d['beat']:.2f}" if d["bar"] is not None else "no beat") + \
         (f"  {d['section']}" if d["section"] else "") + (f"  {d['chord']}" if d["chord"] else "")
    lyr = d["line"] or ""
    if d["word"] and lyr:
        lyr = lyr.replace(d["word"], f"[{d['word']}]", 1)
    tags = hits_on(song, t, fps)
    l2 = (f'"{lyr}"' if lyr else "") + ("  " + " ".join(tags) if tags else "") + (f"  {extra}" if extra else "")
    return l1, l2


def tile_sheet(items, cols, path, title, tw=None):
    """items: [(PIL image, line1, line2, highlight)] -> one PNG with a margin under every tile."""
    from PIL import Image, ImageDraw
    if not items:
        return None
    tw = items[0][0].width
    th = items[0][0].height
    mh = 40
    rows = (len(items) + cols - 1) // cols
    W, H = cols * (tw + 6) + 6, rows * (th + mh + 6) + 40
    sheet = Image.new("RGB", (W, H), (14, 14, 16))
    dr = ImageDraw.Draw(sheet)
    f1, f2, ft = font("mono", 12), font("inter", 12), font("mono_bold", 16)
    dr.text((8, 10), title, fill=(255, 210, 63), font=ft)
    for k, (im, l1, l2, hi) in enumerate(items):
        x, y = 6 + (k % cols) * (tw + 6), 40 + (k // cols) * (th + mh + 6)
        sheet.paste(im, (x, y))
        if hi:
            dr.rectangle([x - 2, y - 2, x + tw + 1, y + th + 1], outline=(255, 210, 63), width=2)
        dr.text((x + 2, y + th + 4), l1[:max(10, tw // 7)], fill=(230, 230, 230), font=f1)
        dr.text((x + 2, y + th + 21), l2[:max(10, tw // 7)], fill=(170, 170, 175), font=f2)
    sheet.save(path)
    return path


# ------------------------------------------------------------------ sheet / strip / beats
def cmd_sheet(V, song, a):
    fps = V.fps
    if a.times:
        ts = [float(x) for x in a.times.split(",")]
        label = "times"
    elif a.section:
        s = song.section(a.section)
        ts = list(np.linspace(s["t0"], s["t1"] - 1 / fps, a.n or 16))
        label = a.section.replace(" ", "-")
    elif a.moments:
        ts = [m["t"] for m in song.moments(a.moments)]
        label = f"moments{a.moments}"
    elif a.range:
        ts = list(np.linspace(a.range[0], a.range[1], a.n or 24))
        label = f"{a.range[0]:g}-{a.range[1]:g}"
    else:
        step = a.every or max(2.0, round(V.duration / 24, 1))
        ts = list(np.arange(V.f0 / fps, V.f0 / fps + V.duration - 1 / fps, step))
        label = f"every{step:g}s"
    idx = {V.local(t): t for t in ts}
    tw = a.width or (320 if V.w >= V.h else 200)
    ims = V.frames(list(idx), tw)
    items = [(ims[i], *margin(song, (V.f0 + i) / fps, fps), False) for i in sorted(ims)]
    p = tile_sheet(items, a.cols or (6 if V.w >= V.h else 8), os.path.join(out_dir(V.path), f"sheet_{label}.png"),
                   f"{os.path.basename(V.path)} · {len(items)} frames · {label}")
    print(p)
    return p


def cmd_strip(V, song, a):
    fps = V.fps
    if a.at is not None:
        c = V.local(a.at)
        n = a.n or 8
        idx = list(range(c - n // 2 + 1, c + n // 2 + 1))
        label = f"at{a.at:g}"
    else:
        idx = sorted({V.local(t) for t in np.linspace(a.range[0], a.range[1], a.n or 12)})
        label = f"{a.range[0]:g}-{a.range[1]:g}"
    tw = a.width or (360 if V.w >= V.h else 220)
    ims = V.frames(idx, tw)
    items = [(ims[i], *margin(song, (V.f0 + i) / fps, fps), a.at is not None and i == V.local(a.at)) for i in sorted(ims)]
    p = tile_sheet(items, len(items) if len(items) <= 8 else 6, os.path.join(out_dir(V.path), f"strip_{label}.png"),
                   f"{os.path.basename(V.path)} · strip {label}")
    print(p)
    return p


def cmd_beats(V, song, a):
    fps = V.fps
    if a.bars:
        b0, b1 = a.bars
        label = f"bars{b0}-{b1}"
    else:
        s = song.section(a.section) if a.section else max(song.sections, key=lambda s: s["loud"])
        b0, b1 = s["bar0"], s["bar1"] - 1
        label = s["name"].replace(" ", "-")
    pairs = []
    for b in range(b0, b1 + 1):
        if b >= len(song.downbeats):
            break
        f = V.local(song.downbeats[b])
        pairs.append((b, f - 1, f))
    tw = a.width or (300 if V.w >= V.h else 180)
    ims = V.frames([x for _, p, q in pairs for x in (p, q)], tw)
    items = []
    for b, p, q in pairs:
        for i, tag in ((p, f"bar {b} · 1 frame before"), (q, f"bar {b} · ON the downbeat")):
            if i in ims:
                l1, l2 = margin(song, (V.f0 + i) / fps, fps)
                items.append((ims[i], l1, tag + "  " + l2, i == q))
    path = tile_sheet(items, 4 if V.w >= V.h else 6, os.path.join(out_dir(V.path), f"beats_{label}.png"),
                      f"{os.path.basename(V.path)} · beat sheet {label}: pairs = frame before | frame on each downbeat")
    print(path)
    return path


# ------------------------------------------------------------------ measurements of the video
def measure(V, w=160, h=90):
    """Per-frame: change (mean abs difference to the previous frame, 0..1), luma (0..1), saturation (0..1)."""
    diff, luma, sat = [], [], []
    prev = None
    ww = w if V.w >= V.h else int(round(h * V.w / V.h / 2)) * 2
    hh = h if V.w >= V.h else int(round(w * V.h / V.w / 2)) * 2
    for blk in V.stream(ww, hh):
        f = blk.astype(np.float32) / 255.0
        for k in range(len(f)):
            x = f[k]
            diff.append(0.0 if prev is None else float(np.abs(x - prev).mean()))
            mx, mn = x.max(-1), x.min(-1)
            luma.append(float((x @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()))
            sat.append(float(np.where(mx > 1e-3, (mx - mn) / np.maximum(mx, 1e-3), 0).mean()))
            prev = x
    return np.array(diff), np.array(luma), np.array(sat)


def musical_events(song, fps, t_end):
    """{kind: song times}: the felt pulse (beats, filled through gaps and extrapolated past the ends), 8ths, 16ths,
    kicks, snares, sung words, onsets, stops."""
    beats = np.array(song.beats)
    per = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    fill = []
    for x, y in zip(beats[:-1], beats[1:]):
        k = 1
        while y - x > 1.6 * per and x + k * per < y - 0.5 * per and k <= 16:
            fill.append(x + k * per)
            k += 1
    per_end = float(np.median(np.diff(beats[-16:]))) if len(beats) > 2 else per
    per_start = float(np.median(np.diff(beats[:16]))) if len(beats) > 2 else per
    pre = beats[0] - per_start * np.arange(1, int(beats[0] / per_start) + 2)
    post = beats[-1] + per_end * np.arange(1, int(max(0, t_end - beats[-1]) / per_end) + 2)
    B = np.sort(np.r_[beats, fill, pre[pre >= 0], post])
    E8 = np.sort(np.r_[B, (B[:-1] + B[1:]) / 2])
    E16 = np.sort(np.r_[E8, (E8[:-1] + E8[1:]) / 2])

    def ts(kind):
        try:
            return np.array([h["t"] for h in song.hits(kind)])
        except Exception:
            return np.array([])
    stops = []
    for m in song.m["moments"]:
        if "stop" in m["kind"]:
            stops.append(m["t"])
            try:
                stops.append(m["t"] + float(m["text"].split("stop-time: ")[1].split(" s")[0]) + 0.2)
            except Exception:
                pass
    return {"beat": B, "8th": E8, "kick": ts("kick"), "snare": ts("snare"), "word": np.array([w[0] for w in song.words]),
            "stop": np.array(stops), "16th": E16, "onset": ts("onset")}


def nearest_event(ev, t):
    best = (9.0, None, None)
    for k in ("beat", "8th", "word", "kick", "snare", "stop", "16th", "onset"):
        arr = ev[k]
        if len(arr):
            i = int(np.argmin(np.abs(arr - t)))
            d = t - arr[i]
            if abs(d) < abs(best[0]) - 0.004:
                best = (float(d), k, float(arr[i]))
    return best


def visual_events(diff, fps):
    from scipy.signal import find_peaks
    from scipy.ndimage import gaussian_filter1d
    base = gaussian_filter1d(diff, 8)
    pk, _ = find_peaks(diff, height=np.maximum(0.06, base * 2.2), distance=3)
    out = []
    for p in pk:
        q = p
        while q > 0 and diff[q - 1] > 0.35 * diff[p] and p - q < 6:      # the change starts where the ramp starts
            q -= 1
        out.append((q, float(diff[p])))
    return out


def cmd_sync(V, song, a, D=None):
    fps = V.fps
    diff, luma, sat = D if D is not None else measure(V)
    ev = musical_events(song, fps, V.f0 / fps + len(diff) / fps)
    rows, per_sec = [], {}
    tol = 1.0 / fps + 0.004
    for q, strength in visual_events(diff, fps):
        t = (V.f0 + q) / fps
        d, k, te = nearest_event(ev, t)
        ok = abs(d) <= tol
        s = song.section_at(t)
        name = s["name"] if s else ("before the music" if t < song.m["music"]["start"] else "after")
        rows.append({"t": round(t, 3), "frame": V.f0 + q, "strength": round(strength, 3), "event": k,
                     "event_t": round(te, 3) if te is not None else None, "offset_ms": round(d * 1000, 1), "ok": ok, "section": name})
        per_sec.setdefault(name, []).append(rows[-1])
    sections = []
    order = [s["name"] for s in song.sections]
    for name in sorted(per_sec, key=lambda n: order.index(n) if n in order else (-1 if n.startswith("before") else 999)):
        r = per_sec[name]
        offs = np.array([x["offset_ms"] for x in r if x["ok"]])
        sections.append({"section": name, "events": len(r), "in_sync": sum(x["ok"] for x in r),
                         "median_offset_ms": round(float(np.median(offs)), 1) if len(offs) else None,
                         "mean_abs_ms": round(float(np.mean(np.abs([x["offset_ms"] for x in r]))), 1)})
    n_ok = sum(x["ok"] for x in rows)
    res = {"video": os.path.relpath(V.path, ROOT), "fps": fps, "events": len(rows), "in_sync": n_ok,
           "share": round(n_ok / len(rows), 3) if rows else None, "tolerance_ms": round(tol * 1000, 1),
           "sections": sections, "rows": rows}
    d = out_dir(V.path)
    jsave(os.path.join(d, "sync.json"), res)
    L = [f"# Sync: {os.path.basename(V.path)}", "",
         f"Hard visual changes (cuts, flashes, hits) found by frame differencing, each matched to the nearest musical event "
         f"(beat, 8th, sung word, kick, snare, stop, 16th, onset). In sync = within one frame ({tol * 1000:.0f} ms).", "",
         f"**{n_ok}/{len(rows)} visual events land within one frame of a musical event.**", "",
         "| section | events | in sync | median offset of the synced (ms) | mean abs offset (ms) |", "|---|---|---|---|---|"]
    L += [f"| {s['section']} | {s['events']} | {s['in_sync']} | {s['median_offset_ms']} | {s['mean_abs_ms']} |" for s in sections]
    bad = [x for x in rows if not x["ok"]]
    L += ["", "A positive offset = the picture changes after the music; drift = the median moving section to section.", ""]
    if bad:
        L += ["## Off the music", "", "| t | frame | section | nearest | offset ms | strength |", "|---|---|---|---|---|---|"]
        L += [f"| {mmss(x['t'])} | {x['frame']} | {x['section']} | {x['event']} {x['event_t']} | {x['offset_ms']:+.0f} | {x['strength']} |"
              for x in bad[:80]]
    open(os.path.join(d, "sync.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"sync: {n_ok}/{len(rows)} visual events within one frame -> {os.path.join(d, 'sync.md')}")
    return res


def cmd_motion(V, song, a, D=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fps = V.fps
    diff, luma, sat = D if D is not None else measure(V)
    n = len(diff)
    t = (V.f0 + np.arange(n)) / fps
    from scipy.ndimage import gaussian_filter1d
    motion = gaussian_filter1d(diff, max(1, fps // 6))
    try:
        cur = song.frames["curves"]
        k = np.clip(np.round(t * song.fps).astype(int), 0, song.n_frames - 1)
        mus = {name: np.array(cur[name])[k] for name in ("loud", "drums", "bass", "vocals", "other")}
    except Exception:
        mus = {}
    bg, fg, dim = "#101114", "#e8e6e1", "#6f7278"
    fig, axes = plt.subplots(4, 1, figsize=(18, 8.5), sharex=True, gridspec_kw={"height_ratios": [0.35, 1.2, 1, 1], "hspace": 0.1})
    fig.patch.set_facecolor(bg)
    for ax in axes:
        ax.set_facecolor(bg)
        ax.tick_params(colors=dim, labelsize=9)
        for sp in ax.spines.values():
            sp.set_visible(False)
    ax = axes[0]
    cols = {"chorus": "#e8734a", "verse": "#5b8fd9", "bridge": "#a46fd9", "intro": "#6c7a89", "outro": "#6c7a89",
            "instrumental": "#3fae8f", "break": "#2f6f60", "pre-chorus": "#d9a441", "post-chorus": "#d98a41", "refrain": "#d98a41"}
    for s in song.sections:
        ax.axvspan(s["t0"], s["t1"], color=cols.get(s["label"].split(" ")[0], "#6c7a89"), alpha=0.85)
        ax.text((s["t0"] + s["t1"]) / 2, 0.5, s["name"], ha="center", va="center", fontsize=8, color=bg, clip_on=True)
    ax.set_yticks([])
    ax.set_ylim(0, 1)
    ax = axes[1]
    m01 = motion / (np.percentile(motion, 99) + 1e-9)
    ax.fill_between(t, 0, np.clip(m01, 0, 1.2), color="#ffd23f", alpha=0.35, lw=0, label="video motion (smoothed change)")
    ax.plot(t, np.clip(m01, 0, 1.2), color="#ffd23f", lw=0.9)
    if "loud" in mus:
        ax.plot(t, mus["loud"], color="#e8734a", lw=1.2, label="song loudness")
    for mo in song.moments(3):
        ax.axvline(mo["t"], color=fg, lw=0.8, alpha=0.5)
    ax.legend(loc="upper left", fontsize=8, facecolor=bg, labelcolor=fg, frameon=False)
    ax.set_ylabel("motion / loud", color=fg, fontsize=9)
    ax = axes[2]
    ax.plot(t, luma, color=fg, lw=0.9, label="video brightness")
    ax.plot(t, sat, color="#7fb2ff", lw=0.9, label="video saturation")
    ax.legend(loc="upper left", fontsize=8, facecolor=bg, labelcolor=fg, frameon=False)
    ax.set_ylim(0, 1)
    ax = axes[3]
    for name, c in (("drums", "#e8e6e1"), ("bass", "#7fb2ff"), ("vocals", "#e8734a"), ("other", "#3fae8f")):
        if name in mus:
            ax.plot(t, mus[name], color=c, lw=0.8, label=name)
    ax.legend(loc="upper left", fontsize=8, facecolor=bg, labelcolor=fg, frameon=False, ncol=4)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("song time (s)", color=dim)
    fig.suptitle(f"{os.path.basename(V.path)}: the picture against the music", color=fg, x=0.01, ha="left", fontsize=13)
    d = out_dir(V.path)
    p = os.path.join(d, "motion.png")
    fig.savefig(p, dpi=70, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    rows = []
    for s in song.sections:
        sel = (t >= s["t0"]) & (t < s["t1"])
        if sel.sum() < 3:
            continue
        r = float(np.corrcoef(motion[sel], mus["loud"][sel])[0, 1]) if "loud" in mus and np.std(motion[sel]) > 1e-6 and np.std(mus["loud"][sel]) > 1e-6 else None
        rows.append({"section": s["name"], "motion": round(float(motion[sel].mean() * 100), 2), "brightness": round(float(luma[sel].mean()), 2),
                     "saturation": round(float(sat[sel].mean()), 2), "loud": round(float(mus["loud"][sel].mean()), 2) if "loud" in mus else None,
                     "motion_follows_loudness_r": round(r, 2) if r is not None and np.isfinite(r) else None})
    jsave(os.path.join(d, "motion.json"), {"sections": rows})
    print(p)
    for r in rows:
        print(f"   {r['section']:<16} motion {r['motion']:6.2f}  brightness {r['brightness']:.2f}  loud {r['loud']}  r={r['motion_follows_loudness_r']}")
    return p, rows


def cmd_look(V, song, a):
    """Everything in one folder: sheet over the whole video, beat sheet of the loudest section, the ★★★ moments,
    motion plot, sync report, and index.md that lists them with the key numbers."""
    D = measure(V)
    paths = {}
    a2 = argparse.Namespace(**{**vars(a), "times": None, "section": None, "moments": None, "range": None, "n": None})
    paths["sheet"] = cmd_sheet(V, song, a2)
    if song.moments(3):
        paths["moments"] = cmd_sheet(V, song, argparse.Namespace(**{**vars(a2), "moments": 3}))
    paths["beats"] = cmd_beats(V, song, argparse.Namespace(**{**vars(a), "section": None, "bars": None}))
    paths["motion"], rows = cmd_motion(V, song, a, D)
    sync = cmd_sync(V, song, a, D)
    d = out_dir(V.path)
    L = [f"# Eyes on {os.path.basename(V.path)}", "", "Open these (they are pictures; look at them):", ""]
    L += [f"- **{k}**: `{os.path.relpath(p, ROOT)}`" for k, p in paths.items() if p]
    L += ["", f"**Sync:** {sync['in_sync']}/{sync['events']} visual events within one frame of the music "
          f"(`{os.path.relpath(os.path.join(d, 'sync.md'), ROOT)}`)", "", "| section | motion | brightness | loud | motion~loudness r |", "|---|---|---|---|---|"]
    L += [f"| {r['section']} | {r['motion']} | {r['brightness']} | {r['loud']} | {r['motion_follows_loudness_r']} |" for r in rows]
    open(os.path.join(d, "index.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(os.path.join(d, "index.md"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sheet", "strip", "beats", "motion", "sync", "look"])
    ap.add_argument("video")
    ap.add_argument("--times")
    ap.add_argument("--every", type=float)
    ap.add_argument("--section")
    ap.add_argument("--moments", type=int)
    ap.add_argument("--range", nargs=2, type=float)
    ap.add_argument("--n", type=int)
    ap.add_argument("--at", type=float)
    ap.add_argument("--bars", nargs=2, type=int)
    ap.add_argument("--cols", type=int)
    ap.add_argument("--width", type=int)
    a = ap.parse_args()
    need_project()
    path = a.video if os.path.isabs(a.video) else os.path.join(ROOT, a.video)
    song = Song(ROOT)
    V = Video(path, delivery()["fps"])
    {"sheet": cmd_sheet, "strip": cmd_strip, "beats": cmd_beats, "motion": cmd_motion, "sync": cmd_sync, "look": cmd_look}[a.cmd](V, song, a)


if __name__ == "__main__":
    main()
