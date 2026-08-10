# Notra

**Turn it on. Go to sleep.**

Record a lecture, get a properly typeset PDF of notes.

You hit record, the lecture happens, you hit stop. Notra transcribes the audio
locally with Whisper, sends the transcript to Claude or Gemini, and compiles the
result into a LaTeX-quality PDF with definitions, theorems, derivations, the
questions students asked in class, and a ranked **Exam Focus** section
highlighting what's most likely to come up.

Transcription runs **locally and free**. No audio ever leaves your machine;
only the text transcript is sent to the AI provider.

## Download (Linux)

Grab `Notra-x86_64.AppImage` from the
[latest release](https://github.com/zerodecay357/notra/releases), then:

```bash
chmod +x Notra-x86_64.AppImage
./Notra-x86_64.AppImage
```

That is the whole install. Python, ffmpeg and the LaTeX engine are all inside
the file, so there is nothing else to set up.

**Requirements**

| | |
|---|---|
| OS | Ubuntu 22.04 or newer (Debian 12+, Fedora 36+, recent Arch/Mint all work) |
| CPU | 64-bit Intel/AMD. No ARM build yet |
| API key | free Gemini key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), no card needed, or Claude at [console.anthropic.com](https://console.anthropic.com) for the best notes |

**First run.** Click **Settings**, choose your provider, paste the key. Your
first recording downloads the Whisper model (about 500 MB) and the first PDF
fetches the LaTeX packages it needs, so keep internet on for that first run.
Everything is cached afterwards and works offline.

**If it will not start**, your system is probably missing the library AppImages
use to mount themselves:

```bash
sudo apt install libfuse2
```

Or run it without needing that at all:

```bash
./Notra-x86_64.AppImage --appimage-extract-and-run
```

## Run from source

For development, or on a platform without a build yet:

```bash
pip3 install -r requirements.txt
./run.sh
```

Open <http://localhost:8000>, click **Settings**, paste your API key. This mode
needs `ffmpeg` (`sudo apt install ffmpeg`) and a LaTeX engine — **tectonic** is
recommended (a single binary from
[tectonic-typesetting.github.io](https://tectonic-typesetting.github.io), drop
it in `bin/` or on PATH; `scripts/fetch-bin.sh` will fetch it for you), or TeX
Live's `pdflatex` (`sudo apt install texlive-latex-extra
texlive-fonts-recommended`, about 4 GB).

Your API key is written to `.env` in Notra's per-user data directory (see
Layout below), never inside this repo, so there is nothing to accidentally
commit.

## Building the desktop app

```bash
pip3 install -r requirements-desktop.txt
./scripts/fetch-bin.sh                    # static ffmpeg + tectonic into bin/
pyinstaller notra.spec                    # -> dist/notra/
```

Build inside a clean virtualenv with `PYTHONPATH` unset — unrelated packages on
the system path get swept into the bundle otherwise. `desktop_main.py` also
accepts `NOTRA_SELFTEST=1`, which exercises transcription and PDF compilation
inside the built app without needing an API key.

## Using it

1. Pick your **course** from the dropdown (or "＋ New course…" to create one),
   and fill in **date** and **lecture topic** (these become the title block of
   the PDF and steer how Claude writes).
2. Pick your audio source:
   - **Microphone** — in-person lecture.
   - **System audio** — an online lecture. Chrome will ask which tab/window to
     share; **tick "Share tab audio"** or nothing will be captured.
   - **Both** — you're in a hybrid class.
3. **Record.** Pause and resume as often as you like.
4. **Stop & generate.** Four stages run automatically: prepare audio →
   transcribe → Claude writes → compile PDF. Progress is live.
5. Read the PDF in-app, or download the `.pdf` / `.tex`.

The **Courses** tab in the sidebar collects everything course by course: a card
per course with its lecture count and most recent lecture, and inside it just
that course's notes.

Courses are a folder-based database: each one is a directory under
`courses/<course>/` in the data directory, and every finished PDF is
automatically filed into that course's `lectures/` folder (with a small `.json`
of the lecture details next to it). Deleting a course's folder removes it from
the dropdown.

Anything you record is kept in the sidebar library. **Regenerate** re-runs
Claude against the existing transcript — useful after you change the topic
field, the notes style, or add extra instructions. It doesn't re-transcribe, so
it's fast and cheap.

## What the PDF contains

Fixed house style, defined in `app/latex.py`. Claude writes only the body, and
only using this closed set of boxes — which is why it compiles reliably:

| Box | Used for |
|---|---|
| Definition | formal definitions |
| Theorem | stated results |
| Derivation | step-by-step working in display math |
| Example | worked examples from the lecture |
| **Key Point** | the load-bearing idea of a section |
| **Likely in Exam** | a specific predicted question, with reasoning |
| Common Pitfall | mistakes the lecturer called out |
| Asked in class | a student's question + the answer given |
| **Exam Focus** | ranked revision list, once at the end |

Plus `\hl{}` highlighting for the single most important phrase in a passage, and
a References section citing standard texts with chapter numbers.

## Settings

| Setting | Notes |
|---|---|
| AI provider | Claude (best notes) or Gemini (free tier — great for trying Notra without a card). |
| Claude model | Opus 5 gives the best notes. Sonnet 5 is faster and cheaper. |
| Gemini model | 2.5 Flash is fast with a generous free tier; 2.5 Pro is the quality option. |
| Effort | `high` is the sweet spot; `xhigh` for dense technical lectures. |
| Whisper model | `small` is the balance. `medium` is noticeably more accurate but ~3× slower on CPU. `tiny` for a quick draft. |
| CPU cores for transcription | `0` (auto) uses all cores but one, so the machine stays responsive while it works. Set a specific number to reserve more headroom, or if you want to push it higher than the auto default. |
| Spoken language | Leave on automatic unless it's mis-detecting. |
| Notes style | `detailed` (replaces attending) or `concise` (revision-oriented). |
| Your data | Shows where your lectures, PDFs and key are stored, with a button to open that folder. |

**Speed:** on a 12-core CPU with no GPU, `small` transcribes roughly 5–8× faster
than real time — a 50-minute lecture takes about 7–10 minutes. Claude then takes
1–3 minutes. Transcription is the slow part and it's the part that's free.

## Layout

```
app/
  main.py        FastAPI routes
  pipeline.py    the job: audio → transcript → LaTeX → PDF
  transcribe.py  faster-whisper wrapper
  notes.py       Claude prompt + streaming call + LaTeX repair pass
  latex.py       document preamble, sanitising, tectonic/pdflatex
  binaries.py    finds bundled (bin/) or system executables
  db.py          SQLite
  courses.py     folder-based course database
  static/        the web UI
```

Your recordings, transcripts, and PDFs live outside the repo, in
`~/.local/share/notra` (override with `NOTRA_DATA_DIR`):

```
~/.local/share/notra/
  notra.db  .env
  lectures/<id>/
    recording.webm  audio.wav  transcript.txt  notes.tex  notes.pdf
  courses/<course>/
    course.json  lectures/<date>_<topic>_<id>.pdf (+ .json details)
```

## If something goes wrong

**"No speech was recognised"** — the wrong input device was captured. For system
audio, re-share and tick "Share tab audio".

**LaTeX failed to compile** — Notra automatically sends the error back to Claude
once for a repair pass. If it still fails, the errors are shown in the app and
the broken source is kept at `data/lectures/<id>/notes.tex` so you can fix it by
hand. With tectonic, missing packages are fetched automatically (it needs
internet on first compile); with pdflatex, missing `.sty` files are usually
fixed by `sudo apt install texlive-latex-extra`.

**Notes are thin or wrong** — check the Transcript tab first. If the transcript
is garbled, switch Whisper to `medium` and regenerate. If the transcript is good
but the notes miss something, add a line in "Extra instructions for Claude" and
hit Regenerate.

Claude works only from what was said. It's instructed not to invent formulas or
results, and to mark unintelligible stretches rather than fill them in — but the
notes are a study aid, so spot-check anything you're relying on for an exam.
# notra
