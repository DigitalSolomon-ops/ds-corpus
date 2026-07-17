"""Global settings model — config/settings.yaml.

Strict parsing: unknown keys are a validation error, not a warning.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Budgets(BaseModel):
    """Hard caps. Hitting one is a clean checkpoint-and-stop, never a crash."""

    model_config = ConfigDict(extra="forbid")

    max_documents_per_run: int = Field(gt=0)
    max_bytes_per_run: int = Field(gt=0)
    max_wall_seconds: int = Field(gt=0)
    max_triage_usd: float = Field(gt=0)


class Significance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Gates everything that is not a canon hit; canon hits short-circuit.
    threshold: int = Field(default=45, ge=0, le=100)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The global license allowlist. Per-source policies may only narrow this.
    license_allowlist: list[str] = Field(min_length=1)
    budgets: Budgets
    significance: Significance = Significance()
    user_agent: str = Field(min_length=1, description="Descriptive UA with contact address")
    contact_email: str = Field(pattern=r".+@.+\..+")
    gcs_bucket: str = Field(min_length=1)


def load_settings(path: Path) -> Settings:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return Settings.model_validate(raw)
