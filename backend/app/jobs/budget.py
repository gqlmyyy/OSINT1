"""Investigation budgets (spec 19) — the guard against graph explosion."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import get_settings


@dataclass
class Budget:
    max_depth: int
    max_entities: int
    max_jobs: int
    jobs_used: int = 0
    entities_used: int = 0
    _seen: set[tuple[str, str, str]] = field(default_factory=set)
    exhausted_reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(
        cls,
        *,
        max_depth: int | None = None,
        max_entities: int | None = None,
        max_jobs: int | None = None,
    ) -> Budget:
        settings = get_settings()
        return cls(
            max_depth=min(max_depth or settings.max_depth, settings.max_depth),
            max_entities=min(max_entities or settings.max_entities, settings.max_entities),
            max_jobs=min(max_jobs or settings.max_jobs, settings.max_jobs),
        )

    def can_schedule(self, provider: str, target_type: str, normalized: str, depth: int) -> bool:
        """Loop guard + budget check. Returns False and records why when refused."""
        if depth > self.max_depth:
            self._note(f"depth limit {self.max_depth} reached")
            return False
        if self.jobs_used >= self.max_jobs:
            self._note(f"job limit {self.max_jobs} reached")
            return False
        if self.entities_used >= self.max_entities:
            self._note(f"entity limit {self.max_entities} reached")
            return False
        key = (provider, target_type, normalized)
        if key in self._seen:
            return False
        self._seen.add(key)
        self.jobs_used += 1
        return True

    def record_entities(self, count: int) -> None:
        self.entities_used += count

    @property
    def entities_exhausted(self) -> bool:
        return self.entities_used >= self.max_entities

    def _note(self, reason: str) -> None:
        if reason not in self.exhausted_reasons:
            self.exhausted_reasons.append(reason)

    def snapshot(self) -> dict[str, object]:
        return {
            "jobs_used": self.jobs_used,
            "max_jobs": self.max_jobs,
            "entities_used": self.entities_used,
            "max_entities": self.max_entities,
            "max_depth": self.max_depth,
            "limits_hit": self.exhausted_reasons,
        }
