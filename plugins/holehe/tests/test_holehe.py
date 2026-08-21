from __future__ import annotations

from app.core.enums import Assertion, TargetType
from app.providers.external_tool import ToolResult
from app.providers.types import Target

TARGET = Target(type=TargetType.EMAIL, value="a@example.com", normalized="a@example.com")

STDOUT = """\x1b[92m[+]\x1b[0m Instagram
[+] Spotify
[-] Twitter
[x] Github rate limited
"""


async def test_parses_used_platforms_as_unverified(load_provider, make_ctx, monkeypatch) -> None:
    provider = load_provider("holehe")
    module = type(provider).__module__

    async def fake_run(binary, args, timeout):
        assert "--only-used" in args
        return ToolResult(stdout=STDOUT, stderr="", returncode=0)

    monkeypatch.setattr(f"{module}.run_tool", fake_run)
    observations = await provider.search(TARGET, make_ctx(lambda r: None))

    platforms = {o.data["platform"] for o in observations}
    assert platforms == {"Instagram", "Spotify"}
    assert all(o.assertion is Assertion.UNVERIFIED for o in observations)
    assert all("No profile content was retrieved" in o.excerpt for o in observations)


async def test_capabilities_do_not_feed_recursion(load_provider) -> None:
    """Registration-existence answers are too weak to seed further crawling."""
    provider = load_provider("holehe")
    assert provider.capabilities().recursive is False
