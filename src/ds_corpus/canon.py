"""Canon layer — config/canon/*.yaml and config/authorities.yaml.

Canon files are hand-authored want-lists: the works that matter, with a human
standing behind each entry. This module only loads and validates them; it never
generates them. Coverage against the canon is the system's success metric.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ds_corpus.registry import DOMAINS


class WorkStatus(str, Enum):
    WANTED = "wanted"
    RESOLVED = "resolved"
    UNAVAILABLE_OPEN = "unavailable_open"


class Authority(BaseModel):
    """A list that confers canonical status (e.g. philpapers_core)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1)
    url: str | None = None


class AuthoritiesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorities: list[Authority] = Field(min_length=1)

    @field_validator("authorities")
    @classmethod
    def _unique_ids(cls, v: list[Authority]) -> list[Authority]:
        seen: set[str] = set()
        for a in v:
            if a.id in seen:
                raise ValueError(f"duplicate authority id: {a.id}")
            seen.add(a.id)
        return v

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(a.id for a in self.authorities)


class AcceptableEditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefer_translations: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    min_word_count: int | None = Field(default=None, gt=0)


class HuntConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Source ids to fan out across when hunting for open editions.
    # Cross-checked against sources.yaml at validate time.
    sources: list[str] = Field(min_length=1)


class CanonWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    original_year: int = Field(ge=-1000, le=2100)
    domain: str
    why_canonical: str = Field(min_length=10)
    authorities: list[str] = Field(min_length=1)
    primary_source: bool
    acceptable_editions: AcceptableEditions = AcceptableEditions()
    hunt: HuntConfig
    status: WorkStatus = WorkStatus.WANTED

    @field_validator("domain")
    @classmethod
    def _known_domain(cls, v: str) -> str:
        if v not in DOMAINS:
            raise ValueError(f"unknown domain: {v!r}; known: {sorted(DOMAINS)}")
        return v


class CanonFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    works: list[CanonWork] = Field(min_length=1)


class Canon(BaseModel):
    """All canon files merged, with cross-file invariants enforced."""

    model_config = ConfigDict(extra="forbid")

    works: list[CanonWork]

    def by_domain(self, domain: str) -> list[CanonWork]:
        return [w for w in self.works if w.domain == domain]

    def get(self, work_id: str) -> CanonWork | None:
        return next((w for w in self.works if w.id == work_id), None)


class CanonValidationError(ValueError):
    pass


def load_authorities(path: Path) -> AuthoritiesFile:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise CanonValidationError(f"{path}: expected a mapping at top level")
    return AuthoritiesFile.model_validate(raw)


def load_canon(
    canon_dir: Path,
    authorities: AuthoritiesFile,
    known_source_ids: frozenset[str] | None = None,
) -> Canon:
    """Load and merge config/canon/*.yaml.

    Enforces: per-file schema, cross-file work-id uniqueness, every authority
    reference exists in authorities.yaml, and (when known_source_ids is given)
    every hunt source exists in the sources registry.
    """
    paths = sorted(canon_dir.glob("*.yaml"))
    if not paths:
        raise CanonValidationError(f"no canon files found in {canon_dir}")

    works: list[CanonWork] = []
    seen_ids: dict[str, Path] = {}
    errors: list[str] = []

    for path in paths:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            errors.append(f"{path.name}: expected a mapping at top level")
            continue
        try:
            cf = CanonFile.model_validate(raw)
        except Exception as e:  # pydantic ValidationError — report per file
            errors.append(f"{path.name}: {e}")
            continue

        for w in cf.works:
            if w.id in seen_ids:
                errors.append(
                    f"{path.name}: duplicate work id {w.id!r} "
                    f"(first seen in {seen_ids[w.id].name})"
                )
                continue
            seen_ids[w.id] = path

            missing_auth = set(w.authorities) - authorities.ids
            if missing_auth:
                errors.append(
                    f"{path.name}: work {w.id!r} references unknown "
                    f"authorities: {sorted(missing_auth)}"
                )
            if known_source_ids is not None:
                missing_src = set(w.hunt.sources) - known_source_ids
                if missing_src:
                    errors.append(
                        f"{path.name}: work {w.id!r} hunts unknown "
                        f"sources: {sorted(missing_src)}"
                    )
            works.append(w)

    if errors:
        raise CanonValidationError("\n".join(errors))
    return Canon(works=works)
