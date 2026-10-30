# blender: headless 3D passes with the song data

```
python tools/studio.py run <p> blender/run.py work/scenes/stage.py --out work/renders/stage --section "chorus 1"
python tools/studio.py run <p> blender/run.py work/scenes/stage.py --out work/renders/stage.mp4 --engine cycles --samples 64
python tools/studio.py run <p> blender/run.py work/scenes/stage.py --still 61.2 --out work/style-frames/stage
```
Options: `--from/--to`, `--frames a:b`, `--section NAME` (default: the whole song) · `--engine auto|eevee|cycles|workbench`
(auto: Eevee when there is an NVIDIA GPU, Cycles on the CPU otherwise) · `--samples N` · `--preview` / `--scale` ·
`--transparent` (film transparent, RGBA PNGs for layering) · `--workers N` (Blender processes on parts of the range) ·
`--still t1,t2` · `--fresh`.

Output: a folder of `frame_000123.png` numbered by song frame (Blender frame = song frame), or `.mp4`. Resume is built
in: Blender skips frames that exist; when the scene's inputs (below) or the settings change, old frames are cleared. Eevee and
Workbench need OpenGL: on Linux without a display the runner wraps Blender in `xvfb-run`. Cycles uses OptiX / CUDA /
HIP / Metal when Blender finds a GPU, else the CPU; it denoises with OpenImageDenoise when the build has it.
Writes `<out>.mv.json` (range, engine, Blender version, timings, declared text).

## A scene script
```python
import bpy

def build(mv):              # once: objects, materials, lights, a camera (required); keyframes if you like
    bpy.ops.object.camera_add(location=(0, -10, 2))
    mv.scene.camera = bpy.context.active_object
    ...

def frame(mv, f):           # optional: before every frame f; must depend only on f (frames render in any order)
    t = mv.t(f)             # song time of frame f (the end of its slot: what happens in the slot shows in the frame)
    k = mv.song.pulse("kick", t)
```
`mv`: `mv.song` (tools/mv/song.py `Song`: `t(bar, beat)`, `pos(t)`, `at(t)`, `cue(t)`, `hits(kind)`, `pulse(kind, t)`,
`since(kind, t)`, `section(name)`, `moments(stars)`, `curve(name)`, `value(name, f)`), `mv.fps`, `mv.width`,
`mv.height`, `mv.f0`, `mv.f1`, `mv.seed`, `mv.preview`, `mv.scene`, `mv.t(f)`, `mv.frame(t)`, `mv.curve(name)`,
`mv.value(name, f)`, `mv.random(f, key)`, and `mv.text(text, t0, t1, box=[x, y, w, h], role, color, size)` to declare
text this pass shows (for the text checks; type is usually better in a web-engine pass on top).

## Shared code and models
A scene script can import what the whole video shares. These folders are on `sys.path`, in this order:
- the scene's own folder (`work/scenes/stage/` for `work/scenes/stage/scene.py`): its own helpers;
- the `lib/` folder next to it (`work/scenes/lib/` for that scene; a pitch's own `scenes/lib/` for its
  `work/pitches/round-K/pN/scenes/proof/scene.py`, and the same once it is promoted to `work/scenes/pitch-K-N/`);
- `work/scenes/lib/`: the film's shared code (the palette, the motifs, the motion rules);
- `work/kit/`: the assets built once and used across the video: a character rig, a set of procedural models, an
  environment (`work/kit/city/__init__.py` -> `from city import build_city`). Procedural models in Python are the
  first choice (code: deterministic, cached by content); a `.blend` there is appended or linked by its path
  (`os.path.join(mv.project, "work", "kit", "city", "city.blend")`).
They come after Python's and Blender's own modules: a module named like one of those (`random.py`, `math.py`, `bpy`,
`mathutils`, `bmesh`, an add-on such as `rigify`) or like the studio's `mv` package loses to it, so give yours names of
their own. The studio's tools are not on the path: a `work/kit/models.py` or `eyes.py` is the scene's own.
```python
from city import build_city          # work/kit/city/__init__.py
import look                          # work/scenes/lib/look.py

def build(mv):
    build_city(mv, palette=look.PALETTE)
```
The cache follows them: the frames of a render are kept while the scene's own folder (only the file itself when it lies
loose in `work/scenes/`, `work/` or the project), the `lib/` next to it, `work/scenes/lib/` and `work/kit/` are what they were and the
settings are the same; any change there renders it again (`mv.common.scene_inputs`; `studio.py plan` uses the same
rule to tell a Blender segment's render is stale).

Determinism: no `time.time()`, seed every random source (`mv.random(f, key)`), keep `frame()` a pure function of f.
Example (mechanics, not a look): `examples/kick_rings.py`.
