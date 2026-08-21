from __future__ import annotations

from app.core.enums import Assertion, TargetType
from app.providers.external_tool import ToolResult, ToolUnavailable
from app.providers.types import Target

TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

STDOUT = """[*] Checking username example_user on:
[+] GitHub: https://github.com/example_user
[+] Reddit: https://www.reddit.com/user/example_user
[-] Facebook: Not Found!
Total Websites Username Detected On : 2
"""


async def test_parses_found_lines_only(load_provider, make_ctx, monkeypatch) -> None:
    provider = load_provider("sherlock")
    module = type(provider).__module__

    async def fake_run(binary, args, timeout):
        return ToolResult(stdout=STDOUT, stderr="", returncode=0)

    monkeypatch.setattr(f"{module}.run_tool", fake_run)
    observations = await provider.search(TARGET, make_ctx(lambda r: None))

    urls = {o.url for o in observations}
    assert urls == {
        "https://github.com/example_user",
        "https://www.reddit.com/user/example_user",
    }
    assert all(o.assertion is Assertion.UNVERIFIED for o in observations), (
        "Sherlock reports presence only; the platform must not present that as observed"
    )
    assert {o.data["platform"] for o in observations} == {"GitHub", "Reddit"}


async def test_absent_binary_yields_nothing(load_provider, make_ctx, monkeypatch) -> None:
    provider = load_provider("sherlock")
    module = type(provider).__module__

    async def missing(binary, args, timeout):
        raise ToolUnavailable("sherlock is not installed on this host")

    monkeypatch.setattr(f"{module}.run_tool", missing)
    assert await provider.search(TARGET, make_ctx(lambda r: None)) == []
