from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture()
def config_dir() -> Path:
    """The real seed config shipped in the repo — it must always validate."""
    return CONFIG_DIR


VALID_SOURCE = {
    "id": "arxiv",
    "domain": ["coding"],
    "tier": "open_api",
    "enabled": True,
    "adapter": "arxiv",
    "license_policy": {"mode": "per_record", "allow": ["cc-by-4.0"]},
    "query": {},
    "rate": {"requests_per_second": 0.25},
    "schedule": "weekly",
}

VALID_WORK = {
    "id": "kant_critique_pure_reason",
    "title": "Critique of Pure Reason",
    "author": "Immanuel Kant",
    "original_year": 1781,
    "domain": "metaphysics",
    "why_canonical": "Foundational to modern metaphysics and epistemology.",
    "authorities": ["philpapers_core"],
    "primary_source": True,
    "hunt": {"sources": ["gutenberg"]},
    "status": "wanted",
}

VALID_AUTHORITIES = {
    "authorities": [
        {"id": "philpapers_core", "name": "PhilPapers", "weight": 0.9},
    ]
}


def deep_copy_source(**overrides) -> dict:
    import copy

    d = copy.deepcopy(VALID_SOURCE)
    d.update(overrides)
    return d


def deep_copy_work(**overrides) -> dict:
    import copy

    d = copy.deepcopy(VALID_WORK)
    d.update(overrides)
    return d
