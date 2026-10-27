"""Small questions about the song, answered from the song folder, so the director doesn't re-read the big files.

  python tools/studio.py run <project> songq.py summary            tempo, key, length, sections, the ★★★ moments
  python tools/studio.py run <project> songq.py at 83.2 [91.5 ...]  bar:beat, section, chord, lyric line + word, moments
  python tools/studio.py run <project> songq.py t 17 2.5           song time (and frame) of bar 17, beat 2.5
  python tools/studio.py run <project> songq.py section "chorus 2"  its bars, times, lines, chords
  python tools/studio.py run <project> songq.py moments [--stars 2]
  python tools/studio.py run <project> songq.py bars 16 24         the bar-by-bar rows for bars 16..24
  python tools/studio.py run <project> songq.py hits kick 30 40    the kick hits between 30 s and 40 s (time, frame, strength)
  python tools/studio.py run <project> songq.py lines              every lyric line with its sung time and bar:beat

Inside the project folder (the director's cwd) `python ../../tools/songq.py ...` works too: it finds the project from
the current folder when MV_PROJECT isn't set.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv.song import Song  # noqa: E402


def mmss(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


def find_project():
    if os.environ.get("MV_PROJECT"):
        return os.environ["MV_PROJECT"]
    d = os.getcwd()
    while d and not os.path.exists(os.path.join(d, "song", "song-map.json")):
        nd = os.path.dirname(d)
        if nd == d:
            sys.exit("no song folder found: run inside a project or through studio.py run <project> songq.py ...")
        d = nd
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["summary", "at", "t", "section", "moments", "bars", "hits", "lines"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--stars", type=int, default=1)
    a = ap.parse_args()
    S = Song(find_project())
    m = S.m
    if a.what == "summary":
        t = m["tempo"]
        print(f"{m.get('artist', '')} – {m.get('title', '')}  ({m['file']})")
        print(f"{mmss(S.duration)} long; music {mmss(m['music']['start'])}–{mmss(m['music']['end'])}; {t['bpm']} BPM {t['meter']}, "
              f"bar {t['bar_s']:.2f} s, {t['grid']}; key {m['key']['name']}; {m['loudness']['integrated_lufs']} LUFS")
        for s in S.sections:
            print(f"  {s['name']:<16} bars {s['bar0']:>3}–{s['bar1'] - 1:<3} {mmss(s['t0'])}–{mmss(s['t1'])}  "
                  f"{s['loud']:>6} LUFS  V D B O {s['voice']} {s['drums']} {s['bass']} {s['other']}  {s['first_line'][:40]}")
        for mo in S.moments(3):
            print(f"  ★★★ {mmss(mo['t'])} {mo['pos']:>7}  {mo['text']}")
    elif a.what == "at":
        for x in a.args:
            d = S.at(float(x))
            print(f"{mmss(d['t'])}  frame {d['frame']}  bar {d['bar']}:{d['beat']}  {d['section']}  chord {d['chord']}  "
                  f"line: {d['line'] or '-'}  word: {d['word'] or '-'}  {' · '.join(d['moments'])}")
    elif a.what == "t":
        bar, beat = int(a.args[0]), float(a.args[1]) if len(a.args) > 1 else 0.0
        t = S.t(bar, beat)
        print(f"{bar}:{beat:g} = {t:.3f} s ({mmss(t)}), frame {S.frame(t)} at {S.fps} fps")
    elif a.what == "section":
        s = S.section(" ".join(a.args))
        print({k: v for k, v in s.items() if k not in ("why",)})
        for i in s["lines"]:
            x = S.lines[i]
            print(f"  {mmss(x['t'])}  {x['text']}")
    elif a.what == "moments":
        for mo in S.moments(a.stars):
            print(f"{mmss(mo['t'])}  {mo['pos']:>7}  {'★' * mo['stars']:<3}  {mo['text']}")
    elif a.what == "bars":
        b0, b1 = int(a.args[0]), int(a.args[1]) if len(a.args) > 1 else int(a.args[0])
        for b in S.bars[b0:b1 + 1]:
            ws = " ".join(f"{w}@{p:g}" for w, p, _ in b["words"])
            print(f"{b['i']:4d} {mmss(b['t'])} {b.get('section', ''):<14} {b['chord']:<8} {b['loud']:6.1f} "
                  f"V{b['v']} D{b['d']} B{b['b']} O{b['o']}  {ws}  {', '.join(b['events'])}")
    elif a.what == "hits":
        kind = a.args[0]
        t0 = float(a.args[1]) if len(a.args) > 1 else 0
        t1 = float(a.args[2]) if len(a.args) > 2 else S.duration
        for h in S.hits(kind):
            if t0 <= h["t"] <= t1:
                print(f"{h['t']:8.3f}  frame {h['f']:6d}  " + "  ".join(f"{k} {v}" for k, v in h.items() if k not in ("t", "f")))
    elif a.what == "lines":
        for x in S.lines:
            p = S.pos(x["t"])
            print(f"{mmss(x['t'])}  {(f'{p[0]}:{p[1]:g}' if p else '-'):>8}  {x['text']}")


if __name__ == "__main__":
    main()
