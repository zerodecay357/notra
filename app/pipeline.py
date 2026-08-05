"""Background job: audio -> transcript -> LaTeX -> PDF."""

from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, costs, courses, db, latex, media, notes, transcribe

# Transcription is CPU-bound and wants all the cores to itself — serialise it.
_transcribe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notra-transcribe")

# The Claude call is network-bound (mostly waiting), so a "Regenerate" on one
# lecture shouldn't have to queue behind another lecture's transcription.
# Kept small since Whisper on the same machine still wants the CPU.
_notes_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notra-notes")

# Weights of each stage in the overall progress bar.
_SPAN = {
    "converting": (0.00, 0.04),
    "transcribing": (0.04, 0.70),
    "writing": (0.70, 0.93),
    "compiling": (0.93, 1.00),
}


def _advance(lecture_id: str, stage: str, fraction: float) -> None:
    lo, hi = _SPAN[stage]
    db.set_progress(lecture_id, stage, lo + (hi - lo) * max(0.0, min(1.0, fraction)))


def submit(lecture_id: str, source_path: Path) -> None:
    _transcribe_executor.submit(_run, lecture_id, source_path)


def submit_regenerate(lecture_id: str) -> None:
    _notes_executor.submit(_run_notes_only, lecture_id)


def _reset_usage(lecture_id: str) -> None:
    """Zero the running usage/cost total before a fresh generate() call —
    a regenerate replaces the notes, so it shouldn't inherit the old run's
    token count. A repair() later in the same job still accumulates on top
    via _record_usage."""
    db.update(
        lecture_id,
        input_tokens=0, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0,
        cost_usd=0, energy_wh=0, co2_g=0,
    )


def _record_usage(lecture_id: str, model: str, *usages: dict) -> None:
    """Accumulate token usage (across generate + any repair calls) onto the
    lecture's running total and store the cost/energy/CO2 estimate."""
    lecture = db.get(lecture_id) or {}
    prior = {
        "input_tokens": lecture.get("input_tokens", 0),
        "output_tokens": lecture.get("output_tokens", 0),
        "cache_creation_tokens": lecture.get("cache_creation_tokens", 0),
        "cache_read_tokens": lecture.get("cache_read_tokens", 0),
    }
    total = costs.merge_usage(prior, *usages)
    impact = costs.estimate(model, total)
    db.update(
        lecture_id,
        **total,
        cost_usd=impact["cost_usd"],
        energy_wh=impact["energy_wh"],
        co2_g=impact["co2_g"],
    )


def _render(lecture_id: str, meta: dict, body: str) -> None:
    """Build the .tex, compile it, and retry once via Claude if it fails."""
    workdir = config.lecture_dir(lecture_id)

    document = latex.build_document(meta, body)
    (workdir / "notes.tex").write_text(document, encoding="utf-8")

    ok, log = latex.compile_pdf(workdir)
    if not ok:
        _advance(lecture_id, "compiling", 0.3)
        (workdir / "compile_error.log").write_text(log, encoding="utf-8")
        try:
            fixed, repair_usage = notes.repair(meta, latex.sanitize_body(body), log)
        except Exception as exc:  # repair itself failed
            raise RuntimeError(f"LaTeX failed to compile and could not be repaired: {exc}\n\n{log}")
        _record_usage(lecture_id, config.get("CLAUDE_MODEL"), repair_usage)

        (workdir / "notes.tex").write_text(latex.build_document(meta, fixed), encoding="utf-8")
        ok, log2 = latex.compile_pdf(workdir)
        if not ok:
            raise RuntimeError(
                "LaTeX still failed to compile after one repair attempt.\n\n" + log2
            )
        body = fixed

    (workdir / "notes_body.tex").write_text(body, encoding="utf-8")
    db.update(lecture_id, has_pdf=1)

    try:
        courses.file_lecture(db.get(lecture_id) or {})
    except Exception:
        traceback.print_exc()  # filing a copy must never fail the job


def _run(lecture_id: str, source_path: Path) -> None:
    workdir = config.lecture_dir(lecture_id)
    try:
        # 1. Normalise audio -----------------------------------------------
        db.update(lecture_id, status="processing", error="")
        _advance(lecture_id, "converting", 0.2)
        wav_path = workdir / "audio.wav"
        media.to_wav(source_path, wav_path)
        duration = media.wav_duration(wav_path)
        db.update(lecture_id, duration_sec=duration)
        _advance(lecture_id, "converting", 1.0)

        if duration < 1.0:
            raise RuntimeError("The recording is empty. Check your microphone and try again.")

        # 2. Transcribe ------------------------------------------------------
        _advance(lecture_id, "transcribing", 0.0)
        result = transcribe.transcribe(
            wav_path, duration, lambda f: _advance(lecture_id, "transcribing", f)
        )
        timestamped = transcribe.format_transcript(result["segments"])
        db.update(
            lecture_id,
            transcript=result["text"],
            language=result["language"],
            segments_json=json.dumps(result["segments"]),
            whisper_model=config.get("WHISPER_MODEL"),
        )
        (workdir / "transcript.txt").write_text(timestamped, encoding="utf-8")

        if not result["text"].strip():
            raise RuntimeError(
                "No speech was recognised in this recording. The microphone may have "
                "been muted or capturing the wrong input."
            )

        # 3. Notes -----------------------------------------------------------
        _advance(lecture_id, "writing", 0.0)
        meta = db.get(lecture_id) or {}
        meta["duration_sec"] = duration
        model = config.get("CLAUDE_MODEL")
        _reset_usage(lecture_id)
        body, summary, usage = notes.generate(
            meta, timestamped, lambda f: _advance(lecture_id, "writing", f)
        )
        db.update(lecture_id, summary=summary, claude_model=model)
        _record_usage(lecture_id, model, usage)

        # 4. PDF -------------------------------------------------------------
        _advance(lecture_id, "compiling", 0.1)
        _render(lecture_id, meta, body)

        db.update(lecture_id, status="ready", stage="done", progress=1.0, error="")

    except Exception as exc:
        traceback.print_exc()
        db.update(lecture_id, status="error", stage="error", error=str(exc)[:4000])


def _run_notes_only(lecture_id: str) -> None:
    """Re-run notes + PDF against the transcript we already have."""
    try:
        meta = db.get(lecture_id)
        if not meta:
            return
        if not (meta.get("transcript") or "").strip():
            raise RuntimeError("This lecture has no transcript to work from.")

        db.update(lecture_id, status="processing", error="", has_pdf=0)

        segments = meta.get("segments") or []
        timestamped = (
            transcribe.format_transcript(segments) if segments else meta["transcript"]
        )

        _advance(lecture_id, "writing", 0.0)
        model = config.get("CLAUDE_MODEL")
        _reset_usage(lecture_id)
        body, summary, usage = notes.generate(
            meta, timestamped, lambda f: _advance(lecture_id, "writing", f)
        )
        db.update(lecture_id, summary=summary, claude_model=model)
        _record_usage(lecture_id, model, usage)

        _advance(lecture_id, "compiling", 0.1)
        _render(lecture_id, meta, body)

        db.update(lecture_id, status="ready", stage="done", progress=1.0, error="")

    except Exception as exc:
        traceback.print_exc()
        db.update(lecture_id, status="error", stage="error", error=str(exc)[:4000])
