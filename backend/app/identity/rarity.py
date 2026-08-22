"""How identifying is a username?

The problem this solves: before it existed, the correlation engine treated `johnsmith`
and `xk7q_zephyr` as equally strong evidence that two accounts belong to one person. They
are not remotely equal. Thousands of unrelated people register `johnsmith`; approximately
one registers `xk7q_zephyr`. Weighting them identically is how OSINT tools manufacture
confident nonsense at scale.

**What this is.** A deterministic, offline, structural estimate of how contested a handle
is, in [0, 1] — 0 meaning "everybody has this one", 1 meaning "this looks unique". No
network call, no external corpus, no per-deployment learning, so the same handle always
scores the same and every score can be explained by naming the rule that produced it.

**What this is not.** It is not a frequency measurement. We do not know how many people
actually hold a handle, and the model does not pretend to: it recognises *patterns*
associated with contested handles (a bare first name, a surname, a dictionary word, a
role account, a name with digits appended) and treats everything else as merely
unremarkable — never as proven-rare. An unknown handle lands mid-scale, not at the top,
because absence of evidence that a handle is common is not evidence that it is rare.

**Bounded by construction.** Rarity is only ever a *modifier* on an existing likelihood
ratio, clamped to :data:`MIN_FACTOR`..:data:`MAX_FACTOR`. It cannot introduce a signal,
cannot make a username into direct evidence, and — as
:func:`max_username_only_score` proves arithmetically — cannot on its own lift a
username-only pair past "possible". The Bayesian core is untouched.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.identity.wordlists import (
    ALL_TOKENS,
    COMMON_HANDLES,
    COMMON_WORDS,
    GIVEN_NAMES,
    NAME_TOKENS,
    NICKNAMES,
    SURNAMES,
)

#: Bounds on the multiplier applied to a username signal's likelihood ratio.
#: Deliberately asymmetric: a recognisably common handle is penalised hard (down to a
#: fifth of the base weight), while an unrecognised one is rewarded only modestly. The
#: asymmetry encodes the project's bias — being conservative costs a missed lead, being
#: permissive costs a false accusation about a real person.
MIN_FACTOR = 0.20
MAX_FACTOR = 1.80

#: Commonness is clamped away from the extremes: no handle is *certainly* unique, and
#: none is held by literally everyone.
MIN_COMMONNESS = 0.02
MAX_COMMONNESS = 0.98

#: Structural adjustments are capped in total, so a pile of small nudges cannot
#: overturn the rule that classified the handle.
MAX_STRUCTURAL_ADJUSTMENT = 0.25

_SEPARATORS = re.compile(r"[._\-\s]+")
_TRAILING_DIGITS = re.compile(r"\d+$")
_DIGITS = re.compile(r"\d")
#: Leetspeak substitutions, so `th3_gr1ndhouse` is compared against real words.
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s"})


@dataclass(frozen=True)
class RarityAssessment:
    """A rarity score plus the reason it came out that way.

    The reason is not decoration: it is what lets the UI tell a user *why* their handle
    is a correlation risk, and what lets a reviewer disagree with the model.
    """

    handle: str
    #: 0 = extremely common, 1 = looks unique.
    rarity: float
    #: The rule that classified it, in plain language.
    reason: str
    #: Multiplier applied to the username signal's likelihood ratio.
    factor: float

    @property
    def is_common(self) -> bool:
        """True when the handle is contested enough to be weak identity evidence."""
        return self.rarity < 0.35

    @property
    def label(self) -> str:
        if self.rarity < 0.20:
            return "very common"
        if self.rarity < 0.35:
            return "common"
        if self.rarity < 0.65:
            return "unremarkable"
        if self.rarity < 0.85:
            return "distinctive"
        return "highly distinctive"


def _normalise(handle: str) -> str:
    return _SEPARATORS.sub("", handle.strip().lower().lstrip("@"))


def _stem(core: str) -> str:
    """Drop trailing digits: `david99` is the handle `david` with noise attached."""
    stripped = _TRAILING_DIGITS.sub("", core)
    return stripped or core


def _split_into_known_tokens(text: str, vocabulary: frozenset[str]) -> list[str] | None:
    """Greedy longest-first decomposition of ``text`` into vocabulary words.

    Returns ``None`` when the text does not decompose cleanly. Greedy rather than exact:
    a wrong split occasionally misses a decomposition, which errs toward treating a
    handle as unremarkable — the safe direction.
    """
    tokens: list[str] = []
    index = 0
    while index < len(text):
        for end in range(len(text), index + 2, -1):  # words of 3+ characters
            candidate = text[index:end]
            if candidate in vocabulary:
                tokens.append(candidate)
                index = end
                break
        else:
            return None
    return tokens or None


def _shannon_entropy(text: str) -> float:
    """Bits per character. Distinguishes `davidsmith` from `qzvlmr9931`."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for character in text:
        counts[character] = counts.get(character, 0) + 1
    total = len(text)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _classify(core: str, stem: str) -> tuple[float, str]:
    """The rule cascade. Returns (commonness, human-readable reason)."""
    deleet = stem.translate(_LEET)
    had_digits = stem != core

    if core in COMMON_HANDLES or stem in COMMON_HANDLES:
        return 0.97, "a role or throwaway handle registered on nearly every platform"
    if stem in NICKNAMES:
        # More contested than the formal name: many Michaels, only some register `mike`,
        # but every one of them wants it.
        return 0.94, "a common short form of a personal name"
    if stem in GIVEN_NAMES:
        return 0.92, "a common given name"
    if stem in SURNAMES:
        return 0.90, "a common surname"
    if deleet in GIVEN_NAMES or deleet in NICKNAMES or deleet in SURNAMES:
        return 0.84, "a common personal name with character substitutions"
    if stem in COMMON_WORDS:
        return 0.85, "a single ordinary dictionary word"
    if deleet in COMMON_WORDS:
        return 0.78, "an ordinary word with character substitutions"

    name_parts = _split_into_known_tokens(stem, NAME_TOKENS)
    if name_parts and len(name_parts) >= 2:
        return 0.92, f"a person's name ({' + '.join(name_parts)}), shared by many people"

    word_parts = _split_into_known_tokens(deleet, ALL_TOKENS)
    if word_parts and len(word_parts) >= 2:
        joined = " + ".join(word_parts)
        # Two ordinary words is a very popular handle-construction pattern, but the
        # combination is meaningfully narrower than either word alone.
        return 0.60, f"a combination of ordinary words ({joined})"

    # A recognisable word or name buried in something longer: partially contested.
    for token in sorted(ALL_TOKENS, key=len, reverse=True):
        if len(token) >= 4 and token in deleet:
            return 0.45, f"contains the ordinary word '{token}'"

    if had_digits:
        return 0.28, "an unrecognised word with digits appended"
    return 0.20, "no common name or dictionary word recognised"


def _structural_adjustment(core: str, stem: str) -> tuple[float, list[str]]:
    """Bounded nudges from shape alone. Positive means *more* common."""
    notes: list[str] = []
    adjustment = 0.0

    if len(core) <= 4:
        adjustment += 0.15
        notes.append("very short handles are heavily contested")
    elif len(core) >= 14:
        adjustment -= 0.08
        notes.append("long handles collide less often")

    entropy = _shannon_entropy(stem)
    if entropy >= 3.3:
        adjustment -= 0.15
        notes.append("high character diversity")
    elif entropy <= 2.2:
        adjustment += 0.05
        notes.append("low character diversity")

    interior_digits = len(_DIGITS.findall(core[:-1] if core else ""))
    if interior_digits >= 2:
        adjustment -= 0.05
        notes.append("digits mixed through the handle")

    bounded = max(-MAX_STRUCTURAL_ADJUSTMENT, min(MAX_STRUCTURAL_ADJUSTMENT, adjustment))
    return bounded, notes


def assess(handle: str) -> RarityAssessment:
    """Score one handle. Deterministic and network-free."""
    core = _normalise(handle)
    if not core:
        # Nothing to judge. Neutral rather than rare: an empty handle is not evidence.
        return RarityAssessment(handle, 0.5, "empty handle", 1.0)

    stem = _stem(core)
    commonness, reason = _classify(core, stem)
    adjustment, notes = _structural_adjustment(core, stem)
    commonness = max(MIN_COMMONNESS, min(MAX_COMMONNESS, commonness + adjustment))

    rarity = round(1.0 - commonness, 4)
    factor = round(MIN_FACTOR + (MAX_FACTOR - MIN_FACTOR) * rarity, 4)
    full_reason = "; ".join([reason, *notes])
    return RarityAssessment(handle=handle, rarity=rarity, reason=full_reason, factor=factor)


def rarity(handle: str) -> float:
    return assess(handle).rarity


def factor_for(handle: str) -> float:
    """The bounded multiplier applied to a username signal's likelihood ratio."""
    return assess(handle).factor


def max_username_only_score(base_lr: float, prior_odds: float) -> float:
    """The highest score a username agreement alone can ever produce.

    Exists so the "a username is never enough" invariant is *proved* from the model's
    own constants rather than spot-checked against example handles. Used by the tests;
    if a future weight change would break the invariant, that test fails immediately.
    """
    log_odds = math.log(prior_odds) + math.log(base_lr * MAX_FACTOR)
    return 1 / (1 + math.exp(-log_odds))


__all__ = [
    "MAX_FACTOR",
    "MIN_FACTOR",
    "RarityAssessment",
    "assess",
    "factor_for",
    "max_username_only_score",
    "rarity",
]
