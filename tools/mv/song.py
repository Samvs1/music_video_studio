"""The song folder as a Python object, for scene scripts, Blender scenes and the tools. Standard library only (it also
runs inside Blender's Python).

    from mv.song import Song
    song = Song("projects/my-song")          # or the song/ folder itself; default: $MV_PROJECT
    song.t(17, 2.5)                          # seconds of bar 17, 2.5 beats after its downbeat
    song.pos(83.2)                           # (bar, beat) -> (37, 1.98)
    song.at(83.2)                            # {'t', 'frame', 'bar', 'beat', 'section', 'chord', 'line', 'word', ...}
    song.cue(t)                              # the beat or 8th at or just before t (where lyric text appears)
    song.frame(t)                            # round(t * fps)
    song.curve("drums")                      # one value per video frame (frames.json)
    song.hits("kick")                        # [{'t', 'f', 's'}, ...] (hits.json)
    song.pulse("kick", t, decay=0.15)        # 0..1: a decaying pulse from the hits at or before t
    song.moments(3)                          # the ★★★ moments
    song.section("chorus 2")                 # {'name', 't0', 't1', 'bar0', 'bar1', ...}

Times are song time = video time. See song/README.md for the timing rules.
"""
import bisect, json, math, os


class Song:
    def __init__(self, where=None):
        where = where or os.environ.get("MV_PROJECT") or "."
        where = os.path.abspath(where)
        self.dir = where if os.path.exists(os.path.join(where, "song-map.json")) else os.path.join(where, "song")
        self.m = self._load("song-map.json")
        self._frames = None
        self._hits = None
        self.duration = float(self.m["duration"])
        self.beats = [float(b) for b in self.m["beats"]]
        self.bars = self.m["bars"]
        self.downbeats = [float(b["t"]) for b in self.bars]
        self.beat_s = float(self.m["tempo"]["beat_s"])
        self.bpm = float(self.m["tempo"]["bpm"])
        self.sections = self.m["sections"]
        self.lines = self.m["lyrics"]
        self.words = sorted(((float(w["t"]), w["w"], x["i"]) for x in self.lines for w in x["words"]))
        self._dbi = [self._nearest_beat(d) for d in self.downbeats]

    def _load(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return json.load(f)

    @property
    def frames(self):
        if self._frames is None:
            self._frames = self._load("frames.json")
        return self._frames

    @property
    def fps(self):
        return self.frames["fps"]

    @property
    def n_frames(self):
        return self.frames["frames"]

    # ------------------------------------------------------------------ time <-> bar:beat
    def _nearest_beat(self, t):
        i = bisect.bisect_left(self.beats, t)
        if i > 0 and (i == len(self.beats) or t - self.beats[i - 1] < self.beats[i] - t):
            i -= 1
        return i

    def _beat_time(self, x):
        """Time of fractional beat index x (extrapolated with the beat length outside the grid)."""
        B = self.beats
        if x <= 0:
            return B[0] + x * self.beat_s
        if x >= len(B) - 1:
            return B[-1] + (x - (len(B) - 1)) * self.beat_s
        i = int(math.floor(x))
        return B[i] + (x - i) * (B[i + 1] - B[i])

    def t(self, bar, beat=0.0):
        """Song time of bar:beat (bar 0 = the first downbeat, beat 0 = the downbeat, 2.5 = the 8th after beat 2)."""
        if bar < 0 or bar >= len(self._dbi):
            base = (bar - (len(self._dbi) - 1)) * 4 + self._dbi[-1] if bar >= 0 else bar * 4 + self._dbi[0]
            return self._beat_time(base + beat)
        return self._beat_time(self._dbi[bar] + beat)

    def pos(self, t):
        """(bar, beat) of song time t, or None outside the beat grid."""
        if not self.beats or t < self.beats[0] - 2 * self.beat_s or t > self.beats[-1] + 4 * self.beat_s:
            return None
        bar = max(0, bisect.bisect_right(self.downbeats, t + 1e-6) - 1)
        j = bisect.bisect_right(self.beats, t + 1e-6) - 1
        if j < 0:
            frac = (t - self.beats[0]) / self.beat_s
            j = 0
        else:
            nxt = self.beats[j + 1] if j + 1 < len(self.beats) else self.beats[j] + self.beat_s
            frac = (t - self.beats[j]) / (nxt - self.beats[j])
        return bar, round(j - self._dbi[bar] + frac, 3)

    def frame(self, t):
        return int(round(t * self.fps))

    def cue(self, t):
        """The beat or 8th at or just before t: where lyric text for a word sung at t appears."""
        grid = []
        for i, b in enumerate(self.beats):
            grid.append(b)
            nxt = self.beats[i + 1] if i + 1 < len(self.beats) else b + self.beat_s
            grid.append((b + nxt) / 2)
        i = bisect.bisect_right(grid, t + 1e-4) - 1
        return grid[i] if i >= 0 else t

    def snap(self, t, step="beat"):
        """Nearest beat ('beat'), 8th ('8th') or downbeat ('bar') to t."""
        arr = self.downbeats if step == "bar" else self.beats
        if step == "8th":
            arr = sorted(self.beats + [(a + b) / 2 for a, b in zip(self.beats, self.beats[1:])])
        i = bisect.bisect_left(arr, t)
        cands = arr[max(0, i - 1):i + 1]
        return min(cands, key=lambda x: abs(x - t)) if cands else t

    # ------------------------------------------------------------------ what happens when
    def section_at(self, t):
        for s in self.sections:
            if s["t0"] <= t < s["t1"]:
                return s
        return None

    def section(self, name):
        for s in self.sections:
            if s["name"] == name:
                return s
        raise KeyError(f"no section {name!r}: {', '.join(s['name'] for s in self.sections)}")

    def chord_at(self, t):
        for c in self.m["chords"]:
            if c["t"] <= t < (c["end"] or 1e9):
                return c["chord"]
        return None

    def line_at(self, t, hold=0.3):
        for x in self.lines:
            if x["t"] - 0.05 <= t <= x["end"] + hold:
                return x
        return None

    def word_at(self, t):
        i = bisect.bisect_right([w[0] for w in self.words], t + 1e-4) - 1
        if i < 0:
            return None
        w = self.words[i]
        line = self.line_at(t)
        return w[1] if line is not None and line["i"] == w[2] else None

    def moments(self, stars=1):
        return [m for m in self.m["moments"] if m["stars"] >= stars]

    def at(self, t):
        p = self.pos(t)
        s = self.section_at(t)
        line = self.line_at(t)
        near = [m for m in self.m["moments"] if abs(m["t"] - t) < 0.25]
        return {"t": round(t, 3), "frame": self.frame(t) if self._frames or os.path.exists(os.path.join(self.dir, "frames.json")) else None,
                "bar": p[0] if p else None, "beat": p[1] if p else None, "section": s["name"] if s else None,
                "chord": self.chord_at(t), "line": line["text"] if line else None, "word": self.word_at(t),
                "moments": [f"{'★' * m['stars']} {m['text']}" for m in near]}

    # ------------------------------------------------------------------ frame data
    def curve(self, name):
        c = self.frames["curves"].get(name)
        if c is None:
            c = self.frames["grid"].get(name)
        if c is None:
            raise KeyError(f"no curve {name!r}: {', '.join(list(self.frames['curves']) + list(self.frames['grid']))}")
        return c

    def value(self, name, frame):
        c = self.curve(name)
        return c[max(0, min(len(c) - 1, int(frame)))]

    def hits(self, kind, min_s=0.0):
        if self._hits is None:
            self._hits = self._load("hits.json")
        h = self._hits[kind]
        return [x for x in h if x.get("s", 1.0) >= min_s] if isinstance(h, list) else h

    def pulse(self, kind, t, decay=0.15, min_s=0.2):
        """Sum of exponentially decaying impulses from the hits at or before t (clamped to 1)."""
        hs = self.hits(kind, min_s)
        ts = [h["t"] for h in hs]
        i = bisect.bisect_right(ts, t + 1e-6)
        v = 0.0
        for h in hs[max(0, i - 8):i]:
            v += h.get("s", 1.0) * math.exp(-(t - h["t"]) / decay)
        return min(1.0, v)

    def since(self, kind, t, min_s=0.2):
        """Seconds since the last hit of `kind` at or before t (inf if none)."""
        ts = [h["t"] for h in self.hits(kind, min_s)]
        i = bisect.bisect_right(ts, t + 1e-6) - 1
        return t - ts[i] if i >= 0 else float("inf")
