"""Deterministic significance scoring — the cheap first stage of the gate.

Returns 0–100 from weighted, fully explainable signals. It runs on every
non-canon candidate before any money is spent on Claude: obvious chaff is
rejected here for free, and only what clears the floor is worth a triage call.

A canon hit short-circuits to 100 — a work we deliberately asked for is
significant by definition, and never needs judging.

Signal weights (brief §5.1):
    canon_hit          50   (short-circuit)
    authority_count    20   independent authority lists naming it
    citation_impact    15   log-scaled cited_by (OpenAlex)
    primary_source      5
    survival            5    age-weighted, still-referenced
    edition_quality     5    clean text > OCR > images
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

W_AUTHORITY = 20
W_CITATION = 15
W_PRIMARY = 5
W_SURVIVAL = 5
W_EDITION = 5

# "now" for survival math. Passed in where it matters; this is only a default
# so the module stays import-pure (no wall-clock at import time).
DEFAULT_CURRENT_YEAR = 2026


@dataclass
class SignificanceSignals:
    canon_hit: bool = False
    authority_count: int = 0
    cited_by: int | None = None          # OpenAlex citation count; None = unknown
    primary_source: bool = False
    original_year: int | None = None
    edition_quality: int = 0             # 0..5, from the editions ranker/format


@dataclass
class SignificanceResult:
    score: int                            # 0..100
    is_canon_hit: bool
    breakdown: dict = field(default_factory=dict)

    def clears(self, threshold: int) -> bool:
        return self.score >= threshold


def score(sig: SignificanceSignals, current_year: int = DEFAULT_CURRENT_YEAR) -> SignificanceResult:
    if sig.canon_hit:
        # Short-circuit: no arithmetic, no triage, no spend.
        return SignificanceResult(100, True, {"canon_hit": True})

    breakdown: dict = {}
    total = 0.0

    # Authority lists — the strongest non-canon signal. Two independent lists
    # naming a work is already decisive; more saturates.
    authority = min(W_AUTHORITY, sig.authority_count * 10)
    breakdown["authority"] = authority
    total += authority

    # Citation impact, log-scaled so a runaway cite count can't dominate.
    # None means "we have no citation data" — score 0 here, not a penalty; the
    # absence is recorded so triage can still weigh in.
    if sig.cited_by is not None:
        citation = min(W_CITATION, math.log10(sig.cited_by + 1) * 5)
        breakdown["citation_impact"] = round(citation, 1)
        total += citation
    else:
        breakdown["citation_impact"] = None

    if sig.primary_source:
        breakdown["primary_source"] = W_PRIMARY
        total += W_PRIMARY

    # Survival: age matters only for works that are *still referenced*. An old
    # paper nobody cites isn't "surviving", it's just old — so this requires
    # positive citations to count at all.
    if sig.original_year is not None and sig.cited_by:
        age = max(0, current_year - sig.original_year)
        survival = min(W_SURVIVAL, (min(age, 200) / 200) * W_SURVIVAL)
        breakdown["survival"] = round(survival, 1)
        total += survival
    else:
        breakdown["survival"] = 0

    edition = min(W_EDITION, max(0, sig.edition_quality))
    breakdown["edition_quality"] = edition
    total += edition

    return SignificanceResult(int(round(min(100.0, total))), False, breakdown)
