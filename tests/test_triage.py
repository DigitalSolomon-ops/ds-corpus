from __future__ import annotations

import pytest

from ds_corpus.triage import (
    TriageError,
    TriageRecord,
    TriageVerdict,
    build_excerpt,
    build_user_content,
    estimate_cost_usd,
    parse_verdict,
)


def test_build_excerpt_head_and_tail():
    body = " ".join(str(i) for i in range(3000))
    head, tail = build_excerpt(body, head_words=1500, tail_words=500)
    assert head.split()[0] == "0" and head.split()[-1] == "1499"
    assert tail.split()[-1] == "2999"


def test_build_excerpt_short_body_no_tail():
    head, tail = build_excerpt("just a few words here", head_words=1500, tail_words=500)
    assert head == "just a few words here"
    assert tail == ""


def test_parse_verdict_strict_json():
    v = parse_verdict("gutenberg:1", '{"significance": 82, "category": "epistemology",'
                      ' "is_primary_source": true, "is_derivative": false,'
                      ' "rationale": "Foundational.", "suggested_tags": ["kant"]}')
    assert v.significance == 82 and v.category == "epistemology"
    assert v.is_primary_source and not v.is_derivative
    assert v.suggested_tags == ["kant"]


def test_parse_verdict_tolerates_code_fence():
    v = parse_verdict("x", '```json\n{"significance": 30}\n```')
    assert v.significance == 30


def test_parse_verdict_clamps_range():
    assert parse_verdict("x", '{"significance": 250}').significance == 100
    assert parse_verdict("x", '{"significance": -5}').significance == 0


def test_parse_verdict_rejects_garbage():
    with pytest.raises(TriageError):
        parse_verdict("x", "not json at all")
    with pytest.raises(TriageError):
        parse_verdict("x", '{"category": "no significance field"}')


def test_verdict_accept_threshold():
    v = TriageVerdict("x", 45, "c", False, False, "")
    assert v.accepted(45) and not v.accepted(46)


def _rec(doc_id="d", n_words=2000):
    body = " ".join(["word"] * n_words)
    head, tail = build_excerpt(body)
    return TriageRecord(doc_id, {"title": "T", "domain": "coding"}, head, tail)


def test_estimate_cost_scales_and_discounts():
    one = estimate_cost_usd([_rec("a")])
    ten = estimate_cost_usd([_rec(f"a{i}") for i in range(10)])
    assert 0 < one < ten
    # batch discount is applied: a big batch is still cents, not dollars
    assert estimate_cost_usd([_rec(f"a{i}") for i in range(50)]) < 1.0


def test_user_content_includes_metadata_and_excerpts():
    rec = _rec()
    content = build_user_content(rec)
    assert "FRONT-MATTER" in content and "OPENING" in content
