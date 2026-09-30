"""Web app tests: upload, background extraction, job/deck status endpoints
and restart recovery. Everything runs against temporary directories, so no
repository artifacts are touched."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import create_app

POLL_TIMEOUT = 60.0


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(config, "STYLE_GUIDES_DIR", tmp_path / "style_guides")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "deckdna.sqlite3")
    with TestClient(create_app()) as test_client:
        yield test_client


def _upload_deck(client, fixture_deck, name="sample_deck.pptx"):
    response = client.post(
        "/v1/decks",
        files={
            "file": (
                name,
                fixture_deck.read_bytes(),
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation",
            )
        },
    )
    return response


def _wait_for_job(client, job_id, timeout=POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in ("completed", "failed"):
            return job
        assert time.monotonic() < deadline, f"job did not finish in time: {job}"
        time.sleep(0.05)


def test_upload_runs_extraction_and_reports_deck(client, fixture_deck):
    response = _upload_deck(client, fixture_deck)
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["status"] == "queued"
    assert created["deck_id"].startswith("deck_")
    assert created["job_id"].startswith("job_")

    job = _wait_for_job(client, created["job_id"])
    assert job["status"] == "completed", job
    assert job["progress"] == 100
    assert job["error"] is None
    assert job["result"]["slide_count"] >= 1
    assert job["result"]["style_guide_id"] == created["deck_id"]

    deck = client.get(f"/v1/decks/{created['deck_id']}").json()
    assert deck["status"] == "completed"
    assert deck["slide_count"] == job["result"]["slide_count"]
    assert deck["template_count"] == job["result"]["template_count"]
    assert deck["style_guide_id"] == created["deck_id"]
    assert deck["error"] is None
    assert deck["job"]["status"] == "completed"

    from core.style_guide import StyleGuide
    from core.templates import load_templates

    guide_path = config.STYLE_GUIDES_DIR / f"{created['deck_id']}.json"
    templates_path = config.STYLE_GUIDES_DIR / f"{created['deck_id']}_templates.json"
    assert guide_path.is_file() and templates_path.is_file()
    guide = StyleGuide.model_validate_json(guide_path.read_text(encoding="utf-8"))
    assert guide.source_slide_count == deck["slide_count"]
    templates = load_templates(templates_path)
    assert len(templates) == deck["slide_count"]


def test_upload_rejects_non_pptx(client):
    response = client.post(
        "/v1/decks", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 400
    assert "pptx" in response.json()["detail"]


def test_upload_rejects_empty_file(client):
    response = client.post(
        "/v1/decks",
        files={"file": ("empty.pptx", b"", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_rejects_oversized_file(client, fixture_deck, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    response = _upload_deck(client, fixture_deck)
    assert response.status_code == 413
    assert list(config.UPLOADS_DIR.glob("deck_*")) == []


def test_upload_sanitizes_file_name(client, fixture_deck):
    response = _upload_deck(client, fixture_deck, name="../../quarterly review!.pptx")
    assert response.status_code == 202, response.text
    created = response.json()
    deck = client.get(f"/v1/decks/{created['deck_id']}").json()
    assert "/" not in deck["source_name"] and "\\" not in deck["source_name"]
    assert ".." not in deck["source_name"]
    assert deck["source_name"].endswith(".pptx")
    stored = list((config.UPLOADS_DIR / created["deck_id"]).iterdir())
    assert len(stored) == 1 and stored[0].name == deck["source_name"]
    job = _wait_for_job(client, created["job_id"])
    assert job["status"] == "completed", job


def test_invalid_pptx_fails_the_job_and_the_deck(client):
    response = client.post(
        "/v1/decks",
        files={"file": ("broken.pptx", b"not a zip archive", "application/octet-stream")},
    )
    assert response.status_code == 202, response.text
    created = response.json()
    job = _wait_for_job(client, created["job_id"])
    assert job["status"] == "failed"
    assert job["error"]
    assert job["result"] is None

    deck = client.get(f"/v1/decks/{created['deck_id']}").json()
    assert deck["status"] == "failed"
    assert deck["error"]


def test_unknown_deck_and_job_return_404(client):
    deck_response = client.get("/v1/decks/deck_does_not_exist")
    assert deck_response.status_code == 404
    job_response = client.get("/v1/jobs/job_does_not_exist")
    assert job_response.status_code == 404
    assert "job_does_not_exist" in job_response.json()["detail"]


def test_restart_marks_interrupted_jobs_and_decks_failed(client):
    db.insert_job("job_stale", "extraction")
    db.insert_deck("deck_stale", "x.pptx", config.UPLOADS_DIR / "x.pptx", "job_stale")
    db.update_job("job_stale", status="running", stage="parsing", progress=10)
    db.update_deck_status("deck_stale", "running")

    create_app()  # a fresh process: recovery runs at startup

    job = db.get_job("job_stale")
    assert job["status"] == "failed"
    assert "restarted" in job["error"]
    assert db.get_deck("deck_stale")["status"] == "failed"
