from __future__ import annotations

from ds_corpus.significance import SignificanceSignals as S
from ds_corpus.significance import score


def test_canon_hit_short_circuits_to_100():
    r = score(S(canon_hit=True, authority_count=0))
    assert r.score == 100 and r.is_canon_hit
    assert r.breakdown == {"canon_hit": True}


def test_canon_hit_beats_everything_regardless_of_other_signals():
    # Even with zero other evidence, a canon hit clears any threshold.
    r = score(S(canon_hit=True))
    assert r.clears(45) and r.clears(99)


def test_authorities_are_strongest_non_canon_signal():
    assert score(S(authority_count=1)).breakdown["authority"] == 10
    assert score(S(authority_count=2)).breakdown["authority"] == 20
    assert score(S(authority_count=5)).breakdown["authority"] == 20  # saturates


def test_citation_impact_log_scaled_and_capped():
    assert score(S(cited_by=0)).breakdown["citation_impact"] == 0.0
    assert score(S(cited_by=9)).breakdown["citation_impact"] == 5.0     # log10(10)*5
    assert score(S(cited_by=100000)).breakdown["citation_impact"] == 15  # capped


def test_missing_citation_data_is_none_not_penalty():
    r = score(S(cited_by=None, authority_count=1))
    assert r.breakdown["citation_impact"] is None
    assert r.score == 10  # only the authority counted; no penalty for unknown


def test_survival_requires_being_still_cited():
    old_uncited = score(S(original_year=1850, cited_by=0))
    old_cited = score(S(original_year=1850, cited_by=50))
    assert old_uncited.breakdown["survival"] == 0
    assert old_cited.breakdown["survival"] > 0


def test_edition_quality_clamped():
    assert score(S(edition_quality=40)).breakdown["edition_quality"] == 5
    assert score(S(edition_quality=-3)).breakdown["edition_quality"] == 0


def test_below_and_above_threshold():
    # A random work with no signals scores low -> rejected cheaply.
    weak = score(S(edition_quality=5))
    assert weak.score == 5 and not weak.clears(45)
    # Two authority hits + primary + good edition clears the floor -> triage.
    strong = score(S(authority_count=2, primary_source=True, edition_quality=5, cited_by=1000))
    assert strong.clears(45)


def test_score_never_exceeds_100():
    r = score(S(authority_count=9, cited_by=10**9, primary_source=True,
                original_year=1600, edition_quality=5))
    assert r.score <= 100
