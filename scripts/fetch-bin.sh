#!/usr/bin/env bash
# Fetch static ffmpeg + tectonic binaries into bin/, for a self-contained
# Linux build (used by notra.spec / the packaged desktop app). Not needed
# for the normal ./run.sh dev workflow, which is happy to use whatever the
# system already has.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p bin
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Fetching static ffmpeg (johnvansickle.com, amd64)..."
curl -fL --progress-bar -o "$tmp/ffmpeg.tar.xz" \
  https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xf "$tmp/ffmpeg.tar.xz" -C "$tmp"
ffdir="$(find "$tmp" -maxdepth 1 -type d -name 'ffmpeg-*-amd64-static')"
cp "$ffdir/ffmpeg" bin/ffmpeg
chmod +x bin/ffmpeg
echo "  -> bin/ffmpeg ($(du -h bin/ffmpeg | cut -f1))"

echo "Fetching static tectonic (x86_64-unknown-linux-musl)..."
tag="$(curl -fsSL https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest \
  | python3 -c 'import json,sys,urllib.parse; print(urllib.parse.quote(json.load(sys.stdin)["tag_name"]))')"
version="${tag#tectonic%40}"
curl -fL --progress-bar -o "$tmp/tectonic.tar.gz" \
  "https://github.com/tectonic-typesetting/tectonic/releases/download/${tag}/tectonic-${version}-x86_64-unknown-linux-musl.tar.gz"
tar -xzf "$tmp/tectonic.tar.gz" -C "$tmp"
cp "$tmp/tectonic" bin/tectonic
chmod +x bin/tectonic
echo "  -> bin/tectonic ($(du -h bin/tectonic | cut -f1))"

echo
echo "Done. bin/ffmpeg and bin/tectonic are ready (git-ignored, not committed)."
