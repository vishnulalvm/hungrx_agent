import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.app.core.errors import register_exception_handlers
from apps.api.app.middleware.request_context import RequestContextMiddleware
from apps.api.app.routers.health import router as health_router
from apps.api.app.routers.v1 import v1_router
from core.config.logging import configure_logging
from core.config.settings import get_settings
from database.session import get_engine

logger = logging.getLogger("hungrx.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting hungrX API (environment=%s)", settings.environment)

    yield

    logger.info("Shutting down hungrX API")
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="hungrX API",
        version="0.1.0",
        lifespan=lifespan,
        # Docs are always mounted here (dev convenience); nothing stops an
        # operator from hiding them behind a reverse proxy in production.
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)

    return app


app = create_app()
