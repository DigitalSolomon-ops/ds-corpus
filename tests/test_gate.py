from __future__ import annotations

from ds_corpus.gate import Decision, GateItem, evaluate, rejection_markdown
from ds_corpus.significance import SignificanceSignals
from ds_corpus.triage import TriageError, TriageRecord, TriageVerdict


class FakeTriageClient:
    """Deterministic offline judge. Scores by a rule so tests are stable, and
    records what it was asked to triage."""

    def __init__(self, rule=None, fail=False):
        self.rule = rule or (lambda rec: 80)
        self.seen: list[str] = []
        self.fail = fail

    def triage(self, records: list[TriageRecord]) -> list[TriageVerdict]:
        if self.fail:
            raise TriageError("simulated triage outage")
        self.seen = [r.doc_id for r in records]
        return [
            TriageVerdict(r.doc_id, self.rule(r), "cat", True, False, "judged")
            for r in records
        ]


def _item(doc_id, *, canon=False, authority=0, cited=None, primary=False, body="word " * 2000):
    return GateItem(
        doc_id=doc_id,
        fm_fields={"title": doc_id, "domain": "coding"},
        body=body,
        signals=SignificanceSignals(
            canon_hit=canon, authority_count=authority, cited_by=cited,
            primary_source=primary, original_year=2000, edition_quality=5),
    )


def test_canon_hit_accepted_without_triage():
    client = FakeTriageClient()
    r = evaluate([_item("k", canon=True)], threshold=45, triage_client=client, max_triage_usd=5)
    assert r.accepted[0].decision is Decision.ACCEPT_CANON
    assert r.accepted[0].significance == 100
    assert client.seen == []                 # canon hits never cost Claude


def test_below_floor_rejected_without_triage():
    client = FakeTriageClient()
    # only edition_quality=5 -> score 5, below floor 45
    r = evaluate([_item("weak")], threshold=45, triage_client=client, max_triage_usd=5)
    assert r.rejected[0].decision is Decision.REJECT_DETERMINISTIC
    assert client.seen == []                 # no Claude spent on obvious chaff
    assert "below deterministic floor" in r.rejected[0].rationale


def test_survivor_triaged_and_accepted():
    client = FakeTriageClient(rule=lambda rec: 90)
    # authority 2 (20) + cited 1000 (15) + edition 5 + survival -> clears 45
    r = evaluate([_item("strong", authority=2, cited=1000, primary=True)],
                 threshold=45, triage_client=client, max_triage_usd=5)
    assert client.seen == ["strong"]
    assert r.accepted[0].decision is Decision.ACCEPT_TRIAGE
    assert r.accepted[0].triage["rationale"] == "judged"


def test_survivor_triaged_and_rejected_keeps_rationale():
    client = FakeTriageClient(rule=lambda rec: 20)   # judge says chaff
    r = evaluate([_item("meh", authority=2, cited=1000, primary=True)],
                 threshold=45, triage_client=client, max_triage_usd=5)
    assert r.rejected[0].decision is Decision.REJECT_TRIAGE
    assert r.rejected[0].triage is not None          # verdict retained for audit


def test_cost_cap_leaves_records_pending_not_rejected():
    client = FakeTriageClient()
    items = [_item(f"d{i}", authority=2, cited=1000, primary=True) for i in range(3)]
    r = evaluate(items, threshold=45, triage_client=client, max_triage_usd=0.0000001)
    assert client.seen == []                         # cap tripped before any call
    assert len(r.pending) == 3
    assert all(o.decision is Decision.PENDING_BUDGET for o in r.pending)
    assert r.rejected == []                          # never discarded


def test_triage_outage_leaves_pending():
    client = FakeTriageClient(fail=True)
    r = evaluate([_item("d", authority=2, cited=1000, primary=True)],
                 threshold=45, triage_client=client, max_triage_usd=5)
    assert r.accepted == [] and r.rejected == []
    assert r.pending[0].decision is Decision.ERROR


def test_no_client_leaves_survivors_pending():
    r = evaluate([_item("d", authority=2, cited=1000, primary=True)],
                 threshold=45, triage_client=None, max_triage_usd=5)
    assert r.pending[0].decision is Decision.PENDING_BUDGET


def test_audit_sample_flags_decisions():
    client = FakeTriageClient(rule=lambda rec: 90 if "yes" in rec.doc_id else 10)
    items = ([_item(f"yes{i}", authority=2, cited=1000, primary=True) for i in range(15)]
             + [_item(f"no{i}") for i in range(15)])  # 15 accept-ish + 15 floor-rejects
    r = evaluate(items, threshold=45, triage_client=client, max_triage_usd=5)
    assert len(r.audit_sample) <= 10
    assert r.audit_sample and all(x["flagged_for_review"] for x in r.audit_sample)


def test_rejection_markdown_has_rationale():
    from ds_corpus.gate import GateOutcome
    md = rejection_markdown(GateOutcome("d", Decision.REJECT_TRIAGE, 12, "too thin",
                                        {"significance": 12}))
    assert "too thin" in md and "reject_triage" in md
