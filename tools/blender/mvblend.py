"""Runs INSIDE Blender (started by tools/blender/run.py): builds the scene from a director's script and renders frames.

A scene script defines:
    def build(mv):          # once: make objects, materials, lights, camera; keyframe from the song data if you like
        ...
    def frame(mv, f):       # optional: called before every frame f is rendered (song frame number; time = f / mv.fps)
        ...                 # must depend only on f (frames render in any order, in several processes)

`mv` gives: mv.song (tools/mv/song.py Song: t(bar, beat), pos(t), at(t), cue(t), hits(kind), pulse(kind, t), ...),
mv.fps, mv.width, mv.height, mv.f0, mv.f1 (the frames being rendered), mv.seed, mv.preview,
mv.curve(name) (one value per frame), mv.value(name, f), mv.t(f) (song time of frame f: the end of its slot), mv.frame(t),
mv.random(f, key) (stable per frame), mv.text(...) (declare on-screen text for the checks), mv.scene (bpy scene).
Blender frame numbers ARE song frames (frame 0 = song time 0).
"""
import importlib.util, json, os, random, sys, time

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
A = json.loads(argv[0])
sys.path.insert(0, A["tools"])
from mv.song import Song  # noqa: E402


class MV:
    def __init__(self):
        self.song = Song(A["project"])
        self.fps, self.width, self.height = A["fps"], A["w"], A["h"]
        self.f0, self.f1, self.seed, self.preview = A["f0"], A["f1"], A["seed"], A["preview"]
        self.scene = bpy.context.scene
        self.texts = []
        self.project = A["project"]

    def t(self, f):
        """Song time of frame f: the end of its slot, so whatever happens in the frame's slot (a downbeat, a kick)
        shows in it (the studio's rule: an event at t is in frame round(t * fps))."""
        return (f + 0.5) / self.fps - 1e-6

    def frame(self, t):
        return int(round(t * self.fps))

    def curve(self, name):
        return self.song.curve(name)

    def value(self, name, f):
        return self.song.value(name, f)

    def random(self, f=0, key=""):
        return random.Random(f"{self.seed}:{key}:{f}").random()

    def text(self, text, t0, t1, box, role="text", color=None, size=None):
        """Declare text this pass shows (box = [x, y, w, h] in output pixels) so the text checks can see it."""
        self.texts.append({"text": text, "role": role, "t0": t0, "t1": t1, "box": box, "color": color, "size": size})


def engine_ids():
    try:
        return {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    except Exception:
        return set()


def setup_render(scene, mv):
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = A["w"], A["h"], 100
    r.fps, r.fps_base = A["fps"], 1.0
    scene.frame_start, scene.frame_end = A["f0"], A["f1"] - 1
    r.film_transparent = bool(A["transparent"])
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGBA" if A["transparent"] else "RGB"
    r.image_settings.color_depth = "8"
    r.use_overwrite = False                   # resume: frames already on disk are skipped
    r.use_placeholder = True                  # several processes can share one frame range
    r.filepath = os.path.join(A["out_dir"], "frame_######")
    want = A["engine"]
    ids = engine_ids()
    eevee = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids else "BLENDER_EEVEE"
    used = None
    if want == "eevee":
        r.engine = eevee
        used = "eevee"
        for attr in ("taa_render_samples",):
            if hasattr(scene.eevee, attr):
                setattr(scene.eevee, attr, A["samples"] or (16 if mv.preview else 64))
    elif want == "workbench":
        r.engine = "BLENDER_WORKBENCH"
        used = "workbench"
        try:
            scene.display.render_aa = "8" if not mv.preview else "FXAA"
        except Exception:
            pass
    else:
        r.engine = "CYCLES"
        used = "cycles"
        c = scene.cycles
        c.samples = A["samples"] or (16 if mv.preview else 64)
        try:
            import _cycles
            oidn = bool(getattr(_cycles, "with_openimagedenoise", True))
        except Exception:
            oidn = True
        c.use_denoising = bool(oidn)              # some Linux distro builds ship without OpenImageDenoise
        if oidn:
            try:
                c.denoiser = "OPENIMAGEDENOISE"
            except Exception:
                pass
        c.device = "CPU"
        if A.get("cycles_gpu", True):
            try:
                prefs = bpy.context.preferences.addons["cycles"].preferences
                for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
                    try:
                        prefs.compute_device_type = kind
                    except Exception:
                        continue
                    prefs.get_devices()
                    devs = [d for d in prefs.devices if d.type == kind]
                    if devs:
                        for d in prefs.devices:
                            d.use = d.type == kind
                        c.device = "GPU"
                        used = f"cycles ({kind})"
                        break
            except Exception:
                pass
    return used


def main():
    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    mv = MV()
    mv.scene = scene
    random.seed(A["seed"])
    spec = importlib.util.spec_from_file_location("mv_scene", A["scene"])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mv_scene"] = mod
    spec.loader.exec_module(mod)
    if hasattr(mod, "build"):
        mod.build(mv)
    used = setup_render(scene, mv)
    if hasattr(mod, "frame"):
        def handler(sc, *_):
            mod.frame(mv, sc.frame_current)
        bpy.app.handlers.frame_change_pre.append(handler)
    if scene.camera is None:
        cams = [o for o in scene.objects if o.type == "CAMERA"]
        if cams:
            scene.camera = cams[0]
        else:
            raise SystemExit("the scene has no camera: add one in build(mv)")
    print(f"MVBLEND engine={used} build={time.time() - t0:.2f}s", flush=True)
    if A.get("stills"):
        for f in A["stills"]:
            scene.frame_set(f)
            scene.render.filepath = os.path.join(A["out_dir"], f"still_{f:06d}.png")
            bpy.ops.render.render(write_still=True)
    else:
        bpy.ops.render.render(animation=True)
    meta = {"engine": used, "texts": mv.texts, "blender": bpy.app.version_string, "seconds": round(time.time() - t0, 2)}
    with open(os.path.join(A["out_dir"], f"_meta_{A['f0']}_{A['f1']}.json"), "w") as fh:
        json.dump(meta, fh)
    print(f"MVBLEND done in {time.time() - t0:.1f}s", flush=True)


try:
    main()
except SystemExit:
    raise
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
