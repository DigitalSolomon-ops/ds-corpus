# ds-corpus

Curated open-corpus library: canon-driven ingest → Markdown → GCS, on a schedule.

A hand-authored canon (`config/canon/*.yaml`) of works that matter drives the
fetching; the system hunts for **open** editions of known-important works.
A significance gate rejects chaff with a written rationale. Coverage against
the canon — not document count — is the success metric.

## Kill switch

**The presence of the object `gs://ds-corpus-library/HALT` aborts the run at
the next task boundary.** Create it to stop everything:

```
gcloud storage cp /dev/null gs://ds-corpus-library/HALT
```

Delete it to allow runs again. This is checked before every task, not just at
job start.

## Routine schedule (local)

Two Windows scheduled tasks run the corpus automatically (they only fire while
the machine is awake — cloud/unattended is P9):

| Task | Cadence | Sources |
|---|---|---|
| `ds-corpus-weekly` | Sundays 18:00 | arxiv, gutenberg, internet_archive |
| `ds-corpus-monthly` | 1st of month 18:30 | hathitrust, iiif |

Both invoke `infra/local-schedule.ps1 -Cadence <weekly|monthly>`, which runs
`ds-corpus run --schedule <cadence> --local` and logs to
`<library>/_runs/scheduled-*.log`. Trigger a run by hand with
`Start-ScheduledTask -TaskName ds-corpus-weekly`.

## Status

Phases P1–P8 (partial) complete: config + validators, polite HTTP + fail-closed
licensing, write path, significance gate (Haiku triage), canon resolver, and
adapters for arxiv, gutenberg, internet_archive, hathitrust, and IIIF. Canon is
41 works across law/metaphysics/coding/art. See `CLAUDE.md` for the phase
tracker and governing constraints. Next: P9 (cloud/unattended), P10 (mirror +
FTS search).

## Quick start (local dev)

```
python -m venv .venv && .venv\Scripts\activate
pip install -e .[dev]
ds-corpus sources validate
ds-corpus canon validate
ds-corpus sources list
pytest
```

## Layout

- `config/settings.yaml` — global knobs, license allowlist, budget caps
- `config/sources.yaml` — WHERE to fetch from; the authorization surface
- `config/canon/*.yaml` — WHAT to hunt for; hand-authored want-lists
- `config/authorities.yaml` — lists that confer canonical status
- `src/ds_corpus/` — package
- `tests/` — all offline; network is always mocked
