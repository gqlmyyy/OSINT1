FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

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

# Probed with the interpreter already in the image: no apt layer, no extra package in
# the attack surface, and the build stops depending on reaching a Debian mirror -- which
# matters for the restricted and air-gapped networks this tool is meant to run in.
# 127.0.0.1 rather than "localhost" for the same IPv6 reason as the frontend.
HEALTHCHECK --interval=20s --timeout=5s --start-period=25s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; import sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["entrypoint.sh"]
CMD ["api"]
