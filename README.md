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

## Status

Phase **P1 (skeleton)** complete: config schemas, validators, seed registry
(2 sources, 5 metaphysics canon works). No network code yet. See `CLAUDE.md`
for the phase tracker and governing constraints.

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
