from __future__ import annotations

import hashlib

import httpx

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.EMAIL, value="a@example.com", normalized="a@example.com")
IMAGE = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20

PROFILE = {
    "entry": [
        {
            "preferredUsername": "example_user",
            "displayName": "Example User",
            "aboutMe": "engineer",
            "profileUrl": "https://gravatar.com/example_user",
            "accounts": [{"shortname": "github", "url": "https://github.com/example_user"}],
        }
    ]
}


def handler(request: httpx.Request) -> httpx.Response:
    if "/avatar/" in request.url.path:
        return httpx.Response(200, content=IMAGE, headers={"content-type": "image/png"})
    if request.url.path.endswith(".json"):
        return httpx.Response(200, json=PROFILE)
    return httpx.Response(404)


async def test_hashes_the_image_bytes_for_correlation(load_provider, make_ctx) -> None:
    provider = load_provider("gravatar")
    observations = await provider.search(TARGET, make_ctx(handler))

    avatar = next(o for o in observations if o.kind == "avatar")
    assert avatar.data["sha256"] == hashlib.sha256(IMAGE).hexdigest()
    assert avatar.edges[0].target_kind == "email"

    account = next(o for o in observations if o.kind == "social_account")
    assert account.data["username"] == "example_user"
    assert account.data["avatar_hash"] == avatar.data["sha256"]
    assert account.edges[0].target_value == "https://github.com/example_user"


async def test_absent_avatar_yields_nothing(load_provider, make_ctx) -> None:
    provider = load_provider("gravatar")
    assert await provider.search(TARGET, make_ctx(lambda r: httpx.Response(404))) == []
