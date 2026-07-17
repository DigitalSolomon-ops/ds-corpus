from __future__ import annotations

import pytest
from pydantic import ValidationError

from ds_corpus.registry import SourceConfig, SourcesRegistry, load_registry

from conftest import deep_copy_source


def test_seed_registry_validates(config_dir):
    reg = load_registry(config_dir / "sources.yaml")
    assert len(reg.sources) == 12
    # open + deep-archive adapters are enabled; P7 web sources stay off
    enabled = {s.id for s in reg.sources if s.enabled}
    assert enabled == {"arxiv", "gutenberg", "internet_archive", "hathitrust", "iiif"}
    assert "courtlistener" in {s.id for s in reg.sources}


def test_valid_source_parses():
    s = SourceConfig.model_validate(deep_copy_source())
    assert s.tier.value == "open_api"


def test_unknown_top_level_key_fails():
    with pytest.raises(ValidationError, match="extra"):
        SourceConfig.model_validate(deep_copy_source(surprise_key=True))


def test_unknown_nested_key_fails():
    bad = deep_copy_source()
    bad["license_policy"]["fallback"] = "save_anyway"  # no such path exists
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(bad)


def test_empty_license_allowlist_fails():
    bad = deep_copy_source()
    bad["license_policy"]["allow"] = []
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(bad)


def test_unknown_tier_fails():
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(deep_copy_source(tier="firehose"))


def test_unknown_domain_fails():
    with pytest.raises(ValidationError, match="unknown domain"):
        SourceConfig.model_validate(deep_copy_source(domain=["memes"]))


def test_rate_must_be_positive_and_bounded():
    bad = deep_copy_source()
    bad["rate"]["requests_per_second"] = 0
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(bad)
    bad["rate"]["requests_per_second"] = 50  # nobody gets 50 rps; be polite
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(bad)


def test_duplicate_source_ids_fail():
    with pytest.raises(ValidationError, match="duplicate source id"):
        SourcesRegistry.model_validate(
            {"sources": [deep_copy_source(), deep_copy_source()]}
        )


def test_by_domain_filter():
    reg = SourcesRegistry.model_validate(
        {
            "sources": [
                deep_copy_source(),
                deep_copy_source(id="gutenberg", adapter="gutenberg", domain=["metaphysics"]),
            ]
        }
    )
    assert [s.id for s in reg.by_domain("coding")] == ["arxiv"]
    assert [s.id for s in reg.by_domain("metaphysics")] == ["gutenberg"]
