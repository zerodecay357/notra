#!/usr/bin/env bash
# Start Notra on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

missing=()
command -v ffmpeg   >/dev/null || missing+=("ffmpeg  (sudo apt install ffmpeg)")
command -v pdflatex >/dev/null || missing+=("pdflatex (sudo apt install texlive-latex-extra)")
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing system dependencies:"
  printf '  - %s\n' "${missing[@]}"
  echo
fi

echo "Notra → http://localhost:${PORT}"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" "$@"
