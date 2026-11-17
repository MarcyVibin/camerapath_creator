"""python -m camtrack [video] [options]   -- no arguments opens the interactive TUI."""
import argparse
import json
import sys
from pathlib import Path

from .config import Settings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from .tui import main as tui
        return tui()
    p = argparse.ArgumentParser(prog="camtrack", description="Video clip -> COLMAP camera solve -> .blend")
    p.add_argument("video", type=Path)
    p.add_argument("-o", "--output", type=Path, help="output .blend (default: <video>_camera.blend)")
    p.add_argument("-w", "--workspace", type=Path, help="work folder (default: <video>_track/)")
    p.add_argument("--focal-mm", type=float, help="lens prior, 36 mm-equivalent (default: solved)")
    p.add_argument("--force", action="store_true", help="discard workspace and redo every step")
    p.add_argument("--json", action="store_true", help="quiet; print only the result JSON (for agents)")
    a = p.parse_args(argv)

    from .pipeline import run
    s = Settings(workspace=a.workspace, output=a.output, focal_mm=a.focal_mm, force=a.force)
    log = (lambda line: print(line, file=sys.stderr, flush=True) if line.startswith(("==", "--", "pass", "solve")) else None) \
        if a.json else (lambda line: print(line, flush=True))
    try:
        result = run(a.video, s, log)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}) if a.json else f"ERROR: {e}")
        return 1
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
