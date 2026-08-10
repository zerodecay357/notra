"""Locate external executables — bundled copies first, then the system PATH.

When Notra ships as a packaged app, tectonic and ffmpeg live in a bin/
directory next to the application so students install nothing. During
development they usually come from the system instead. Resolution order:

1. <project>/bin/<name>       (the bundled copy an installer provides)
2. whatever `which <name>` finds on PATH
"""

from __future__ import annotations

import os
import shutil
import sys

from . import config

# BASE_DIR already accounts for PyInstaller's frozen layout (sys._MEIPASS),
# so a packaged build finds its bundled bin/ next to the rest of the app.
BIN_DIR = config.BASE_DIR / "bin"


def find(name: str) -> str | None:
    """Absolute path to the executable, or None if unavailable."""
    exe = f"{name}.exe" if sys.platform == "win32" else name
    bundled = BIN_DIR / exe
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which(name)
