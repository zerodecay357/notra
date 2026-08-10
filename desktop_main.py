"""PyInstaller entrypoint for the packaged desktop app. Not used in normal
development — that's run.sh. See notra.spec."""

from __future__ import annotations

import os
import sys


def _selftest() -> int:
    """NOTRA_SELFTEST=1: exercise the local (no-API-key) pipeline stages
    inside the frozen build — transcription + LaTeX compile — without
    launching Qt or calling Claude/Gemini. A packaging QA tool: run this
    against any new platform build before shipping it."""
    import subprocess
    import tempfile
    from pathlib import Path

    from app import binaries, latex, transcribe

    print("engine():", latex.engine())

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        wav = tmp / "audio.wav"
        subprocess.run(
            [
                binaries.find("ffmpeg"), "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-ar", "16000", "-ac", "1", str(wav), "-y", "-loglevel", "error",
            ],
            check=True,
        )
        print("wav exists:", wav.exists(), wav.stat().st_size if wav.exists() else 0)

        os.environ["WHISPER_MODEL"] = "tiny"  # fast for a packaging smoke test
        result = transcribe.transcribe(wav, duration=2.0)
        print("whisper ok, language:", result["language"], "segments:", len(result["segments"]))

        doc = latex.build_document(
            {"course": "Selftest", "topic": "Packaging QA", "lecture_date": "2026-08-10"},
            r"\section*{Smoke Test}This is a selftest body with $E=mc^2$.",
        )
        (tmp / "notes.tex").write_text(doc, encoding="utf-8")
        ok, log = latex.compile_pdf(tmp, "notes.tex")
        print("pdf compiled:", ok, "pdf exists:", (tmp / "notes.pdf").exists())
        if not ok:
            print("LOG TAIL:", log[-2000:])
    return 0


if __name__ == "__main__":
    if os.environ.get("NOTRA_SELFTEST") == "1":
        sys.exit(_selftest())
    from app.desktop import main

    sys.exit(main())
