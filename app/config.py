"""Paths and persisted settings for Notra."""

from __future__ import annotations

import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LECTURES_DIR = DATA_DIR / "lectures"
COURSES_DIR = DATA_DIR / "courses"
DB_PATH = DATA_DIR / "notra.db"
ENV_PATH = BASE_DIR / ".env"
STATIC_DIR = Path(__file__).resolve().parent / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LECTURES_DIR.mkdir(parents=True, exist_ok=True)
COURSES_DIR.mkdir(parents=True, exist_ok=True)

# Keys we persist in .env, with their defaults.
DEFAULTS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "",
    "CLAUDE_MODEL": "claude-opus-5",
    "CLAUDE_EFFORT": "high",
    "WHISPER_MODEL": "small",
    "WHISPER_COMPUTE": "int8",
    "WHISPER_LANGUAGE": "",  # empty = auto-detect
    "NOTES_STYLE": "detailed",  # detailed | concise
}

SECRET_KEYS = {"ANTHROPIC_API_KEY"}

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
