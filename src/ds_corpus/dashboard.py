"""The control room: a dashboard of what a human needs to *provide*.

ds-corpus does the fetching, gating, and ranking; a human does four things the
machine cannot: pass phase gates, author the canon, supply the (free) API keys,
and decide where to spend a curation hour on coverage gaps. This module reads
live project state and renders a self-contained HTML page organized around
those human inputs — an inbox, not a report.

Everything here is derived from real state (index, canon, coverage.json, env,
gates.json) so re-running `ds-corpus dashboard` always reflects the truth.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ds_corpus.canon import Canon
from ds_corpus.index import SQLiteIndex
from ds_corpus.registry import DOMAINS, SourcesRegistry
from ds_corpus.settings import Settings
from ds_corpus.store import FilesystemStore

# Canon authoring target per domain (brief §4.1: "Seed each domain with 40–80").
CANON_TARGET = 40

# Free API keys/tokens the later phases need, and which phase first needs each.
SECRETS = [
    ("COURTLISTENER_TOKEN", "CourtListener (law) — free token", "P7"),
    ("GOVINFO_KEY", "govinfo (law) — free key", "P7"),
    ("SMITHSONIAN_KEY", "Smithsonian Open Access (art) — free key", "P7"),
    ("ANTHROPIC_API_KEY", "Claude triage (significance gate)", "P6"),
]

# The project's own phase roadmap. `built` reflects what has shipped; the
# human gate for a built phase is recorded in gates.json (see gate_state).
PHASES = [
    ("P1", "Skeleton", True, "test", "configs validate, models green"),
    ("P2", "HTTP + licensing", True, "test", "fail-closed license table, polite session"),
    ("P3", "Normalize + write + index", True, "test", "front-matter contract, dedup/supersede"),
    ("P4", "arXiv adapter", True, "human", "read a sample .md: coding/arxiv/*"),
    ("P5", "Canon resolver", True, "human", "review edition picks: _canon/coverage.json"),
    ("P6", "Significance + triage", False, "human", "Haiku Batch gate, _rejected/ rationale"),
    ("P7", "Open-tier adapters", True, "human", "gutenberg canon harvest ingested; more sources pending"),
    ("P8", "Deep archive adapters", False, "human", "internet_archive resolves the Aristotle gap"),
    ("P9", "Cloud Run Job + GCS", False, "human", "one scheduled cloud run; kill switch proven"),
    ("P10", "Local mirror + FTS", False, "human", "incremental rsync + search"),
    ("P11", "Scrape tier", False, "human", "per-source human approval"),
]


@dataclass
class ActionItem:
    priority: int                 # 1 = do first
    kind: str                     # gate | canon | gap | secret
    title: str
    detail: str
    how: str                      # exact command or file to act on


@dataclass
class DashboardData:
    generated_at: str
    library_root: str
    total_docs: int
    active_by_source: dict
    docs_by_domain: dict
    canon_by_domain: dict         # domain -> {"count", "resolved", "wanted"}
    coverage_present: bool
    wanted_works: list            # [{work_id, title, tried}]
    sources: list                 # [{id, tier, enabled, adapter_built}]
    secrets: list                 # [{env, desc, phase, present}]
    phases: list                  # [{id, title, built, gate_kind, review, signed_off}]
    recent_runs: list
    actions: list                 # [ActionItem]
    halted: bool


def gate_state(store: FilesystemStore) -> dict:
    """Human sign-offs, recorded via `ds-corpus gate`. Missing file = none."""
    try:
        return json.loads(store.read("_dashboard/gates.json"))
    except (OSError, ValueError):
        return {}


def set_gate(store: FilesystemStore, phase: str, signed: bool) -> None:
    state = gate_state(store)
    if signed:
        state[phase] = {
            "signed_off": True,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    else:
        state.pop(phase, None)
    store.write("_dashboard/gates.json", json.dumps(state, indent=2))


def collect(
    settings: Settings,
    registry: SourcesRegistry,
    canon: Canon,
    store: FilesystemStore,
    index: SQLiteIndex,
) -> DashboardData:
    from ds_corpus.adapters import ADAPTERS

    counts = index.counts()

    # Docs by domain (walk the index paths' first segment).
    docs_by_domain: dict[str, int] = {d: 0 for d in sorted(DOMAINS)}
    for doc in index.all_documents():
        seg = doc["path"].split("/", 1)[0]
        if seg in docs_by_domain:
            docs_by_domain[seg] += 1

    # Canon per domain, with resolved/wanted from coverage.json if present.
    coverage = None
    try:
        coverage = json.loads(store.read("_canon/coverage.json"))
    except (OSError, ValueError):
        pass
    resolved_ids = set()
    wanted_works = []
    if coverage:
        for r in coverage.get("resolutions", []):
            if r["status"] == "resolved":
                resolved_ids.add(r["work_id"])
            else:
                w = canon.get(r["work_id"])
                wanted_works.append({
                    "work_id": r["work_id"],
                    "title": w.title if w else r["work_id"],
                    "tried": r.get("tried", {}),
                    "unbuilt": r.get("unbuilt_sources", []),
                })

    canon_by_domain: dict[str, dict] = {}
    for d in sorted(DOMAINS):
        works = canon.by_domain(d)
        canon_by_domain[d] = {
            "count": len(works),
            "resolved": sum(1 for w in works if w.id in resolved_ids),
            "wanted": sum(1 for w in works if coverage and w.id not in resolved_ids),
        }

    sources = [{
        "id": s.id, "tier": s.tier.value, "enabled": s.enabled,
        "adapter_built": s.adapter in ADAPTERS,
    } for s in registry.sources]

    secrets = [{
        "env": env, "desc": desc, "phase": phase, "present": bool(os.environ.get(env)),
    } for env, desc, phase in SECRETS]

    gates = gate_state(store)
    phases = [{
        "id": pid, "title": title, "built": built, "gate_kind": kind, "review": review,
        "signed_off": gates.get(pid, {}).get("signed_off", False),
    } for pid, title, built, kind, review in PHASES]

    actions = _build_actions(phases, canon_by_domain, wanted_works, secrets, coverage is not None)

    return DashboardData(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        library_root=str(store.root),
        total_docs=counts["total"],
        active_by_source=counts["active_by_source"],
        docs_by_domain=docs_by_domain,
        canon_by_domain=canon_by_domain,
        coverage_present=coverage is not None,
        wanted_works=wanted_works,
        sources=sources,
        secrets=secrets,
        phases=phases,
        recent_runs=index.recent_runs(),
        actions=actions,
        halted=store.halted(),
    )


def _build_actions(phases, canon_by_domain, wanted_works, secrets, coverage_present) -> list[ActionItem]:
    items: list[ActionItem] = []

    # 1. Built phases with human gates not yet signed off — the freshest asks.
    for p in phases:
        if p["built"] and p["gate_kind"] == "human" and not p["signed_off"]:
            items.append(ActionItem(
                1, "gate",
                f"Sign off {p['id']} — {p['title']}",
                f"Review: {p['review']}",
                f"ds-corpus gate {p['id']} pass",
            ))

    # 2. Canon authoring — the human's core job. Empty domains first.
    for domain, c in canon_by_domain.items():
        if c["count"] == 0:
            items.append(ActionItem(
                2, "canon",
                f"Author the {domain} canon (0 works)",
                f"Seed {CANON_TARGET}–80 works that matter; a human stands behind each.",
                f"edit config/canon/{domain}.yaml  →  ds-corpus canon validate",
            ))
        elif c["count"] < CANON_TARGET:
            items.append(ActionItem(
                3, "canon",
                f"Grow the {domain} canon ({c['count']}/{CANON_TARGET})",
                f"{CANON_TARGET - c['count']} more to reach the minimum seed target.",
                f"edit config/canon/{domain}.yaml  →  ds-corpus canon validate",
            ))

    # 3. Coverage gaps — resolved-against but no open edition found.
    for w in wanted_works:
        hint = (f"needs a source not yet built: {', '.join(w['unbuilt'])}"
                if w["unbuilt"] else "no open edition found in hunted sources")
        items.append(ActionItem(
            3, "gap",
            f"Resolve gap: {w['title']}",
            f"{hint}. Consider widening its hunt.sources.",
            f"edit the work's hunt: in config/canon/*.yaml (id={w['work_id']})",
        ))

    # 4. Missing secrets, tagged with the phase that first needs them.
    for s in secrets:
        if not s["present"]:
            items.append(ActionItem(
                4, "secret",
                f"Provide {s['env']}",
                f"{s['desc']} — needed by {s['phase']}.",
                "put it in .env (local) or Secret Manager (cloud); never in git",
            ))

    items.sort(key=lambda a: a.priority)
    return items


# ----------------------------------------------------------------- render

def _bar(done: int, total: int, kind: str = "ok") -> str:
    pct = 0 if total == 0 else min(100, round(100 * done / total))
    return (f'<div class="bar"><div class="fill {kind}" style="width:{pct}%"></div>'
            f'<span class="barlabel">{done}/{total}</span></div>')


def _esc(s) -> str:
    return html.escape(str(s))


def render_html(d: DashboardData) -> str:
    kind_badge = {
        "gate": ("⚖️", "gate"), "canon": ("📚", "canon"),
        "gap": ("🕳️", "gap"), "secret": ("🔑", "secret"),
    }

    # Action cards
    if d.actions:
        action_cards = "\n".join(
            f'''<div class="card action {kind_badge[a.kind][1]}">
              <div class="action-head"><span class="emoji">{kind_badge[a.kind][0]}</span>
                <span class="atitle">{_esc(a.title)}</span></div>
              <div class="adetail">{_esc(a.detail)}</div>
              <code class="how">{_esc(a.how)}</code>
            </div>''' for a in d.actions
        )
    else:
        action_cards = '<div class="card empty">Nothing needed right now — the machine has what it needs. 🎉</div>'

    # Phase timeline
    phase_rows = []
    for p in d.phases:
        if not p["built"]:
            state, cls = "not started", "future"
        elif p["gate_kind"] == "test":
            state, cls = "auto-gated ✓", "done"
        elif p["signed_off"]:
            state, cls = "signed off ✓", "done"
        else:
            state, cls = "awaiting sign-off", "pending"
        phase_rows.append(
            f'''<tr class="{cls}"><td class="pid">{_esc(p["id"])}</td>
            <td>{_esc(p["title"])}</td><td class="pstate">{state}</td>
            <td class="preview">{_esc(p["review"])}</td></tr>'''
        )

    # Canon coverage
    canon_rows = []
    for domain, c in d.canon_by_domain.items():
        cov = (f'{_bar(c["resolved"], c["count"], "ok")}' if d.coverage_present and c["count"]
               else '<span class="muted">—</span>')
        canon_rows.append(
            f'''<tr><td class="dom">{_esc(domain)}</td>
            <td>{_bar(c["count"], CANON_TARGET, "author")}</td>
            <td>{cov}</td>
            <td class="docs">{d.docs_by_domain.get(domain,0)}</td></tr>'''
        )

    # Sources
    src_rows = "\n".join(
        f'''<tr><td>{_esc(s["id"])}</td><td>{_esc(s["tier"])}</td>
        <td>{"✓" if s["enabled"] else "—"}</td>
        <td>{"built" if s["adapter_built"] else "<span class=muted>pending</span>"}</td></tr>'''
        for s in d.sources
    )

    # Secrets
    sec_rows = "\n".join(
        f'''<tr class="{'ok' if s["present"] else 'missing'}"><td>{_esc(s["env"])}</td>
        <td>{_esc(s["desc"])}</td><td>{_esc(s["phase"])}</td>
        <td>{"✓ set" if s["present"] else "✗ missing"}</td></tr>'''
        for s in d.secrets
    )

    # Recent runs
    if d.recent_runs:
        run_rows = []
        for r in d.recent_runs:
            stats = {}
            try:
                stats = json.loads(r["stats_json"]) if r["stats_json"] else {}
            except ValueError:
                pass
            summary = (f'written {stats.get("written",0)}, dup {stats.get("duplicates",0)}, '
                       f'license-skip {stats.get("skipped_license",0)}') if stats else "in progress"
            run_rows.append(
                f'<tr><td class="mono">{_esc(r["run_id"])}</td>'
                f'<td>{_esc(r["started_at"])}</td><td>{_esc(summary)}</td></tr>'
            )
        runs_html = "\n".join(run_rows)
    else:
        runs_html = '<tr><td colspan="3" class="muted">no runs yet</td></tr>'

    src_by = ", ".join(f"{k}: {v}" for k, v in sorted(d.active_by_source.items())) or "—"
    halt_banner = ('<div class="halt">⛔ HALT file present — scheduled runs will abort at the '
                   'next task boundary.</div>' if d.halted else "")

    return _TEMPLATE.format(
        generated=_esc(d.generated_at),
        root=_esc(d.library_root),
        total=d.total_docs,
        src_by=_esc(src_by),
        n_actions=len(d.actions),
        halt_banner=halt_banner,
        action_cards=action_cards,
        phase_rows="\n".join(phase_rows),
        canon_rows="\n".join(canon_rows),
        src_rows=src_rows,
        sec_rows=sec_rows,
        runs=runs_html,
        canon_target=CANON_TARGET,
    )


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ds-corpus · control room</title>
<style>
:root {{
  --bg:#f6f7f9; --panel:#fff; --ink:#1b1f24; --muted:#6b7280; --line:#e4e7eb;
  --accent:#4f46e5; --ok:#0f9d58; --author:#d97706; --gap:#dc2626; --secret:#7c3aed;
  --gatebg:#eef2ff; --canonbg:#fffbeb; --gapbg:#fef2f2; --secretbg:#f5f3ff;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0e1116; --panel:#161b22; --ink:#e6edf3; --muted:#8b949e; --line:#2a2f37;
    --accent:#818cf8; --gatebg:#1e2340; --canonbg:#2a2411; --gapbg:#2a1618; --secretbg:#211a33; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 60px; }}
header.top {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:12px; justify-content:space-between; }}
h1 {{ font-size:22px; margin:0; letter-spacing:-.01em; }}
h1 small {{ color:var(--muted); font-weight:500; font-size:14px; }}
.gen {{ color:var(--muted); font-size:12.5px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:14px; margin:18px 0 6px; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:14px 18px; flex:1; min-width:150px; }}
.stat .n {{ font-size:26px; font-weight:700; }}
.stat .k {{ color:var(--muted); font-size:12.5px; text-transform:uppercase; letter-spacing:.04em; }}
h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
  margin:34px 0 12px; }}
.halt {{ background:var(--gapbg); border:1px solid var(--gap); color:var(--gap);
  padding:10px 14px; border-radius:10px; margin:16px 0; font-weight:600; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:12px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.card.action {{ border-left:4px solid var(--line); }}
.action.gate {{ border-left-color:var(--accent); background:var(--gatebg); }}
.action.canon {{ border-left-color:var(--author); background:var(--canonbg); }}
.action.gap {{ border-left-color:var(--gap); background:var(--gapbg); }}
.action.secret {{ border-left-color:var(--secret); background:var(--secretbg); }}
.action-head {{ display:flex; align-items:center; gap:8px; font-weight:650; }}
.emoji {{ font-size:16px; }}
.adetail {{ color:var(--muted); font-size:13.5px; margin:6px 0 10px; }}
.how {{ display:block; background:rgba(127,127,127,.12); border-radius:7px; padding:7px 9px;
  font-size:12.5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; overflow-x:auto; white-space:nowrap; }}
.card.empty {{ text-align:center; color:var(--muted); grid-column:1/-1; padding:26px; }}
table {{ width:100%; border-collapse:collapse; background:var(--panel);
  border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); font-size:13.5px; }}
th {{ color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; }}
tr:last-child td {{ border-bottom:none; }}
.pid {{ font-weight:700; }}
tr.done .pstate {{ color:var(--ok); }}
tr.pending .pstate {{ color:var(--author); font-weight:600; }}
tr.future {{ opacity:.5; }}
.preview {{ color:var(--muted); font-size:12.5px; }}
.dom,.docs {{ font-weight:600; }} .docs {{ text-align:right; }}
.bar {{ position:relative; background:rgba(127,127,127,.15); border-radius:6px; height:20px; min-width:120px; overflow:hidden; }}
.fill {{ height:100%; }} .fill.ok {{ background:var(--ok); }} .fill.author {{ background:var(--author); }}
.barlabel {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  font-size:11.5px; font-weight:600; }}
.mono,.docs {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
.mono {{ font-size:11.5px; }}
tr.missing td:last-child {{ color:var(--gap); font-weight:600; }}
tr.ok td:last-child {{ color:var(--ok); }}
.muted {{ color:var(--muted); }}
.scroll {{ overflow-x:auto; }}
footer {{ margin-top:40px; color:var(--muted); font-size:12px; text-align:center; }}
</style></head>
<body><div class="wrap">
<header class="top">
  <h1>ds-corpus <small>· control room</small></h1>
  <span class="gen">generated {generated} · {root}</span>
</header>
{halt_banner}
<div class="stats">
  <div class="stat"><div class="n">{total}</div><div class="k">documents</div></div>
  <div class="stat"><div class="n">{n_actions}</div><div class="k">things you can provide</div></div>
  <div class="stat"><div class="n" style="font-size:15px;line-height:1.7">{src_by}</div><div class="k">active by source</div></div>
</div>

<h2>⭘ Your turn — what the project needs from you</h2>
<div class="cards">{action_cards}</div>

<h2>Phases &amp; gates</h2>
<div class="scroll"><table>
<thead><tr><th>Phase</th><th>Title</th><th>Gate</th><th>What to review</th></tr></thead>
<tbody>{phase_rows}</tbody></table></div>

<h2>Canon coverage &nbsp;<span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">— authored vs target ({canon_target}), resolved vs authored, docs ingested</span></h2>
<div class="scroll"><table>
<thead><tr><th>Domain</th><th>Authored / {canon_target}</th><th>Resolved</th><th>Docs</th></tr></thead>
<tbody>{canon_rows}</tbody></table></div>

<h2>Sources</h2>
<div class="scroll"><table>
<thead><tr><th>id</th><th>tier</th><th>enabled</th><th>adapter</th></tr></thead>
<tbody>{src_rows}</tbody></table></div>

<h2>Secrets (free keys)</h2>
<div class="scroll"><table>
<thead><tr><th>env var</th><th>what</th><th>needed by</th><th>status</th></tr></thead>
<tbody>{sec_rows}</tbody></table></div>

<h2>Recent runs</h2>
<div class="scroll"><table>
<thead><tr><th>run</th><th>started</th><th>result</th></tr></thead>
<tbody>{runs}</tbody></table></div>

<footer>Regenerate any time with <code>ds-corpus dashboard</code>. Sign off a gate with
<code>ds-corpus gate P5 pass</code>. This page is derived entirely from live project state.</footer>
</div></body></html>
"""
