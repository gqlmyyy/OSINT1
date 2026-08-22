"""TikTok provider: declared-only stub. Always unavailable, never scrapes."""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.enums import TargetType
from app.providers.types import Target

PROVIDER_SOURCE = Path(__file__).resolve().parents[1] / "provider.py"
TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")


def test_registers_and_loads(load_provider) -> None:
    provider = load_provider("tiktok")
    assert provider.platform == "TikTok"


async def test_always_reports_unavailable(load_provider, monkeypatch) -> None:
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
    health = await load_provider("tiktok").health_check()
    assert health.state == "unavailable"
    assert "TIKTOK_ACCESS_TOKEN" in health.detail


async def test_search_always_returns_nothing(load_provider, make_ctx) -> None:
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("tiktok stub must never make a network request")

    assert await load_provider("tiktok").search(TARGET, make_ctx(handler)) == []


def test_declares_that_it_requires_a_key(load_provider) -> None:
    assert load_provider("tiktok").capabilities().requires_api_key is True


def test_provider_contains_no_scraping_path() -> None:
    tree = ast.parse(PROVIDER_SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    body = ast.unparse(tree).lower()

    forbidden = ["cookie", "captcha", "selenium", "playwright", "webdriver", "signature", "msToken"]
    present = [token for token in forbidden if token.lower() in body]
    assert not present, f"scraping machinery found in the provider: {present}"
