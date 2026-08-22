"""Security headers and per-caller API rate limiting."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.security.auth import TokenError, decode_token
from app.security.ratelimit import TokenBucket

Handler = Callable[[Request], Awaitable[Response]]

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    # Narrowly scoped for the GPS-coordinate map embed in the entity inspector
    # (image_geo results). Nothing else in the frontend uses an iframe.
    "frame-src https://www.openstreetmap.org; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Content-Security-Policy": CSP,
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user when authenticated, per-IP otherwise."""

    def __init__(self, app: object, requests_per_minute: int | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        rpm = requests_per_minute or get_settings().api_rate_limit_per_minute
        self.bucket = TokenBucket(rate_per_minute=rpm, burst=max(rpm // 2, 20))

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        if request.url.path in ("/health", "/api/docs", "/api/openapi.json"):
            return await call_next(request)

        key = _caller_key(request)
        if not await self.bucket.try_acquire(key):
            retry = int(await self.bucket.retry_after(key)) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": {"code": "rate_limited", "message": "too many requests"}},
                headers={"Retry-After": str(retry), **SECURITY_HEADERS},
            )
        return await call_next(request)


def _caller_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        try:
            return f"user:{decode_token(header[7:], 'access').subject}"
        except TokenError:
            pass
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"
