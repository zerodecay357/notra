"""Folder-based course database.

Each course is a directory under data/courses/<slug>/ holding a course.json
with its details and a lectures/ folder where every finished PDF is filed
(alongside a small .json sidecar with the lecture's details). The directory
listing IS the database — deleting a folder removes the course.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import config


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "course"


def ensure(name: str) -> Path | None:
    """Create the course folder (and course.json) if missing; return its path."""
    name = (name or "").strip()
    if not name:
        return None
    path = config.COURSES_DIR / slugify(name)
    (path / "lectures").mkdir(parents=True, exist_ok=True)
    meta_path = path / "course.json"
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps({"name": name, "created_at": time.time()}, indent=2),
            encoding="utf-8",
        )
    return path


def list_courses() -> list[dict]:
    out: list[dict] = []
    for path in config.COURSES_DIR.iterdir():
        if not path.is_dir():
            continue
        meta: dict = {"name": path.name, "slug": path.name}
        meta_path = path / "course.json"
        if meta_path.exists():
            try:
                meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                pass  # a hand-made folder without valid json is still a course
        lectures = path / "lectures"
        meta["pdf_count"] = len(list(lectures.glob("*.pdf"))) if lectures.is_dir() else 0
        out.append(meta)
    out.sort(key=lambda c: str(c["name"]).lower())
    return out


def _safe(text: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "" for c in text).strip()
    return safe.replace(" ", "_")[:80]


def file_lecture(lecture: dict) -> None:
    """Copy the finished PDF + a details sidecar into the course folder."""
    course = (lecture.get("course") or "").strip()
    src = config.LECTURES_DIR / str(lecture.get("id")) / "notes.pdf"
    if not course or not src.exists():
        return
    folder = ensure(course) / "lectures"

    parts = [lecture.get("lecture_date") or "", _safe(lecture.get("topic") or "notes")]
    stem = "_".join(p for p in parts if p) + f"_{lecture['id'][:6]}"
    shutil.copy2(src, folder / f"{stem}.pdf")

    details = {
        k: lecture.get(k, "")
        for k in ("id", "course", "topic", "lecture_date", "instructor",
                  "summary", "duration_sec")
    }
    (folder / f"{stem}.json").write_text(json.dumps(details, indent=2), encoding="utf-8")


def migrate_existing() -> None:
    """Seed course folders from lectures already in the database."""
    from . import db

    for lecture in db.list_lectures():
        try:
            ensure(lecture.get("course") or "")
            if lecture.get("has_pdf"):
                file_lecture(lecture)
        except Exception:
            continue  # one bad row must not block startup
