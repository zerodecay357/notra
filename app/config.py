"""Paths and persisted settings for Notra.

DATA_DIR lives outside the install tree, in the OS's per-user data
location. This matters once Notra ships as a packaged app: a PyInstaller
bundle is read-only and can live anywhere (Program Files, /Applications,
a mounted image), so it can't also hold the user's recordings/database —
and on every platform, writing into the install directory is either
disallowed outright or bad practice anyway.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

# BASE_DIR is the app's own install location — for finding bundled assets
# (static/, bin/), never for storing user data. Frozen (PyInstaller) builds
# extract/ship their files under sys._MEIPASS instead of next to this file.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

STATIC_DIR = Path(__file__).resolve().parent / "static" if not getattr(sys, "frozen", False) \
    else BASE_DIR / "app" / "static"


def _user_data_dir() -> Path:
    """Per-user data root. Linux only for now (XDG); Mac/Windows to follow
    when those installers are built."""
    override = os.environ.get("NOTRA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "notra"


DATA_DIR = _user_data_dir()
LECTURES_DIR = DATA_DIR / "lectures"
COURSES_DIR = DATA_DIR / "courses"
DB_PATH = DATA_DIR / "notra.db"
ENV_PATH = DATA_DIR / ".env"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LECTURES_DIR.mkdir(parents=True, exist_ok=True)
COURSES_DIR.mkdir(parents=True, exist_ok=True)

# One-time migration from the pre-2.4 layout, where everything lived
# inside the repo (BASE_DIR/data, BASE_DIR/.env). Only runs when the new
# location is still empty, so it never clobbers anything.
_OLD_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_OLD_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _OLD_DATA_DIR.is_dir() and not any(DATA_DIR.iterdir()):
    for item in _OLD_DATA_DIR.iterdir():
        shutil.move(str(item), str(DATA_DIR / item.name))
if _OLD_ENV_PATH.is_file() and not ENV_PATH.exists():
    shutil.move(str(_OLD_ENV_PATH), str(ENV_PATH))

# Keys we persist in .env, with their defaults.
DEFAULTS: dict[str, str] = {
    "AI_PROVIDER": "anthropic",  # anthropic | gemini
    "ANTHROPIC_API_KEY": "",
    "CLAUDE_MODEL": "claude-opus-5",
    "CLAUDE_EFFORT": "high",
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-2.5-flash",
    "WHISPER_MODEL": "small",
    "WHISPER_COMPUTE": "int8",
    "WHISPER_LANGUAGE": "",  # empty = auto-detect
    "WHISPER_CPU_THREADS": "0",  # 0 = auto (all cores but one)
    "NOTES_STYLE": "detailed",  # detailed | concise
}

SECRET_KEYS = {"ANTHROPIC_API_KEY", "GEMINI_API_KEY"}

_lock = threading.Lock()


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_settings() -> dict[str, str]:
    """Defaults < process environment < .env file."""
    settings = dict(DEFAULTS)
    for key in DEFAULTS:
        if os.environ.get(key):
            settings[key] = os.environ[key]
    if ENV_PATH.exists():
        for key, value in _parse_env(ENV_PATH.read_text(encoding="utf-8")).items():
            if key in DEFAULTS and value != "":
                settings[key] = value
    return settings


def save_settings(updates: dict[str, str]) -> dict[str, str]:
    with _lock:
        current = dict(DEFAULTS)
        if ENV_PATH.exists():
            current.update(_parse_env(ENV_PATH.read_text(encoding="utf-8")))
        for key, value in updates.items():
            if key in DEFAULTS and value is not None:
                current[key] = str(value)
        lines = ["# Notra settings — this file is git-ignored, keep your key here."]
        for key in DEFAULTS:
            lines.append(f"{key}={current.get(key, '')}")
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_settings()


def get(key: str, default: str = "") -> str:
    return load_settings().get(key, default) or default


def lecture_dir(lecture_id: str) -> Path:
    path = LECTURES_DIR / lecture_id
    path.mkdir(parents=True, exist_ok=True)
    return path
