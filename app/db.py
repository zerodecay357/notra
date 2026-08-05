"""SQLite storage for lectures. One row per recording."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures (
    id              TEXT PRIMARY KEY,
    course          TEXT NOT NULL DEFAULT '',
    topic           TEXT NOT NULL DEFAULT '',
    lecture_date    TEXT NOT NULL DEFAULT '',
    instructor      TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    duration_sec    REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued',
    stage           TEXT NOT NULL DEFAULT 'queued',
    progress        REAL NOT NULL DEFAULT 0,
    error           TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT '',
    transcript      TEXT NOT NULL DEFAULT '',
    segments_json   TEXT NOT NULL DEFAULT '[]',
    summary         TEXT NOT NULL DEFAULT '',
    has_pdf         INTEGER NOT NULL DEFAULT 0,
    whisper_model   TEXT NOT NULL DEFAULT '',
    claude_model    TEXT NOT NULL DEFAULT '',
    extra_notes     TEXT NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    energy_wh       REAL NOT NULL DEFAULT 0,
    co2_g           REAL NOT NULL DEFAULT 0
);
"""

# Columns added after the original schema — added via ALTER TABLE for
# databases that already exist on disk (init() below is the migration).
_ADDED_COLUMNS = {
    "input_tokens": "INTEGER NOT NULL DEFAULT 0",
    "output_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_creation_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
    "cost_usd": "REAL NOT NULL DEFAULT 0",
    "energy_wh": "REAL NOT NULL DEFAULT 0",
    "co2_g": "REAL NOT NULL DEFAULT 0",
}

LIST_FIELDS = (
    "id, course, topic, lecture_date, instructor, created_at, updated_at, "
    "duration_sec, status, stage, progress, error, language, summary, has_pdf, "
    "cost_usd, co2_g"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(lectures)")}
        for col, decl in _ADDED_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE lectures ADD COLUMN {col} {decl}")


def create_lecture(**fields: Any) -> str:
    lecture_id = uuid.uuid4().hex[:12]
    now = time.time()
    row = {
        "id": lecture_id,
        "course": fields.get("course", ""),
        "topic": fields.get("topic", ""),
        "lecture_date": fields.get("lecture_date", ""),
        "instructor": fields.get("instructor", ""),
        "created_at": now,
        "updated_at": now,
        "duration_sec": fields.get("duration_sec", 0),
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "extra_notes": fields.get("extra_notes", ""),
    }
    cols = ", ".join(row)
    placeholders = ", ".join(f":{k}" for k in row)
    with _lock, _connect() as conn:
        conn.execute(f"INSERT INTO lectures ({cols}) VALUES ({placeholders})", row)
    return lecture_id


def update(lecture_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    params = dict(fields, id=lecture_id)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE lectures SET {assignments} WHERE id = :id", params)


def set_progress(lecture_id: str, stage: str, progress: float) -> None:
    update(lecture_id, stage=stage, progress=max(0.0, min(1.0, progress)))


def get(lecture_id: str) -> dict[str, Any] | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["segments"] = json.loads(data.pop("segments_json") or "[]")
    return data


def list_lectures() -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            f"SELECT {LIST_FIELDS} FROM lectures ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete(lecture_id: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM lectures WHERE id = ?", (lecture_id,))
