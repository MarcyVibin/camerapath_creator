"""Video -> COLMAP solve -> polished model -> .blend.

Extend by adding a `def step(job)` and inserting it into STEPS. Each finished step leaves a
`.done_<name>` marker in the workspace, so a re-run resumes after the last finished step.
"""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .config import Settings

Log = Callable[[str], None]


@dataclass
class Job:
    video: Path
    settings: Settings
    log: Log
    ws: Path
    info: dict = field(default_factory=dict)      # width, height, fps, frames (from ffprobe)
    stats: dict = field(default_factory=dict)

    @property
    def db(self) -> Path: return self.ws / "database.db"
    @property
    def images(self) -> Path: return self.ws / "images"
    @property
    def model(self) -> Path: return self.ws / "model"          # best binary model, polished in place
    @property
    def model_txt(self) -> Path: return self.ws / "model_txt"
    @property
    def blend(self) -> Path:
        s = self.settings
        return s.output or self.video.with_name(self.video.stem + "_camera.blend")

    def sh(self, *args) -> str:
        """Run a command, stream its output to the log, raise on failure; returns the output."""
        cmd = [str(a) for a in args]
        self.log("$ " + " ".join(cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
        out = []
        for line in p.stdout:
            out.append(line)
            self.log(line.rstrip())
        if p.wait():
            raise RuntimeError(f"{Path(cmd[0]).name} failed (exit {p.returncode}); see log above")
        return "".join(out)

    def colmap(self, command: str, **opts) -> str:
        args = [self.settings.colmap, command]
        for k, v in opts.items():
            args += ["--" + k.replace("__", "."), int(v) if isinstance(v, bool) else v]
        return self.sh(*args)

    def analyze(self, label: str) -> dict:
        """model_analyzer stats as {snake_case_key: number}; also logged."""
        out = self.colmap("model_analyzer", path=self.model)
        stats = {}
        for k, v in re.findall(r"\]\s*([A-Za-z][\w ]+?):\s*([-\d.]+)", out):
            stats[k.strip().lower().replace(" ", "_")] = float(v)
        self.stats[label] = stats
        return stats


# ------------------------------------------------------------------------------------ steps
def probe(job: Job):
    out = subprocess.run([job.settings.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets",
                          "-of", "json", str(job.video)], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    fps = Fraction(s["r_frame_rate"])
    job.info = dict(width=s["width"], height=s["height"], fps_num=fps.numerator, fps_den=fps.denominator,
                    frames=int(s["nb_read_packets"]))
    job.log(f"video: {job.info}")


def frames(job: Job):
    # every frame, named by index: 00000.jpg = first frame = Blender frame 1 (no gaps -> no wobble)
    if job.images.exists():
        shutil.rmtree(job.images)
    job.images.mkdir(parents=True)
    job.sh(job.settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", job.video, "-fps_mode", "passthrough",
           "-q:v", "2", "-start_number", "0", job.images / "%05d.jpg")


def features(job: Job):
    s = job.settings
    job.db.unlink(missing_ok=True)
    opts = dict(database_path=job.db, image_path=job.images, ImageReader__single_camera=True,
                ImageReader__camera_model="SIMPLE_RADIAL", SiftExtraction__max_num_features=s.max_features)
    if s.focal_mm:  # lens prior: f_px = mm / 36 * width
        w, h = job.info["width"], job.info["height"]
        opts["ImageReader__camera_params"] = f"{s.focal_mm / 36 * w},{w / 2},{h / 2},0"
    job.colmap("feature_extractor", **opts)


def match(job: Job):
    job.colmap("sequential_matcher", database_path=job.db, SequentialMatching__overlap=job.settings.overlap,
               FeatureMatching__guided_matching=True)


def solve(job: Job):
    out = job.ws / "sparse"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    # default caps tracks at 100 views, which chops long video tracks into pieces
    job.colmap("global_mapper", database_path=job.db, image_path=job.images, output_path=out,
               GlobalMapper__track_max_num_views_per_track=1_000_000)
    models = [d for d in out.iterdir() if (d / "images.bin").exists()]
    if not models:
        raise RuntimeError("COLMAP could not reconstruct the clip (too little parallax/texture?)")
    best = max(models, key=lambda d: (d / "images.bin").stat().st_size)  # most registered frames
    shutil.rmtree(job.model, ignore_errors=True)
    shutil.copytree(best, job.model)
    job.log(f"solve stats: {job.analyze('solve')}")


def polish(job: Job):
    """Merge/complete tracks (longer tracks), drop bad/short ones, re-bundle-adjust; tighter each pass."""
    s, tmp = job.settings, job.ws / "model_tmp"
    for i, max_err in enumerate(s.refine_passes, 1):
        job.log(f"-- polish pass {i}: max reprojection error {max_err}px")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        job.colmap("point_triangulator", database_path=job.db, image_path=job.images, input_path=job.model,
                   output_path=tmp, clear_points=False, refine_intrinsics=True,
                   Mapper__filter_max_reproj_error=max_err, Mapper__tri_complete_max_reproj_error=max_err,
                   Mapper__tri_merge_max_reproj_error=max_err)
        job.colmap("point_filtering", input_path=tmp, output_path=job.model,
                   max_reproj_error=max_err, min_track_len=s.min_track_len)
        bundle_adjust(job)
        job.log(f"pass {i} stats: {job.analyze(f'polish_{i}')}")
    shutil.rmtree(tmp, ignore_errors=True)


def bundle_adjust(job: Job):
    """Bundle-adjust job.model in place: CASPAR on the GPU (~11x faster than Ceres on the CPU, same
    result to 1e-6 px), or Ceres when this COLMAP build has no CASPAR (it then aborts before writing)."""
    s = job.settings
    if s.ba_backend == "CASPAR":
        try:
            # CASPAR can't refine sensor_from_rig; a single camera has none to refine
            return job.colmap("bundle_adjuster", input_path=job.model, output_path=job.model,
                              BundleAdjustment__backend="CASPAR", BundleAdjustment__refine_sensor_from_rig=False)
        except RuntimeError:
            job.log("CASPAR bundle adjustment not available in this COLMAP build -> using Ceres (CPU)")
            s.ba_backend = "CERES"
    job.colmap("bundle_adjuster", input_path=job.model, output_path=job.model, BundleAdjustmentCeres__use_gpu=True)


def export(job: Job):
    shutil.rmtree(job.model_txt, ignore_errors=True)
    job.model_txt.mkdir()
    job.colmap("model_converter", input_path=job.model, output_path=job.model_txt, output_type="TXT")


def blender(job: Job, model_txt: Path | None = None):
    script = Path(__file__).with_name("blender_import.py")
    i = job.info
    job.sh(job.settings.blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", script, "--",
           model_txt or job.model_txt, job.video, job.blend, i["fps_num"], i["fps_den"], i["frames"])


STEPS: list[tuple[str, Callable[[Job], None]]] = [
    ("frames", frames), ("features", features), ("match", match), ("solve", solve),
    ("polish", polish), ("export", export), ("blender", blender),
]


def blender_only(model: Path, video: Path, settings: Settings | None = None, log: Log = print) -> str:
    """Build the .blend from an existing solve, skipping COLMAP. `model` may be a camtrack workspace,
    a COLMAP TXT model folder, or a binary model folder (converted to TXT next to it). Returns the .blend path."""
    model, video = Path(model).resolve(), Path(video).resolve()
    if (model / "model_txt").is_dir():
        model = model / "model_txt"
    job = Job(video, settings or Settings(), log, ws=model.parent)
    if not video.is_file():
        raise FileNotFoundError(video)
    if not (model / "cameras.txt").exists():
        if not (model / "cameras.bin").exists():
            raise FileNotFoundError(f"No COLMAP model (cameras.txt/.bin) in {model}")
        txt = model.with_name(model.name + "_txt")
        txt.mkdir(exist_ok=True)
        job.colmap("model_converter", input_path=model, output_path=txt, output_type="TXT")
        model = txt
    probe(job)
    blender(job, model)
    return str(job.blend)


def run(video: Path, settings: Settings | None = None, log: Log = print) -> dict:
    s = settings or Settings()
    video = Path(video).resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    ws = Path(s.workspace or video.with_name(video.stem + "_track")).resolve()
    if s.force:
        shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    job = Job(video, s, log, ws)
    stats_file = ws / "stats.json"
    if stats_file.exists():
        job.stats = json.loads(stats_file.read_text())
    probe(job)
    for name, fn in STEPS:
        marker = ws / f".done_{name}"
        if marker.exists():
            log(f"== {name} (done, skipping)")
            continue
        log(f"== {name}")
        fn(job)
        stats_file.write_text(json.dumps(job.stats, indent=2))
        marker.touch()
    final = job.stats.get(f"polish_{len(s.refine_passes)}") or job.stats.get("solve", {})
    result = dict(blend=str(job.blend), workspace=str(ws), video=str(video), frames=job.info["frames"],
                  stats=final, history=job.stats)
    (ws / "result.json").write_text(json.dumps(result, indent=2))
    return result
