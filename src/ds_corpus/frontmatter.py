"""Front-matter contract (§7 of the brief). Validate on write; failure = not written."""

from __future__ import annotations

from enum import Enum

import yaml
from pydantic import BaseModel, ConfigDict, Field


class BodyStatus(str, Enum):
    FULL_TEXT = "full_text"
    METADATA_ONLY = "metadata_only"
    IMAGES_ONLY = "images_only"


class AlternateEdition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    url: str
    translator: str | None = None
    rank: int = Field(ge=1)


class FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    canon_id: str | None = None
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    translator: str | None = None
    domain: str
    source_id: str
    source_url: str
    canonical_url: str | None = None
    # `license` is a required, non-null string. The fail-closed gate lives in
    # the writer, but the schema backs it up: a record with no license cannot
    # even be represented as writable.
    license: str = Field(min_length=1)
    license_url: str | None = None
    pd_basis: str | None = None
    publisher: str | None = None
    original_year: int | None = Field(default=None, ge=-1000, le=2100)
    published_at: str | None = None
    retrieved_at: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_format: str
    converter: str
    body_status: BodyStatus
    word_count: int = Field(ge=0)
    significance_score: int | None = Field(default=None, ge=0, le=100)
    significance_signals: dict | None = None
    triage: dict | None = None
    alternate_editions: list[AlternateEdition] = Field(default_factory=list)
    iiif_manifest: str | None = None
    tags: list[str] = Field(default_factory=list)
    attribution: str = Field(min_length=1)


def to_markdown(fm: FrontMatter, body: str) -> str:
    """Serialize to `---\\n<yaml>\\n---\\n\\n<body>`. images_only/metadata_only
    records carry an empty body — never a fabricated one."""
    if fm.body_status is not BodyStatus.FULL_TEXT and body.strip():
        raise ValueError(
            f"{fm.id}: body_status={fm.body_status.value} but a non-empty body "
            "was supplied — refusing to write a body we don't have text for"
        )
    data = fm.model_dump(mode="json", exclude_none=False)
    yml = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    return f"---\n{yml}---\n\n{body.strip()}\n" if body.strip() else f"---\n{yml}---\n"


def parse(text: str) -> tuple[FrontMatter, str]:
    """Inverse of to_markdown. Raises on malformed or invalid front-matter."""
    if not text.startswith("---\n"):
        raise ValueError("missing front-matter delimiter")
    end = text.index("\n---", 4)
    raw = yaml.safe_load(text[4:end])
    body = text[end + 4 :].lstrip("\n")
    return FrontMatter.model_validate(raw), body
