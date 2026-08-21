from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.evidence.canonical import CanonicalError
from app.evidence.normalizer import NormalizationError
from app.graph.store import EvidenceRequired
from app.security.auth import TokenError
from app.security.ssrf import SSRFBlocked

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": {
                    "code": "validation_error",
                    "message": "request failed validation",
                    "fields": [
                        {"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()
                    ],
                }
            },
        )

    @app.exception_handler(TokenError)
    async def _token(_: Request, exc: TokenError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(SSRFBlocked)
    async def _ssrf(_: Request, exc: SSRFBlocked) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": {"code": "egress_blocked", "message": str(exc)}},
        )

    @app.exception_handler(NormalizationError)
    async def _normalization(_: Request, exc: NormalizationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": {"code": "invalid_identifier", "message": str(exc)}},
        )

    @app.exception_handler(CanonicalError)
    async def _canonical(_: Request, exc: CanonicalError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": {"code": "canonicalization_failed", "message": str(exc)}},
        )

    @app.exception_handler(EvidenceRequired)
    async def _evidence(_: Request, exc: EvidenceRequired) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": {"code": "evidence_required", "message": str(exc)}},
        )

    @app.exception_handler(LookupError)
    async def _lookup(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})
