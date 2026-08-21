"""Adapter behaviour without the real binary: parsing, safety, and graceful absence."""

from __future__ import annotations

import pytest

from app.core.enums import TargetType
from app.providers.external_tool import ToolResult, ToolUnavailable, validate_argument
from app.providers.types import Target

TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

REPORT = {
    "GitHub": {
        "url_user": "https://github.com/example_user",
        "status": {
            "status": "Claimed",
            "ids": {
                "username": "example_user",
                "fullname": "Example User",
                "email": "e@example.com",
                "website": "example.com",
            },
        },
    },
    "SomewhereElse": {"url_user": "https://elsewhere.test/example_user",
                      "status": {"status": "Available"}},
    "Malformed": "not a dict",
}


async def test_absent_binary_is_reported_not_raised(load_provider) -> None:
    provider = load_provider("maigret")
    health = await provider.health_check()
    assert health.state in ("ok", "unavailable")
    if health.state == "unavailable":
        assert "not found on PATH" in health.detail


async def test_claimed_profiles_become_observations(load_provider, make_ctx, monkeypatch) -> None:
    provider = load_provider("maigret")
    module = type(provider).__module__

    async def fake_run(binary, args, timeout):
        assert "example_user" in args, "the handle must be passed as its own argv element"
        assert all(not a.startswith("&&") for a in args)
        return ToolResult(stdout="", stderr="", returncode=0, files=[REPORT])

    monkeypatch.setattr(f"{module}.run_tool", fake_run)
    observations = await provider.search(TARGET, make_ctx(lambda r: None))

    assert [o.data["platform"] for o in observations] == ["GitHub"]
    hit = observations[0]
    assert hit.data["email"] == "e@example.com"
    assert ("email", "e@example.com") in hit.derived_targets
    assert ("domain", "example.com") in hit.derived_targets
    assert any(e.target_kind == "email" for e in hit.edges)


async def test_missing_tool_returns_empty(load_provider, make_ctx, monkeypatch) -> None:
    provider = load_provider("maigret")
    module = type(provider).__module__

    async def missing(binary, args, timeout):
        raise ToolUnavailable("maigret is not installed on this host")

    monkeypatch.setattr(f"{module}.run_tool", missing)
    assert await provider.search(TARGET, make_ctx(lambda r: None)) == []


@pytest.mark.parametrize(
    "hostile",
    ["ex; rm -rf /", "ex && curl evil", "ex$(id)", "ex`id`", "ex|tee /tmp/x", "ex\nid"],
)
def test_shell_metacharacters_are_refused_before_argv(hostile: str) -> None:
    with pytest.raises(ValueError, match="unsafe value"):
        validate_argument(hostile)
