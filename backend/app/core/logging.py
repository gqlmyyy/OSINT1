"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from app.core.config import get_settings

#: Anything that looks like a credential is masked before it reaches a log sink.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub(a) for a in record.args)
        return True

    def _scrub(self, value: Any) -> Any:
        return self.redact(value) if isinstance(value, str) else value

    @staticmethod
    def redact(text: str) -> str:
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[redacted]", text)
        return text


def configure_logging() -> None:
    settings = get_settings()
    level = logging.DEBUG if settings.debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "asyncio", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
