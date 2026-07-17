from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from ds_corpus.canon import (
    AuthoritiesFile,
    CanonValidationError,
    CanonWork,
    load_authorities,
    load_canon,
)
from ds_corpus.registry import load_registry

from conftest import VALID_AUTHORITIES, deep_copy_work


def _write_canon(tmp_path, name: str, works: list[dict]) -> None:
    (tmp_path / name).write_text(
        yaml.safe_dump({"works": works}), encoding="utf-8"
    )


@pytest.fixture()
def auths() -> AuthoritiesFile:
    return AuthoritiesFile.model_validate(VALID_AUTHORITIES)


def test_seed_canon_validates(config_dir):
    reg = load_registry(config_dir / "sources.yaml")
    auths = load_authorities(config_dir / "authorities.yaml")
    c = load_canon(
        config_dir / "canon",
        auths,
        known_source_ids=frozenset(s.id for s in reg.sources),
    )
    assert len(c.works) == 5
    assert all(w.domain == "metaphysics" for w in c.works)
    assert c.get("kant_critique_pure_reason") is not None


def test_valid_work_parses():
    w = CanonWork.model_validate(deep_copy_work())
    assert w.status.value == "wanted"
    assert w.acceptable_editions.min_word_count is None  # optional block defaults


def test_unknown_key_fails():
    with pytest.raises(ValidationError, match="extra"):
        CanonWork.model_validate(deep_copy_work(ocr_if_needed=True))


def test_unknown_status_fails():
    with pytest.raises(ValidationError):
        CanonWork.model_validate(deep_copy_work(status="maybe_later"))


def test_unknown_domain_fails():
    with pytest.raises(ValidationError, match="unknown domain"):
        CanonWork.model_validate(deep_copy_work(domain="alchemy"))


def test_authorities_required_nonempty():
    with pytest.raises(ValidationError):
        CanonWork.model_validate(deep_copy_work(authorities=[]))


def test_duplicate_work_id_across_files_fails(tmp_path, auths):
    _write_canon(tmp_path, "a.yaml", [deep_copy_work()])
    _write_canon(tmp_path, "b.yaml", [deep_copy_work()])
    with pytest.raises(CanonValidationError, match="duplicate work id"):
        load_canon(tmp_path, auths)


def test_unknown_authority_reference_fails(tmp_path, auths):
    _write_canon(
        tmp_path, "a.yaml", [deep_copy_work(authorities=["list_i_made_up"])]
    )
    with pytest.raises(CanonValidationError, match="unknown authorities"):
        load_canon(tmp_path, auths)


def test_unknown_hunt_source_fails_when_registry_given(tmp_path, auths):
    _write_canon(
        tmp_path, "a.yaml", [deep_copy_work(hunt={"sources": ["scihub"]})]
    )
    with pytest.raises(CanonValidationError, match="unknown"):
        load_canon(tmp_path, auths, known_source_ids=frozenset({"gutenberg"}))


def test_empty_canon_dir_fails(tmp_path, auths):
    with pytest.raises(CanonValidationError, match="no canon files"):
        load_canon(tmp_path, auths)


def test_duplicate_authority_ids_fail():
    with pytest.raises(ValidationError, match="duplicate authority id"):
        AuthoritiesFile.model_validate(
            {
                "authorities": [
                    {"id": "x", "name": "X", "weight": 0.5},
                    {"id": "x", "name": "X again", "weight": 0.4},
                ]
            }
        )
