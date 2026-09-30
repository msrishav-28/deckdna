"""Deck upload and inspection endpoints (blueprint section 13):

POST /v1/decks       upload a .pptx, start learning its style
GET  /v1/decks/{id}  extraction status and counts for one deck
GET  /v1/jobs/{id}   stage, progress and result for one background job
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app import config, db
from app.extraction import submit_extraction

router = APIRouter()

_CHUNK_BYTES = 1024 * 1024


def _safe_name(name: str) -> str:
    """Keep only a bare, boring file name; the upload directory is already
    unique per deck, so the name is for humans only."""
    base = Path(name).name  # drop any directory components
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned or "deck.pptx"


def _job_summary(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": job["progress"],
        "error": job["error"],
    }


@router.post("/v1/decks", status_code=202)
async def create_deck(request: Request, file: UploadFile) -> dict:
    name = _safe_name(file.filename or "")
    if Path(name).suffix.lower() != ".pptx":
        raise HTTPException(
            status_code=400,
            detail="Only .pptx decks can be uploaded (this file looks like "
            f"'{Path(name).suffix or 'no extension'}').",
        )

    deck_id = f"deck_{uuid.uuid4().hex[:12]}"
    target_dir = config.UPLOADS_DIR / deck_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    size = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Uploads are limited to "
                            f"{config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB; "
                            "this deck is larger."
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    if size == 0:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    queue = request.app.state.queue
    job_id = queue.create_job("extraction")
    db.insert_deck(deck_id, name, target, job_id)
    try:
        submit_extraction(queue, deck_id, target, job_id)
    except Exception as exc:
        db.fail_deck(deck_id, f"the extraction worker could not be started: {exc}")
        db.update_job(job_id, status="failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="The extraction worker could not be started."
        )
    return {"deck_id": deck_id, "status": "queued", "job_id": job_id}


@router.get("/v1/decks/{deck_id}")
def get_deck(deck_id: str) -> dict:
    deck = db.get_deck(deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=f"No deck '{deck_id}' was uploaded.")
    job = db.get_job(deck["job_id"]) if deck["job_id"] else None
    return {
        "deck_id": deck["deck_id"],
        "status": deck["status"],
        "source_name": deck["source_name"],
        "slide_count": deck["slide_count"],
        "style_guide_id": deck["style_guide_id"],
        "template_count": deck["template_count"],
        "error": deck["error"],
        "created_at": deck["created_at"],
        "updated_at": deck["updated_at"],
        "job": _job_summary(job) if job is not None else None,
    }


@router.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job '{job_id}' exists.")
    result = job["result"] or {}
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": job["progress"],
        "output_url": result.get("output_url"),
        "result": job["result"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
