#!/usr/bin/env python
"""Double-click: small UI that turns an existing COLMAP solve + video into a .blend (no solving)."""
import ctypes
import sys
import traceback

from camtrack.tui import main_blender_import

if sys.platform == "win32":
    ctypes.windll.kernel32.SetConsoleTitleW("camtrack: Blender import")
try:
    main_blender_import()
except Exception:
    traceback.print_exc()
    input("\nPress Enter to close...")
    sys.exit(1)
