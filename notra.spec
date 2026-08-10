# PyInstaller spec for Notra's native desktop build (Linux/Ubuntu for now).
#
#   pip3 install -r requirements-desktop.txt
#   ./scripts/fetch-bin.sh          # bundled ffmpeg + tectonic into bin/
#   pyinstaller notra.spec
#
# Produces dist/notra/notra — a --onedir build (instant launch, vs. the
# multi-second self-extraction of --onefile). Ship the whole dist/notra/
# folder; notra is the executable inside it.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("app/static", "app/static"),
    ("bin/ffmpeg", "bin"),
    ("bin/tectonic", "bin"),
]
binaries = []
hiddenimports = []

# faster_whisper/ctranslate2 need their bundled data (tokenizer assets,
# native .so loader) as well as submodules — collect_all is appropriate here.
for pkg in ("faster_whisper", "ctranslate2"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# uvicorn/anthropic/google.genai are pure-code SDKs — only their submodules
# matter (no bundled data/binaries), and collect_all would also sweep in
# google-genai's shipped tests/ package, which drags in matplotlib -> sympy
# -> torch transitively. collect_submodules avoids that.
for pkg in ("uvicorn", "anthropic", "google.genai"):
    hiddenimports += [
        m for m in collect_submodules(pkg) if ".tests." not in m and not m.endswith(".tests")
    ]

a = Analysis(
    ["desktop_main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="notra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="notra",
)
