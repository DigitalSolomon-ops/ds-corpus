from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from ds_corpus.adapters import gutenberg
from ds_corpus.canon import Canon, CanonWork
from ds_corpus.http import PoliteSession
from ds_corpus.registry import load_registry
from ds_corpus.resolver import resolve_all, resolve_work, write_coverage
from ds_corpus.settings import load_settings
from ds_corpus.store import FilesystemStore

from conftest import CONFIG_DIR


def _gutendex_page(*books):
    return {"count": len(books), "results": list(books)}


def _book(bid, title, *, authors, translators=(), copyright=False, dl=100, text=True):
    formats = {}
    if text:
        formats["text/plain; charset=utf-8"] = f"https://www.gutenberg.org/files/{bid}/{bid}-0.txt"
    formats["application/x-mobipocket-ebook"] = "ignored"
    return {
        "id": bid,
        "title": title,
        "authors": [{"name": a} for a in authors],
        "translators": [{"name": t} for t in translators],
        "languages": ["en"],
        "copyright": copyright,
        "download_count": dl,
        "formats": formats,
    }


def _work(**over):
    base = dict(
        id="kant_critique_pure_reason",
        title="Critique of Pure Reason",
        author="Immanuel Kant",
        original_year=1781,
        domain="metaphysics",
        why_canonical="Foundational to modern metaphysics.",
        authorities=["philpapers_core"],
        primary_source=True,
        acceptable_editions={"prefer_translations": ["Meiklejohn"], "avoid": ["abridged"]},
        hunt={"sources": ["gutenberg"]},
    )
    base.update(over)
    return CanonWork.model_validate(base)


def _mock_session_factory(handler):
    @contextmanager
    def factory(source):
        s = PoliteSession(
            "ds-corpus/test (contact: t@e.com)",
            source.rate.requests_per_second,
            respect_robots=False,
            transport=httpx.MockTransport(handler),
            sleep=lambda _: None,
        )
        try:
            yield s
        finally:
            s.close()
    return factory


@pytest.fixture()
def registry():
    return load_registry(CONFIG_DIR / "sources.yaml")


@pytest.fixture()
def settings():
    return load_settings(CONFIG_DIR / "settings.yaml")


# --- gutendex hunt parsing ------------------------------------------------

def test_gutendex_search_parses_and_drops_bodiless(registry):
    def handler(request):
        return httpx.Response(200, json=_gutendex_page(
            _book(4280, "The Critique of Pure Reason", authors=["Kant, Immanuel"],
                  translators=["Meiklejohn, J. M. D."], dl=900),
            _book(1, "Scan only", authors=["Kant, Immanuel"], text=False),  # no body -> dropped
        ))
    factory = _mock_session_factory(handler)
    with factory(registry.get("gutenberg")) as s:
        eds = gutenberg.search(s, _work())
    assert [e.edition_id for e in eds] == ["4280"]
    assert eds[0].translators == ["Meiklejohn, J. M. D."]
    assert eds[0].raw_license == "Public domain in the USA."


def test_gutendex_copyright_true_yields_no_license(registry):
    def handler(request):
        return httpx.Response(200, json=_gutendex_page(
            _book(99, "Recent Translation", authors=["Kant"], copyright=True)))
    factory = _mock_session_factory(handler)
    with factory(registry.get("gutenberg")) as s:
        eds = gutenberg.search(s, _work())
    assert eds[0].raw_license is None    # copyrighted -> gate will drop it later


# --- resolver end to end (mocked) -----------------------------------------

def test_resolve_work_picks_preferred_translation(registry, settings):
    def handler(request):
        return httpx.Response(200, json=_gutendex_page(
            _book(4280, "Critique of Pure Reason", authors=["Kant, Immanuel"],
                  translators=["Meiklejohn, J. M. D."], dl=900),
            _book(5000, "Critique of Pure Reason", authors=["Kant, Immanuel"],
                  translators=["Anonymous"], dl=50),
        ))
    r = resolve_work(_work(), registry, settings, _mock_session_factory(handler))
    assert r.status == "resolved"
    assert r.selected["edition_id"] == "4280"
    assert r.selected_signals["preferred_translation"] == "Meiklejohn"
    assert len(r.alternates) == 1 and r.alternates[0]["edition_id"] == "5000"
    assert r.tried == {"gutenberg": 2}


def test_resolve_work_disqualifies_abridged(registry, settings):
    def handler(request):
        return httpx.Response(200, json=_gutendex_page(
            _book(1, "Critique of Pure Reason (Abridged)", authors=["Kant"], dl=100000)))
    r = resolve_work(_work(), registry, settings, _mock_session_factory(handler))
    assert r.status == "unavailable_open"
    assert r.disqualified and "avoid" in r.disqualified[0]["disqualified"]


def test_search_falls_back_to_author_only(registry):
    # First query (surname + title words) returns nothing; broad author query
    # returns the book. The hunt must fall back rather than give up.
    calls = []

    def handler(request):
        q = dict(request.url.params).get("search", "")
        calls.append(q)
        if q == "Spinoza ethics":                    # targeted (title lowercased) -> empty
            return httpx.Response(200, json=_gutendex_page())
        if q == "Spinoza":                           # broad -> hit
            return httpx.Response(200, json=_gutendex_page(
                _book(3800, "Ethics", authors=["Spinoza, Benedictus de"], dl=400)))
        return httpx.Response(200, json=_gutendex_page())

    work = _work(id="spinoza_ethics", title="Ethics, Demonstrated in Geometrical Order",
                 author="Baruch Spinoza", original_year=1677,
                 acceptable_editions={})
    factory = _mock_session_factory(handler)
    with factory(registry.get("gutenberg")) as s:
        eds = gutenberg.search(s, work)
    assert [e.edition_id for e in eds] == ["3800"]
    assert calls == ["Spinoza ethics", "Spinoza"]    # tried targeted, then broad


def test_resolve_work_reports_unbuilt_source(registry, settings):
    def handler(request):
        return httpx.Response(200, json=_gutendex_page())
    w = _work(hunt={"sources": ["gutenberg", "internet_archive"]})
    r = resolve_work(w, registry, settings, _mock_session_factory(handler))
    assert "internet_archive" in r.unbuilt_sources


def test_coverage_report_written(tmp_path, registry, settings):
    def handler(request):
        # resolve Kant, leave the rest wanting (empty results)
        if b"Kant" in request.url.query or "Kant" in str(request.url):
            return httpx.Response(200, json=_gutendex_page(
                _book(4280, "Critique of Pure Reason", authors=["Kant, Immanuel"],
                      translators=["Meiklejohn"], dl=900)))
        return httpx.Response(200, json=_gutendex_page())

    canon = Canon(works=[
        _work(),
        _work(id="aristotle_metaphysics", title="Metaphysics", author="Aristotle",
              original_year=-350, acceptable_editions={}),
    ])
    resolutions = resolve_all(canon, registry, settings, _mock_session_factory(handler))
    store = FilesystemStore(tmp_path / "lib")
    summary = write_coverage(resolutions, canon, store, domain="metaphysics")

    assert summary["resolved"] == 1 and summary["wanted"] == 1
    cov = json.loads(store.read("_canon/coverage.json"))
    assert cov["by_domain"]["metaphysics"] == {"resolved": 1, "wanted": 1}
    wanted = store.read("_canon/WANTED.md")
    assert "aristotle_metaphysics" in wanted
    assert "Critique of Pure Reason" not in wanted.split("## ")[-1]  # resolved one not listed
