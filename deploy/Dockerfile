# GAgent app image (FastAPI backend). Self-contained: no source bind mounts.
#
# Dependencies come from pyproject.toml + uv.lock (single source of truth).
# The historical "patch layer" (deploy/Dockerfile.runtime-overlay) existed only
# because the old production image was built from an opaque digest; with this
# recipe that crutch is gone — its 19 hand-installed packages now live in
# pyproject.toml.
#
# Build: docker build -f deploy/Dockerfile -t gagent-app:<tag> .
# Context: repo root (BuildKit uses deploy/Dockerfile.dockerignore).

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/app/home

WORKDIR /app

# docker-cli: creates sibling sandbox containers via DOCKER_HOST (the socket
# proxy in the stack; a raw socket is never mounted into this container).
# procps: orphan-process cleanup at startup. sqlite3: the ops container reuses
# this image to VACUUM INTO backups.
# Mirrors: CN hosts (the .8 reality) cannot reach deb.debian.org / pypi.org.
# Defaults point at TUNA; override with --build-arg for a networked builder.
ARG APT_MIRROR_HOST=mirrors.tuna.tsinghua.edu.cn
RUN if [ -n "${APT_MIRROR_HOST}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR_HOST}|g" /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR_HOST}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-cli procps sqlite3 \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" uv

# Dependency layer first so source edits do not re-resolve the environment.
# --no-dev keeps black out; pytest stays in (main deps) for in-container smoke.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY tool_box ./tool_box
COPY skills ./skills
COPY scripts ./scripts

# Non-root runtime user. APP_UID/APP_GID must match the owner of the runtime
# bind mount so session dirs stay writable (LOCAL_INFRA §120).
ARG APP_UID=1001
ARG APP_GID=1001
RUN groupadd -g "${APP_GID}" gagent 2>/dev/null || true \
    && useradd -u "${APP_UID}" -g "${APP_GID}" -d /app/home -s /usr/sbin/nologin gagent 2>/dev/null || true \
    && mkdir -p /app/home /app/runtime /app/data \
    && chown -R "${APP_UID}:${APP_GID}" /app/home
USER ${APP_UID}:${APP_GID}

EXPOSE 9000

# Readiness (DB + runtime writable), not the static liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9000/health/ready', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
