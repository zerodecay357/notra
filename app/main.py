"""Notra — FastAPI app. Serves the UI and the lecture API."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, courses, db, latex, media, notes, pipeline

app = FastAPI(title="Notra", docs_url=None, redoc_url=None)

db.init()
courses.migrate_existing()

app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


# ---------------------------------------------------------------- UI --------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((config.STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------ health --------

@app.get("/api/health")
def health() -> dict:
    settings = config.load_settings()
    return {
        "ffmpeg": media.ffmpeg_available(),
        "pdflatex": latex.pdflatex_available(),
        "api_key": notes.credentials_available(),
        "whisper_model": settings.get("WHISPER_MODEL"),
        "claude_model": settings.get("CLAUDE_MODEL"),
    }


# ---------------------------------------------------------- settings --------

@app.get("/api/settings")
def get_settings() -> dict:
    settings = config.load_settings()
    out = dict(settings)
    key = settings.get("ANTHROPIC_API_KEY", "")
    out["ANTHROPIC_API_KEY"] = f"…{key[-4:]}" if key else ""
    out["api_key_set"] = bool(key)
    return out


@app.post("/api/settings")
async def post_settings(payload: dict) -> dict:
    updates = {k: v for k, v in payload.items() if k in config.DEFAULTS}
    # An empty key field means "leave it alone", not "erase it".
    if not str(updates.get("ANTHROPIC_API_KEY", "")).strip():
        updates.pop("ANTHROPIC_API_KEY", None)
    config.save_settings(updates)
    return get_settings()


# ----------------------------------------------------------- courses --------

@app.get("/api/courses")
def list_courses() -> list[dict]:
    return courses.list_courses()


@app.post("/api/courses")
async def create_course(payload: dict) -> list[dict]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Course name is required.")
    courses.ensure(name)
    return courses.list_courses()


# ---------------------------------------------------------- lectures --------

@app.get("/api/lectures")
def list_lectures() -> list[dict]:
    return db.list_lectures()


@app.post("/api/lectures")
async def create_lecture(
    audio: UploadFile = File(...),
    course: str = Form(""),
    topic: str = Form(""),
    lecture_date: str = Form(""),
    instructor: str = Form(""),
    extra_notes: str = Form(""),
) -> dict:
    courses.ensure(course)
    lecture_id = db.create_lecture(
        course=course.strip(),
        topic=topic.strip(),
        lecture_date=lecture_date.strip(),
        instructor=instructor.strip(),
        extra_notes=extra_notes.strip(),
    )

    workdir = config.lecture_dir(lecture_id)
    suffix = Path(audio.filename or "recording.webm").suffix or ".webm"
    raw_path = workdir / f"recording{suffix}"

    with raw_path.open("wb") as fh:
        while chunk := await audio.read(1 << 20):
            fh.write(chunk)
    await audio.close()

    if raw_path.stat().st_size == 0:
        db.delete(lecture_id)
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, "The uploaded recording was empty.")

    pipeline.submit(lecture_id, raw_path)
    return {"id": lecture_id}


def _require(lecture_id: str) -> dict:
    lecture = db.get(lecture_id)
    if lecture is None:
        raise HTTPException(404, "Lecture not found.")
    return lecture


@app.get("/api/lectures/{lecture_id}")
def get_lecture(lecture_id: str) -> dict:
    lecture = _require(lecture_id)
    # The segment list can be thousands of entries and the UI never reads it;
    # the transcript endpoint serves the formatted text instead.
    lecture.pop("segments", None)
    return lecture


@app.post("/api/lectures/{lecture_id}")
async def update_lecture(lecture_id: str, payload: dict) -> dict:
    _require(lecture_id)
    allowed = {"course", "topic", "lecture_date", "instructor", "extra_notes"}
    updates = {k: str(v).strip() for k, v in payload.items() if k in allowed}
    if updates:
        db.update(lecture_id, **updates)
    return _require(lecture_id)


@app.post("/api/lectures/{lecture_id}/regenerate")
async def regenerate(lecture_id: str, payload: dict | None = None) -> dict:
    lecture = _require(lecture_id)
    if lecture["status"] == "processing":
        raise HTTPException(409, "This lecture is still being processed.")
    if payload:
        allowed = {"course", "topic", "lecture_date", "instructor", "extra_notes"}
        updates = {k: str(v).strip() for k, v in payload.items() if k in allowed}
        if updates:
            db.update(lecture_id, **updates)
    db.update(lecture_id, status="queued", stage="queued", progress=0.0, error="")
    pipeline.submit_regenerate(lecture_id)
    return {"ok": True}


@app.delete("/api/lectures/{lecture_id}")
def delete_lecture(lecture_id: str) -> dict:
    _require(lecture_id)
    db.delete(lecture_id)
    shutil.rmtree(config.LECTURES_DIR / lecture_id, ignore_errors=True)
    return {"ok": True}


# ------------------------------------------------------------- files --------

def _slug(lecture: dict) -> str:
    parts = [lecture.get("course") or "", lecture.get("topic") or "notes"]
    raw = "-".join(p for p in parts if p)
    safe = "".join(c if c.isalnum() or c in " -_" else "" for c in raw).strip()
    return (safe.replace(" ", "_") or "lecture_notes")[:80]


@app.get("/api/lectures/{lecture_id}/pdf")
def get_pdf(lecture_id: str, download: int = 0):
    lecture = _require(lecture_id)
    path = config.LECTURES_DIR / lecture_id / "notes.pdf"
    if not path.exists():
        raise HTTPException(404, "No PDF has been generated for this lecture yet.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{_slug(lecture)}.pdf" if download else None,
    )


@app.get("/api/lectures/{lecture_id}/tex")
def get_tex(lecture_id: str):
    lecture = _require(lecture_id)
    path = config.LECTURES_DIR / lecture_id / "notes.tex"
    if not path.exists():
        raise HTTPException(404, "No LaTeX source for this lecture yet.")
    return FileResponse(path, media_type="text/plain", filename=f"{_slug(lecture)}.tex")


@app.get("/api/lectures/{lecture_id}/transcript")
def get_transcript(lecture_id: str):
    lecture = _require(lecture_id)
    path = config.LECTURES_DIR / lecture_id / "transcript.txt"
    if path.exists():
        return PlainTextResponse(path.read_text(encoding="utf-8"))
    return PlainTextResponse(lecture.get("transcript") or "")


@app.get("/api/lectures/{lecture_id}/audio")
def get_audio(lecture_id: str):
    _require(lecture_id)
    folder = config.LECTURES_DIR / lecture_id
    for candidate in sorted(folder.glob("recording.*")):
        return FileResponse(candidate)
    wav = folder / "audio.wav"
    if wav.exists():
        return FileResponse(wav, media_type="audio/wav")
    raise HTTPException(404, "No audio stored for this lecture.")


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request, exc: RuntimeError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
