"""Run loop: one source at a time, never parallel. Budgets are hard caps
that checkpoint-and-stop cleanly; HALT is checked at every task boundary."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ds_corpus import licensing
from ds_corpus.adapters import ADAPTERS
from ds_corpus.gate import GateItem
from ds_corpus.gate import evaluate as gate_evaluate
from ds_corpus.gate import rejection_markdown
from ds_corpus.http import PoliteSession, RobotsDisallowed
from ds_corpus.index import SQLiteIndex
from ds_corpus.normalize import ConversionError
from ds_corpus.registry import SourceConfig, SourcesRegistry
from ds_corpus.settings import Settings
from ds_corpus.significance import SignificanceSignals
from ds_corpus.store import FilesystemStore
from ds_corpus.writer import Outcome, ingest


@dataclass
class RunStats:
    written: int = 0
    superseded: int = 0
    duplicates: int = 0
    skipped_license: int = 0
    quarantined: int = 0
    errors: int = 0
    seen: int = 0
    rejected: int = 0                 # significance gate rejections
    pending: int = 0                  # cost-capped / unjudged, retry next run
    triaged: int = 0                  # docs sent to Claude triage
    estimated_triage_usd: float = 0.0
    bytes_written: int = 0
    halted: bool = False
    budget_stop: str | None = None
    by_outcome_detail: list[str] = field(default_factory=list)


def _run_id(source_id: str) -> str:
    # microsecond precision so two runs in the same second don't collide on
    # the runs table's unique key.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return f"{ts}-{source_id}"


def run_source(
    source: SourceConfig,
    settings: Settings,
    store: FilesystemStore,
    index: SQLiteIndex,
    *,
    canon=None,
    dry_run: bool = False,
    limit: int | None = None,
    full: bool = False,
    log=print,
) -> RunStats:
    stats = RunStats()
    run_id = _run_id(source.id)
    started = time.monotonic()
    effective_limit = min(
        limit if limit is not None else settings.budgets.max_documents_per_run,
        settings.budgets.max_documents_per_run,
    )

    if store.halted():
        log(f"[{source.id}] HALT present — refusing to start")
        stats.halted = True
        return stats

    adapter_cls = ADAPTERS.get(source.adapter)
    if adapter_cls is None:
        raise ValueError(f"no adapter registered for {source.adapter!r}")
    adapter = adapter_cls()
    canon_driven = getattr(adapter, "canon_driven", False)
    if canon_driven:
        if canon is None:
            raise ValueError(f"source {source.id} is canon-driven but no canon was provided")
        adapter.canon = canon
    cursor = None if full else index.get_cursor(source.id)

    # Canon-driven adapters bypass the significance gate — a canon hit is
    # significant by definition. Everything else runs the two-stage gate.
    gated = not canon_driven
    gate_items: list[GateItem] = []
    bodies: dict[str, tuple[str, str | None, str]] = {}
    triage_client = _make_triage_client(settings) if (gated and not dry_run) else None

    if not dry_run:
        index.start_run(run_id)
    log(f"[{source.id}] run {run_id} cursor={cursor!r} dry_run={dry_run} limit={effective_limit}")

    # --limit caps documents WRITTEN. Most candidates fail the license gate
    # without costing a download, so scanning is cheap — but bound it anyway
    # so a run over an all-closed stretch of the source still terminates.
    scan_cap = effective_limit if dry_run else effective_limit * 500

    with PoliteSession(settings.user_agent, source.rate.requests_per_second) as session:
        try:
            for cand in adapter.harvest(session, source, cursor, scan_cap):
                stats.seen += 1
                # gated docs are written after the loop, so bound collection by
                # queue length; ungated adapters bound by docs already written.
                reached = (len(gate_items) if gated
                           else stats.written + stats.superseded) >= effective_limit
                if reached:
                    stats.budget_stop = "limit"
                    break

                # task boundary: kill switch, wall clock
                if store.halted():
                    stats.halted = True
                    log(f"[{source.id}] HALT — stopping cleanly")
                    break
                if time.monotonic() - started > settings.budgets.max_wall_seconds:
                    stats.budget_stop = "max_wall_seconds"
                    break
                if stats.bytes_written > settings.budgets.max_bytes_per_run:
                    stats.budget_stop = "max_bytes_per_run"
                    break

                doc_id = cand.fm_fields["id"]
                # pre-check license so we never download a body we can't keep
                lic = licensing.resolve(cand.raw_license)
                if not licensing.is_allowed(lic, source.license_policy.allow):
                    stats.skipped_license += 1
                    _event(store, run_id, dry_run, {
                        "event": "skipped_license", "id": doc_id,
                        "raw": cand.raw_license, "resolved": lic,
                    })
                    continue

                if dry_run:
                    log(f"  WOULD FETCH {doc_id} -> {cand.rel_path} [{lic}]")
                    continue

                try:
                    body = cand.fetch_body(session)
                except Exception as e:
                    # ConversionError/RobotsDisallowed are expected failure
                    # modes -> quarantine; anything else also quarantines but
                    # counts as an error (and blocks cursor advancement).
                    if isinstance(e, (ConversionError, RobotsDisallowed)):
                        stats.quarantined += 1
                    else:
                        stats.errors += 1
                    qpath = f"_quarantine/{doc_id.replace(':', '_').replace('/', '_')}.error.json"
                    store.write(qpath, json.dumps(
                        {"id": doc_id, "stage": "fetch/convert", "error": f"{type(e).__name__}: {e}"},
                        indent=2,
                    ))
                    _event(store, run_id, dry_run, {"event": "quarantined", "id": doc_id, "error": str(e)})
                    continue

                if gated:
                    # Non-canon docs don't write yet — they queue for the
                    # significance gate (deterministic + Claude triage) below.
                    gate_items.append(GateItem(
                        doc_id=doc_id,
                        fm_fields=cand.fm_fields,
                        body=body,
                        signals=_signals_from_fm(cand.fm_fields),
                    ))
                    bodies[doc_id] = (body, cand.raw_license, cand.rel_path)
                    continue

                _ingest_and_count(cand.fm_fields, body, cand.raw_license, cand.rel_path,
                                  store, index, source, stats, run_id, dry_run, log)
        except RobotsDisallowed as e:
            log(f"[{source.id}] robots.txt disallows {e} — stopping (fail polite)")
            stats.errors += 1

        # --- significance gate for non-canon adapters --------------------
        if gated and gate_items and not dry_run:
            gate_result = gate_evaluate(
                gate_items,
                threshold=settings.significance.threshold,
                triage_client=triage_client,
                max_triage_usd=settings.budgets.max_triage_usd,
                log=log,
            )
            stats.triaged = gate_result.triaged_count
            stats.estimated_triage_usd = gate_result.estimated_usd
            for a in gate_result.accepted:
                body, raw_lic, rel_path = bodies[a.doc_id]
                fm = dict(next(g.fm_fields for g in gate_items if g.doc_id == a.doc_id))
                fm["significance_score"] = a.significance
                if a.triage is not None:
                    fm["triage"] = a.triage
                _ingest_and_count(fm, body, raw_lic, rel_path,
                                  store, index, source, stats, run_id, dry_run, log)
            for rej in gate_result.rejected:
                rpath = f"_rejected/{rej.doc_id.replace(':', '_').replace('/', '_')}.md"
                store.write(rpath, rejection_markdown(rej))
                stats.rejected += 1
                _event(store, run_id, dry_run, {
                    "event": rej.decision.value, "id": rej.doc_id,
                    "significance": rej.significance, "rationale": rej.rationale})
            stats.pending = len(gate_result.pending)
            for p in gate_result.pending:
                _event(store, run_id, dry_run, {
                    "event": p.decision.value, "id": p.doc_id, "rationale": p.rationale})
            for entry in gate_result.audit_sample:
                _event(store, run_id, dry_run, dict(entry, event="audit_sample"))

    # persist cursor only on real runs that ended cleanly (no hard error)
    if not dry_run:
        if adapter.new_cursor and stats.errors == 0:
            index.set_cursor(source.id, adapter.new_cursor)
        index.finish_run(run_id, json.dumps(stats.__dict__, default=str))
        _event(store, run_id, dry_run, {"event": "finished", "stats": {
            k: v for k, v in stats.__dict__.items() if k != "by_outcome_detail"}})

    log(
        f"[{source.id}] done: seen={stats.seen} written={stats.written} "
        f"superseded={stats.superseded} dup={stats.duplicates} "
        f"license-skip={stats.skipped_license} quarantined={stats.quarantined} "
        f"errors={stats.errors}"
        + (f" rejected={stats.rejected} triaged={stats.triaged}" if gated else "")
        + (f" pending={stats.pending}" if stats.pending else "")
        + (f" est-triage=${stats.estimated_triage_usd:.4f}" if stats.estimated_triage_usd else "")
        + (f" STOPPED({stats.budget_stop})" if stats.budget_stop else "")
        + (" HALTED" if stats.halted else "")
    )
    return stats


def run_schedule(
    schedule: str,
    registry: SourcesRegistry,
    settings: Settings,
    store: FilesystemStore,
    index: SQLiteIndex,
    *,
    canon=None,
    dry_run: bool = False,
    log=print,
) -> dict[str, RunStats]:
    """Sequential over enabled sources on this cadence. Never parallel."""
    results: dict[str, RunStats] = {}
    for source in registry.sources:
        if not source.enabled or source.schedule.value != schedule:
            continue
        if store.halted():
            log(f"HALT present — skipping remaining sources (next: {source.id})")
            break
        results[source.id] = run_source(
            source, settings, store, index, canon=canon, dry_run=dry_run, log=log
        )
    return results


def _event(store: FilesystemStore, run_id: str, dry_run: bool, event: dict) -> None:
    if not dry_run:
        event["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.append_run_event(run_id, event)


# text-layer quality by source_format, feeding significance.edition_quality
_FORMAT_QUALITY = {"text": 5, "html": 4, "pdf": 4, "epub": 4}


def _signals_from_fm(fm: dict) -> SignificanceSignals:
    """Build deterministic-scorer signals from what a candidate already knows.
    Citation and authority enrichment (OpenAlex, authority matching) land with
    those sources; until then cited_by is unknown (None), not zero-penalized."""
    return SignificanceSignals(
        canon_hit=bool(fm.get("canon_id")),
        authority_count=len((fm.get("significance_signals") or {}).get("authorities", [])),
        cited_by=(fm.get("significance_signals") or {}).get("cited_by"),
        primary_source=bool((fm.get("significance_signals") or {}).get("primary_source", False)),
        original_year=fm.get("original_year"),
        edition_quality=_FORMAT_QUALITY.get(fm.get("source_format", ""), 3),
    )


def _make_triage_client(settings: Settings):
    """Real Batch triage client, only when triage is enabled and a key exists.
    Triage still only fires on records that clear the deterministic floor, and
    is bounded by max_triage_usd — so constructing it never forces spend."""
    import os

    if not settings.triage.enabled or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from ds_corpus.triage import AnthropicBatchTriageClient

    return AnthropicBatchTriageClient(model=settings.triage.model)


def _ingest_and_count(fm_fields, body, raw_license, rel_path, store, index,
                      source, stats: RunStats, run_id, dry_run, log) -> None:
    result = ingest(
        fm_fields=fm_fields, body=body, raw_license=raw_license, rel_path=rel_path,
        store=store, index=index, license_allowlist=source.license_policy.allow,
    )
    _event(store, run_id, dry_run, {
        "event": result.outcome.value, "id": result.doc_id,
        "path": result.path, "detail": result.detail,
    })
    if result.outcome is Outcome.WRITTEN:
        stats.written += 1
        stats.bytes_written += len(body.encode("utf-8"))
        log(f"  WROTE {result.path}")
    elif result.outcome is Outcome.SUPERSEDED_OLD:
        stats.superseded += 1
        stats.bytes_written += len(body.encode("utf-8"))
        log(f"  WROTE (supersedes) {result.path}")
    elif result.outcome is Outcome.DUPLICATE:
        stats.duplicates += 1
    elif result.outcome is Outcome.SKIPPED_LICENSE:
        stats.skipped_license += 1
    elif result.outcome is Outcome.QUARANTINED:
        stats.quarantined += 1
