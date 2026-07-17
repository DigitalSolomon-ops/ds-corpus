"""HathiTrust full-view gate + generic IIIF images-only adapter.

Covers the brief's §12 fixtures: a HathiTrust limited-view response (never
ingested) and a IIIF manifest (images_only, no fabricated body, license gated).
"""

from __future__ import annotations

import httpx
import pytest

from ds_corpus.adapters.hathitrust import (
    is_full_view,
    license_for_rights,
    select_full_view_item,
)
from ds_corpus.adapters.iiif import _label, _rights_url
from ds_corpus.canon import Canon
from ds_corpus.frontmatter import BodyStatus, parse
from ds_corpus.http import PoliteSession
from ds_corpus.index import SQLiteIndex
from ds_corpus.registry import load_registry
from ds_corpus.scheduler import run_source
from ds_corpus.settings import load_settings
from ds_corpus.store import FilesystemStore

from conftest import CONFIG_DIR


# --- HathiTrust rights gate (unit) ---------------------------------------

def test_full_view_rights_allowed():
    for code in ["pd", "pdus", "world", "cc-zero", "cc-by-4.0", "cc-by-sa-3.0"]:
        assert is_full_view(code), code


def test_limited_view_rights_rejected():
    for code in ["ic", "und", "nobody", "op", "orph", "icus", "cc-by-nc-nd-4.0", "", None]:
        assert not is_full_view(code), code


def test_license_mapping():
    assert license_for_rights("pd") == "public domain"
    assert license_for_rights("cc-zero") == "cc0-1.0"
    assert license_for_rights("ic") is None


def test_select_skips_limited_prefers_fullview():
    # §12 fixture: a record whose items are limited-view except one PD item.
    block = {"items": [
        {"htid": "mdp.limited1", "rightsCode": "ic"},
        {"htid": "mdp.limited2", "rightsCode": "und"},
        {"htid": "uc1.open", "rightsCode": "pd"},
    ]}
    picked = select_full_view_item(block)
    assert picked["htid"] == "uc1.open"


def test_select_returns_none_when_all_limited():
    block = {"items": [{"htid": "a", "rightsCode": "ic"}, {"htid": "b", "rightsCode": "nobody"}]}
    assert select_full_view_item(block) is None


# --- harvest harness -----------------------------------------------------

@pytest.fixture()
def env(tmp_path):
    store = FilesystemStore(tmp_path / "lib")
    index = SQLiteIndex(tmp_path / "lib" / "_index.sqlite3")
    yield store, index
    index.close()


def _patched_init(handler):
    real = PoliteSession.__init__

    def init(self, ua, rate, respect_robots=True, transport=None, sleep=None):
        real(self, ua, rate, respect_robots=False,
             transport=httpx.MockTransport(handler), sleep=lambda _: None)
    return init


def _run(store, index, handler, monkeypatch, source_id, query_override):
    monkeypatch.setattr(PoliteSession, "__init__", _patched_init(handler))
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    source = load_registry(CONFIG_DIR / "sources.yaml").get(source_id)
    source = source.model_copy(update={"query": query_override})
    return run_source(source, settings, store, index, canon=Canon(works=[]),
                      log=lambda *_: None)


# --- HathiTrust: limited-view NEVER ingested -----------------------------

def test_hathitrust_limited_view_never_ingested(env, monkeypatch):
    store, index = env
    # §12 limited-view fixture: every item is in-copyright / undetermined.
    limited = {"oclc:999": {"records": {"1": {"titles": ["A Copyrighted Book"]}},
                            "items": [{"htid": "mdp.x", "rightsCode": "ic"},
                                      {"htid": "mdp.y", "rightsCode": "und"}]}}
    handler = lambda r: httpx.Response(200, json=limited)
    stats = _run(store, index, handler, monkeypatch, "hathitrust", {"ids": ["oclc:999"]})
    assert stats.written == 0
    assert list(store.list()) == []                    # nothing ingested at all


def test_hathitrust_full_view_ingested_as_metadata(env, monkeypatch):
    store, index = env
    full = {"oclc:424495": {"records": {"1": {"titles": ["The Federalist"],
                                              "recordURL": "https://catalog.hathitrust.org/Record/1"}},
                            "items": [{"htid": "uc1.b1", "rightsCode": "ic"},   # skipped
                                      {"htid": "uc1.pd", "rightsCode": "pd",
                                       "itemURL": "https://babel.hathitrust.org/cgi/pt?id=uc1.pd"}]}}
    handler = lambda r: httpx.Response(200, json=full)
    stats = _run(store, index, handler, monkeypatch, "hathitrust", {"ids": ["oclc:424495"]})
    assert stats.written == 1
    path = next(p for p in store.list() if "hathitrust" in p)
    fm, body = parse(store.read(path))
    assert fm.body_status is BodyStatus.METADATA_ONLY
    assert fm.license == "public-domain"
    assert body.strip() == ""                          # metadata only, no body
    assert fm.id == "hathitrust:uc1.pd"                # the full-view item, not the ic one


# --- IIIF: images_only, fail-closed license ------------------------------

_WALTERS = {
    "@id": "https://purl.stanford.edu/qm670kv1873/iiif/manifest",
    "@context": "http://iiif.io/api/presentation/2/context.json",
    "label": "Walters Ms. W.168, Book of Hours",
    "license": "https://creativecommons.org/licenses/by-sa/3.0/legalcode",
    "attribution": "Walters Art Museum",
}
_VATICAN = {
    "@id": "https://digi.vatlib.it/iiif/MSS_Vat.lat.3773/manifest.json",
    "label": "Vat.lat.3773",
    "attribution": "Images Copyright Biblioteca Apostolica Vaticana",
    # no license / rights -> not open
}


def test_iiif_label_and_rights_parse():
    assert _label(_WALTERS).startswith("Walters")
    assert _rights_url(_WALTERS).endswith("by-sa/3.0/legalcode")
    assert _rights_url(_VATICAN) is None
    # IIIF 3.0 language-map label
    assert _label({"label": {"en": ["English Title"]}}) == "English Title"


def test_iiif_open_manifest_becomes_images_only(env, monkeypatch):
    store, index = env
    handler = lambda r: httpx.Response(200, json=_WALTERS)
    stats = _run(store, index, handler, monkeypatch, "iiif",
                 {"manifests": ["https://purl.stanford.edu/qm670kv1873/iiif/manifest"]})
    assert stats.written == 1
    path = next(p for p in store.list() if "iiif" in p and p.endswith(".md"))
    fm, body = parse(store.read(path))
    assert fm.body_status is BodyStatus.IMAGES_ONLY
    assert body.strip() == ""                          # NEVER a fabricated body
    assert fm.iiif_manifest.endswith("/iiif/manifest")
    assert fm.license == "cc-by-sa-3.0"
    assert "manuscript" in fm.tags
    # verify must be clean: empty-body records hash on identity, not empty body
    from ds_corpus.writer import verify
    assert verify(store, index) == []


def test_iiif_closed_manifest_skipped_faildclosed(env, monkeypatch):
    """Vatican manuscript: viewable but not openly licensed -> not ingested."""
    store, index = env
    handler = lambda r: httpx.Response(200, json=_VATICAN)
    stats = _run(store, index, handler, monkeypatch, "iiif",
                 {"manifests": ["https://digi.vatlib.it/iiif/MSS_Vat.lat.3773/manifest.json"]})
    assert stats.written == 0
    assert list(store.list()) == []                    # openness constraint holds


def test_iiif_mixed_ingests_only_open(env, monkeypatch):
    store, index = env

    def handler(request):
        if "vatlib" in str(request.url):
            return httpx.Response(200, json=_VATICAN)
        return httpx.Response(200, json=_WALTERS)

    stats = _run(store, index, handler, monkeypatch, "iiif", {"manifests": [
        "https://purl.stanford.edu/qm670kv1873/iiif/manifest",
        "https://digi.vatlib.it/iiif/MSS_Vat.lat.3773/manifest.json",
    ]})
    assert stats.written == 1                           # Walters in, Vatican out
