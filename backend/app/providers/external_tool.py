"""Shared machinery for the optional external-CLI adapters.

Security posture (threat model B4):

* the binary is located with ``shutil.which`` — never taken from user input
* execution is ``asyncio.create_subprocess_exec`` with an **argv list**; there is no
  shell, so no metacharacter in a target value can become a command
* the target is validated against a strict charset before it reaches argv
* the process runs in a throwaway working directory, with a hard timeout and a
  process-group kill on expiry
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.providers.base import SAFE_TARGET_RE


class ToolUnavailable(RuntimeError):
    pass


@dataclass
class ToolResult:
    stdout: str
    stderr: str
    returncode: int
    #: JSON reports the tool wrote into its working directory, read before cleanup.
    files: list[dict[str, Any]] = field(default_factory=list)

    def json_payload(self) -> dict[str, Any] | list[Any] | None:
        """Parse stdout as JSON, for tools that report on the pipe rather than to a file."""
        text = self.stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
            if start < 0:
                return None
            try:
                return json.loads(text[start:])
            except ValueError:
                return None


def find_binary(name: str) -> str | None:
    return shutil.which(name)


def validate_argument(value: str) -> str:
    if not SAFE_TARGET_RE.match(value):
        raise ValueError(f"refusing to pass an unsafe value to an external tool: {value!r}")
    return value


async def run_tool(binary: str, args: list[str], timeout: float) -> ToolResult:  # noqa: ASYNC109
    """Run ``binary`` with ``args`` (argv list, no shell) inside a temp directory."""
    resolved = find_binary(binary)
    if resolved is None:
        raise ToolUnavailable(f"{binary} is not installed on this host")

    for arg in args:
        if arg.startswith("-"):
            continue
        validate_argument(arg)

    with tempfile.TemporaryDirectory(prefix="graphintel-tool-") as tmp:
        workdir = Path(tmp)
        process = await asyncio.create_subprocess_exec(
            resolved,
            *args,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": str(workdir),
                "LC_ALL": "C.UTF-8",
                "PYTHONIOENCODING": "utf-8",
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                await process.wait()
            raise TimeoutError(f"{binary} exceeded {timeout}s") from None

        # Reports must be read before the temp directory is removed.
        return ToolResult(
            stdout=stdout.decode("utf-8", "replace"),
            stderr=stderr.decode("utf-8", "replace"),
            returncode=process.returncode or 0,
            files=_read_json_reports(workdir),
        )


def _read_json_reports(workdir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(workdir.rglob("*.json"))[:50]:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                parsed = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out
