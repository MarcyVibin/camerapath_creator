"""Runs INSIDE Blender: builds a scene from a COLMAP TXT model and saves it.

blender -b --factory-startup --python blender_import.py -- <model_txt> <video> <out.blend> <fps_num> <fps_den> <frames>

Result:
  COLMAP_Space (empty)   levelled so the fitted ground is world XY (Z = 0); scale THIS to real size
   |- COLMAP_Camera      animated, lens + k1 from the solve, video as background
   |- COLMAP_Points      sparse 3D points
   |- COLMAP_Ground      shadow catcher on the fitted ground (Cycles)
Text "camtrack_ground.py" in the .blend refits the ground from selected points (see blender_ground.py).
"""
import os
import re
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_ground import level_scene  # noqa: E402

MODEL, VIDEO, OUT, FPS_NUM, FPS_DEN, N_FRAMES = sys.argv[sys.argv.index("--") + 1:]
FPS_NUM, FPS_DEN, N_FRAMES = int(FPS_NUM), int(FPS_DEN), int(N_FRAMES)
SENSOR_MM = 36.0
MAX_POINTS = 100_000


def rows(name):
    with open(os.path.join(MODEL, name), encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                yield line.split()


# ---------------------------------------------------------------- camera intrinsics (single camera)
p = next(rows("cameras.txt"))
model, w, h, prm = p[1], int(p[2]), int(p[3]), [float(v) for v in p[4:]]
if model not in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
    raise ValueError(f"Unsupported camera model {model}")
f, cx, cy = prm[:3]
k1 = prm[3] if len(prm) > 3 else 0.0
k2 = prm[4] if len(prm) > 4 else 0.0

# ---------------------------------------------------------------- poses: image 00000.jpg -> frame 1
FLIP = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, -1)))     # OpenCV camera axes -> Blender camera axes
poses = {}
it = rows("images.txt")
for p in it:
    next(it, None)                                       # skip the 2D-points line
    num = re.findall(r"\d+", os.path.basename(p[9]))
    if not num:
        continue
    R = Quaternion(map(float, p[1:5])).to_matrix()       # world -> camera
    t = Vector(map(float, p[5:8]))
    poses[int(num[-1]) + 1] = (-(R.transposed() @ t), (R.transposed() @ FLIP).to_quaternion())
frames = sorted(poses)
missing = N_FRAMES - len(frames)
print(f"COLMAP: {len(frames)}/{N_FRAMES} frames registered" + (f", {missing} interpolated" if missing else ""))

# ---------------------------------------------------------------- scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = w, h
scene.render.fps = round(FPS_NUM / FPS_DEN)
scene.render.fps_base = scene.render.fps * FPS_DEN / FPS_NUM   # 29.97 -> fps 30, base 1.001
scene.frame_start, scene.frame_end = 1, N_FRAMES

space = bpy.data.objects.new("COLMAP_Space", None)
space.empty_display_type = "ARROWS"
space.rotation_euler = (-1.5707963, 0, 0)               # fallback if no ground fit: COLMAP "up" is usually -Y
scene.collection.objects.link(space)

cd = bpy.data.cameras.new("COLMAP_Camera")
cd.sensor_fit, cd.sensor_width = "HORIZONTAL", SENSOR_MM
cd.lens = f / w * SENSOR_MM
cd.shift_x = -(cx - w / 2) / w
cd.shift_y = (cy - h / 2) / w
cd.clip_start, cd.clip_end = 0.01, 10000
cam = bpy.data.objects.new("COLMAP_Camera", cd)
cam.parent, cam.rotation_mode = space, "QUATERNION"
scene.collection.objects.link(cam)
scene.camera = cam

prev = None
for fr in frames:
    loc, q = poses[fr]
    if prev is not None and prev.dot(q) < 0:
        q.negate()                                       # keep quaternions continuous
    prev = q
    cam.location, cam.rotation_quaternion = loc, q
    cam.keyframe_insert("location", frame=fr)
    cam.keyframe_insert("rotation_quaternion", frame=fr)
act = cam.animation_data.action
try:
    curves = act.fcurves
except AttributeError:                                   # Blender 4.4+/5.x layered actions
    curves = [fc for l in act.layers for s in l.strips for b in s.channelbags for fc in b.fcurves]
for fc in curves:
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"

# ---------------------------------------------------------------- point cloud (longest tracks first)
pts = sorted(((len(p) - 8, (float(p[1]), float(p[2]), float(p[3]))) for p in rows("points3D.txt")), reverse=True)
me = bpy.data.meshes.new("COLMAP_Points")
me.from_pydata([xyz for _, xyz in pts[:MAX_POINTS]], [], [])
po = bpy.data.objects.new("COLMAP_Points", me)
po.parent = space
scene.collection.objects.link(po)

# ---------------------------------------------------------------- shadow catcher on the ground plane
scene.render.engine = "CYCLES"                          # shadow catchers are Cycles-only
scene.render.film_transparent = True                    # needed to composite the catcher over the plate
xyz = np.array([v for _, v in pts[:20_000]])            # longest tracks = most reliable points
if len(xyz) >= 50:
    ground = bpy.data.objects.new("COLMAP_Ground", bpy.data.meshes.new("COLMAP_Ground"))
    ground.parent = space
    ground.is_shadow_catcher = True
    scene.collection.objects.link(ground)
    first_view = poses[frames[0]][1] @ Vector((0, 0, -1))   # camera looks down its -Z
    n = level_scene(space, ground, xyz, [list(loc) for loc, _ in poses.values()], list(first_view))
    print(f"Ground: {n}/{len(xyz)} points on plane; scene levelled (ground = world XY)")
else:
    print("Ground: too few points for a plane fit, no shadow catcher created")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_ground.py"), encoding="utf-8") as fh:
    bpy.data.texts.new("camtrack_ground.py").from_string(fh.read())   # manual refit tool, see its docstring

# ---------------------------------------------------------------- plate + distortion for the compositor
clip = bpy.data.movieclips.load(os.path.abspath(VIDEO))
scene.active_clip = clip
cd.show_background_images = True
bg = cd.background_images.new()
bg.source, bg.clip = "MOVIE_CLIP", clip
tc = clip.tracking.camera
tc.sensor_width, tc.units, tc.focal_length = SENSOR_MM, "MILLIMETERS", cd.lens
tc.distortion_model, tc.k1, tc.k2 = "POLYNOMIAL", k1, k2

os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(OUT))
print(f"Saved {OUT}: lens {cd.lens:.2f} mm, k1 {k1:.4f}, {len(me.vertices)} points")
