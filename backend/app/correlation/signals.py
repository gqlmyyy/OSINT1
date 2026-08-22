"""Match signals.

A signal looks at two entities and says "these look alike because X". Each signal carries
a *likelihood ratio*: how much more often this agreement happens between two accounts of
the same person than between two unrelated accounts. Ratios above 1 are evidence for a
match, below 1 against. The engine combines them in log space, which is why the weights
compose instead of saturating the way an additive point score does.

Adding probabilistic matching, embeddings, or image similarity later means adding a
``MatchSignal`` here — the engine does not change.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.core.enums import EntityType
from app.evidence import normalizer as norm
from app.identity import avatar, rarity


@dataclass(frozen=True)
class EntityView:
    """The subset of an entity a signal is allowed to see."""

    id: str
    type: str
    label: str
    canonical_key: str
    attributes: dict[str, Any]
    identifiers: dict[str, set[str]]
    sources: list[str]

    def ids(self, kind: str) -> set[str]:
        return self.identifiers.get(kind, set())

    def attr(self, key: str) -> str | None:
        value = self.attributes.get(key)
        return str(value) if isinstance(value, (str, int, float)) and str(value).strip() else None


@dataclass(frozen=True)
class SignalOutcome:
    name: str
    fired: bool
    likelihood_ratio: float
    reason: str
    direct_evidence: bool = False

    @property
    def log_odds(self) -> float:
        return math.log(self.likelihood_ratio)


class MatchSignal(ABC):
    name: str = "signal"
    #: Likelihood ratio applied when the signal agrees.
    positive_lr: float = 2.0
    #: Applied when the signal is *contradicted* (both sides present and different).
    negative_lr: float = 1.0
    #: True when agreement is a directly observed fact rather than a heuristic.
    direct_evidence: bool = False

    @abstractmethod
    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        """Return an outcome, or ``None`` when the signal cannot be judged."""

    def _hit(self, reason: str) -> SignalOutcome:
        return SignalOutcome(
            self.name, True, self.positive_lr, reason, direct_evidence=self.direct_evidence
        )

    def _miss(self, reason: str) -> SignalOutcome | None:
        if self.negative_lr == 1.0:
            return None
        return SignalOutcome(self.name, False, self.negative_lr, reason)


class SharedIdentifierSignal(MatchSignal):
    kind = "username"

    def _values(self, view: EntityView) -> set[str]:
        return view.ids(self.kind)

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = self._values(a), self._values(b)
        if not left or not right:
            return None
        shared = left & right
        if shared:
            return self._hit(f"same {self.kind}: {sorted(shared)[0]}")
        return self._miss(f"different {self.kind}")


class SameUsernameSignal(SharedIdentifierSignal):
    """A shared handle, weighted by how contested that handle is.

    Two accounts sharing `johnsmith` and two accounts sharing `xk7q_zephyr` are not
    equally good evidence, and treating them alike is the single largest source of false
    identity matches in username-driven OSINT. The base likelihood ratio is therefore
    multiplied by a bounded rarity factor (see :mod:`app.identity.rarity`).

    What the modifier is *not* allowed to do, by construction:

    * it cannot introduce a signal — no shared handle, no outcome;
    * it cannot make a username into direct evidence, so it can never gate ``CONFIRMED``;
    * it cannot lift a username-only pair past "possible" — the ceiling is
      ``base_lr x MAX_FACTOR``, which
      :func:`app.identity.rarity.max_username_only_score` evaluates to ~0.66 against the
      engine's prior, below the 0.70 "probable" threshold.

    The Bayesian core is untouched: this changes one likelihood ratio, not how ratios
    combine.
    """

    name = "same_username"
    kind = "username"
    positive_lr = 6.0
    negative_lr = 0.85

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = self._values(a), self._values(b)
        if not left or not right:
            return None
        shared = left & right
        if not shared:
            return self._miss(f"different {self.kind}")

        handle = sorted(shared)[0]
        assessment = rarity.assess(handle)
        weighted_lr = self.positive_lr * assessment.factor
        return SignalOutcome(
            self.name,
            True,
            weighted_lr,
            f"same username: {handle} ({assessment.label} handle - {assessment.reason})",
            direct_evidence=self.direct_evidence,
        )


class SameEmailSignal(SharedIdentifierSignal):
    name = "same_email"
    kind = "email"
    positive_lr = 40.0
    direct_evidence = True


class SameAvatarSignal(MatchSignal):
    name = "same_avatar"
    positive_lr = 18.0
    direct_evidence = True

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left = a.attr("avatar_hash") or a.attr("avatar_sha256")
        right = b.attr("avatar_hash") or b.attr("avatar_sha256")
        if not left or not right:
            return None
        if left.lower() == right.lower():
            return self._hit(f"identical avatar image hash ({left[:12]}...)")
        return None


class SimilarAvatarSignal(MatchSignal):
    """The same *picture* used on both accounts, after re-encoding.

    Platforms resize and re-compress avatars on upload, so the same photograph has a
    different byte hash everywhere — which is why :class:`SameAvatarSignal` alone misses
    most real cases. A perceptual hash closes that gap.

    Three properties make this safe to act on, and all three are deliberate:

    * ``direct_evidence = False``. A perceptual match says two accounts show the same
      image, not that one person controls both — the image could be a stock photo, a
      meme, a band logo or a platform default. So this signal can raise a score but can
      never gate the ``CONFIRMED`` band, which still requires a directly observed shared
      artefact.
    * The likelihood ratio is well below the exact-hash signal's, because "visually the
      same file" is weaker evidence than "byte-identical file".
    * It stays quiet in the ambiguous middle. Beyond the same-image threshold it emits
      nothing rather than a weak match: a vaguely similar avatar is not evidence.

    Skipped entirely when the exact hashes already agree, so one shared avatar cannot be
    counted twice.
    """

    name = "same_image_candidate"
    positive_lr = 5.0
    direct_evidence = False  # a picture is not a person; see the class docstring

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        exact_left = a.attr("avatar_hash") or a.attr("avatar_sha256")
        exact_right = b.attr("avatar_hash") or b.attr("avatar_sha256")
        if exact_left and exact_right and exact_left.lower() == exact_right.lower():
            return None  # SameAvatarSignal already covers this, at full weight

        left, right = a.attr("avatar_phash"), b.attr("avatar_phash")
        if not left or not right:
            return None
        try:
            distance = avatar.hamming_distance(left, right)
        except ValueError:
            return None
        if distance > avatar.SAME_IMAGE_THRESHOLD:
            return None
        return self._hit(
            f"same image candidate: profile pictures match perceptually "
            f"({avatar.similarity(left, right):.0%} of bits, distance {distance}). "
            "This identifies the picture, not the person."
        )


class SameWebsiteSignal(MatchSignal):
    name = "same_website"
    positive_lr = 14.0
    direct_evidence = True

    @staticmethod
    def _site(view: EntityView) -> str | None:
        raw = view.attr("website") or view.attr("blog") or view.attr("homepage")
        if not raw:
            return None
        try:
            return norm.normalize_domain(raw)
        except norm.NormalizationError:
            return None

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = self._site(a), self._site(b)
        if not left or not right:
            return None
        if left == right:
            return self._hit(f"both declare the same public website: {left}")
        return None


class UsernameVariantSignal(MatchSignal):
    name = "username_variant"
    positive_lr = 2.4

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = a.ids("username"), b.ids("username")
        if not left or not right or (left & right):
            return None
        left_variants = {v for value in left for v in norm.username_variants(value)}
        right_variants = {v for value in right for v in norm.username_variants(value)}
        shared = left_variants & right_variants
        if shared:
            return self._hit(f"username variants overlap ({sorted(shared)[0]})")
        return None


class SameDisplayNameSignal(MatchSignal):
    name = "same_display_name"
    positive_lr = 2.2

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = a.attr("display_name"), b.attr("display_name")
        if not left or not right:
            return None
        if norm.normalize_text(left) == norm.normalize_text(right):
            return self._hit(f"same display name: {left}")
        return None


class SameBioSignal(MatchSignal):
    name = "same_bio"
    positive_lr = 3.0
    negative_lr = 0.8

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = a.attr("bio"), b.attr("bio")
        if not left or not right or len(left) < 12 or len(right) < 12:
            return None
        ln, rn = norm.normalize_text(left), norm.normalize_text(right)
        if ln == rn:
            return self._hit("identical public bio text")
        overlap = _jaccard(set(ln.split()), set(rn.split()))
        if overlap >= 0.6:
            return self._hit(f"public bios overlap {overlap:.0%}")
        if overlap < 0.1:
            return self._miss("public bios differ")
        return None


class SameRepositorySignal(MatchSignal):
    name = "same_repository"
    positive_lr = 9.0
    direct_evidence = True

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        left, right = a.ids("repository"), b.ids("repository")
        if not left or not right:
            return None
        shared = left & right
        return self._hit(f"same repository: {sorted(shared)[0]}") if shared else None


class SharedExternalLinkSignal(MatchSignal):
    name = "shared_external_link"
    positive_lr = 7.0
    direct_evidence = True

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        shared = a.ids("url") & b.ids("url")
        return self._hit(f"both link to {sorted(shared)[0]}") if shared else None


class DifferentPlatformSignal(MatchSignal):
    """Two accounts on the *same* platform are usually different people."""

    name = "same_platform_penalty"
    negative_lr = 0.45

    def evaluate(self, a: EntityView, b: EntityView) -> SignalOutcome | None:
        if a.type != EntityType.SOCIAL_ACCOUNT or b.type != EntityType.SOCIAL_ACCOUNT:
            return None
        left, right = a.attr("platform"), b.attr("platform")
        if left and right and left.lower() == right.lower():
            return self._miss(f"both accounts are on {left}, which usually means two people")
        return None


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


DEFAULT_SIGNALS: list[MatchSignal] = [
    SameEmailSignal(),
    SameAvatarSignal(),
    SimilarAvatarSignal(),
    SameWebsiteSignal(),
    SameRepositorySignal(),
    SharedExternalLinkSignal(),
    SameUsernameSignal(),
    UsernameVariantSignal(),
    SameDisplayNameSignal(),
    SameBioSignal(),
    DifferentPlatformSignal(),
]
