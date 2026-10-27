"""Smoke-test Blender scene: a slab that turns on every beat, for the bars around the stop and the tempo change."""
import math
import bpy


def build(mv):
    sc = mv.scene
    w = bpy.data.worlds.new("w")
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.02, 0.025, 1)
    sc.world = w
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "OBJECT"
    sc.display.shading.background_type = "WORLD"
    mv.objs = []
    for i in range(5):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-4 + 2 * i, 0, 0))
        ob = bpy.context.active_object
        ob.scale = (0.7, 0.7, 2.4)
        ob.color = (0.9, 0.55 + 0.08 * i, 0.35, 1)
        m = bpy.data.materials.new(f"m{i}")
        m.diffuse_color = ob.color
        ob.data.materials.append(m)
        mv.objs.append(ob)
    bpy.ops.object.light_add(type="SUN", location=(4, -6, 8))
    bpy.ops.object.camera_add(location=(0, -12, 2.5))
    cam = bpy.context.active_object
    cam.rotation_euler = (math.radians(84), 0, 0)
    sc.camera = cam


def frame(mv, f):
    t = mv.t(f)
    p = mv.song.pos(t)
    beat = math.floor(p[1]) + 4 * p[0] if p else 0
    for i, ob in enumerate(mv.objs):
        ob.rotation_euler = (0, 0, math.radians(37 * ((beat + i) % 5)))    # snaps to a new angle on every beat
