"""FastAPI application factory: lifespan, middleware, exception handlers, routers."""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import artifacts, auth, courses, export, lectures, models
from app.api import media as media_routes
from app.core.config import get_settings
from app.core.db import create_all
from app.core.job_lock import LockedError
from app.core.logging import configure_logging, correlation_id
from app.services import llm
from app.services.media import MediaError
from app.workers import worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, prepare storage, materialise the schema, start the worker."""
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)

    Path(settings.STORAGE_ROOT).mkdir(parents=True, exist_ok=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)

    create_all()
    worker.reconcile_orphaned_lectures()
    worker.start_worker()

    # Best-effort Ollama probe. A missing Ollama is not a startup failure - we log and go.
    try:
        available = llm.list_models()
        if available and settings.OLLAMA_MODEL not in available:
            logger.warning(
                "Configured Ollama model not present on server",
                extra={"model": settings.OLLAMA_MODEL, "available": available},
            )
        elif not available:
            logger.warning("Ollama returned no models; is the daemon running?")
    except Exception as exc:  # noqa: BLE001 - deliberately swallow, best-effort probe
        logger.warning("Ollama probe failed: %s", exc)

    try:
        yield
    finally:
        try:
            worker.stop_worker()
        finally:
            llm.close_client()


def create_app() -> FastAPI:
    """Build the FastAPI application with all middleware, handlers, and routers wired."""
    settings = get_settings()
    app = FastAPI(
        title="Lecture Transcriptor",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _correlation_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        cid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        token = correlation_id.set(cid)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = cid
            return response
        finally:
            correlation_id.reset(token)

    @app.exception_handler(LockedError)
    async def _locked_handler(request: Request, exc: LockedError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc) or "another transcription is currently running"},
        )

    @app.exception_handler(MediaError)
    async def _media_error_handler(request: Request, exc: MediaError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc) or "media error"})

    app.include_router(auth.router)
    app.include_router(courses.router)
    app.include_router(lectures.nested_router)
    app.include_router(lectures.detail_router)
    app.include_router(artifacts.router)
    app.include_router(media_routes.router)
    app.include_router(export.router)
    app.include_router(models.router)

    return app


# Module-level ASGI app so `uvicorn app.main:app` works out of the box.
app = create_app()
