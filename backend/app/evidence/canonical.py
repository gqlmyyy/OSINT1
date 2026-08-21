"""Canonical key derivation — the single source of truth for entity deduplication."""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.enums import EntityType
from app.evidence import normalizer as n

#: Observation ``kind`` values a provider may emit, mapped to a graph node type.
KIND_TO_ENTITY: dict[str, EntityType] = {
    "username": EntityType.USERNAME,
    "email": EntityType.EMAIL,
    "phone": EntityType.PHONE,
    "social_account": EntityType.SOCIAL_ACCOUNT,
    "domain": EntityType.DOMAIN,
    "subdomain": EntityType.DOMAIN,
    "url": EntityType.URL,
    "website": EntityType.WEBSITE,
    "ip": EntityType.IP,
    "repository": EntityType.REPOSITORY,
    "organization": EntityType.ORGANIZATION,
    "company": EntityType.COMPANY,
    "location": EntityType.LOCATION,
    "avatar": EntityType.AVATAR,
    "image": EntityType.IMAGE,
    "person": EntityType.PERSON,
    "hash": EntityType.CRYPTO_HASH,
    "technology": EntityType.TECHNOLOGY,
    "display_name": EntityType.PERSON,
    "full_name": EntityType.PERSON,
}


class CanonicalError(ValueError):
    pass


def entity_type_for(kind: str) -> EntityType:
    try:
        return KIND_TO_ENTITY[kind]
    except KeyError as exc:
        raise CanonicalError(f"unknown observation kind: {kind!r}") from exc


def canonical_key(kind: str, value: str, attributes: dict[str, Any] | None = None) -> str:
    """Derive the dedup key. Never accepts a caller-supplied key."""
    attributes = attributes or {}
    entity_type = entity_type_for(kind)
    value = value.strip()
    if not value:
        raise CanonicalError("cannot derive a canonical key from an empty value")

    if entity_type is EntityType.SOCIAL_ACCOUNT:
        platform = n.normalize_text(str(attributes.get("platform") or "unknown")).replace(" ", "")
        handle = n.normalize_username(str(attributes.get("username") or value))
        return f"{platform}:user:{handle}"

    if entity_type is EntityType.REPOSITORY:
        platform = n.normalize_text(str(attributes.get("platform") or "git")).replace(" ", "")
        full_name = str(attributes.get("full_name") or value).strip().lower().strip("/")
        return f"{platform}:repo:{full_name}"

    if entity_type is EntityType.USERNAME:
        return f"username:{n.normalize_username(value)}"
    if entity_type is EntityType.EMAIL:
        return f"email:{n.normalize_email(value)}"
    if entity_type is EntityType.PHONE:
        return f"phone:{n.normalize_phone(value)}"
    if entity_type in (EntityType.DOMAIN, EntityType.WEBSITE):
        prefix = "domain" if entity_type is EntityType.DOMAIN else "website"
        return f"{prefix}:{n.normalize_domain(value)}"
    if entity_type is EntityType.IP:
        return f"ip:{n.normalize_ip(value)}"
    if entity_type is EntityType.URL:
        digest = hashlib.sha1(n.normalize_url(value).encode(), usedforsecurity=False).hexdigest()
        return f"url:{digest}"
    if entity_type in (EntityType.AVATAR, EntityType.IMAGE):
        image_hash = attributes.get("sha256") or attributes.get("hash")
        if image_hash:
            return f"avatar:{str(image_hash).lower()}"
        return f"avatar:{hashlib.sha256(n.normalize_url(value).encode()).hexdigest()}"
    if entity_type is EntityType.CRYPTO_HASH:
        algo = str(attributes.get("algo") or "sha256").lower()
        return f"hash:{algo}:{n.normalize_hash(value)}"
    if entity_type is EntityType.TECHNOLOGY:
        return f"tech:{n.normalize_text(value).replace(' ', '-')}"
    if entity_type in (EntityType.ORGANIZATION, EntityType.COMPANY):
        return f"org:{n.normalize_text(value)}"
    if entity_type is EntityType.LOCATION:
        return f"location:{n.normalize_text(value)}"
    if entity_type is EntityType.PERSON:
        return f"person:{n.normalize_text(value)}"
    raise CanonicalError(f"no canonical rule for {entity_type}")


def label_for(kind: str, value: str, attributes: dict[str, Any] | None = None) -> str:
    attributes = attributes or {}
    entity_type = entity_type_for(kind)
    if entity_type is EntityType.SOCIAL_ACCOUNT:
        platform = attributes.get("platform") or "account"
        handle = attributes.get("username") or value
        return f"{platform}/{handle}"
    if entity_type is EntityType.REPOSITORY:
        return str(attributes.get("full_name") or value)
    if entity_type is EntityType.URL:
        return value if len(value) <= 90 else value[:87] + "..."
    return value


def relationship_dedupe_key(source_key: str, rel_type: str, target_key: str) -> str:
    payload = f"{source_key}|{rel_type}|{target_key}".encode()
    return hashlib.sha256(payload).hexdigest()[:64]
