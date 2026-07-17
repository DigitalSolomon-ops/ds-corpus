from __future__ import annotations

from pathlib import Path

from ds_corpus.adapters.arxiv import _rel_path, parse_oai_page

FIXTURE = (Path(__file__).parent / "fixtures" / "arxiv_oai_page.xml").read_text(encoding="utf-8")


def test_parse_records_and_token():
    records, token = parse_oai_page(FIXTURE)
    assert token == "6001234|1001"
    assert [r.arxiv_id for r in records] == ["2601.01234", "2601.09999"]  # deleted skipped


def test_record_fields():
    r = parse_oai_page(FIXTURE)[0][0]
    assert r.title == "On Computable Corpora and Their Curation"  # whitespace collapsed
    assert r.authors == ["Turing, Alan M.", "Hopper, Grace"]
    assert r.datestamp == "2026-01-05"
    assert r.created == "2026-01-03"
    assert r.categories == ["cs.DL", "cs.CL"]
    assert r.license_uri == "http://creativecommons.org/licenses/by/4.0/"


def test_default_arxiv_grant_is_present_but_will_be_gated():
    r = parse_oai_page(FIXTURE)[0][1]
    assert r.license_uri == "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"
    from ds_corpus.licensing import is_allowed, resolve
    assert not is_allowed(resolve(r.license_uri), ["cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "public-domain"])


def test_no_records_match_is_empty_not_error():
    xml = (
        '<?xml version="1.0"?>'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        '<error code="noRecordsMatch">no matches</error></OAI-PMH>'
    )
    assert parse_oai_page(xml) == ([], None)


def test_rel_path_new_and_legacy_ids():
    assert _rel_path("2601.01234") == "coding/arxiv/2601/2601.01234.md"
    assert _rel_path("cs/0309136") == "coding/arxiv/legacy/cs-0309136.md"
