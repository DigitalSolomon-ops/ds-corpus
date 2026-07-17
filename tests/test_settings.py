from __future__ import annotations

import pytest
from pydantic import ValidationError

from ds_corpus.settings import Settings, load_settings

VALID = {
    "license_allowlist": ["public-domain", "cc0-1.0"],
    "budgets": {
        "max_documents_per_run": 100,
        "max_bytes_per_run": 1024,
        "max_wall_seconds": 600,
        "max_triage_usd": 1.0,
    },
    "significance": {"threshold": 45},
    "user_agent": "ds-corpus/test (contact: test@example.com)",
    "contact_email": "test@example.com",
    "gcs_bucket": "ds-corpus-library",
}


def test_seed_settings_validate(config_dir):
    s = load_settings(config_dir / "settings.yaml")
    assert s.budgets.max_triage_usd > 0
    assert "public-domain" in s.license_allowlist
    assert "@" in s.user_agent  # UA must carry a contact address


def test_valid_settings_parse():
    s = Settings.model_validate(VALID)
    assert s.significance.threshold == 45


def test_unknown_key_fails():
    bad = dict(VALID, keep_unlicensed="yes please")
    with pytest.raises(ValidationError, match="extra"):
        Settings.model_validate(bad)


def test_empty_license_allowlist_fails():
    bad = dict(VALID, license_allowlist=[])
    with pytest.raises(ValidationError):
        Settings.model_validate(bad)


@pytest.mark.parametrize(
    "field",
    ["max_documents_per_run", "max_bytes_per_run", "max_wall_seconds", "max_triage_usd"],
)
def test_every_budget_cap_must_be_positive(field):
    bad = dict(VALID, budgets=dict(VALID["budgets"], **{field: 0}))
    with pytest.raises(ValidationError):
        Settings.model_validate(bad)


def test_threshold_bounded_0_100():
    bad = dict(VALID, significance={"threshold": 101})
    with pytest.raises(ValidationError):
        Settings.model_validate(bad)
