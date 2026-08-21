"""Input detection and value normalization.

One place decides what a raw string *is* and what its canonical form looks like, so the
same identifier typed three different ways lands on one node.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import idna

from app.core.enums import TargetType

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9\-._]+\.[A-Za-z]{2,63}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s().\-]{6,24}$")
HASH_RE = re.compile(r"^[A-Fa-f0-9]{32}$|^[A-Fa-f0-9]{40}$|^[A-Fa-f0-9]{64}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")

#: Tracking parameters removed before a URL becomes a canonical key.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "ref",
        "ref_src",
        "igshid",
        "mc_cid",
        "mc_eid",
    }
)


class NormalizationError(ValueError):
    pass


def detect_type(raw: str) -> TargetType:
    """Best-effort classification of an analyst-typed identifier."""
    value = raw.strip()
    if not value:
        raise NormalizationError("empty value")
    if URL_RE.match(value):
        return TargetType.URL
    if value.startswith("@") and USERNAME_RE.match(value[1:]):
        return TargetType.USERNAME
    if EMAIL_RE.match(value):
        return TargetType.EMAIL
    try:
        ipaddress.ip_address(value)
        return TargetType.IP
    except ValueError:
        pass
    if HASH_RE.match(value):
        return TargetType.HASH
    if value.startswith(("www.", "http")) or DOMAIN_RE.match(value):
        return TargetType.DOMAIN
    if PHONE_RE.match(value) and sum(c.isdigit() for c in value) >= 7:
        return TargetType.PHONE
    if USERNAME_RE.match(value):
        return TargetType.USERNAME
    if " " in value:
        return TargetType.FULL_NAME
    return TargetType.DISPLAY_NAME


def normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def username_variants(value: str) -> list[str]:
    """The `example_user` / `example.user` / `example-user` family (spec 5)."""
    base = normalize_username(value)
    core = re.sub(r"[._\-]", "", base)
    variants = {base, core}
    for sep in ("_", ".", "-"):
        variants.add(base.replace("_", sep).replace(".", sep).replace("-", sep))
    stripped = re.sub(r"\d+$", "", base)
    if stripped and stripped != base:
        variants.add(stripped)
    return sorted(v for v in variants if v)


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value):
        raise NormalizationError(f"not an email address: {value!r}")
    local, _, domain = value.partition("@")
    return f"{local}@{normalize_domain(domain)}"


def normalize_domain(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if URL_RE.match(host):
        host = urlsplit(host).hostname or host
    host = host.removeprefix("www.")
    if not host:
        raise NormalizationError("empty domain")
    try:
        return idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        if not DOMAIN_RE.match(host):
            raise NormalizationError(f"not a domain: {value!r}") from None
        return host


def normalize_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise NormalizationError(f"not an IP address: {value!r}") from exc


def normalize_phone(value: str) -> str:
    digits = re.sub(r"[^\d+]", "", value.strip())
    if not digits:
        raise NormalizationError(f"not a phone number: {value!r}")
    return digits if digits.startswith("+") else f"+{digits.lstrip('+')}"


def normalize_url(value: str) -> str:
    """Lowercase host, drop default port, strip tracking params and fragment."""
    raw = value.strip()
    if not URL_RE.match(raw):
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise NormalizationError(f"unsupported URL scheme: {parts.scheme}")
    host = (parts.hostname or "").lower()
    if not host:
        raise NormalizationError(f"URL has no host: {value!r}")
    try:
        host = idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        pass
    netloc = host
    if parts.port and not ((parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)):
        netloc = f"{host}:{parts.port}"
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    )
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def normalize_hash(value: str) -> str:
    return value.strip().lower()


_NORMALIZERS = {
    TargetType.USERNAME: normalize_username,
    TargetType.EMAIL: normalize_email,
    TargetType.DOMAIN: normalize_domain,
    TargetType.URL: normalize_url,
    TargetType.IP: normalize_ip,
    TargetType.PHONE: normalize_phone,
    TargetType.HASH: normalize_hash,
    TargetType.DISPLAY_NAME: normalize_text,
    TargetType.FULL_NAME: normalize_text,
    TargetType.COMPANY: normalize_text,
}


def normalize(value: str, target_type: TargetType | None = None) -> tuple[TargetType, str]:
    """Return ``(type, normalized_value)`` for an analyst-supplied identifier."""
    raw = value.strip()
    if not raw:
        raise NormalizationError("empty value")
    if len(raw) > 512:
        raise NormalizationError("value too long (max 512 characters)")
    detected = target_type or detect_type(raw)
    normalizer = _NORMALIZERS.get(detected, normalize_text)
    return detected, normalizer(raw)


def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def md5_hex(value: str) -> str:
    """Gravatar identifiers are MD5 by specification; used only as a lookup key."""
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()
