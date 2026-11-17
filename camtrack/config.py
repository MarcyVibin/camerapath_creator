"""Settings and external tool discovery. Override any tool path with an env var."""
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _find(env: str, exe: str, candidates: list[str]) -> str:
    """Return a tool path from $env, PATH, or known install locations (first hit wins)."""
    if os.environ.get(env):
        return os.environ[env]
    for c in [shutil.which(exe), *candidates]:
        if c and Path(c).exists():
            return str(c)
    return exe  # let the subprocess call fail with a clear "not found"


def _blender_candidates() -> list[str]:
    root = Path(r"C:\Program Files\Blender Foundation")
    return [str(p / "blender.exe") for p in sorted(root.glob("Blender*"), reverse=True)] if root.exists() else []


@dataclass
class Settings:
    workspace: Path | None = None        # default: <video dir>/<stem>_track
    output: Path | None = None           # default: <video dir>/<stem>_camera.blend
    focal_mm: float | None = None        # lens prior (36 mm sensor equiv.); None = let COLMAP solve it
    max_features: int = 8192
    overlap: int = 20                    # sequential matcher neighbours per frame
    refine_passes: tuple[float, ...] = (2.0, 1.0)   # max reprojection error (px) per polish pass
    min_track_len: int = 3
    force: bool = False                  # wipe workspace and redo every step
    ba_backend: str = "CASPAR"           # GPU bundle adjustment in polish; falls back to CERES if unsupported
    # own build with CUDA + CASPAR first (see USAGE.md), then the prebuilt release
    colmap: str = field(default_factory=lambda: _find("CAMTRACK_COLMAP", "colmap", [
        r"D:\colmap-caspar\bin\colmap.exe", str(Path.home() / "Tools/COLMAP/bin/colmap.exe")]))
    blender: str = field(default_factory=lambda: _find("CAMTRACK_BLENDER", "blender", _blender_candidates()))
    ffmpeg: str = field(default_factory=lambda: _find("CAMTRACK_FFMPEG", "ffmpeg", []))
    ffprobe: str = field(default_factory=lambda: _find("CAMTRACK_FFPROBE", "ffprobe", []))
