"""Application factory, lifespan (background jobs) and SPA hosting."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from app import __version__
from app.alerts import Notifier
from app.api import router as api_router
from app.config import Settings, load_settings
from app.db import Database
from app.scheduler import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def mount_spa(app: FastAPI) -> None:
    """Serve the built React dashboard for any non-API path (SPA fallback)."""
    index = DIST_DIR / "index.html"
    built = index.exists()

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if not built:
            return JSONResponse(
                {"detail": "Frontend build not found. Run `npm run build` inside frontend/."},
                status_code=404,
            )
        candidate = (DIST_DIR / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(DIST_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    owns_db = db is None
    database = db or Database(settings.database_path)
    alert_notifier = Notifier(
        settings, client=httpx.AsyncClient(transport=transport) if transport is not None else None
    )
    scheduler = Scheduler(database, settings, alert_notifier, transport=transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await alert_notifier.aclose()
            if owns_db:
                database.close()

    app = FastAPI(
        title="Sentinel",
        version=__version__,
        description="API uptime and latency monitor with alert rules and incident recovery",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database
    app.state.scheduler = scheduler
    app.state.notifier = alert_notifier

    app.include_router(api_router)
    mount_spa(app)
    return app


app = create_app()
