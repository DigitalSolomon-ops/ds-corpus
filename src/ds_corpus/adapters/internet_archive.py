"""Internet Archive — the deep-archive tier reference adapter.

IA holds the rare material the open tier doesn't: Aristotle's Metaphysics,
Shannon 1948, scanned first editions. It is also where the brief's safety rules
bite hardest, so the gates here are strict and explicit:

- **No lending / access-restricted items, ever.** IA's controlled-lending books
  are NOT open. An item with `access-restricted-item: true`, or in an
  `inlibrary` / `lendinglibrary` collection, is rejected outright — never
  ingested (brief §6, §12.2). This is checked against the authoritative
  metadata, not the search index.
- **License is fail-closed.** A resolvable open license (a CC `licenseurl`, or
  `possible-copyright-status: NOT_IN_COPYRIGHT / PUBLIC_DOMAIN`) or the item is
  skipped. No "probably PD, save it anyway."
- **No fabricated bodies.** An item with a real text layer (`_djvu.txt`) is
  ingested `full_text`. An item with only page images and no text is a
  `images_only` metadata record with an EMPTY body and a link — never OCR'd,
  never invented (brief §4.3, §12.3). OCR is a later phase with its own gate.

Search resolves candidates; the authoritative metadata call (license +
restriction + text layer) happens per candidate before anything is selected or
fetched, so a restricted or unlicensed item can never slip through.
"""

from __future__ import annotations

from typing import Iterator

from ds_corpus import licensing
from ds_corpus.adapters.base import Adapter, Candidate
from ds_corpus.canon import Canon, CanonWork
from ds_corpus.editions import Edition, rank_editions
from ds_corpus.http import PoliteSession
from ds_corpus.normalize import normalize_markdown, strip_gutenberg_boilerplate
from ds_corpus.registry import SourceConfig

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata"
DOWNLOAD_URL = "https://archive.org/download"

# Collections that mean controlled lending / restricted access — NOT open.
_LENDING_COLLECTIONS = frozenset({"inlibrary", "lendinglibrary", "printdisabled"})
# How many search hits to probe with the (rate-limited) metadata call.
_MAX_PROBES = 6


def is_restricted(md: dict) -> bool:
    """True if the item is access-controlled / lending — must not be ingested."""
    flag = str(md.get("access-restricted-item", "")).strip().lower()
    if flag in ("true", "1", "yes"):
        return True
    coll = md.get("collection", [])
    if isinstance(coll, str):
        coll = [coll]
    return bool({str(c).lower() for c in coll} & _LENDING_COLLECTIONS)


def resolve_ia_license(md: dict) -> str | None:
    """Resolve an IA item's license to a normalized id, or None (fail closed).

    A CC `licenseurl` resolves through the shared table; otherwise IA's
    `possible-copyright-status` of NOT_IN_COPYRIGHT / PUBLIC_DOMAIN means public
    domain. Anything else is unknown and will be skipped."""
    url = md.get("licenseurl")
    if url:
        return licensing.resolve(url)
    status = str(md.get("possible-copyright-status", "")).upper()
    if "NOT_IN_COPYRIGHT" in status or "PUBLIC_DOMAIN" in status or "PUBLIC DOMAIN" in status:
        return "public-domain"
    return None


def pick_text_file(files: list[dict]) -> str | None:
    """The item's plain-text layer, if any. `_djvu.txt` is IA's canonical OCR
    text; a bare `_text.txt` is the fallback. None => images-only."""
    for f in files:
        name = f.get("name", "")
        if name.endswith("_djvu.txt") or f.get("format") == "DjVuTXT":
            return name
    for f in files:
        if f.get("name", "").endswith("_text.txt"):
            return name
    return None


def _raw_license_for(md: dict) -> str | None:
    """The raw license string to hand the writer's fail-closed gate."""
    if md.get("licenseurl"):
        return md["licenseurl"]
    status = str(md.get("possible-copyright-status", "")).upper()
    if "NOT_IN_COPYRIGHT" in status or "PUBLIC" in status:
        return "public domain"
    return None


def _edition_from_metadata(identifier: str, md: dict, files: list[dict],
                           downloads: int | None) -> Edition | None:
    """Build an Edition from authoritative metadata, or None if it must be
    excluded (restricted or no resolvable open license)."""
    if is_restricted(md):
        return None
    if resolve_ia_license(md) is None:
        return None

    text_file = pick_text_file(files)
    creators = md.get("creator", [])
    if isinstance(creators, str):
        creators = [creators]
    lang = md.get("language")
    if isinstance(lang, list):
        lang = lang[0] if lang else None
    lang_en = "en" if str(lang).lower() in ("eng", "en", "english") else lang

    if text_file:
        url = f"{DOWNLOAD_URL}/{identifier}/{text_file}"
        fmt = "text"
    else:
        # images-only: link to the item page; NO body will be fabricated.
        url = f"https://archive.org/details/{identifier}"
        fmt = "images_only"

    return Edition(
        source_id="internet_archive",
        edition_id=identifier,
        title=str(md.get("title", "")),
        authors=[str(c) for c in creators],
        url=url,
        raw_license=_raw_license_for(md),
        format_type=fmt,
        language=lang_en,
        download_count=downloads,
        raw={"identifier": identifier, "text_file": text_file,
             "collection": md.get("collection")},
    )


def _search_identifiers(session: PoliteSession, work: CanonWork, rows: int) -> list[dict]:
    surname = work.author.split()[-1]
    title_head = " ".join(w for w in work.title.split()[:3] if w.isalpha())
    q = f'title:({title_head}) AND creator:({surname}) AND mediatype:texts'
    resp = session.get(SEARCH_URL, params={
        "q": q,
        "fl[]": ["identifier", "title", "downloads"],
        "rows": rows, "output": "json", "sort[]": "downloads desc",
    })
    return resp.json().get("response", {}).get("docs", [])


def _resolve_candidates(session: PoliteSession, work: CanonWork,
                        max_results: int) -> list[Edition]:
    """Search, then probe each hit's authoritative metadata (bounded), keeping
    only open, licensed items. Restriction and license are decided here, before
    ranking or fetching — a bad item never reaches selection."""
    docs = _search_identifiers(session, work, rows=max(max_results, _MAX_PROBES))
    editions: list[Edition] = []
    for doc in docs[:_MAX_PROBES]:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        meta = session.get(f"{METADATA_URL}/{identifier}").json()
        md = meta.get("metadata", {})
        if not md:
            continue
        ed = _edition_from_metadata(identifier, md, meta.get("files", []),
                                    doc.get("downloads"))
        if ed is not None:
            editions.append(ed)
        if len(editions) >= max_results:
            break
    return editions


def search(session: PoliteSession, work: CanonWork, max_results: int = 6) -> list[Edition]:
    """Hunt IA for open editions of `work`. Used by the resolver."""
    return _resolve_candidates(session, work, max_results)


def _attribution(work: CanonWork, ed: Edition) -> str:
    return (f"{work.author} ({work.original_year}). {work.title}. "
            f"Internet Archive ({ed.edition_id}). {ed.raw_license or 'open'}.")


class InternetArchiveAdapter(Adapter):
    """Canon-driven harvest of IA open editions. Same shape as the Gutenberg
    adapter: resolve each canon work that hunts IA, fetch the winner's text
    (or record an images-only metadata stub), ingest as a canon hit."""

    id = "internet_archive"
    canon_driven = True
    canon: Canon | None = None

    def harvest(self, session, source: SourceConfig, cursor, limit) -> Iterator[Candidate]:
        if self.canon is None:
            raise RuntimeError("InternetArchiveAdapter.harvest requires canon to be set")

        yielded = 0
        for work in self.canon.works:
            if "internet_archive" not in work.hunt.sources:
                continue
            if work.domain not in source.domain:
                continue
            editions = search(session, work)
            viable = [s for s in rank_editions(work, editions) if s.disqualified is None]
            if not viable:
                continue
            yield self._candidate(work, viable[0].edition)
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def _candidate(self, work: CanonWork, ed: Edition) -> Candidate:
        images_only = ed.format_type == "images_only"
        fm_fields = {
            "id": f"internet_archive:{ed.edition_id}",
            "canon_id": work.id,
            "title": ed.title or work.title,
            "authors": ed.authors or [work.author],
            "domain": work.domain,
            "source_id": "internet_archive",
            "source_url": f"https://archive.org/details/{ed.edition_id}",
            "canonical_url": ed.url if images_only else None,
            "publisher": "Internet Archive",
            "original_year": work.original_year if work.original_year >= 0 else None,
            "source_format": "images" if images_only else "text",
            "converter": "ds-corpus/0.2.0",
            "body_status": "images_only" if images_only else "full_text",
            "significance_score": 100,
            "significance_signals": {
                "canon_hit": True,
                "authority_count": len(work.authorities),
                "primary_source": work.primary_source,
            },
            "triage": None,
            "tags": (["primary-source"] if work.primary_source else [])
                    + (["images-only", "metadata-only"] if images_only else []),
            "iiif_manifest": None,
            "attribution": _attribution(work, ed),
        }

        def fetch_body(session: PoliteSession) -> str:
            if images_only:
                return ""                       # never fabricate a body
            raw = session.get(ed.url).text
            return normalize_markdown(strip_gutenberg_boilerplate(raw))

        return Candidate(
            fm_fields=fm_fields,
            raw_license=ed.raw_license,
            rel_path=f"{work.domain}/internet_archive/{work.id}.md",
            fetch_body=fetch_body,
        )
