from __future__ import annotations

from pathlib import Path

from ds_corpus.adapters.arxiv import _rel_path, parse_oai_page, year_from_id

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


def test_year_from_id():
    assert year_from_id("2409.14583") == 2024      # new-style
    assert year_from_id("2601.01234") == 2026
    assert year_from_id("cs/0309136") == 2003      # old-style archive/YYMMNNN
    assert year_from_id("math/9108001") == 1991    # earliest arXiv era -> 19YY
    assert year_from_id("nonsense") is None


def test_candidate_year_prefers_id_over_created():
    # fixture record 2601.01234 has created=2026-01-03; id encodes 2026 too,
    # but the point is the id wins. Use the parsed record through the adapter.
    from ds_corpus.adapters.arxiv import ArxivAdapter
    rec = parse_oai_page(FIXTURE)[0][0]
    rec.created = "2030-01-01"                      # simulate a later-version created date
    cand = ArxivAdapter()._candidate(rec)
    assert cand.fm_fields["original_year"] == 2026  # from the id, not 2030
