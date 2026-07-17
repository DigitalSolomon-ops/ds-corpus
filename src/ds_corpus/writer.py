"""Ingest pipeline: license gate -> normalize -> dedup -> validate -> write.

Every outcome is explicit and logged; the only path that puts a document in
the library is the one where every gate passed. Rejections and failures are
kept (in _rejected/ and _quarantine/), never silently dropped — and nothing
is ever deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from ds_corpus import licensing
from ds_corpus.frontmatter import FrontMatter, to_markdown
from ds_corpus.index import SQLiteIndex
from ds_corpus.normalize import body_sha256, normalize_markdown, word_count
from ds_corpus.store import FilesystemStore


class Outcome(str, Enum):
    WRITTEN = "written"
    SKIPPED_LICENSE = "skipped_license"
    DUPLICATE = "duplicate"
    SUPERSEDED_OLD = "superseded_old"      # written as .v2, old marked superseded
    QUARANTINED = "quarantined"


@dataclass
class IngestResult:
    outcome: Outcome
    doc_id: str
    path: str | None = None
    detail: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ingest(
    *,
    fm_fields: dict,
    body: str,
    raw_license: str | None,
    rel_path: str,
    store: FilesystemStore,
    index: SQLiteIndex,
    license_allowlist: list[str],
) -> IngestResult:
    """fm_fields: front-matter fields EXCEPT license/content_sha256/word_count/
    retrieved_at, which this pipeline owns."""
    doc_id = fm_fields["id"]

    # Gate 1 — license, fail-closed. Before any write, before any hashing.
    license_id = licensing.resolve(raw_license)
    if not licensing.is_allowed(license_id, license_allowlist):
        return IngestResult(
            Outcome.SKIPPED_LICENSE,
            doc_id,
            detail=f"raw={raw_license!r} resolved={license_id!r}",
        )

    # Normalize; dedup on the normalized body. Metadata/images-only records
    # carry an empty body, which would all hash identically and wrongly dedup
    # to a single record — so for those, hash their identity instead.
    normalized = normalize_markdown(body)
    if normalized.strip():
        sha = body_sha256(normalized)
    else:
        sha = body_sha256(doc_id + "\x00" + str(fm_fields.get("source_url", "")))
    if index.has_hash(sha):
        return IngestResult(Outcome.DUPLICATE, doc_id, detail=sha)

    # Same id, different content -> supersede: keep both, new gets .v2 path.
    existing = index.get_document(doc_id)
    supersede_old = False
    final_id = doc_id
    if existing is not None:
        supersede_old = True
        version = 2
        while index.get_document(f"{doc_id}.v{version}") is not None:
            version += 1
        final_id = f"{doc_id}.v{version}"
        rel_path = rel_path.removesuffix(".md") + f".v{version}.md"

    fields = dict(fm_fields)
    fields.update(
        id=final_id,
        license=license_id,
        content_sha256=sha,
        word_count=word_count(normalized),
        retrieved_at=fields.get("retrieved_at") or _now_iso(),
    )

    # Gate 2 — front-matter contract. Failure -> quarantine, not library.
    try:
        fm = FrontMatter.model_validate(fields)
        content = to_markdown(fm, normalized)
    except Exception as e:
        qpath = f"_quarantine/{doc_id.replace(':', '_').replace('/', '_')}.error.json"
        store.write(
            qpath,
            json.dumps(
                {"id": doc_id, "error": str(e), "fields": {k: str(v) for k, v in fields.items()}},
                indent=2,
                ensure_ascii=False,
            ),
        )
        return IngestResult(Outcome.QUARANTINED, doc_id, path=qpath, detail=str(e))

    # Write (atomic), then index (transactional). A crash between the two
    # leaves an unindexed file, which `verify` detects and can re-index —
    # the reverse order could index a document that doesn't exist.
    store.write(rel_path, content)
    index.add_document(
        doc_id=final_id,
        canon_id=fm.canon_id,
        domain=fm.domain,
        source_id=fm.source_id,
        path=rel_path,
        content_sha256=sha,
        retrieved_at=fm.retrieved_at,
    )
    if supersede_old:
        index.supersede(doc_id, final_id)
        return IngestResult(Outcome.SUPERSEDED_OLD, final_id, path=rel_path)
    return IngestResult(Outcome.WRITTEN, final_id, path=rel_path)


def verify(store: FilesystemStore, index: SQLiteIndex) -> list[str]:
    """Index <-> store reconciliation. Returns a list of problems (empty = clean)."""
    from ds_corpus.frontmatter import parse

    problems: list[str] = []
    indexed = {d["path"]: d for d in index.all_documents()}

    on_disk = set(store.list())
    for path in on_disk:
        if path.startswith(("_rejected/", "_quarantine/", "_runs/", "_canon/")):
            continue
        try:
            fm, body = parse(store.read(path))
        except Exception as e:
            problems.append(f"{path}: unparseable front-matter: {e}")
            continue
        # Mirror ingest's hashing: empty-body (metadata/images_only) records
        # hash their identity, not the empty body.
        normalized = normalize_markdown(body)
        if normalized.strip():
            expected = body_sha256(normalized)
        else:
            expected = body_sha256(fm.id + "\x00" + str(fm.source_url or ""))
        entry = indexed.get(path)
        if entry is None:
            problems.append(f"{path}: on disk but not in index")
        elif entry["content_sha256"] != fm.content_sha256:
            problems.append(f"{path}: index sha != front-matter sha")
        elif expected != fm.content_sha256:
            problems.append(f"{path}: body hash != front-matter sha")

    for path, entry in indexed.items():
        if path not in on_disk:
            problems.append(f"{entry['id']}: in index but missing from store ({path})")
    return problems
