"""The significance gate — orchestrates deterministic scoring and Claude triage.

For a batch of non-canon candidates the flow is:

    canon hit?            -> accept, significance=100, no triage, no spend
    deterministic < floor -> reject to _rejected/ (cheap, no Claude)
    deterministic >= floor -> triage with Claude; accept/reject by verdict

Canon hits never reach here — canon-driven adapters short-circuit upstream.
Cost is capped: if the estimated triage spend would exceed the run's budget,
triage is skipped and those records stay *pending* (not rejected), so a cap
never silently discards work. An audit sample of decisions is logged for the
human to spot-check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

from ds_corpus import significance as sig_mod
from ds_corpus.significance import SignificanceSignals
from ds_corpus.triage import (
    TriageClient,
    TriageError,
    TriageRecord,
    build_excerpt,
    estimate_cost_usd,
)


class Decision(str, Enum):
    ACCEPT_CANON = "accept_canon"
    ACCEPT_TRIAGE = "accept_triage"
    REJECT_DETERMINISTIC = "reject_deterministic"   # below floor, no Claude spent
    REJECT_TRIAGE = "reject_triage"                 # Claude judged it chaff
    PENDING_BUDGET = "pending_budget"               # cost cap hit; try next run
    ERROR = "error"


@dataclass
class GateItem:
    """One candidate entering the gate. `signals` feeds the deterministic
    scorer; body feeds triage excerpting."""
    doc_id: str
    fm_fields: dict
    body: str
    signals: SignificanceSignals


@dataclass
class GateOutcome:
    doc_id: str
    decision: Decision
    significance: int
    rationale: str
    triage: dict | None = None          # full verdict when triaged; else None


@dataclass
class GateResult:
    accepted: list[GateOutcome] = field(default_factory=list)
    rejected: list[GateOutcome] = field(default_factory=list)
    pending: list[GateOutcome] = field(default_factory=list)
    triaged_count: int = 0
    estimated_usd: float = 0.0
    audit_sample: list[dict] = field(default_factory=list)


# how many accept/reject decisions to flag for human spot-check per run
AUDIT_SAMPLE_SIZE = 10


def evaluate(
    items: list[GateItem],
    *,
    threshold: int,
    triage_client: TriageClient | None,
    max_triage_usd: float,
    current_year: int = sig_mod.DEFAULT_CURRENT_YEAR,
    audit_index: int = 0,
    log=lambda *_: None,
) -> GateResult:
    """Run the two-stage gate. `audit_index` seeds deterministic sampling
    without a RNG (which is unavailable in some runtimes and non-reproducible)."""
    result = GateResult()

    # Stage 1 — deterministic. Canon hits accept; below-floor reject cheaply;
    # survivors queue for triage.
    to_triage: list[tuple[GateItem, TriageRecord]] = []
    for item in items:
        score = sig_mod.score(item.signals, current_year=current_year)
        if score.is_canon_hit:
            result.accepted.append(GateOutcome(
                item.doc_id, Decision.ACCEPT_CANON, 100, "canon hit — significant by definition"))
            continue
        if not score.clears(threshold):
            result.rejected.append(GateOutcome(
                item.doc_id, Decision.REJECT_DETERMINISTIC, score.score,
                f"below deterministic floor ({score.score} < {threshold}); signals={score.breakdown}"))
            continue
        head, tail = build_excerpt(item.body)
        to_triage.append((item, TriageRecord(item.doc_id, item.fm_fields, head, tail)))

    # Stage 2 — Claude triage on survivors, under the cost cap.
    records = [rec for _, rec in to_triage]
    est = estimate_cost_usd(records) if records else 0.0
    result.estimated_usd = est

    if records:
        if triage_client is None:
            for item, _ in to_triage:
                result.pending.append(GateOutcome(
                    item.doc_id, Decision.PENDING_BUDGET, -1,
                    "passed deterministic floor but no triage client configured"))
        elif est > max_triage_usd:
            log(f"[gate] triage skipped: est ${est:.4f} > cap ${max_triage_usd:.2f}; "
                f"{len(records)} record(s) left pending")
            for item, _ in to_triage:
                result.pending.append(GateOutcome(
                    item.doc_id, Decision.PENDING_BUDGET, -1,
                    f"cost cap: est ${est:.4f} exceeds ${max_triage_usd:.2f}"))
        else:
            _run_triage(to_triage, threshold, triage_client, result, log)

    _build_audit_sample(result, audit_index)
    return result


def _run_triage(to_triage, threshold, triage_client, result: GateResult, log) -> None:
    by_id = {item.doc_id: item for item, _ in to_triage}
    records = [rec for _, rec in to_triage]
    try:
        verdicts = triage_client.triage(records)
    except TriageError as e:
        # A triage failure leaves the batch pending, not rejected — we don't
        # discard a document because the judge was unavailable.
        log(f"[gate] triage error, leaving batch pending: {e}")
        for item, _ in to_triage:
            result.pending.append(GateOutcome(
                item.doc_id, Decision.ERROR, -1, f"triage error: {e}"))
        return

    result.triaged_count = len(verdicts)
    seen = set()
    for v in verdicts:
        seen.add(v.doc_id)
        verdict_dict = {
            "significance": v.significance, "category": v.category,
            "is_primary_source": v.is_primary_source, "is_derivative": v.is_derivative,
            "rationale": v.rationale, "suggested_tags": v.suggested_tags,
        }
        if v.accepted(threshold):
            result.accepted.append(GateOutcome(
                v.doc_id, Decision.ACCEPT_TRIAGE, v.significance, v.rationale, verdict_dict))
        else:
            result.rejected.append(GateOutcome(
                v.doc_id, Decision.REJECT_TRIAGE, v.significance,
                v.rationale or "below threshold on triage", verdict_dict))
    # Any survivor the judge didn't return a verdict for stays pending.
    for doc_id, item in by_id.items():
        if doc_id not in seen:
            result.pending.append(GateOutcome(
                doc_id, Decision.PENDING_BUDGET, -1, "no triage verdict returned"))


def _build_audit_sample(result: GateResult, audit_index: int) -> None:
    """Flag up to AUDIT_SAMPLE_SIZE accept/reject decisions for human review,
    spread across the batch deterministically (every k-th, seeded by run)."""
    decided = result.accepted + result.rejected
    if not decided:
        return
    step = max(1, len(decided) // AUDIT_SAMPLE_SIZE)
    for n, outcome in enumerate(decided):
        if (n + audit_index) % step == 0 and len(result.audit_sample) < AUDIT_SAMPLE_SIZE:
            result.audit_sample.append({
                "doc_id": outcome.doc_id,
                "decision": outcome.decision.value,
                "significance": outcome.significance,
                "rationale": outcome.rationale,
                "flagged_for_review": True,
            })


def rejection_markdown(outcome: GateOutcome) -> str:
    """The _rejected/<id>.md audit record — kept, never deleted, so the rubric
    can be tuned against real decisions."""
    fm = {
        "id": outcome.doc_id,
        "decision": outcome.decision.value,
        "significance": outcome.significance,
        "rationale": outcome.rationale,
    }
    if outcome.triage:
        fm["triage"] = outcome.triage
    return "---\n" + json.dumps(fm, indent=2, ensure_ascii=False) + "\n---\n"
