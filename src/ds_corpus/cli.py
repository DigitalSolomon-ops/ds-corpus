"""ds-corpus CLI.

P1 surface: sources list|validate, canon validate. Everything else is a
stub that names the phase it arrives in — visible shape, honest status.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from pydantic import ValidationError

from ds_corpus import canon as canon_mod
from ds_corpus import registry as registry_mod
from ds_corpus.settings import load_settings

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _config_dir(ctx: click.Context) -> Path:
    return ctx.obj["config_dir"]


@click.group()
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory holding settings.yaml, sources.yaml, authorities.yaml, canon/",
)
@click.pass_context
def main(ctx: click.Context, config_dir: Path) -> None:
    """ds-corpus — curated open-corpus library."""
    # Windows consoles default to cp1252; make our output UTF-8 so any stray
    # non-ASCII char in a message can never crash a command mid-run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config_dir


# ---------------------------------------------------------------- sources

@main.group()
def sources() -> None:
    """Inspect and validate the sources registry."""


@sources.command("list")
@click.option("--domain", default=None, help="Filter by domain")
@click.pass_context
def sources_list(ctx: click.Context, domain: str | None) -> None:
    reg = _load_registry_or_die(_config_dir(ctx))
    rows = reg.by_domain(domain) if domain else reg.sources
    if not rows:
        click.echo("no sources match")
        return
    for s in rows:
        state = "enabled " if s.enabled else "disabled"
        click.echo(
            f"{s.id:<20} {s.tier.value:<13} {state} "
            f"{s.schedule.value:<8} domains={','.join(s.domain)}"
        )


@sources.command("validate")
@click.pass_context
def sources_validate(ctx: click.Context) -> None:
    cfg = _config_dir(ctx)
    reg = _load_registry_or_die(cfg)
    _load_settings_or_die(cfg)
    click.echo(f"OK: {len(reg.sources)} source(s) valid ({cfg / 'sources.yaml'})")
    click.echo(f"OK: settings valid ({cfg / 'settings.yaml'})")


# ------------------------------------------------------------------ canon

@main.group("canon")
def canon_group() -> None:
    """Inspect and validate the canon want-lists."""


@canon_group.command("validate")
@click.pass_context
def canon_validate(ctx: click.Context) -> None:
    cfg = _config_dir(ctx)
    reg = _load_registry_or_die(cfg)
    try:
        auths = canon_mod.load_authorities(cfg / "authorities.yaml")
    except (ValidationError, canon_mod.CanonValidationError, OSError) as e:
        _die(f"authorities.yaml invalid:\n{e}")
    try:
        c = canon_mod.load_canon(
            cfg / "canon",
            auths,
            known_source_ids=frozenset(s.id for s in reg.sources),
        )
    except (canon_mod.CanonValidationError, OSError) as e:
        _die(f"canon invalid:\n{e}")
    by_domain: dict[str, int] = {}
    for w in c.works:
        by_domain[w.domain] = by_domain.get(w.domain, 0) + 1
    summary = ", ".join(f"{d}={n}" for d, n in sorted(by_domain.items()))
    click.echo(f"OK: {len(c.works)} canon work(s) valid ({summary})")
    click.echo(f"OK: {len(auths.authorities)} authority list(s) valid")


def _load_canon_or_die(cfg: Path):
    reg = _load_registry_or_die(cfg)
    try:
        auths = canon_mod.load_authorities(cfg / "authorities.yaml")
        c = canon_mod.load_canon(
            cfg / "canon", auths, known_source_ids=frozenset(s.id for s in reg.sources)
        )
    except (canon_mod.CanonValidationError, ValidationError, OSError) as e:
        _die(f"canon invalid:\n{e}")
    return c, reg


def _session_factory(settings):
    """PoliteSession per source, honoring its rate limit."""
    from ds_corpus.http import PoliteSession

    def factory(source):
        return PoliteSession(settings.user_agent, source.rate.requests_per_second)

    return factory


@canon_group.command("resolve")
@click.option("--work", "work_id", required=True, help="Canon work id to resolve")
@click.option("--dry-run", is_flag=True, help="Hunt and rank; print the pick, write nothing")
@click.pass_context
def canon_resolve(ctx: click.Context, work_id: str, dry_run: bool) -> None:
    """Find the best open edition of one canon work."""
    from ds_corpus.resolver import resolve_work

    cfg = _config_dir(ctx)
    settings = _load_settings_or_die(cfg)
    canon, reg = _load_canon_or_die(cfg)
    work = canon.get(work_id)
    if work is None:
        _die(f"unknown canon work: {work_id}")

    res = resolve_work(work, reg, settings, _session_factory(settings))
    click.echo(f"{work.id} — {res.status}")
    if res.selected:
        s = res.selected
        click.echo(f"  SELECTED [{s['source']}:{s['edition_id']}] score={s['score']}")
        click.echo(f"    {s['title']}")
        if s["translators"]:
            click.echo(f"    translators: {', '.join(s['translators'])}")
        click.echo(f"    {s['url']}")
        click.echo(f"    signals: {res.selected_signals}")
        for a in res.alternates:
            click.echo(f"  alt  [{a['source']}:{a['edition_id']}] score={a['score']} — {a['title']}")
    else:
        click.echo(f"  no viable edition. tried={res.tried} "
                   f"unbuilt={res.unbuilt_sources} note={res.note}")
    for d in res.disqualified:
        click.echo(f"  DQ  [{d['source']}:{d['edition_id']}] {d['disqualified']} — {d['title']}")


@canon_group.command("coverage")
@click.option("--domain", default=None, help="Restrict to one domain")
@click.option("--dry-run", is_flag=True, help="Resolve and print; do not write coverage files")
@click.pass_context
def canon_coverage(ctx: click.Context, domain: str | None, dry_run: bool) -> None:
    """Resolve the (domain's) canon and report resolved/wanted coverage."""
    from ds_corpus.resolver import resolve_all, write_coverage

    cfg = _config_dir(ctx)
    settings = _load_settings_or_die(cfg)
    canon, reg = _load_canon_or_die(cfg)

    resolutions = resolve_all(
        canon, reg, settings, _session_factory(settings), domain=domain, log=click.echo
    )
    resolved = sum(1 for r in resolutions if r.status == "resolved")
    click.echo(f"\ncoverage: {resolved}/{len(resolutions)} resolved"
               + (f" ({domain})" if domain else ""))
    if dry_run:
        click.echo("(dry-run: coverage.json / WANTED.md not written)")
        return
    store, index = _open_local(ctx)
    index.close()
    write_coverage(resolutions, canon, store, domain=domain)
    click.echo(f"wrote {store.root / '_canon' / 'coverage.json'}")
    click.echo(f"wrote {store.root / '_canon' / 'WANTED.md'}")


# ------------------------------------------------------------------ stubs

@main.command()
@click.option("--source", "source_id", default=None, help="Run one source by id")
@click.option("--schedule", "schedule", default=None,
              type=click.Choice(["daily", "weekly", "monthly"]),
              help="Run every enabled source on this cadence, sequentially")
@click.option("--local", is_flag=True, help="Filesystem + SQLite backends")
@click.option("--dry-run", is_flag=True, help="Print what would happen; zero writes")
@click.option("--limit", type=int, default=None, help="Max candidates this run")
@click.option("--full", is_flag=True, help="Ignore cursor; recrawl from the start")
@click.pass_context
def run(ctx, source_id, schedule, local, dry_run, limit, full) -> None:
    """Run ingest for a source or schedule."""
    from ds_corpus.scheduler import run_schedule, run_source

    if not local:
        _die("cloud mode arrives in P9 — use --local")
    if bool(source_id) == bool(schedule):
        _die("pass exactly one of --source or --schedule")

    cfg = _config_dir(ctx)
    settings = _load_settings_or_die(cfg)
    # Canon is needed by canon-driven adapters (gutenberg); loading it always
    # keeps the wiring simple and validates it on every run.
    canon, reg = _load_canon_or_die(cfg)
    store, index = _open_local(ctx)
    try:
        if source_id:
            source = reg.get(source_id)
            if source is None:
                _die(f"unknown source: {source_id}")
            if not source.enabled:
                _die(f"source {source_id} is disabled in sources.yaml")
            stats = run_source(
                source, settings, store, index, canon=canon,
                dry_run=dry_run, limit=limit, full=full, log=click.echo,
            )
            if stats.errors:
                sys.exit(1)
        else:
            results = run_schedule(
                schedule, reg, settings, store, index, canon=canon,
                dry_run=dry_run, log=click.echo,
            )
            if any(s.errors for s in results.values()):
                sys.exit(1)
    finally:
        index.close()


@main.command()
@click.option("--open", "open_browser", is_flag=True, help="Open the page after writing it")
@click.pass_context
def dashboard(ctx: click.Context, open_browser: bool) -> None:
    """Render the human-input control room to _dashboard/index.html."""
    from ds_corpus import dashboard as dash

    cfg = _config_dir(ctx)
    settings = _load_settings_or_die(cfg)
    canon, reg = _load_canon_or_die(cfg)
    store, index = _open_local(ctx)
    try:
        data = dash.collect(settings, reg, canon, store, index)
    finally:
        index.close()
    store.write("_dashboard/index.html", dash.render_html(data))
    out = store.root / "_dashboard" / "index.html"
    click.echo(f"wrote {out}  ({len(data.actions)} action item(s))")
    for a in data.actions[:6]:
        click.echo(f"  [{a.kind}] {a.title}")
    if open_browser:
        import webbrowser
        webbrowser.open(out.as_uri())


@main.command()
@click.argument("phase")
@click.argument("action", type=click.Choice(["pass", "reset"]))
@click.pass_context
def gate(ctx: click.Context, phase: str, action: str) -> None:
    """Record (or clear) a human sign-off on a phase gate. E.g. `gate P5 pass`."""
    from ds_corpus import dashboard as dash

    phase = phase.upper()
    known = {p[0] for p in dash.PHASES}
    if phase not in known:
        _die(f"unknown phase {phase!r}; known: {', '.join(sorted(known))}")
    store, index = _open_local(ctx)
    index.close()
    dash.set_gate(store, phase, signed=(action == "pass"))
    click.echo(f"{phase}: {'signed off' if action == 'pass' else 'sign-off cleared'}")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Document counts from the local index."""
    store, index = _open_local(ctx)
    c = index.counts()
    click.echo(f"library: {store.root}")
    click.echo(f"documents (incl. superseded): {c['total']}")
    for src, n in sorted(c["active_by_source"].items()):
        click.echo(f"  active from {src}: {n}")
    if store.halted():
        click.echo("!! HALT file present — runs will abort", err=True)
    index.close()


@main.command()
def search() -> None:
    _not_yet("P10")


@main.command()
def reindex() -> None:
    _not_yet("P10")


@main.command()
@click.pass_context
def verify(ctx: click.Context) -> None:
    """Index <-> store reconciliation."""
    from ds_corpus.writer import verify as verify_fn

    store, index = _open_local(ctx)
    problems = verify_fn(store, index)
    index.close()
    if problems:
        for p in problems:
            click.echo(f"PROBLEM: {p}", err=True)
        sys.exit(1)
    click.echo("OK: index and store agree")


@main.command()
def mirror() -> None:
    _not_yet("P10")


@main.command()
def deploy() -> None:
    _not_yet("P9")


# ---------------------------------------------------------------- helpers

def _open_local(ctx: click.Context):
    """Local-mode store + index from settings.local_library_dir."""
    from ds_corpus.index import SQLiteIndex
    from ds_corpus.store import FilesystemStore

    settings = _load_settings_or_die(_config_dir(ctx))
    root = Path(settings.local_library_dir).expanduser()
    store = FilesystemStore(root)
    index = SQLiteIndex(root / "_index.sqlite3")
    return store, index


def _load_registry_or_die(cfg: Path) -> registry_mod.SourcesRegistry:
    try:
        return registry_mod.load_registry(cfg / "sources.yaml")
    except (ValidationError, ValueError, OSError) as e:
        _die(f"sources.yaml invalid:\n{e}")
        raise AssertionError  # unreachable; _die exits


def _load_settings_or_die(cfg: Path):
    try:
        return load_settings(cfg / "settings.yaml")
    except (ValidationError, ValueError, OSError) as e:
        _die(f"settings.yaml invalid:\n{e}")


def _die(msg: str) -> None:
    click.echo(f"FAIL: {msg}", err=True)
    sys.exit(1)


def _not_yet(phase: str) -> None:
    click.echo(f"not built yet — arrives in {phase}", err=True)
    sys.exit(2)


if __name__ == "__main__":
    main()
