from __future__ import annotations

import httpx
import pytest

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.USERNAME, value="opensourcenews", normalized="opensourcenews")

#: A close approximation of Telegram's public channel preview markup
#: (t.me/s/<channel>), used because live t.me is not reachable from this test
#: environment. Structure follows the well-documented public format widely relied on
#: by open-source Telegram-preview readers.
CHANNEL_PAGE = """
<!DOCTYPE html><html><head><title>Open Source News</title></head><body>
<div class="tgme_channel_info">
  <div class="tgme_channel_info_header">
    <a class="tgme_page_photo_image" href="/opensourcenews">
      <img src="https://cdn.telesco.pe/file/avatar123.jpg">
    </a>
    <div class="tgme_channel_info_header_title">
      <span dir="auto">Open Source News</span>
    </div>
  </div>
  <div class="tgme_channel_info_description">Daily #opensource and #osint links. Contact @editor_bot</div>
  <div class="tgme_channel_info_counters">
    <div class="tgme_channel_info_counter">
      <span class="counter_value">12.4K</span><span class="counter_type">subscribers</span>
    </div>
  </div>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="opensourcenews/501">
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_text js-message_text" dir="auto">New release notes for #osint tools, see https://example.com/changelog cc @maintainer_x</div>
      <div class="tgme_widget_message_footer">
        <a class="tgme_widget_message_date" href="https://t.me/opensourcenews/501">
          <time class="time" datetime="2026-05-01T09:00:00+00:00">09:00</time>
        </a>
      </div>
    </div>
  </div>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="opensourcenews/502">
    <div class="tgme_widget_message_bubble">
      <div class="tgme_widget_message_text js-message_text" dir="auto">Weekly roundup #opensource</div>
      <div class="tgme_widget_message_footer">
        <a class="tgme_widget_message_date" href="https://t.me/opensourcenews/502">
          <time class="time" datetime="2026-05-08T09:00:00+00:00">09:00</time>
        </a>
      </div>
    </div>
  </div>
</div>
</body></html>
"""

NOT_A_CHANNEL_PAGE = "<!DOCTYPE html><html><body><div>Nothing here</div></body></html>"


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/s/opensourcenews":
        return httpx.Response(200, text=CHANNEL_PAGE)
    if request.url.path == "/s/privatechannel":
        return httpx.Response(200, text=NOT_A_CHANNEL_PAGE)
    return httpx.Response(404)


async def test_extracts_channel_profile_and_public_posts(load_provider, make_ctx) -> None:
    observations = await load_provider("telegram").search(TARGET, make_ctx(handler))

    profile = next(o for o in observations if o.kind == "social_account")
    assert profile.data["display_name"] == "Open Source News"
    assert "Daily #opensource" in profile.data["bio"]
    assert profile.data["avatar"] == "https://cdn.telesco.pe/file/avatar123.jpg"
    assert profile.data["subscribers"] == "12.4K"

    bio_edges = {(e.type, e.target_kind, e.target_value) for e in profile.edges}
    assert ("USES_HASHTAG", "hashtag", "opensource") in bio_edges
    assert ("USES_HASHTAG", "hashtag", "osint") in bio_edges
    assert ("MENTIONS", "social_account", "editor_bot") in bio_edges

    posts = [o for o in observations if o.kind == "post"]
    assert len(posts) == 2
    assert posts[0].url == "https://t.me/opensourcenews/501"
    assert posts[0].data["hashtags"] == ["osint"]
    assert posts[0].data["mentions"] == ["maintainer_x"]
    assert any(e.type == "LINKS_TO" for e in posts[0].edges)


async def test_channel_that_does_not_exist_or_is_private_yields_nothing(
    load_provider, make_ctx
) -> None:
    target = Target(type=TargetType.USERNAME, value="privatechannel", normalized="privatechannel")
    observations = await load_provider("telegram").search(target, make_ctx(handler))
    assert observations == []


async def test_404_yields_nothing(load_provider, make_ctx) -> None:
    target = Target(type=TargetType.USERNAME, value="doesnotexist99", normalized="doesnotexist99")
    assert await load_provider("telegram").search(target, make_ctx(handler)) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://t.me/opensourcenews", "opensourcenews"),
        ("https://t.me/s/opensourcenews", "opensourcenews"),
        ("@opensourcenews", "opensourcenews"),
        ("opensourcenews", "opensourcenews"),
        ("ab", None),  # too short to be a real channel handle
        ("https://example.com/notgram", None),
    ],
)
def test_channel_parsing(value: str, expected: str | None) -> None:
    from graphintel_plugins import social_telegram

    assert social_telegram.parse_channel(value) == expected


def test_no_join_or_private_access_machinery_in_the_provider() -> None:
    """Policy test: this provider must never gain a path to non-public content."""
    import ast
    from pathlib import Path

    source_path = Path(__file__).resolve().parents[1] / "provider.py"
    tree = ast.parse(source_path.read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    body = ast.unparse(tree).lower()

    forbidden = ["session_string", "bot_token", "invite", "join_chat", "mtproto", "telethon", "pyrogram"]
    present = [token for token in forbidden if token in body]
    assert not present, f"private-access machinery found in the provider: {present}"
