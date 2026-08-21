FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is the healthcheck; nothing else is added to keep the attack surface small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
RUN pip install --no-cache-dir /app/backend

COPY backend /app/backend
COPY plugins /app/plugins
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# The application never needs to write to its own code, so it runs unprivileged.
RUN useradd --create-home --uid 10001 graphintel \
    && chown -R graphintel:graphintel /app
USER graphintel

ENV PYTHONPATH=/app/backend \
    PLUGINS_PATH=/app/plugins

WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=25s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["entrypoint.sh"]
CMD ["api"]
