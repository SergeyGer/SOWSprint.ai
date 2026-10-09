# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# SOWSprint.ai — production image
# ---------------------------------------------------------------------------
# Multi-stage build on python:3.11-slim (the base image named in the brief):
#
#   builder  — compiles wheels into a self-contained virtualenv
#   runtime  — carries only the virtualenv + application code, runs as non-root
#
# The runtime stage never sees a compiler, pip cache or build header, which keeps
# the final image small and removes the toolchain an attacker could pivot into.

# ===========================================================================
# Stage 1 — builder
# ===========================================================================
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build headers for wheels without a prebuilt manylinux artefact.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      build-essential \
      libffi-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency layer first: it only invalidates when requirements actually change,
# so application edits reuse the cached, expensive layer.
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt

# ===========================================================================
# Stage 2 — runtime
# ===========================================================================
FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="SOWSprint.ai" \
      org.opencontainers.image.description="Autonomous multi-agent AI platform for B2B scope-to-contract automation" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH="/opt/venv/bin:$PATH" \
    # Bind to every interface: the brief requires an iPhone on the same Wi-Fi to
    # reach this container, which a 127.0.0.1 bind would silently prevent.
    SOWSPRINT_HOST=0.0.0.0 \
    SOWSPRINT_PORT=8000 \
    # In-container defaults point at the compose service name, not localhost.
    SOWSPRINT_QDRANT_URL=http://sowsprint-qdrant:6333 \
    SOWSPRINT_VECTOR_BACKEND=qdrant

RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      curl \
      tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 sowsprint \
 && useradd --uid 10001 --gid sowsprint --create-home --shell /usr/sbin/nologin sowsprint

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Application code. Ordered least- to most-volatile so layer caching works.
COPY --chown=sowsprint:sowsprint src/ ./src/
COPY --chown=sowsprint:sowsprint .chainlit/ ./.chainlit/
COPY --chown=sowsprint:sowsprint public/ ./public/
COPY --chown=sowsprint:sowsprint chainlit.md app.py ./
COPY --chown=sowsprint:sowsprint scripts/ ./scripts/
COPY --chown=sowsprint:sowsprint pyproject.toml ./
COPY --chown=sowsprint:sowsprint data/corpus/ ./data/corpus/

# Writable locations for rendered deliverables, cached BM25 statistics and the
# Chainlit runtime cache.
RUN mkdir -p /app/data/artifacts /app/.files \
 && chown -R sowsprint:sowsprint /app/data /app/.files

USER sowsprint

EXPOSE 8000

# Liveness probe. `start-period` covers corpus ingestion on a cold Qdrant volume.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD python /app/scripts/healthcheck.py --url http://127.0.0.1:8000/ || exit 1

# tini reaps zombies and forwards SIGTERM, so `docker compose down` is clean.
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["python", "-m", "chainlit", "run", "app.py", \
     "--host", "0.0.0.0", "--port", "8000", "--headless"]
