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


@canon_group.command("coverage")
def canon_coverage() -> None:
    _not_yet("P5")


@canon_group.command("resolve")
def canon_resolve() -> None:
    _not_yet("P5")


# ------------------------------------------------------------------ stubs

@main.command()
def run() -> None:
    """Run ingest for a source or schedule."""
    _not_yet("P4")


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
