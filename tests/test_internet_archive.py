"""Internet Archive adapter + the brief's three critical safety tests."""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

from ds_corpus.adapters import internet_archive as ia
from ds_corpus.adapters.internet_archive import (
    InternetArchiveAdapter,
    is_restricted,
    pick_text_file,
    resolve_ia_license,
)
from ds_corpus.canon import Canon, CanonWork
from ds_corpus.frontmatter import BodyStatus, parse
from ds_corpus.http import PoliteSession
from ds_corpus.index import SQLiteIndex
from ds_corpus.registry import load_registry
from ds_corpus.scheduler import run_source
from ds_corpus.settings import load_settings
from ds_corpus.store import FilesystemStore

from conftest import CONFIG_DIR


# --- unit: safety predicates ---------------------------------------------

def test_is_restricted_flags_lending():
    assert is_restricted({"access-restricted-item": "true"})
    assert is_restricted({"collection": ["inlibrary", "americana"]})
    assert is_restricted({"collection": "lendinglibrary"})
    assert not is_restricted({"collection": ["americana", "greekclassicslist"]})
    assert not is_restricted({})


def test_resolve_ia_license():
    assert resolve_ia_license({"possible-copyright-status": "NOT_IN_COPYRIGHT"}) == "public-domain"
    assert resolve_ia_license({"licenseurl": "http://creativecommons.org/licenses/by/4.0/"}) == "cc-by-4.0"
    assert resolve_ia_license({"possible-copyright-status": "IN_COPYRIGHT"}) is None
    assert resolve_ia_license({}) is None


def test_pick_text_file():
    assert pick_text_file([{"name": "x_djvu.txt", "format": "DjVuTXT"}]) == "x_djvu.txt"
    assert pick_text_file([{"name": "x.pdf"}, {"name": "x_text.txt"}]) == "x_text.txt"
    assert pick_text_file([{"name": "x_jp2.zip"}, {"name": "x.pdf"}]) is None  # images only


# --- harvest harness -----------------------------------------------------

def _work(hunt=("internet_archive",), title="Metaphysics", author="Aristotle", year=-350):
    return CanonWork.model_validate(dict(
        id="aristotle_metaphysics", title=title, author=author, original_year=year,
        domain="metaphysics", why_canonical="The work that named the field of study.",
        authorities=["philpapers_core"], primary_source=True,
        hunt={"sources": list(hunt)}))


def _search_json(*identifiers):
    return {"response": {"docs": [{"identifier": i, "downloads": 500} for i in identifiers]}}


def _meta_json(identifier, *, restricted=False, status="NOT_IN_COPYRIGHT",
               licenseurl=None, has_text=True, collection=None):
    md = {"identifier": identifier, "title": "The Metaphysics of Aristotle",
          "creator": "Aristotle", "language": "eng",
          "collection": collection or ["americana", "greekclassicslist"],
          "mediatype": "texts", "possible-copyright-status": status}
    if restricted:
        md["access-restricted-item"] = "true"
    if licenseurl:
        md["licenseurl"] = licenseurl
    files = [{"name": f"{identifier}_djvu.txt", "format": "DjVuTXT"}] if has_text else \
            [{"name": f"{identifier}_jp2.zip", "format": "Single Page Processed JP2 ZIP"}]
    return {"metadata": md, "files": files}


def _make_handler(routes):
    def handler(request):
        path = request.url.path
        if "advancedsearch" in path:
            return httpx.Response(200, json=routes["search"])
        if path.startswith("/metadata/"):
            ident = path.split("/metadata/")[1]
            return httpx.Response(200, json=routes["meta"][ident])
        if path.startswith("/download/"):
            return httpx.Response(200, text=routes.get("text", "The body of the Metaphysics."))
        return httpx.Response(404)
    return handler


@contextmanager
def _session(handler):
    s = PoliteSession("ds-corpus/test (contact: t@e.com)", 10, respect_robots=False,
                      transport=httpx.MockTransport(handler), sleep=lambda _: None)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def env(tmp_path):
    store = FilesystemStore(tmp_path / "lib")
    index = SQLiteIndex(tmp_path / "lib" / "_index.sqlite3")
    yield store, index
    index.close()


def _run(store, index, handler, monkeypatch, canon_works):
    monkeypatch.setattr(PoliteSession, "__init__", _patched_init(handler))
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    source = load_registry(CONFIG_DIR / "sources.yaml").get("internet_archive")
    return run_source(source, settings, store, index, canon=Canon(works=canon_works),
                      log=lambda *_: None)


def _patched_init(handler):
    real = PoliteSession.__init__

    def init(self, ua, rate, respect_robots=True, transport=None, sleep=None):
        real(self, ua, rate, respect_robots=False,
             transport=httpx.MockTransport(handler), sleep=lambda _: None)
    return init


# --- happy path: open full-text item is ingested -------------------------

def test_harvest_open_fulltext(env, monkeypatch):
    store, index = env
    handler = _make_handler({
        "search": _search_json("metaphysicsaris00arisgoog"),
        "meta": {"metaphysicsaris00arisgoog": _meta_json("metaphysicsaris00arisgoog")},
    })
    stats = _run(store, index, handler, monkeypatch, [_work()])
    assert stats.written == 1
    path = "metaphysics/internet_archive/aristotle_metaphysics.md"
    fm, body = parse(store.read(path))
    assert fm.body_status is BodyStatus.FULL_TEXT
    assert fm.license == "public-domain"
    assert fm.canon_id == "aristotle_metaphysics"
    assert "Metaphysics" in body


# --- THE three critical safety tests -------------------------------------

def test_license_null_never_written(env, monkeypatch):
    """An item with no resolvable open license is never ingested."""
    store, index = env
    handler = _make_handler({
        "search": _search_json("copyrighted_item"),
        "meta": {"copyrighted_item": _meta_json("copyrighted_item", status="IN_COPYRIGHT")},
    })
    stats = _run(store, index, handler, monkeypatch, [_work()])
    assert stats.written == 0
    assert list(store.list()) == []          # nothing on disk at all


def test_lending_item_never_ingested(env, monkeypatch):
    """An access-restricted / lending item is rejected before ingest."""
    store, index = env
    handler = _make_handler({
        "search": _search_json("lending_only"),
        "meta": {"lending_only": _meta_json("lending_only", restricted=True)},
    })
    stats = _run(store, index, handler, monkeypatch, [_work()])
    assert stats.written == 0
    assert list(store.list()) == []


def test_lending_collection_never_ingested(env, monkeypatch):
    store, index = env
    handler = _make_handler({
        "search": _search_json("in_library"),
        "meta": {"in_library": _meta_json("in_library", collection=["inlibrary"])},
    })
    stats = _run(store, index, handler, monkeypatch, [_work()])
    assert stats.written == 0


def test_images_only_never_fabricates_body(env, monkeypatch):
    """An item with no text layer is a metadata record: body_status images_only
    and an EMPTY body — never OCR'd or invented."""
    store, index = env
    handler = _make_handler({
        "search": _search_json("scanned_manuscript"),
        "meta": {"scanned_manuscript": _meta_json("scanned_manuscript", has_text=False)},
    })
    stats = _run(store, index, handler, monkeypatch, [_work()])
    assert stats.written == 1
    path = "metaphysics/internet_archive/aristotle_metaphysics.md"
    fm, body = parse(store.read(path))
    assert fm.body_status is BodyStatus.IMAGES_ONLY
    assert body.strip() == ""                # no fabricated body
    assert "images-only" in fm.tags


def test_prefers_text_over_images(env, monkeypatch):
    store, index = env
    handler = _make_handler({
        "search": _search_json("images_item", "text_item"),
        "meta": {
            "images_item": _meta_json("images_item", has_text=False),
            "text_item": _meta_json("text_item", has_text=True),
        },
    })
    stats = _run(store, index, handler, monkeypatch, [_work()])
    fm, _ = parse(store.read("metaphysics/internet_archive/aristotle_metaphysics.md"))
    assert fm.id == "internet_archive:text_item"   # clean text beats images
    assert fm.body_status is BodyStatus.FULL_TEXT
