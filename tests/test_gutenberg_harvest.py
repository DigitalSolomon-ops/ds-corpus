from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

from ds_corpus.adapters.gutenberg import GutenbergAdapter
from ds_corpus.canon import Canon, CanonWork
from ds_corpus.frontmatter import parse
from ds_corpus.http import PoliteSession
from ds_corpus.index import SQLiteIndex
from ds_corpus.normalize import strip_gutenberg_boilerplate
from ds_corpus.registry import load_registry
from ds_corpus.scheduler import run_source
from ds_corpus.settings import load_settings
from ds_corpus.store import FilesystemStore

from conftest import CONFIG_DIR

PG_TEXT = (
    "The Project Gutenberg eBook of Critique of Pure Reason\r\n"
    "This ebook is for the use of anyone... lots of legal header ...\r\n"
    "*** START OF THE PROJECT GUTENBERG EBOOK 4280 ***\r\n"
    "\r\n"
    "THE CRITIQUE OF PURE REASON\r\n"
    "\r\n"
    "Transcendental doctrine of the faculty of judgement.\r\n"
    "*** END OF THE PROJECT GUTENBERG EBOOK 4280 ***\r\n"
    "This footer is licensing boilerplate about redistribution.\r\n"
)


def test_strip_boilerplate_keeps_only_the_work():
    body = strip_gutenberg_boilerplate(PG_TEXT)
    assert "THE CRITIQUE OF PURE REASON" in body
    assert "legal header" not in body
    assert "licensing boilerplate" not in body


def test_strip_boilerplate_no_sentinels_keeps_all():
    raw = "A plain public-domain text with no PG markers at all."
    assert strip_gutenberg_boilerplate(raw) == raw


def test_clean_person_drops_parenthetical_expansion():
    from ds_corpus.adapters.gutenberg import _clean_person

    assert _clean_person("Meiklejohn, J. M. D. (John Miller Dow)") == "Meiklejohn, J. M. D."
    assert _clean_person("Jowett, Benjamin") == "Jowett, Benjamin"


def _work():
    return CanonWork.model_validate(dict(
        id="kant_critique_pure_reason",
        title="Critique of Pure Reason",
        author="Immanuel Kant",
        original_year=1781,
        domain="metaphysics",
        why_canonical="Foundational to modern metaphysics.",
        authorities=["philpapers_core", "cambridge_companion"],
        primary_source=True,
        acceptable_editions={"prefer_translations": ["Meiklejohn"]},
        hunt={"sources": ["gutenberg"]},
    ))


def _gutendex_page(bid):
    return {"count": 1, "results": [{
        "id": bid,
        "title": "The Critique of Pure Reason",
        "authors": [{"name": "Kant, Immanuel"}],
        "translators": [{"name": "Meiklejohn, J. M. D."}],
        "languages": ["en"],
        "copyright": False,
        "download_count": 900,
        "formats": {"text/plain; charset=utf-8": f"https://www.gutenberg.org/files/{bid}/{bid}-0.txt"},
    }]}


def _handler(request):
    if "gutendex" in request.url.host:
        return httpx.Response(200, json=_gutendex_page(4280))
    if request.url.path.endswith(".txt"):
        return httpx.Response(200, text=PG_TEXT)
    return httpx.Response(404)


@pytest.fixture()
def env(tmp_path):
    store = FilesystemStore(tmp_path / "lib")
    index = SQLiteIndex(tmp_path / "lib" / "_index.sqlite3")
    yield store, index
    index.close()


def _patch_session(monkeypatch):
    """Make every PoliteSession created inside the scheduler use the mock
    transport and no robots/rate delay."""
    real_init = PoliteSession.__init__

    def fake_init(self, user_agent, rate_per_second, respect_robots=True,
                  transport=None, sleep=None):
        real_init(self, user_agent, rate_per_second, respect_robots=False,
                  transport=httpx.MockTransport(_handler), sleep=lambda _: None)

    monkeypatch.setattr(PoliteSession, "__init__", fake_init)


def test_harvest_ingests_canon_hit_with_short_circuit(env, monkeypatch):
    store, index = env
    _patch_session(monkeypatch)
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    source = load_registry(CONFIG_DIR / "sources.yaml").get("gutenberg")
    canon = Canon(works=[_work()])

    stats = run_source(source, settings, store, index, canon=canon, log=lambda *_: None)
    assert stats.written == 1 and stats.errors == 0

    path = "metaphysics/gutenberg/kant_critique_pure_reason.md"
    assert store.exists(path)
    fm, body = parse(store.read(path))
    assert fm.canon_id == "kant_critique_pure_reason"
    assert fm.license == "public-domain"
    assert fm.significance_score == 100
    assert fm.significance_signals["canon_hit"] is True
    assert fm.significance_signals["authority_count"] == 2
    assert fm.triage is None
    assert fm.translator == "Meiklejohn, J. M. D."
    assert "THE CRITIQUE OF PURE REASON" in body
    assert "licensing boilerplate" not in body
    assert "Project Gutenberg. Public domain." in fm.attribution


def test_harvest_is_idempotent(env, monkeypatch):
    store, index = env
    _patch_session(monkeypatch)
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    source = load_registry(CONFIG_DIR / "sources.yaml").get("gutenberg")
    canon = Canon(works=[_work()])

    run_source(source, settings, store, index, canon=canon, log=lambda *_: None)
    stats2 = run_source(source, settings, store, index, canon=canon, log=lambda *_: None)
    assert stats2.written == 0 and stats2.duplicates == 1   # same text -> dedup


def test_canon_driven_source_without_canon_raises(env):
    store, index = env
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    source = load_registry(CONFIG_DIR / "sources.yaml").get("gutenberg")
    with pytest.raises(ValueError, match="canon-driven"):
        run_source(source, settings, store, index, canon=None, log=lambda *_: None)
