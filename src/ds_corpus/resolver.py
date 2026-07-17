"""Canon resolver: a canonical work -> the best open edition of it.

For each wanted work: hunt its configured sources, rank the candidates, select
the winner, record the runners-up as alternates, and report coverage. The gap
list (works we could not resolve, and what we tried) is a first-class
deliverable — it tells a human where an hour of curation is best spent.

Resolution stops at selection; turning a resolution into an ingested document
is the source adapter's harvest job (gutenberg harvest = P7). Keeping resolve
read-only means a human reviews edition choices *before* anything is fetched.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ds_corpus.canon import Canon, CanonWork
from ds_corpus.editions import Edition, ScoredEdition, rank_editions
from ds_corpus.http import PoliteSession
from ds_corpus.registry import SourcesRegistry
from ds_corpus.settings import Settings
from ds_corpus.store import FilesystemStore

# Source id -> hunt function. Only sources with a real hunt implementation
# appear here; a work hunting a not-yet-built source is reported honestly as
# "source not available", never silently treated as a miss of the work itself.
from ds_corpus.adapters import gutenberg as _gutenberg

HUNTERS = {
    "gutenberg": _gutenberg.search,
}


@dataclass
class Resolution:
    work_id: str
    domain: str
    status: str                              # resolved | unavailable_open
    selected: dict | None = None             # the winning edition (serialized)
    selected_score: float | None = None
    selected_signals: dict = field(default_factory=dict)
    alternates: list[dict] = field(default_factory=list)
    tried: dict = field(default_factory=dict)   # source_id -> candidate count
    unbuilt_sources: list[str] = field(default_factory=list)
    disqualified: list[dict] = field(default_factory=list)
    note: str | None = None


def _ed_summary(se: ScoredEdition, rank: int) -> dict:
    e = se.edition
    return {
        "source": e.source_id,
        "edition_id": e.edition_id,
        "title": e.title,
        "translators": e.translators,
        "url": e.url,
        "score": se.score,
        "rank": rank,
        "disqualified": se.disqualified,
    }


def resolve_work(
    work: CanonWork,
    registry: SourcesRegistry,
    settings: Settings,
    session_factory,
) -> Resolution:
    """Hunt, rank, select. `session_factory(source)` yields a PoliteSession
    honoring that source's rate limit (injected so tests stay offline)."""
    res = Resolution(work_id=work.id, domain=work.domain, status="unavailable_open")
    pool: list[Edition] = []

    for source_id in work.hunt.sources:
        hunter = HUNTERS.get(source_id)
        if hunter is None:
            res.unbuilt_sources.append(source_id)
            continue
        source = registry.get(source_id)
        if source is None or not source.enabled:
            res.unbuilt_sources.append(source_id)
            continue
        with session_factory(source) as session:
            try:
                found = hunter(session, work)
            except Exception as e:  # a source failing must not sink the work
                res.tried[source_id] = f"error: {type(e).__name__}"
                continue
        res.tried[source_id] = len(found)
        pool.extend(found)

    if not pool:
        res.note = (
            "no candidates from any built hunt source"
            + (f"; unbuilt: {res.unbuilt_sources}" if res.unbuilt_sources else "")
        )
        return res

    ranked = rank_editions(work, pool)
    viable = [s for s in ranked if s.disqualified is None]
    res.disqualified = [_ed_summary(s, i) for i, s in enumerate(ranked) if s.disqualified]

    if not viable:
        res.note = "all candidates disqualified (see disqualified list)"
        return res

    winner = viable[0]
    res.status = "resolved"
    res.selected = _ed_summary(winner, 0)
    res.selected_score = winner.score
    res.selected_signals = winner.signals
    res.alternates = [_ed_summary(s, i + 1) for i, s in enumerate(viable[1:5])]
    return res


def resolve_all(
    canon: Canon,
    registry: SourcesRegistry,
    settings: Settings,
    session_factory,
    domain: str | None = None,
    log=lambda *_: None,
) -> list[Resolution]:
    works = canon.by_domain(domain) if domain else canon.works
    out: list[Resolution] = []
    for w in works:
        log(f"resolving {w.id} ...")
        r = resolve_work(w, registry, settings, session_factory)
        log(f"  -> {r.status}" + (f" [{r.selected['title']}]" if r.selected else ""))
        out.append(r)
    return out


def write_coverage(
    resolutions: list[Resolution],
    canon: Canon,
    store: FilesystemStore,
    domain: str | None = None,
) -> dict:
    """Write _canon/coverage.json and _canon/WANTED.md. Returns the summary."""
    resolved = [r for r in resolutions if r.status == "resolved"]
    unresolved = [r for r in resolutions if r.status != "resolved"]

    by_domain: dict[str, dict] = {}
    for r in resolutions:
        d = by_domain.setdefault(r.domain, {"resolved": 0, "wanted": 0})
        d["resolved" if r.status == "resolved" else "wanted"] += 1

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domain_filter": domain,
        "total": len(resolutions),
        "resolved": len(resolved),
        "wanted": len(unresolved),
        "by_domain": by_domain,
        "resolutions": [asdict(r) for r in resolutions],
    }
    store.write("_canon/coverage.json", json.dumps(summary, indent=2, ensure_ascii=False))

    lines = [
        "# WANTED — canon works with no resolved open edition",
        "",
        f"_Generated {summary['generated_at']}_"
        + (f" · domain: {domain}" if domain else ""),
        "",
        f"Resolved **{len(resolved)}** / {len(resolutions)}. "
        f"The works below are where a human curation hour pays off.",
        "",
    ]
    if not unresolved:
        lines.append("Nothing wanted — every hunted work resolved. 🎉")
    for r in unresolved:
        work = canon.get(r.work_id)
        lines.append(f"## {work.title if work else r.work_id}  ·  `{r.work_id}`")
        if work:
            lines.append(f"- **Author:** {work.author} ({work.original_year})")
        lines.append(f"- **Tried:** {r.tried or '—'}")
        if r.unbuilt_sources:
            lines.append(f"- **Hunt sources not yet built:** {', '.join(r.unbuilt_sources)}")
        if r.disqualified:
            lines.append(f"- **Disqualified candidates:** {len(r.disqualified)} "
                         f"(e.g. {r.disqualified[0]['disqualified']})")
        if r.note:
            lines.append(f"- **Note:** {r.note}")
        lines.append("")
    store.write("_canon/WANTED.md", "\n".join(lines) + "\n")
    return summary
