from __future__ import annotations

import json

import pytest

from ds_corpus import dashboard as dash
from ds_corpus.canon import Canon, CanonWork
from ds_corpus.index import SQLiteIndex
from ds_corpus.registry import load_registry
from ds_corpus.settings import load_settings
from ds_corpus.store import FilesystemStore

from conftest import CONFIG_DIR


def _work(wid, domain="metaphysics"):
    return CanonWork.model_validate(dict(
        id=wid, title=wid.replace("_", " ").title(), author="Someone",
        original_year=1700, domain=domain,
        why_canonical="Important enough to name here.",
        authorities=["philpapers_core"], primary_source=True,
        hunt={"sources": ["gutenberg"]},
    ))


@pytest.fixture()
def env(tmp_path):
    store = FilesystemStore(tmp_path / "lib")
    index = SQLiteIndex(tmp_path / "lib" / "_index.sqlite3")
    yield store, index
    index.close()


def test_collect_and_render(env):
    store, index = env
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    reg = load_registry(CONFIG_DIR / "sources.yaml")
    canon = Canon(works=[_work("kant_x"), _work("plato_y")])

    data = dash.collect(settings, reg, canon, store, index)
    # empty domains (law/coding/art) each generate a canon authoring action
    assert any(a.kind == "canon" and "law" in a.title for a in data.actions)
    # metaphysics has 2 works -> still under target -> a "grow" action
    assert any(a.kind == "canon" and "metaphysics" in a.title for a in data.actions)
    # built human-gate phases not signed off -> gate actions (P4, P5, P7)
    gate_titles = [a.title for a in data.actions if a.kind == "gate"]
    assert any("P4" in t for t in gate_titles)

    html = dash.render_html(data)
    assert "<!doctype html>" in html
    assert "control room" in html
    assert "kant" not in html.lower() or "Kant" in html   # renders without crashing


def test_gate_roundtrip_changes_actions(env):
    store, index = env
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    reg = load_registry(CONFIG_DIR / "sources.yaml")
    canon = Canon(works=[_work("kant_x")])

    before = dash.collect(settings, reg, canon, store, index)
    p5_before = [a for a in before.actions if a.kind == "gate" and "P5" in a.title]
    assert p5_before                                # P5 needs sign-off initially

    dash.set_gate(store, "P5", signed=True)
    assert json.loads(store.read("_dashboard/gates.json"))["P5"]["signed_off"] is True

    after = dash.collect(settings, reg, canon, store, index)
    p5_after = [a for a in after.actions if a.kind == "gate" and "P5" in a.title]
    assert not p5_after                             # P5 gate cleared from the inbox

    dash.set_gate(store, "P5", signed=False)        # reset restores it
    again = dash.collect(settings, reg, canon, store, index)
    assert [a for a in again.actions if a.kind == "gate" and "P5" in a.title]


def test_missing_secret_becomes_action(env, monkeypatch):
    store, index = env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    reg = load_registry(CONFIG_DIR / "sources.yaml")
    canon = Canon(works=[_work("kant_x")])
    data = dash.collect(settings, reg, canon, store, index)
    assert any(a.kind == "secret" and "ANTHROPIC_API_KEY" in a.title for a in data.actions)
