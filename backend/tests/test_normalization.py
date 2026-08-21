from __future__ import annotations

import pytest

from app.core.enums import TargetType
from app.evidence.canonical import canonical_key, relationship_dedupe_key
from app.evidence.normalizer import (
    NormalizationError,
    detect_type,
    normalize,
    normalize_url,
    username_variants,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example_user", TargetType.USERNAME),
        ("@example_user", TargetType.USERNAME),
        ("a@b.com", TargetType.EMAIL),
        ("example.com", TargetType.DOMAIN),
        ("https://example.com/x", TargetType.URL),
        ("8.8.8.8", TargetType.IP),
        ("2001:4860:4860::8888", TargetType.IP),
        ("+1 555 010 9999", TargetType.PHONE),
        ("Jane Q Public", TargetType.FULL_NAME),
        ("d41d8cd98f00b204e9800998ecf8427e", TargetType.HASH),
    ],
)
def test_detect_type(raw: str, expected: TargetType) -> None:
    assert detect_type(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example_User", "example_user"),
        ("  @Example_User ", "example_user"),
        ("A@B.COM", "a@b.com"),
        ("WWW.Example.COM", "example.com"),
    ],
)
def test_normalize_values(raw: str, expected: str) -> None:
    assert normalize(raw)[1] == expected


def test_scheme_makes_it_a_url_not_a_domain() -> None:
    """A bare host is a domain; the same host with a scheme is a URL. Both are stable."""
    assert normalize("http://Example.com") == (TargetType.URL, "http://example.com/")
    assert normalize("Example.com", TargetType.DOMAIN) == (TargetType.DOMAIN, "example.com")


def test_url_normalization_strips_tracking_and_fragment() -> None:
    url = "HTTPS://Example.com:443/path/?utm_source=x&b=2&fbclid=y#section"
    assert normalize_url(url) == "https://example.com/path?b=2"


def test_url_normalization_keeps_non_default_port() -> None:
    assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"


def test_username_variants_cover_separator_and_suffix_forms() -> None:
    variants = set(username_variants("example_user01"))
    assert {"example_user01", "example.user01", "example-user01", "exampleuser01"} <= variants
    assert "example_user" in variants


def test_rejects_oversized_and_empty_values() -> None:
    with pytest.raises(NormalizationError):
        normalize("")
    with pytest.raises(NormalizationError):
        normalize("a" * 600)


def test_canonical_key_is_stable_across_casing_and_shape() -> None:
    a = canonical_key("social_account", "Example_User", {"platform": "GitHub"})
    b = canonical_key("social_account", "example_user", {"platform": "github"})
    c = canonical_key("social_account", "x", {"platform": "GITHUB", "username": "EXAMPLE_USER"})
    assert a == b == c == "github:user:example_user"


def test_canonical_key_separates_platforms() -> None:
    assert canonical_key("social_account", "u", {"platform": "GitHub"}) != canonical_key(
        "social_account", "u", {"platform": "Reddit"}
    )


def test_canonical_url_keys_ignore_tracking_parameters() -> None:
    assert canonical_key("url", "https://a.com/p?utm_source=1") == canonical_key(
        "url", "https://a.com/p"
    )


def test_relationship_dedupe_key_is_directional() -> None:
    forward = relationship_dedupe_key("a", "LINKS_TO", "b")
    backward = relationship_dedupe_key("b", "LINKS_TO", "a")
    assert forward != backward
    assert forward == relationship_dedupe_key("a", "LINKS_TO", "b")
