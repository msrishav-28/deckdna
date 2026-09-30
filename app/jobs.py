"""The local background worker (blueprint: "local queue first").

One daemon thread runs jobs strictly in submission order. Every job is a row
in the SQLite store, so stage, progress and result survive page reloads. A
job function receives its job id and a ``report(stage, progress)`` callback;
the value it returns is stored as the job's result. A failed job records its
error and never takes the worker down.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from typing import Callable, Dict, Optional

from app import db

logger = logging.getLogger(__name__)

ProgressReporter = Callable[[str, int], None]
JobFunction = Callable[[str, ProgressReporter], Optional[Dict]]


class JobQueue:
    """A single FIFO worker. Create one per process (tests: one per app)."""

    def __init__(self) -> None:
        self._pending: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        """Run startup once: fail rows interrupted by an earlier process,
        then start the worker thread."""
        with self._start_lock:
            if self._started:
                return
            db.fail_interrupted_jobs()
            self._thread = threading.Thread(
                target=self._work, name="deckdna-worker", daemon=True
            )
            self._thread.start()
            self._started = True

    def create_job(self, kind: str) -> str:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        db.insert_job(job_id, kind)
        return job_id

    def enqueue(self, job_id: str, fn: JobFunction) -> None:
        self.start()
        self._pending.put((job_id, fn))

    def submit(self, kind: str, fn: JobFunction) -> str:
        job_id = self.create_job(kind)
        self.enqueue(job_id, fn)
        return job_id

    # -- worker -------------------------------------------------------------

    def _work(self) -> None:
        while True:
            job_id, fn = self._pending.get()
            try:
                db.update_job(job_id, status="running")
                result = fn(job_id, self._reporter(job_id))
            except Exception as exc:  # a bad job must not kill the worker
                logger.exception("job %s failed", job_id)
                db.update_job(job_id, status="failed", error=str(exc))
            else:
                db.update_job(
                    job_id, status="completed", progress=100, result=result or {}
                )
            finally:
                self._pending.task_done()

    @staticmethod
    def _reporter(job_id: str) -> ProgressReporter:
        def report(stage: str, progress: int) -> None:
            db.update_job(job_id, stage=stage, progress=int(progress))

        return report


def wait_for(
    job_id: str, timeout: float = 60.0, interval: float = 0.02
) -> Optional[dict]:
    """Poll the store until the job reaches a final status (or the timeout
    passes) and return the job row; callers check its ``status``."""
    deadline = time.monotonic() + timeout
    job = db.get_job(job_id)
    while job is not None and job["status"] not in ("completed", "failed"):
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
        job = db.get_job(job_id)
    return job
