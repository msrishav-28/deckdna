"""SQLite metadata store for the web app.

One small module-level API over sqlite3; every function opens a short-lived
connection so the request threads and the single background worker can
safely share the database. Uploaded file content is never stored here —
only identifiers, statuses, counts and artifact paths.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app import config


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    deck_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    job_id TEXT,
    slide_count INTEGER,
    style_guide_id TEXT,
    template_count INTEGER,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS style_guide_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_guide_id TEXT NOT NULL,
    patch_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outlines (
    outline_id TEXT PRIMARY KEY,
    style_guide_id TEXT NOT NULL,
    deck_id TEXT,
    brief_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)


def _row_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


# -- decks ------------------------------------------------------------------


def insert_deck(
    deck_id: str, source_name: str, source_path: Path, job_id: str
) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO decks (deck_id, source_name, source_path, status, "
            "job_id, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (deck_id, source_name, str(source_path), job_id, now, now),
        )


def get_deck(deck_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
    return _row_dict(row)


def complete_deck(
    deck_id: str, slide_count: int, style_guide_id: str, template_count: int
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE decks SET status = 'completed', slide_count = ?, "
            "style_guide_id = ?, template_count = ?, error = NULL, "
            "updated_at = ? WHERE deck_id = ?",
            (slide_count, style_guide_id, template_count, _now(), deck_id),
        )


def fail_deck(deck_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE decks SET status = 'failed', error = ?, updated_at = ? "
            "WHERE deck_id = ?",
            (error, _now(), deck_id),
        )


def update_deck_status(deck_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE decks SET status = ?, updated_at = ? WHERE deck_id = ?",
            (status, _now(), deck_id),
        )


def fail_interrupted_jobs() -> None:
    """Mark rows left queued or running by an earlier process as failed, so a
    server restart never leaves a job reporting progress forever."""
    message = "the server restarted before this job finished"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', stage = 'interrupted', "
            "error = ?, updated_at = ? WHERE status IN ('queued', 'running')",
            (message, now),
        )
        conn.execute(
            "UPDATE decks SET status = 'failed', error = ?, updated_at = ? "
            "WHERE status IN ('queued', 'running')",
            (message, now),
        )


# -- jobs -------------------------------------------------------------------


def insert_job(job_id: str, kind: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, kind, status, stage, progress, "
            "created_at, updated_at) VALUES (?, ?, 'queued', 'queued', 0, ?, ?)",
            (job_id, kind, now, now),
        )


def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    progress: Optional[int] = None,
    result: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    sets = ["updated_at = ?"]
    values: list = [_now()]
    if status is not None:
        sets.append("status = ?")
        values.append(status)
    if stage is not None:
        sets.append("stage = ?")
        values.append(stage)
    if progress is not None:
        sets.append("progress = ?")
        values.append(progress)
    if result is not None:
        sets.append("result_json = ?")
        values.append(json.dumps(result))
    if error is not None:
        sets.append("error = ?")
        values.append(error)
    values.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", values)


def get_job(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    job = _row_dict(row)
    if job is None:
        return None
    raw = job.pop("result_json", None)
    job["result"] = json.loads(raw) if raw else None
    return job


# -- style guide overrides --------------------------------------------------


def insert_style_guide_override(style_guide_id: str, patch: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO style_guide_overrides (style_guide_id, patch_json, "
            "created_at) VALUES (?, ?, ?)",
            (style_guide_id, json.dumps(patch), _now()),
        )


def list_style_guide_overrides(style_guide_id: str) -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, patch_json, created_at FROM style_guide_overrides "
            "WHERE style_guide_id = ? ORDER BY id",
            (style_guide_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "patch": json.loads(row["patch_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


# -- outlines ---------------------------------------------------------------


def insert_outline(
    outline_id: str,
    style_guide_id: str,
    deck_id: str,
    brief: dict,
    plan: dict,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO outlines (outline_id, style_guide_id, deck_id, "
            "brief_json, plan_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                outline_id,
                style_guide_id,
                deck_id,
                json.dumps(brief),
                json.dumps(plan),
                _now(),
            ),
        )


def get_outline(outline_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM outlines WHERE outline_id = ?", (outline_id,)
        ).fetchone()
    outline = _row_dict(row)
    if outline is None:
        return None
    outline["brief"] = json.loads(outline.pop("brief_json"))
    outline["plan"] = json.loads(outline.pop("plan_json"))
    return outline
