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

## Significance gate (P6)

Two stages, reasoning-vs-execution split. Canon-driven adapters bypass it
entirely (canon hit = significant by definition). For everything else:
deterministic scorer (`significance.py`) rejects below-floor chaff for free;
survivors go to Claude Haiku triage (`triage.py`) over the **Batch API** with
a **cached shared rubric**, bounded by `max_triage_usd`. Rejects are written to
`_rejected/<id>.md` **with rationale**, never deleted; an audit sample of
decisions is logged each run. Hitting the cost cap leaves records *pending*
(retry next run), never discarded. The triage client is injected, so tests run
offline; a real run only spends on floor-clearers, up to the cap.

Note: arXiv (and other non-canon sources) score low until citation/authority
enrichment lands (OpenAlex, authority matching) — so they're currently
rejected at the deterministic floor at zero cost, which is the intended
curator behavior, auditable in `_rejected/`.

## Deep archive tier — Internet Archive (P8)

`internet_archive` is canon-driven like Gutenberg, but the tier's safety rules
are absolute and enforced against the **authoritative metadata API** (not the
search index), before any selection or fetch:
- **Never lending/restricted.** `access-restricted-item: true` or an
  `inlibrary`/`lendinglibrary` collection → rejected. IA controlled-lending
  books are not open.
- **Fail-closed license.** A CC `licenseurl` or `possible-copyright-status:
  NOT_IN_COPYRIGHT/PUBLIC_DOMAIN`, else skipped.
- **No fabricated bodies.** Item with a `_djvu.txt` text layer → `full_text`;
  item with only page images → `images_only` metadata record, **empty body**,
  linked, never OCR'd. OCR is a later phase with its own gate.

The other P8 adapters (hathitrust full-view-only, loc, perseus, gallica,
digivatlib, digital_bodleian, oai_generic) clone this pattern — IIIF sources
produce images_only records.

## Canon layer

`config/canon/*.yaml` are hand-authored want-lists — the system hunts for open
editions of known-important works. Canon coverage, not document count, is the
success metric. `config/sources.yaml` is the authorization surface: strict
validation, unknown keys fail.

**Canon-driven adapters** (gutenberg) set `canon_driven = True`; the runner
loads the canon and hands it to them. Their harvest resolves each canon work
that hunts them, fetches the resolver's chosen edition, and ingests it as a
**canon hit** — `significance_score=100`, `triage=null` — because a work we
deliberately asked for is significant by definition (short-circuits the P6
gate). Contrast the arXiv adapter, a firehose we filter. Idempotent with no
cursor: re-resolve every run, body-hash dedup absorbs unchanged re-fetches.

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
- [x] P6 — Significance + triage (deterministic scorer + Haiku Batch gate w/
      prompt caching, cost cap, _rejected/ rationale, audit sample; wired into
      non-canon ingest. HUMAN GATE PENDING: review 20 accepts/20 rejects once a
      run produces triage decisions — needs enrichment or a paid run)
- [~] P7 — Open-tier adapters (gutenberg canon harvest DONE: resolves canon
      works, fetches selected edition, strips PG boilerplate, ingests as canon
      hit w/ significance short-circuit. Remaining P7 sources: standard_ebooks,
      wikisource, courtlistener, govinfo, met_museum, openalex, doaj, bulk_repo)
- [~] P8 — Deep archive adapters (internet_archive DONE: search + canon harvest
      with strict safety gates — no lending/restricted items, fail-closed
      license, images-only = empty body never fabricated. Resolves Aristotle
      gap. Remaining P8: hathitrust full-view-only, loc, perseus, gallica,
      digivatlib, digital_bodleian, oai_generic — all follow the IA pattern)
- [ ] P9 — Cloud Run Job + Scheduler + GCS + Secret Manager
- [ ] P10 — Local mirror + FTS
- [ ] P11 — Scrape tier

Each phase has a human gate (see the build brief). Do not start phase N+1 until
the gate for phase N is explicitly passed.
