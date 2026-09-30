"""The FastAPI application: create_app() wires storage, the local job queue
and the routers together, and nothing else.

Run it with `python -m app.main` or
`uvicorn app.main:create_app --factory`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db, web
from app.api import decks, generation, style_guides
from app.jobs import JobQueue


def create_app() -> FastAPI:
    db.init_db()
    queue = JobQueue()
    queue.start()
    app = FastAPI(
        title="DeckDNA",
        version="0.1.0",
        description=(
            "Upload a deck to learn its style, then generate a new deck in "
            "that style and download it as editable PowerPoint."
        ),
    )
    app.state.queue = queue
    app.include_router(decks.router)
    app.include_router(style_guides.router)
    app.include_router(generation.router)
    app.include_router(web.router)
    app.mount(
        "/static", StaticFiles(directory=str(web.STATIC_DIR)), name="static"
    )
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
