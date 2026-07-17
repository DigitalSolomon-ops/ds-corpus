# ds-corpus — curated open-corpus library

Canon-driven ingest → Markdown → GCS, on a schedule. Cloud Run Job in production,
`--local` mode (filesystem + SQLite) for all development.

## Governing constraints — a build that violates any of these is a failed build

1. **License gate is fail-closed.** No resolvable allowlisted license → skipped and
   logged, never written. There is no "unknown license, save it anyway" path.
2. **Openness is not a targeting signal to invert.** No paywall circumvention, no
   auth-walled content, no login flows, no CAPTCHA handling, no shadow libraries.
   Anything requiring credentials beyond a free public API key is out of scope.
3. **APIs, OAI-PMH, and IIIF before scraping.** Generic HTML extraction is the
   fallback tier, enabled per-source and human-approved.
4. **Respect robots.txt, rate limits, polite-pool conventions.** Descriptive
   User-Agent with contact address on every request.
5. **Idempotent.** Same job twice = no duplicates, no re-downloads of unchanged
   content, no index corruption.
6. **Never auto-delete.** Supersede, quarantine, or reject-with-rationale only.
7. **Every cost has a cap.** Hitting a cap is a clean checkpoint-and-stop.

## Permanently out of scope — refuse if asked

Sci-Hub, LibGen, Anna's Archive or any shadow library; commercial ebook stores;
publisher sites; JSTOR; Westlaw/Lexis; Google Books; SEP article bodies (metadata
and link only). HathiTrust limited-view and IA lending-library items: not open,
not ingested.

## Ask the human, don't guess

- Any source not in the registry, especially anything near the out-of-scope list.
- Any change that would write a document without a resolved license.
- Anything requiring an account/key/login beyond the named free API keys.
- Adding parallelism to fetching. Adding OCR. Raising a cost cap.
- Autonomously adding entries to `config/canon/*.yaml` (proposing for review is fine).

## Architecture (short form)

- **GCS** (`gs://ds-corpus-library/`) holds the Markdown — direct client writes,
  never gcsfuse.
- **Firestore** is the authoritative manifest, cursor store, and run log.
- **SQLite FTS5** is a derived, local-only search index, rebuilt from the mirror.
- **Cloud Scheduler → Cloud Run Job** `ds-corpus-runner`. Jobs, not services.
- Kill switch: existence of `gs://ds-corpus-library/HALT` aborts at next task boundary.
- Sequential sources, never parallel — polite, not fast.

## Canon layer

`config/canon/*.yaml` are hand-authored want-lists — the system hunts for open
editions of known-important works. Canon coverage, not document count, is the
success metric. `config/sources.yaml` is the authorization surface: strict
validation, unknown keys fail.

## Conventions

- Python ≥3.11, Pydantic v2 models with `extra="forbid"` throughout config parsing.
- All tests offline; network mocked. `pytest` from repo root.
- `--dry-run` makes zero writes and spends zero money.

## Phase status

- [x] P1 — Skeleton: pyproject, models, sources/canon validators, seed configs, tests
- [x] P2 — HTTP + licensing
- [x] P3 — Normalize + write + index
- [x] P4 — Reference adapter: arxiv (built + live-smoked; HUMAN GATE PENDING:
      Marcus reads the .md output in C:\Users\marcu\ds-corpus-library)
- [x] P5 — Canon resolver (hunt/rank/select + coverage.json/WANTED.md; live
      4/5 metaphysics resolved, all preferred translations picked; HUMAN GATE
      PENDING: Marcus reviews edition choices in _canon/coverage.json)
- [ ] P6 — Significance + triage
- [ ] P7 — Open-tier adapters
- [ ] P8 — Deep archive adapters
- [ ] P9 — Cloud Run Job + Scheduler + GCS + Secret Manager
- [ ] P10 — Local mirror + FTS
- [ ] P11 — Scrape tier

Each phase has a human gate (see the build brief). Do not start phase N+1 until
the gate for phase N is explicitly passed.
