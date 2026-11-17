"""Ground plane: fit it, build the COLMAP_Ground shadow catcher, and level COLMAP_Space so the ground
is the world XY plane (Z = 0, cameras above, first frame looking along +Y). Runs inside Blender.

Manual fix (this file is stored in every .blend as the text "camtrack_ground.py"):
  select COLMAP_Points > Tab (Edit Mode) > select only ground points (e.g. box-select the lawn
  through the camera) > Scripting tab > camtrack_ground.py > Run Script.
"""
import numpy as np
from mathutils import Matrix, Vector


def fit_plane(xyz, cams, tol=None, iters=1000, seed=0):
    """RANSAC plane with the most points, refit by least squares; normal points to the cameras.
    shortcut: picks the dominant plane, so a big wall can win over a small floor; then refit by hand."""
    rng = np.random.default_rng(seed)
    if tol is None:  # 1% of scene size
        tol = 0.01 * np.percentile(np.linalg.norm(xyz - np.median(xyz, 0), axis=1), 90)
    best = np.zeros(len(xyz), bool)
    for _ in range(iters):
        a, b, c = xyz[rng.choice(len(xyz), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-12:
            continue
        inl = np.abs((xyz - a) @ (n / np.linalg.norm(n))) < tol
        if inl.sum() > best.sum():
            best = inl
    pts = xyz[best]
    center = pts.mean(0)
    normal = np.linalg.svd(pts - center)[2][2]
    if (cams.mean(0) - center) @ normal < 0:
        normal = -normal
    return center, normal, pts


def level_scene(space, ground, xyz, cams, forward):
    """Fit the ground to xyz (COLMAP coords), rebuild the `ground` quad and set `space` so the plane is world XY."""
    center, n, pts = fit_plane(np.asarray(xyz, float), np.asarray(cams, float))
    fwd = np.asarray(forward, float)
    v = fwd - (fwd @ n) * n                                   # first view direction, flattened onto the ground
    if np.linalg.norm(v) < 1e-6:                              # camera looking straight down
        v = np.cross(n, [1, 0, 0] if abs(n[0]) < 0.9 else [0, 1, 0])
    v /= np.linalg.norm(v)
    u = np.cross(v, n)                                        # right-handed: u = X, v = Y, n = Z
    R = np.stack([u, v, n])                                   # COLMAP -> world rotation (rows)
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R, -R @ center
    space.matrix_world = Matrix(M.tolist())

    uv = (pts - center) @ np.stack([u, v], 1)
    lo, hi = np.percentile(uv, 2, 0), np.percentile(uv, 98, 0)   # ignore stray inliers
    corners = [tuple(center + a * u + b * v) for a, b in ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]
    ground.data.clear_geometry()
    ground.data.from_pydata(corners, [], [(0, 1, 2, 3)])
    ground.data.update()
    return len(pts)


if __name__ == "__main__":  # run from Blender's text editor: refit from the selected points
    import bmesh
    import bpy

    points = bpy.data.objects["COLMAP_Points"]
    if points.mode == "EDIT":
        sel = [v.co[:] for v in bmesh.from_edit_mesh(points.data).verts if v.select]
    else:
        sel = [v.co[:] for v in points.data.vertices if v.select]
    if len(sel) < 3:
        raise RuntimeError("Select at least 3 ground points of COLMAP_Points in Edit Mode first")
    scene, cam = bpy.context.scene, bpy.data.objects["COLMAP_Camera"]
    keep = scene.frame_current
    cams = []
    for f in range(scene.frame_start, scene.frame_end + 1, 5):
        scene.frame_set(f)
        cams.append(cam.matrix_local.translation[:])
    scene.frame_set(scene.frame_start)
    forward = list(cam.matrix_local.to_3x3() @ Vector((0, 0, -1)))
    scene.frame_set(keep)
    n = level_scene(bpy.data.objects["COLMAP_Space"], bpy.data.objects["COLMAP_Ground"], sel, cams, forward)
    print(f"camtrack: ground refit from {len(sel)} selected points ({n} on the plane)")
