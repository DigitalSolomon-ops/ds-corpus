from __future__ import annotations

import shutil

import yaml
from click.testing import CliRunner

from ds_corpus.cli import main

from conftest import CONFIG_DIR


def _run(*args):
    return CliRunner().invoke(main, list(args))


def test_sources_validate_green_on_seed():
    r = _run("sources", "validate")
    assert r.exit_code == 0, r.output
    assert "OK" in r.output


def test_canon_validate_green_on_seed():
    r = _run("canon", "validate")
    assert r.exit_code == 0, r.output
    assert "5 canon work(s) valid" in r.output


def test_sources_list_domain_filter():
    r = _run("sources", "list", "--domain", "coding")
    assert r.exit_code == 0
    assert "arxiv" in r.output
    assert "gutenberg" not in r.output


def test_broken_sources_yaml_fails_cleanly(tmp_path):
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    src_file = tmp_path / "config" / "sources.yaml"
    raw = yaml.safe_load(src_file.read_text(encoding="utf-8"))
    raw["sources"][0]["tier"] = "firehose"
    src_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    r = _run("--config-dir", str(tmp_path / "config"), "sources", "validate")
    assert r.exit_code == 1
    assert "FAIL" in r.output


def test_canon_validate_rejects_hunt_source_outside_registry(tmp_path):
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    canon_file = tmp_path / "config" / "canon" / "metaphysics.yaml"
    raw = yaml.safe_load(canon_file.read_text(encoding="utf-8"))
    raw["works"][0]["hunt"]["sources"] = ["not_in_registry"]
    canon_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    r = _run("--config-dir", str(tmp_path / "config"), "canon", "validate")
    assert r.exit_code == 1
    assert "unknown" in r.output


def test_unbuilt_command_exits_2():
    r = _run("search")
    assert r.exit_code == 2
    assert "P10" in r.output


def test_run_requires_local_until_p9():
    r = _run("run", "--source", "arxiv")
    assert r.exit_code == 1
    assert "P9" in r.output
