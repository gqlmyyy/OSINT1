"""SQLAlchemy models. Importing this package registers every mapper."""

from app.models.audit import AuditLog
from app.models.base import Timestamped, UUIDPk, utcnow
from app.models.evidence import Evidence, Observation
from app.models.graph import Entity, Identifier, IdentityCandidate, Relationship
from app.models.investigation import Investigation, Note, Tag, Target
from app.models.job import Job, ProviderRun
from app.models.linked_account import LinkedAccount, LinkedAccountRevocation, OAuthState
from app.models.source import Source
from app.models.user import User

__all__ = [
    "AuditLog",
    "Entity",
    "Evidence",
    "Identifier",
    "IdentityCandidate",
    "Investigation",
    "Job",
    "LinkedAccount",
    "LinkedAccountRevocation",
    "Note",
    "OAuthState",
    "Observation",
    "ProviderRun",
    "Relationship",
    "Source",
    "Tag",
    "Target",
    "Timestamped",
    "UUIDPk",
    "User",
    "utcnow",
]
