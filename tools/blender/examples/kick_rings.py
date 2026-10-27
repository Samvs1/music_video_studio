"""Example Blender scene (a template, not a look): rings that breathe with the kick, a camera that drifts by the bar.

  python tools/studio.py run <project> blender/run.py ../../tools/blender/examples/kick_rings.py --out work/renders/rings --from 10 --to 14

build(mv) makes everything once; frame(mv, f) moves it for song frame f (pure function of f: frames render in any
order). Emission keeps it readable in Workbench, Eevee and Cycles alike.
"""
import math

import bpy


def emissive(name, rgb, strength):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*rgb, 1.0)
    em.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    m.diffuse_color = (*rgb, 1.0)                   # what Workbench shows
    return m


def build(mv):
    sc = mv.scene
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.01, 0.01, 0.015, 1)
    sc.world = world
    sc.display.shading.light = "FLAT"
    sc.display.shading.color_type = "MATERIAL"
    mv.rings = []
    for i in range(6):
        bpy.ops.mesh.primitive_torus_add(major_radius=1.0 + 0.55 * i, minor_radius=0.04 + 0.01 * i, location=(0, 0, 0))
        ob = bpy.context.active_object
        ob.data.materials.append(emissive(f"ring{i}", (0.95, 0.35 + 0.1 * i, 0.2 + 0.12 * i), 4.0))
        mv.rings.append(ob)
    bpy.ops.object.camera_add(location=(0, -9, 3.2))
    cam = bpy.context.active_object
    cam.rotation_euler = (math.radians(72), 0, 0)
    sc.camera = cam
    mv.cam = cam


def frame(mv, f):
    t = mv.t(f)
    kick = mv.song.pulse("kick", t, decay=0.18)
    p = mv.song.pos(t)
    bar_phase = (p[1] / 4.0) if p else 0.0
    for i, ob in enumerate(mv.rings):
        s = 1.0 + 0.18 * kick * (1 - i / 8)
        ob.scale = (s, s, s)
        ob.rotation_euler = (math.radians(90 * bar_phase + 8 * i), math.radians(15 * i), 0)
    mv.cam.location.x = 1.5 * math.sin(t * 0.25)
