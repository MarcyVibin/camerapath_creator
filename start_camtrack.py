#!/usr/bin/env python
"""Double-click: interactive TUI.  Drag a video onto this file: run it directly."""
import ctypes
import sys
import traceback

from camtrack.__main__ import main

if sys.platform == "win32":
    ctypes.windll.kernel32.SetConsoleTitleW("camtrack")
try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1
if len(sys.argv) > 1 or code:  # keep the window open to read the result / error
    input("\nPress Enter to close...")
sys.exit(code)
