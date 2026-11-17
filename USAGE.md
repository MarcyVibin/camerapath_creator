# camtrack — video clip → camera-tracked .blend

Takes one video clip and runs COLMAP on it to solve the camera. It then polishes the solve and
writes a Blender file with the animated camera, the point cloud and the clip as the background plate.

## Humans

- **Double-click `start_camtrack.py`**: a terminal UI opens. **Browse…** picks a video, then
  press **Run**. The bar shows progress per step; the solve step has no sub-progress, so watch the log.
- **Drag a video onto `start_camtrack.py`** to run it straight away (the window stays open at the end).
- Leave *Focal length* blank unless the solve comes out wrong (see below).
- **Already have a solve?** Double-click `start_blender_import.py`. Pick the model folder (a camtrack
  `<video>_track` folder, or any COLMAP TXT/binary model) and the video, then press **Run**.
  This only rebuilds the .blend and skips COLMAP.
- Needs Python 3.11+ with `textual` (`pip install textual`). If double-clicking opens an editor
  instead, right-click → Open with → Python.

## AI agents / scripts (headless)

```
python -m camtrack <video> [-o out.blend] [-w workdir] [--focal-mm 24] [--force] [--json]
```

- Run it from this folder, or put this folder on `PYTHONPATH`.
- `--json` prints one JSON object on stdout and step names on stderr. Exit code 0 means success.
  ```json
  {"ok": true, "blend": "...\\clip_camera.blend", "workspace": "...\\clip_track", "frames": 466,
   "stats": {"registered_images": 466, "mean_reprojection_error": 0.4, "mean_track_length": 9.1, ...},
   "history": {"solve": {...}, "polish_1": {...}, "polish_2": {...}}}
  ```

  On failure you get `{"ok": false, "error": "..."}` and exit code 1.
- Rebuild only the .blend from an existing solve (any COLMAP model folder):
  `python -c "from camtrack.pipeline import blender_only; print(blender_only(r'<model>', r'<video>'))"`
- Runs **resume**: each finished step leaves `<workspace>/.done_<step>`. Delete a marker to redo
  that step, or pass `--force` to redo everything.
- Expect about 20 min for a 15 s 1080p clip on an RTX 3080. The solve is the slow part.

## Defaults

| What        | Default                                                                                                                 |
| ----------- | ----------------------------------------------------------------------------------------------------------------------- |
| Output      | `<video>_camera.blend` next to the video                                                                              |
| Work folder | `<video>_track/` (frames, database, `model/`, `model_txt/`, `stats.json`, `result.json`)                      |
| Tools       | found automatically. Override with `CAMTRACK_COLMAP`, `CAMTRACK_BLENDER`, `CAMTRACK_FFMPEG`, `CAMTRACK_FFPROBE` |

**GPU bundle adjustment (CASPAR).** The polish step's bundle adjustment runs on the GPU when
COLMAP is built with CASPAR. On the sample clip that's ~11× faster than Ceres on the CPU, with the
same result. The prebuilt COLMAP release doesn't include it, so camtrack falls back to Ceres
automatically. To get it, build COLMAP with `tools/build_colmap_caspar.bat`. The prerequisites and
one source fix are listed at the top of the script. camtrack uses `D:\colmap-caspar` when it exists.

## Is the solve good?

- `registered_images` should equal `frames`. Missing frames get interpolated and can wobble.
- `mean_reprojection_error`: under 0.5 px is great, under 1 px is fine.
- Lens = the `COLMAP_Camera` focal length in Blender (36 mm sensor). Drones are usually about 24 mm.
  If it is implausible (12 mm, 80 mm), re-run with `--focal-mm <guess> --force`.

## In Blender

Everything sits under the empty `COLMAP_Space`, which is **levelled automatically**: the import fits
a plane to the points, makes it the world floor (Z = 0, camera above, first frame looking along +Y)
and puts the **shadow catcher** `COLMAP_Ground` on it. The file is set to Cycles with a
transparent film for it. Only the size is unknown: scale `COLMAP_Space` (around the world origin)
until a known distance is right. Then add a cube on the ground and scrub: it should stick.

**Ground wrong** (for example a big roof or wall won the fit)? Refit it from your own selection:
1. Select `COLMAP_Points`, press Tab, and select only ground points. Box-select the ground in camera
   view (Numpad 0), or use a side view.
2. Go to the Scripting tab, open the text `camtrack_ground.py`, and press Run Script.
   The ground and the levelling are redone from just those points.

The clip's lens distortion (k1) is set on the movie clip, ready for the compositor's Movie Distortion node.

## Pipeline (`camtrack/pipeline.py`, extend by adding to `STEPS`)

`frames` (ffmpeg, every frame) → `features` (SIFT) → `match` (sequential, guided) →
`solve` (global_mapper, long tracks not capped) → `polish` (retriangulate/merge tracks → drop
high-error & short tracks → bundle adjust; 2 px then 1 px) → `export` (TXT) → `blender`.
