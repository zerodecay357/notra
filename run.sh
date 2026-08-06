#!/usr/bin/env bash
# Start Notra on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

missing=()
{ [ -x bin/ffmpeg ] || command -v ffmpeg >/dev/null; } \
  || missing+=("ffmpeg  (sudo apt install ffmpeg, or drop a static build in bin/)")
{ [ -x bin/tectonic ] || command -v tectonic >/dev/null || command -v pdflatex >/dev/null; } \
  || missing+=("a LaTeX engine — tectonic recommended (single binary, https://tectonic-typesetting.github.io; put it in bin/), or TeX Live's pdflatex")
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing system dependencies:"
  printf '  - %s\n' "${missing[@]}"
  echo
fi

echo "Notra → http://localhost:${PORT}"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" "$@"
