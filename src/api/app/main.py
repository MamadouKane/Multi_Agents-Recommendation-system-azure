"""The FastAPI application: `uvicorn --factory src.api.app.main:create_app`.

No application object is built at import time: importing this module (tests, tools) must never
read the environment, connect to Azure or switch the telemetry export on.

`create_app` takes the function that builds the services, so tests start the real application,
routes, validation and CORS included, on fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.api.app.contracts import Health
from src.api.app.dependencies import get_services
from src.api.app.routers import chat, products
from src.api.app.services import Services, build_services
from src.api.app.telemetry import configure_telemetry
from src.api.core.settings import Settings, get_settings

WEB_PAGE = Path(__file__).parent / "web" / "index.html"


def create_app(
    settings: Settings | None = None,
    build: Callable[[Settings], Services] = build_services,
) -> FastAPI:
    settings = settings or get_settings()
    telemetry = configure_telemetry(settings)

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

    @app.get("/", include_in_schema=False)
    def web() -> FileResponse:
        """A minimal chat page, served by the API itself: same origin, so no CORS to open."""
        return FileResponse(WEB_PAGE, media_type="text/html")

    @app.get("/health", response_model=Health, tags=["health"])
    def health(services: Annotated[Services, Depends(get_services)]) -> Health:
        return Health(
            status="ok",
            version=settings.app_version,
            products=len(services.catalog),
            recommender=services.recommender_version,
        )

    if telemetry:
        instrument(app)
    return app


def instrument(app: FastAPI) -> None:
    """One request span per HTTP call, the parent of every span of the turn.

    The distribution's automatic instrumentation patches the `fastapi.FastAPI` class, which is
    too late for this module: it imported the class first. Instrumenting the instance is explicit
    and does not depend on import order. Health probes are left out.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
