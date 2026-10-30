"""Paths, the project file, fonts and small ffmpeg helpers shared by every tool.

The project being worked on comes from MV_PROJECT (set by tools/studio.py). A project folder (projects/<name>/) holds:
  project.json   the brief, delivery format, status, lyrics_fix, versions
  song/          the song file (unchanged), its stems/ and the song folder the director reads
  cache/         analysis intermediates, job status
  work/          the director's workspace (scenes, renders, eyes, checks, its log)
  final/         music-video.mp4 and the earlier versions (music-video_v1.mp4, ...)
  review/        the user's notes (notes.json)
"""
import glob, json, os, re, shutil, subprocess, time
from contextlib import contextmanager

STUDIO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the studio install
TOOLS = os.path.join(STUDIO, "tools")
ASSETS = os.path.join(STUDIO, "assets")
FONTS_DIR = os.path.join(ASSETS, "fonts")
PROJECTS = os.path.join(STUDIO, "projects")

ROOT = os.path.abspath(os.environ["MV_PROJECT"]) if os.environ.get("MV_PROJECT") else None
SONG = os.path.join(ROOT, "song") if ROOT else None
STEMS = os.path.join(SONG, "stems") if ROOT else None
CACHE = os.path.join(ROOT, "cache") if ROOT else None
WORK = os.path.join(ROOT, "work") if ROOT else None
FINAL = os.path.join(ROOT, "final") if ROOT else None

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus")

DEFAULT_FORMAT = {"width": 1920, "height": 1080, "fps": 30}
FORMATS = {   # delivery presets offered by the web app ("custom" = width/height/fps given by hand)
    "1080p": {"width": 1920, "height": 1080, "fps": 30, "label": "1920×1080 landscape, 30 fps"},
    "1080p60": {"width": 1920, "height": 1080, "fps": 60, "label": "1920×1080 landscape, 60 fps"},
    "1080p24": {"width": 1920, "height": 1080, "fps": 24, "label": "1920×1080 landscape, 24 fps (filmic)"},
    "4k": {"width": 3840, "height": 2160, "fps": 30, "label": "3840×2160 (4K UHD), 30 fps: slow to render"},
    "vertical": {"width": 1080, "height": 1920, "fps": 30, "label": "1080×1920 vertical (Reels / Shorts / TikTok), 30 fps"},
    "square": {"width": 1080, "height": 1080, "fps": 30, "label": "1080×1080 square, 30 fps"},
    "720p": {"width": 1280, "height": 720, "fps": 30, "label": "1280×720, 30 fps (quick)"},
}


def need_project():
    if not ROOT:
        raise SystemExit("no project: run this through `python tools/studio.py run <project> <tool> ...` (sets MV_PROJECT)")
    return ROOT


def jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def jsave(path, obj, indent=1):
    """Write JSON atomically (a crash never leaves half a file). The temporary file is the writer's own (two processes
    or threads saving one file don't move each other's), and on Windows the replace is tried again for a moment while
    someone reads the file (the web app polls these files every 2 s; an open file can't be replaced there)."""
    import threading
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}-{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False, default=_np)
    for k in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == 39:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.05)


def _np(o):
    import numpy as np
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(type(o))


def project(root=None):
    """The project file (project.json), or {} if there is none."""
    r = root or ROOT
    return jload(os.path.join(r, "project.json"), {}) if r else {}


def save_project(pj, root=None):
    jsave(os.path.join(root or ROOT, "project.json"), pj)


def delivery(pj=None):
    """{'width', 'height', 'fps'} the project delivers in (project.json "format")."""
    pj = project() if pj is None else pj
    f = dict(DEFAULT_FORMAT)
    fm = pj.get("format") or {}
    if isinstance(fm, str):
        fm = FORMATS.get(fm, {})
    for k in ("width", "height", "fps"):
        if fm.get(k):
            f[k] = int(fm[k])
    return f


def song_file(root=None):
    """Absolute path of the song (the one audio file directly in song/), or None."""
    r = root or ROOT
    if not r:
        return None
    sd = os.path.join(r, "song")
    want = (project(r).get("song") or {}).get("file")
    if want and os.path.isfile(os.path.join(sd, want)):
        return os.path.join(sd, want)
    cands = sorted(f for f in os.listdir(sd) if f.lower().endswith(AUDIO_EXT)) if os.path.isdir(sd) else []
    return os.path.join(sd, cands[0]) if cands else None


def slugify(s, n=40):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:n] or "project"


# ------------------------------------------------------------------ fonts (open-licensed, assets/fonts)
FONT_FILES = {
    "inter": "Inter[opsz,wght].ttf", "mono": "SpaceMono-Regular.ttf", "mono_bold": "SpaceMono-Bold.ttf",
    "plex": "IBMPlexMono-Regular.ttf", "plex_bold": "IBMPlexMono-Bold.ttf", "anton": "Anton-Regular.ttf",
    "bebas": "BebasNeue-Regular.ttf", "narrow": "ArchivoNarrow[wght].ttf", "grotesk": "SpaceGrotesk[wght].ttf",
    "syne": "Syne[wght].ttf", "unbounded": "Unbounded[wght].ttf", "fraunces": "Fraunces[SOFT,WONK,opsz,wght].ttf",
    "instrument": "InstrumentSerif-Regular.ttf", "instrument_italic": "InstrumentSerif-Italic.ttf",
    "playfair": "PlayfairDisplay[wght].ttf", "caveat": "Caveat[wght].ttf", "marker": "PermanentMarker-Regular.ttf",
    "elite": "SpecialElite-Regular.ttf", "noto": "NotoSans[wdth,wght].ttf", "georgian": "NotoSansGeorgian[wdth,wght].ttf",
}


def font(name="mono", size=14):
    """A PIL font from assets/fonts (used by the eyes tools for their margins)."""
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(FONTS_DIR, FONT_FILES.get(name, name)), max(4, int(size)))


# ------------------------------------------------------------------ ffmpeg
def ffmpeg():
    return os.environ.get("MV_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe():
    return os.environ.get("MV_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def probe(path):
    """ffprobe as a dict (format + streams)."""
    r = subprocess.run([ffprobe(), "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def audio_duration(path):
    """Exact duration of an audio file in seconds, from its decoded samples (what a player plays)."""
    import soundfile as sf
    try:
        info = sf.info(path)
        if info.frames > 0 and not path.lower().endswith(".mp3"):
            return info.frames / info.samplerate
    except Exception:
        pass
    pcm = decode_audio(path, sr=48000, mono=True)
    return len(pcm) / 48000.0


def decode_audio(path, sr=48000, mono=False, start=None, dur=None):
    """Decode any audio (or a video's first audio stream) with ffmpeg to float32 [n] or [n, 2] at sr."""
    import numpy as np
    cmd = [ffmpeg(), "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-vn", "-map", "0:a:0", "-ac", "1" if mono else "2", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg could not decode {path}: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    a = np.frombuffer(r.stdout, np.float32)
    return a if mono else a.reshape(-1, 2)


_NVENC = None


def has_nvenc():
    """True when ffmpeg can actually encode with an NVIDIA GPU (the encoder is often listed without a GPU)."""
    global _NVENC
    if _NVENC is None:
        if os.environ.get("MV_NO_NVENC"):
            _NVENC = False
        else:
            try:
                r = subprocess.run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.1",
                                    "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, timeout=30)
                _NVENC = r.returncode == 0
            except Exception:
                _NVENC = False
    return _NVENC


def h264_args(quality="final", chunk=False):
    """Encoder arguments: NVENC when it works, libx264 otherwise. quality: preview | final | lossless.
    chunk=True: no B-frames and a closed GOP, so chunks join frame-exact with stream copy."""
    if quality == "lossless":
        a = ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0"]
    elif has_nvenc():
        a = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26", "-b:v", "0"] if quality == "preview" else
             ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17", "-b:v", "0",
              "-maxrate", "60M", "-bufsize", "120M", "-profile:v", "high", "-spatial-aq", "1", "-temporal-aq", "1"])
    else:
        a = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"] if quality == "preview" else
             ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high"])
    if chunk:
        a += ["-bf", "0", "-g", "250"] if "h264_nvenc" not in a else ["-bf", "0", "-g", "250", "-forced-idr", "1"]
    return a + ["-pix_fmt", "yuv420p"]


def frames_of(path):
    """Exact number of video frames (decodes the stream: slow for long files, exact)."""
    r = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except Exception:
        return None


def newest(pattern):
    fs = glob.glob(pattern)
    return max(fs, key=os.path.getmtime) if fs else None


@contextmanager
def file_lock(path, wait=5.0, stale_s=30.0):
    """A lock between processes for one bookkeeping file (path + ".lock", os.open O_CREAT | O_EXCL): the web app and
    the director's runner both write review/notes.json, and the director and its agents render in processes of their
    own (the plan tools' render record). A lock file older than stale_s is left over from a writer that was killed.
    After `wait` s it goes on without the lock: a bookkeeping write never holds up the work."""
    lk = path + ".lock"
    os.makedirs(os.path.dirname(lk), exist_ok=True)
    t0, fd = time.time(), None
    while fd is None and time.time() - t0 < wait:
        try:
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):       # Windows says PermissionError while it is being removed
            try:
                if time.time() - os.path.getmtime(lk) > stale_s:
                    os.remove(lk)
                    continue
            except OSError:
                pass
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            for _ in range(20):
                try:
                    os.remove(lk)
                    break
                except FileNotFoundError:
                    break
                except OSError:                          # Windows: someone is checking its age right now
                    time.sleep(0.05)


SHARED_CODE = (("work", "scenes", "lib"), ("work", "kit"))      # code and assets the director shares across scenes
SKIP_DIRS = ("__pycache__", "node_modules", "renders", "eyes")
SKIP_FILES = (".mp4", ".mov", ".mv.json", ".part", ".tmp", ".pyc", ".blend1")


def tree_files(d, skip=()):
    """Every file in a folder and below, but renders, eyes, caches, frame folders and the folders in `skip`."""
    out, skip = [], {os.path.normcase(os.path.abspath(x)) for x in skip}
    for root, dirs, fs in os.walk(d):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.endswith((".frames", ".parts"))
                   and os.path.normcase(os.path.abspath(os.path.join(root, x))) not in skip]
        out += [os.path.join(root, f) for f in fs if not f.endswith(SKIP_FILES)]
    return sorted(out)


def scene_inputs(scene, project, skip=()):
    """What a Blender scene's render depends on, besides the settings: its own folder (work/scenes/<name>/ with its
    modules and assets; only the file itself when it lies loose in work/scenes/, work/ or the project), the lib folder
    next to it (a pitch's scenes/lib/ for its scenes/<name>/), and the shared code every scene may import
    (work/scenes/lib/, work/kit/: mvblend puts them all on sys.path). A change in any of them renders the scene again."""
    project, scene = os.path.abspath(project), os.path.abspath(scene)
    d = os.path.dirname(scene)
    loose = os.path.normcase(d) in {os.path.normcase(os.path.join(project, *p)) for p in ((), ("work",), ("work", "scenes"))}
    files = {scene}
    if not loose:
        files.update(tree_files(d, skip), tree_files(os.path.join(os.path.dirname(d), "lib"), skip))
    for p in SHARED_CODE:
        files.update(tree_files(os.path.join(project, *p), skip))
    return sorted(files)
