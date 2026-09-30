"""FastAPI dependencies: how a request reaches the services built at startup."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from src.api.app.services import Services


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)
