"""Audio normalisation via ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SAMPLE_RATE = 16000


class MediaError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def to_wav(src: Path, dest: Path) -> Path:
    """Convert any container the browser produced into 16 kHz mono PCM."""
    if not ffmpeg_available():
        raise MediaError("ffmpeg is not installed or not on PATH.")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        raise MediaError(f"ffmpeg failed to decode the recording: {proc.stderr.strip()[:400]}")
    return dest


def wav_duration(path: Path) -> float:
    """Duration in seconds of a 16-bit mono PCM wav, from its size."""
    try:
        payload = max(0, path.stat().st_size - 44)
        return payload / (SAMPLE_RATE * 2)
    except OSError:
        return 0.0
