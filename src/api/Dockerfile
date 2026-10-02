# The API image (task 6.1): multi-stage, non-root, no secret and no .env inside.
#
#   docker build -f src/api/Dockerfile -t coffee-ai-api .
#
# Configuration comes from environment variables set on the Container App; authentication is the
# managed identity (AZURE_CLIENT_ID). Nothing in this image can reach Azure on its own.

ARG PYTHON_VERSION=3.11

# ---- Build: resolve and install the dependencies into a virtualenv ----------------------------
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /build
# The dependency list alone first: this layer is rebuilt only when pyproject.toml changes.
COPY pyproject.toml .
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache -r pyproject.toml

# ---- Runtime: the virtualenv and the code, nothing else -----------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG APP_VERSION=dev
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}
# Security patches of the base image, and no packaging tools at runtime: the system pip,
# setuptools and wheel carried the only fixable HIGH vulnerabilities Trivy found (day 6).
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip setuptools wheel \
    && groupadd --system app && useradd --system --gid app --no-create-home app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# The API and the catalogue rendering it reuses (data_pipelines.knowledge); no training code,
# no evaluation code, no data.
COPY --chown=app:app src/__init__.py src/__init__.py
COPY --chown=app:app src/api src/api
COPY --chown=app:app src/data_pipelines src/data_pipelines
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
# Two workers on 0.5 vCPU: one can serve while the other waits on a model call. The proxy
# headers make the image URLs use https behind the Container Apps ingress.
CMD ["uvicorn", "--factory", "src.api.app.main:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
