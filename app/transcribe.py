"""Local speech-to-text with faster-whisper (CPU, no API key needed)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from . import config

_model = None
_model_key: tuple[str, str] | None = None
_model_lock = threading.Lock()


def _load_model(size: str, compute_type: str):
    global _model, _model_key
    with _model_lock:
        if _model is not None and _model_key == (size, compute_type):
            return _model
        from faster_whisper import WhisperModel

        _model = WhisperModel(size, device="cpu", compute_type=compute_type)
        _model_key = (size, compute_type)
        return _model


def transcribe(
    wav_path: Path,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
) -> dict:
    """Return {text, language, segments:[{start,end,text}]}."""
    settings = config.load_settings()
    size = settings.get("WHISPER_MODEL") or "small"
    compute_type = settings.get("WHISPER_COMPUTE") or "int8"
    language = settings.get("WHISPER_LANGUAGE") or None

    model = _load_model(size, compute_type)

    segment_iter, info = model.transcribe(
        str(wav_path),
        language=language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700},
        beam_size=5,
        condition_on_previous_text=False,
    )

    total = duration or getattr(info, "duration", 0) or 0
    segments: list[dict] = []
    parts: list[str] = []

    for seg in segment_iter:
        text = seg.text.strip()
        if not text:
            continue
        segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        parts.append(text)
        if on_progress and total:
            on_progress(min(1.0, seg.end / total))

    if on_progress:
        on_progress(1.0)

    return {
        "text": " ".join(parts).strip(),
        "language": getattr(info, "language", "") or "",
        "segments": segments,
    }


def format_transcript(segments: list[dict]) -> str:
    """Timestamped transcript — gives Claude anchors for the lecture timeline."""
    lines = []
    for seg in segments:
        minutes, seconds = divmod(int(seg["start"]), 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg['text']}")
    return "\n".join(lines)
