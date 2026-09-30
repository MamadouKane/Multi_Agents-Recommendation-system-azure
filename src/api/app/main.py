"""The FastAPI application: `uvicorn src.api.app.main:app`.

`create_app` takes the function that builds the services, so tests start the real application,
routes, validation and CORS included, on fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.app.contracts import Health
from src.api.app.dependencies import get_services
from src.api.app.routers import chat, products
from src.api.app.services import Services, build_services
from src.api.core.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    build: Callable[[Settings], Services] = build_services,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Catalogue, artefacts and clients are loaded once, not on every request.
        app.state.services = build(settings)
        yield

    app = FastAPI(
        title="Coffee shop assistant",
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(products.router, prefix="/api/v1")

    @app.get("/health", response_model=Health, tags=["health"])
    def health(services: Annotated[Services, Depends(get_services)]) -> Health:
        return Health(status="ok", version=settings.app_version, products=len(services.catalog))

    return app


app = create_app()
