FROM node:22-alpine AS build
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
# Probe 127.0.0.1, never the name "localhost". nginx binds 0.0.0.0:80 (IPv4), and this
# image only auto-adds an IPv6 listener to its *stock* config -- ours replaces it, so
# that never happens. On a host with IPv6 (Docker Desktop) "localhost" can resolve to
# ::1, where nothing is listening, and the probe fails with ECONNREFUSED while nginx is
# serving every real request perfectly well.
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1:80/ >/dev/null 2>&1 || exit 1
