"""Put rendered passes together and add the song: frame-exact, audio untouched.

  python tools/studio.py run <p> compose.py concat a.mp4 b.mp4 c/ -o work/renders/picture.mp4
      Join segments in order (mp4 / mov / PNG folders). Stream copy when every input is H.264 without B-frames
      with the same size, fps and pixel format (the web engine's chunks are); otherwise one re-encode. The frame
      count is verified; the song-frame ranges in the inputs' .mv.json sidecars must follow on without gaps.
  python tools/studio.py run <p> compose.py layer BASE --over OVER[:mode[:opacity]] ... -o out.mp4
      Composite passes over a base (e.g. a 2D type pass with alpha over a Blender pass). BASE / OVER: mp4, mov
      (ProRes 4444 with alpha), a PNG folder (frame_000123.png numbered by song frame, alpha kept) or color:#101010.
      Each input sits at its own song-frame range (from its sidecar); mode: normal (alpha over, default), screen,
      add, multiply, lighten, darken, difference.
  python tools/studio.py run <p> compose.py mux picture.mp4 [-o final/music-video.mp4] [--audio song/x.mp3]
      Add the original song: AAC sources are stream-copied; anything else (MP3, WAV, FLAC) is encoded straight to
      AAC 320k (no filters, no resample, no level change, no offset). The video is stream-copied when it is
      already H.264 yuv420p at the delivery size and fps, else encoded once. No -shortest: the audio is never cut.
  python tools/studio.py run <p> compose.py encode work/renders/b/ -o work/renders/b.mp4   PNG folder -> H.264

Encoder: h264_nvenc when the GPU encoder works, libx264 otherwise (--preview: fast settings). Every output gets an
<out>.mv.json sidecar (range, fps, size, the on-screen texts in song time, intents), so the checks can follow the text
from the passes to the final.
"""
import argparse, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.common import ROOT, ffmpeg, probe, h264_args, delivery, jload, jsave, song_file, frames_of, audio_duration  # noqa: E402

BLEND = {"screen": "screen", "add": "addition", "multiply": "multiply", "lighten": "lighten", "darken": "darken",
         "difference": "difference"}


def P(path):
    return path if os.path.isabs(path) or not ROOT else os.path.join(ROOT, path)


def sidecar(path):
    return jload(path.rstrip("/\\") + ".mv.json", None) or {}


def is_png_dir(path):
    return os.path.isdir(path) and bool(glob.glob(os.path.join(path, "frame_*.png")))


def png_range(path):
    nums = sorted(int(re.search(r"frame_(\d+)\.png$", f).group(1)) for f in glob.glob(os.path.join(path, "frame_*.png")))
    return (nums[0], nums[-1] + 1) if nums else (0, 0)


def info(path, fps):
    """{'kind', 'range' [a, b) in song frames, 'w', 'h', 'codec', 'pix_fmt', 'bframes'} of an input."""
    sc = sidecar(path)
    if is_png_dir(path):
        a, b = png_range(path)
        if sc.get("range"):
            a, b = max(a, sc["range"][0]), min(b, sc["range"][1])
        from PIL import Image
        im = Image.open(os.path.join(path, f"frame_{a:06d}.png"))
        return {"kind": "png", "range": [a, b], "w": im.width, "h": im.height, "alpha": im.mode == "RGBA", "side": sc}
    pr = probe(path)
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    n = frames_of(path) or int(round(float(pr["format"]["duration"]) * fps))
    a = sc.get("range", [0, n])[0]
    return {"kind": "video", "range": [a, a + n], "w": int(v["width"]), "h": int(v["height"]), "codec": v["codec_name"],
            "pix_fmt": v.get("pix_fmt"), "bframes": int(v.get("has_b_frames", 0) or 0),
            "fps": eval_rate(v.get("r_frame_rate", "0/1")), "alpha": "a" in (v.get("pix_fmt") or "").replace("gray", ""), "side": sc}


def eval_rate(r):
    a, b = (r.split("/") + ["1"])[:2]
    return float(a) / float(b or 1)


def input_args(path, inf, fps):
    if inf["kind"] == "png":
        return ["-framerate", str(fps), "-start_number", str(inf["range"][0]), "-i", os.path.join(path, "frame_%06d.png")]
    return ["-i", path]


def merge_side(sides, rng, fps, w, h, extra=None):
    texts, intents = [], []
    for s in sides:
        for t in s.get("texts", []):
            if t["t1"] > rng[0] / fps and t["t0"] < rng[1] / fps and t not in texts:
                texts.append(t)
        for it in s.get("intents", []):
            if it not in intents:
                intents.append(it)
    d = {"range": list(rng), "fps": fps, "w": w, "h": h, "t0": round(rng[0] / fps, 4), "t1": round(rng[1] / fps, 4),
         "texts": sorted(texts, key=lambda t: t["t0"]), "intents": intents, "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    d.update(extra or {})
    return d


def parse_over(spec):
    """'path[:mode[:opacity]]' -> (path, mode, opacity); Windows drive letters survive."""
    m = re.match(r"^(.*?)(?::(normal|screen|add|multiply|lighten|darken|difference))?(?::([0-9.]+))?$", spec)
    return m.group(1), m.group(2) or "normal", float(m.group(3) or 1.0)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd)[:600] + "\n" + r.stderr[-1500:])
    return r


# ------------------------------------------------------------------ concat
def concat(inputs, out, fps=None, preview=False, log=print):
    fps = fps or delivery()["fps"]
    infs = [info(p, fps) for p in inputs]
    for x, y, p in zip(infs, infs[1:], inputs[1:]):
        if x["range"][1] != y["range"][0] and (x["side"] or y["side"]):
            log(f"   WARNING: {os.path.basename(p)} starts at frame {y['range'][0]}, the one before ends at {x['range'][1]}")
    expect = sum(i["range"][1] - i["range"][0] for i in infs)
    same = all(i["kind"] == "video" and i["codec"] == "h264" and i["bframes"] == 0 and i["pix_fmt"] == "yuv420p"
               and (i["w"], i["h"]) == (infs[0]["w"], infs[0]["h"]) and abs(i["fps"] - fps) < 0.01 for i in infs)
    tmp = out + ".tmp.mp4"
    if same:
        lst = out + ".concat.txt"
        with open(lst, "w", encoding="utf-8") as fh:
            fh.write("".join(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n" for p in inputs))
        run([ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
             "-video_track_timescale", str(fps * 1000), "-movflags", "+faststart", tmp])
        os.remove(lst)
        how = "stream copy"
    else:
        W, H = infs[0]["w"], infs[0]["h"]
        args, chains = [], []
        for k, (p, i) in enumerate(zip(inputs, infs)):
            args += input_args(p, i, fps)
            chains.append(f"[{k}:v]scale={W}:{H}:flags=lanczos,fps={fps},format=yuv420p,setsar=1[v{k}]")
        fc = ";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(len(inputs))) + f"concat=n={len(inputs)}:v=1:a=0[o]"
        run([ffmpeg(), "-v", "error", "-y", *args, "-filter_complex", fc, "-map", "[o]",
             *h264_args("preview" if preview else "final"), "-r", str(fps), "-movflags", "+faststart", tmp])
        how = "re-encoded"
    n = frames_of(tmp)
    if n != expect:
        raise RuntimeError(f"concat made {n} frames, expected {expect}")
    os.replace(tmp, out)
    rng = (infs[0]["range"][0], infs[0]["range"][0] + expect)
    jsave(out + ".mv.json", merge_side([i["side"] for i in infs], rng, fps, infs[0]["w"], infs[0]["h"],
                                       {"made_by": f"compose concat ({how})", "inputs": [os.path.relpath(p, ROOT or ".") for p in inputs]}))
    log(f"concat: {len(inputs)} inputs, {expect} frames, {how} -> {out}")
    return out


# ------------------------------------------------------------------ layer
def layer(base, overs, out, fps=None, preview=False, log=print):
    fps = fps or delivery()["fps"]
    d = delivery()
    if base.startswith("color:"):
        fm = jload(os.path.join(ROOT, "song", "frames.json"), {})
        n = int(round(fm["duration"] * fps))
        bi = {"kind": "color", "range": [0, n], "w": d["width"], "h": d["height"], "side": {}}
        args = ["-f", "lavfi", "-i", f"color=c={base[6:]}:s={d['width']}x{d['height']}:r={fps}:d={n / fps:.6f}"]
    else:
        bi = info(base, fps)
        args = input_args(base, bi, fps)
    W, H = bi["w"], bi["h"]
    a0, a1 = bi["range"]
    fc = [f"[0:v]fps={fps},format=gbrap,setsar=1[b0]"]
    cur = "b0"
    sides = [bi["side"]]
    for k, spec in enumerate(overs, start=1):
        path, mode, op = parse_over(spec)
        oi = info(path, fps)
        sides.append(oi["side"])
        args += input_args(path, oi, fps)
        off = (oi["range"][0] - a0) / fps
        lead = f"[{k}:v]fps={fps},format=gbrap,scale={W}:{H}:flags=lanczos,setsar=1"
        if float(op) < 1:
            lead += f",colorchannelmixer=aa={float(op):.3f}"
        if mode == "normal":
            fc.append(f"{lead},setpts=PTS+{off:.6f}/TB[o{k}]")
            fc.append(f"[{cur}][o{k}]overlay=eof_action=pass:format=auto[b{k}]")
        else:
            pad_col = "white" if mode in ("multiply", "darken") else "black"
            dur_after = max(0.0, (a1 - oi["range"][1]) / fps)
            fc.append(f"{lead},tpad=start_duration={max(0, off):.6f}:stop_duration={dur_after:.6f}:color={pad_col}:start_mode=add:stop_mode=add[o{k}]")
            fc.append(f"[{cur}][o{k}]blend=all_mode={BLEND[mode]}:all_opacity={float(op):.3f}:shortest=1[b{k}]")
        cur = f"b{k}"
    fc.append(f"[{cur}]format=yuv420p[vout]")
    n = a1 - a0
    tmp = out + ".tmp.mp4"
    run([ffmpeg(), "-v", "error", "-y", *args, "-filter_complex", ";".join(fc), "-map", "[vout]", "-frames:v", str(n),
         *h264_args("preview" if preview else "final"), "-r", str(fps), "-movflags", "+faststart", tmp])
    got = frames_of(tmp)
    if got != n:
        raise RuntimeError(f"layer made {got} frames, expected {n}")
    os.replace(tmp, out)
    jsave(out + ".mv.json", merge_side(sides, (a0, a1), fps, W, H, {"made_by": "compose layer", "base": base, "over": overs}))
    log(f"layer: {len(overs)} pass(es) over {os.path.basename(base)}, {n} frames -> {out}")
    return out


# ------------------------------------------------------------------ encode (PNG folder -> H.264)
def encode(src, out, fps=None, preview=False, log=print):
    fps = fps or delivery()["fps"]
    i = info(src, fps)
    n = i["range"][1] - i["range"][0]
    run([ffmpeg(), "-v", "error", "-y", *input_args(src, i, fps), "-frames:v", str(n), "-vf", "format=yuv420p",
         *h264_args("preview" if preview else "final"), "-r", str(fps), "-movflags", "+faststart", out])
    jsave(out + ".mv.json", merge_side([i["side"]], i["range"], fps, i["w"], i["h"], {"made_by": "compose encode"}))
    log(f"encode: {n} frames -> {out}")
    return out


# ------------------------------------------------------------------ mux the song
def mux(video, out=None, audio=None, preview=False, force=False, log=print):
    d = delivery()
    fps = d["fps"]
    audio = audio or song_file()
    out = out or os.path.join(ROOT, "final", "music-video.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    vi = info(video, fps)
    dur = audio_duration(audio)
    want = int(round(dur * fps))
    have = vi["range"][1] - vi["range"][0]
    if vi["range"][0] != 0 and not force:
        raise SystemExit(f"the picture starts at song frame {vi['range'][0]}, not 0: the video must cover the whole song")
    if abs(have - want) > 1 and not force:
        raise SystemExit(f"the picture has {have} frames; the song needs {want} ({dur:.3f} s at {fps} fps). "
                         "Render the whole song (or --force to mux anyway)")
    ap = probe(audio)
    acodec = next(s for s in ap["streams"] if s["codec_type"] == "audio")["codec_name"]
    copy_v = vi["kind"] == "video" and vi["codec"] == "h264" and vi["pix_fmt"] == "yuv420p" and \
        (vi["w"], vi["h"]) == (d["width"], d["height"]) and abs(vi.get("fps", 0) - fps) < 0.01
    vargs = ["-c:v", "copy"] if copy_v else [*h264_args("preview" if preview else "final"), "-r", str(fps)]
    if not copy_v and (vi["w"], vi["h"]) != (d["width"], d["height"]):
        vargs = ["-vf", f"scale={d['width']}:{d['height']}:flags=lanczos,setsar=1"] + vargs
    aargs = ["-c:a", "copy"] if acodec == "aac" else ["-c:a", "aac", "-b:a", "320k"]
    tmp = out + ".tmp.mp4"
    run([ffmpeg(), "-v", "error", "-y", *input_args(video, vi, fps), "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
         *vargs, *aargs, "-movflags", "+faststart", tmp])
    os.replace(tmp, out)
    jsave(out + ".mv.json", merge_side([vi["side"]], (0, have), fps, d["width"], d["height"],
                                       {"made_by": "compose mux", "audio": os.path.relpath(audio, ROOT or "."),
                                        "audio_codec_in": acodec, "audio": "stream copy" if acodec == "aac" else "AAC 320k, no filters",
                                        "video": "stream copy" if copy_v else "encoded"}))
    log(f"mux: {have} frames + {os.path.basename(audio)} ({'copied' if acodec == 'aac' else 'AAC 320k'}) "
        f"{'video copied' if copy_v else 'video encoded'} -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("concat"); p.add_argument("inputs", nargs="+"); p.add_argument("-o", "--out", required=True); p.add_argument("--preview", action="store_true")
    p = sp.add_parser("layer"); p.add_argument("base"); p.add_argument("--over", action="append", default=[]); p.add_argument("-o", "--out", required=True); p.add_argument("--preview", action="store_true")
    p = sp.add_parser("mux"); p.add_argument("video"); p.add_argument("-o", "--out"); p.add_argument("--audio"); p.add_argument("--preview", action="store_true"); p.add_argument("--force", action="store_true")
    p = sp.add_parser("encode"); p.add_argument("src"); p.add_argument("-o", "--out", required=True); p.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    if a.cmd == "concat":
        concat([P(x) for x in a.inputs], P(a.out), preview=a.preview)
    elif a.cmd == "layer":
        overs = []
        for x in a.over:
            path, mode, op = parse_over(x)
            overs.append(f"{P(path)}:{mode}:{op}")
        layer(a.base if a.base.startswith("color:") else P(a.base), overs, P(a.out), preview=a.preview)
    elif a.cmd == "mux":
        mux(P(a.video), P(a.out) if a.out else None, P(a.audio) if a.audio else None, a.preview, a.force)
    elif a.cmd == "encode":
        encode(P(a.src), P(a.out), preview=a.preview)


if __name__ == "__main__":
    main()
