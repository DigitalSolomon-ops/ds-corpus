from __future__ import annotations

import pytest

from ds_corpus.frontmatter import BodyStatus, FrontMatter, parse, to_markdown
from ds_corpus.index import SQLiteIndex
from ds_corpus.normalize import body_sha256, normalize_markdown
from ds_corpus.store import FilesystemStore
from ds_corpus.writer import Outcome, ingest, verify

ALLOW = ["public-domain", "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0"]


@pytest.fixture()
def env(tmp_path):
    store = FilesystemStore(tmp_path / "library")
    index = SQLiteIndex(tmp_path / "library" / "_index.sqlite3")
    yield store, index
    index.close()


def _fields(doc_id="gutenberg:1", **over):
    d = {
        "id": doc_id,
        "canon_id": None,
        "title": "Test Work",
        "authors": ["Author, Test"],
        "domain": "metaphysics",
        "source_id": "gutenberg",
        "source_url": "https://example.org/1",
        "publisher": "Test",
        "original_year": 1781,
        "source_format": "text",
        "converter": "ds-corpus/0.2.0",
        "body_status": "full_text",
        "attribution": "Author, T. (1781). Test Work. Public domain.",
    }
    d.update(over)
    return d


def _ingest(store, index, *, doc_id="gutenberg:1", body="Hello corpus world.",
            raw_license="Public domain", rel_path="metaphysics/gutenberg/test.md", **over):
    return ingest(
        fm_fields=_fields(doc_id, **over),
        body=body,
        raw_license=raw_license,
        rel_path=rel_path,
        store=store,
        index=index,
        license_allowlist=ALLOW,
    )


# --- THE test that matters most: no license, no write -------------------

@pytest.mark.parametrize("raw", [None, "", "all rights reserved", "see terms", "cc-by"])
def test_unresolved_license_is_never_written(env, raw):
    store, index = env
    r = _ingest(store, index, raw_license=raw)
    assert r.outcome is Outcome.SKIPPED_LICENSE
    assert list(store.list()) == []                 # nothing on disk
    assert index.counts()["total"] == 0             # nothing in index


def test_known_but_closed_license_is_never_written(env):
    store, index = env
    r = _ingest(store, index, raw_license="http://creativecommons.org/licenses/by-nc/4.0/")
    assert r.outcome is Outcome.SKIPPED_LICENSE
    assert list(store.list()) == []


# --- happy path, dedup, supersede ---------------------------------------

def test_write_then_roundtrip(env):
    store, index = env
    r = _ingest(store, index)
    assert r.outcome is Outcome.WRITTEN
    fm, body = parse(store.read(r.path))
    assert fm.license == "public-domain"
    assert fm.content_sha256 == body_sha256(normalize_markdown(body))
    assert fm.word_count == 3


def test_same_content_twice_is_duplicate_noop(env):
    store, index = env
    _ingest(store, index)
    r2 = _ingest(store, index, doc_id="gutenberg:1-refetch",
                 rel_path="metaphysics/gutenberg/test2.md")
    assert r2.outcome is Outcome.DUPLICATE
    assert index.counts()["total"] == 1
    assert not store.exists("metaphysics/gutenberg/test2.md")


def test_same_id_new_content_supersedes_keeps_both(env):
    store, index = env
    r1 = _ingest(store, index)
    r2 = _ingest(store, index, body="A different, better edition.")
    assert r2.outcome is Outcome.SUPERSEDED_OLD
    assert r2.doc_id == "gutenberg:1.v2"
    assert r2.path.endswith(".v2.md")
    # both files exist — never delete
    assert store.exists(r1.path) and store.exists(r2.path)
    old = index.get_document("gutenberg:1")
    assert old["status"] == "superseded" and old["superseded_by"] == "gutenberg:1.v2"
    assert index.get_document("gutenberg:1.v2")["status"] == "active"


def test_invalid_frontmatter_quarantined_not_written(env):
    store, index = env
    r = _ingest(store, index, authors=[])           # violates contract
    assert r.outcome is Outcome.QUARANTINED
    assert r.path.startswith("_quarantine/")
    assert index.counts()["total"] == 0
    assert not store.exists("metaphysics/gutenberg/test.md")


# --- images_only bodies are never fabricated -----------------------------

def test_images_only_with_body_refuses():
    fm = FrontMatter.model_validate(
        dict(
            _fields("vat:1", body_status="images_only"),
            license="public-domain",
            content_sha256=body_sha256(""),
            word_count=0,
            retrieved_at="2026-07-16T00:00:00Z",
        )
    )
    with pytest.raises(ValueError, match="refusing"):
        to_markdown(fm, "hallucinated transcription")
    assert fm.body_status is BodyStatus.IMAGES_ONLY
    assert "images_only" in to_markdown(fm, "")     # empty body is fine


# --- verify on a synthetic 50-doc library --------------------------------

def test_verify_clean_on_synthetic_50_doc_library(env):
    store, index = env
    for i in range(50):
        r = _ingest(
            store, index,
            doc_id=f"gutenberg:{i}",
            body=f"Document number {i} has its own distinct body text.",
            rel_path=f"metaphysics/gutenberg/doc-{i:03d}.md",
        )
        assert r.outcome is Outcome.WRITTEN
    assert index.counts()["total"] == 50
    assert verify(store, index) == []


def test_verify_detects_tampering_and_drift(env, tmp_path):
    store, index = env
    r = _ingest(store, index)
    # tamper with the body on disk
    p = tmp_path / "library" / r.path
    p.write_text(p.read_text(encoding="utf-8") + "\ninjected line\n", encoding="utf-8")
    problems = verify(store, index)
    assert any("body hash" in x for x in problems)
    # remove file (simulating partial state) -> missing-from-store detected
    p.unlink()
    problems = verify(store, index)
    assert any("missing from store" in x for x in problems)
