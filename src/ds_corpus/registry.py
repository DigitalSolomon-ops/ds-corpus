"""Sources registry — config/sources.yaml.

This file is the authorization surface: a source that is not here, valid, and
enabled does not get fetched. Strict Pydantic validation, fail on unknown keys.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Domains the corpus covers. Extend deliberately, not casually.
DOMAINS = frozenset({"law", "metaphysics", "coding", "art"})


class Tier(str, Enum):
    OPEN_API = "open_api"
    DEEP_ARCHIVE = "deep_archive"
    BULK = "bulk"
    SCRAPE = "scrape"  # fallback tier: per-source, human-approved (P11)


class Schedule(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class LicenseMode(str, Enum):
    PER_RECORD = "per_record"  # license resolved per document
    BLANKET = "blanket"        # whole source carries one known license


class LicensePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: LicenseMode
    # Fail-closed: a record whose license does not match `allow` is skipped
    # and logged, never written.
    allow: list[str] = Field(min_length=1)


class RateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_second: float = Field(gt=0, le=10)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    domain: list[str] = Field(min_length=1)
    tier: Tier
    enabled: bool = True
    adapter: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    license_policy: LicensePolicy
    query: dict = Field(default_factory=dict)
    rate: RateConfig
    schedule: Schedule

    @field_validator("domain")
    @classmethod
    def _known_domains(cls, v: list[str]) -> list[str]:
        unknown = set(v) - DOMAINS
        if unknown:
            raise ValueError(f"unknown domain(s): {sorted(unknown)}; known: {sorted(DOMAINS)}")
        if len(v) != len(set(v)):
            raise ValueError("duplicate domains in list")
        return v


class SourcesRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceConfig] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def _unique_ids(cls, v: list[SourceConfig]) -> list[SourceConfig]:
        seen: set[str] = set()
        for s in v:
            if s.id in seen:
                raise ValueError(f"duplicate source id: {s.id}")
            seen.add(s.id)
        return v

    def by_domain(self, domain: str) -> list[SourceConfig]:
        return [s for s in self.sources if domain in s.domain]

    def get(self, source_id: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.id == source_id), None)


def load_registry(path: Path) -> SourcesRegistry:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return SourcesRegistry.model_validate(raw)
