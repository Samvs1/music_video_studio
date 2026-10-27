"""Render an HTML/JS scene frame by frame in headless Chromium (see README.md next to this file).

  python tools/studio.py run <project> web-engine/render.py <scene.js|scene.html> --out work/renders/a.mp4
        [--from 12.0 --to 30.5 | --frames 360:915 | --section "chorus 1"]   default: the whole song
        [--preview | --scale 0.5]          low-res + fast encode (same fps, so sync checks still hold)
        [--transparent]                    PNG frames with alpha (a folder, or .mov = ProRes 4444) for layering
        [--still 12.5,40.2]                PNG stills at song times instead of a video (style frames)
        [--workers N] [--gpu auto|on|off] [--seed N] [--fresh] [--chunk 2.0]

Paths are relative to the project folder. A re-run after a crash (or a section render) reuses finished chunks of the
same scene version; any change to the scene's folder renders them again. Writes <out>.mv.json next to the output:
the frame range, the browser / GPU used, and every text that was on screen (for the text checks).
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from mv import web  # noqa: E402
from mv.common import ROOT, jload, need_project  # noqa: E402


def frame_range(a, fps, n_total, song_dir):
    if a.frames:
        x, y = a.frames.split(":")
        return int(x or 0), int(y) if y else n_total
    if a.section:
        sm = jload(os.path.join(song_dir, "song-map.json"), {})
        s = next((s for s in sm.get("sections", []) if s["name"].lower() == a.section.lower()), None)
        if not s:
            sys.exit(f"no section {a.section!r}: {', '.join(x['name'] for x in sm.get('sections', []))}")
        return int(round(s["t0"] * fps)), int(round(s["t1"] * fps))
    if a.from_ is not None or a.to is not None:
        return int(round((a.from_ or 0) * fps)), int(round(a.to * fps)) if a.to is not None else n_total
    return 0, n_total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene")
    ap.add_argument("--out")
    ap.add_argument("--from", dest="from_", type=float)
    ap.add_argument("--to", type=float)
    ap.add_argument("--frames")
    ap.add_argument("--section")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--scale", type=float)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=int)
    ap.add_argument("--transparent", action="store_true")
    ap.add_argument("--still")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--gpu", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--chunk", type=float, default=2.0)
    a = ap.parse_args()
    need_project()
    scene = a.scene if os.path.isabs(a.scene) else os.path.join(ROOT, a.scene)
    if not os.path.exists(scene):
        sys.exit(f"no scene at {scene}")
    scale = a.scale or (0.5 if a.preview else 1.0)
    if a.still:
        out_dir = a.out or os.path.join(ROOT, "work", "renders", "stills")
        out_dir = out_dir if os.path.isabs(out_dir) else os.path.join(ROOT, out_dir)
        web.stills(scene, [float(x) for x in a.still.split(",")], out_dir, ROOT, a.width, a.height, scale, a.transparent, a.gpu, a.seed)
        return
    if not a.out:
        sys.exit("--out is required (e.g. work/renders/intro.mp4)")
    out = a.out if os.path.isabs(a.out) else os.path.join(ROOT, a.out)
    os.makedirs(os.path.dirname(out.rstrip("/\\")) or ".", exist_ok=True)
    fm = jload(os.path.join(ROOT, "song", "frames.json"), {})
    from mv.common import delivery
    fps = a.fps or delivery()["fps"]
    n_total = int(round(fm.get("duration", 0) * fps))
    rng = frame_range(a, fps, n_total, os.path.join(ROOT, "song"))
    web.render(scene, out, ROOT, rng, a.width, a.height, fps, "preview" if a.preview else "final", scale, a.transparent,
               a.workers, a.gpu, a.seed, a.chunk, a.fresh)


if __name__ == "__main__":
    main()
