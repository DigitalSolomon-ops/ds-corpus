"""Project Gutenberg via Gutendex (https://gutendex.com).

P5 uses only the *hunt* half — `search()` turns a canonical work into candidate
Editions the resolver can rank. The harvest half (bulk ingest) lands in P7.

Gutendex is a JSON mirror of the Gutenberg catalog. Every Gutenberg text is
public domain in the US; the per-book `copyright` flag (true = still under
copyright somewhere, e.g. recent translations) is a real license signal we
honor: copyright True is treated as unresolved and dropped by the gate.
"""

from __future__ import annotations

from typing import Iterator

from ds_corpus.adapters.base import Adapter, Candidate
from ds_corpus.canon import Canon, CanonWork
from ds_corpus.editions import Edition, rank_editions
from ds_corpus.http import PoliteSession
from ds_corpus.normalize import strip_gutenberg_boilerplate, text_to_markdown
from ds_corpus.registry import SourceConfig

GUTENDEX_URL = "https://gutendex.com/books"

# Preference order for the body URL among Gutendex's format mimetypes.
_TEXT_MIMES = [
    "text/plain; charset=utf-8",
    "text/plain; charset=us-ascii",
    "text/plain",
    "text/html; charset=utf-8",
    "text/html",
]


def _pick_body_url(formats: dict) -> tuple[str | None, str]:
    """Return (url, format_type). Prefers a clean text layer; ignores the
    .zip and image-only entries. Returns (None, 'images_only') if no text."""
    for mime in _TEXT_MIMES:
        url = formats.get(mime)
        if url and not url.endswith(".zip"):
            return url, ("text" if mime.startswith("text/plain") else "good_ocr")
    if "application/epub+zip" in formats:
        return formats["application/epub+zip"], "epub"
    return None, "images_only"


def _license_for(book: dict) -> str | None:
    """Public domain unless Gutendex marks it still-copyrighted."""
    if book.get("copyright") is True:
        return None                      # copyrighted somewhere -> let the gate drop it
    return "Public domain in the USA."   # copyright False or null -> PD (resolver-friendly form)


def _editions_from_page(page: dict) -> Iterator[Edition]:
    for book in page.get("results", []):
        url, fmt = _pick_body_url(book.get("formats", {}))
        yield Edition(
            source_id="gutenberg",
            edition_id=str(book["id"]),
            title=book.get("title", ""),
            authors=[a["name"] for a in book.get("authors", [])],
            translators=[t["name"] for t in book.get("translators", [])],
            url=url or "",
            raw_license=_license_for(book),
            format_type=fmt,
            language=(book.get("languages") or [None])[0],
            download_count=book.get("download_count"),
            raw={"gutendex_id": book["id"], "copyright": book.get("copyright")},
        )


# Structural title words that make a Gutendex query too specific to match.
# Gutendex requires ALL query words to appear, so "Ethics, Demonstrated in..."
# finds nothing while "Ethics" finds the book. Keep only content words.
_QUERY_STOPWORDS = frozenset(
    "the a an of on in and or to for with concerning being its part vol volume "
    "book complete works selected demonstrated geometrical order".split()
)


def _clean_title_words(title: str, keep: int = 2) -> list[str]:
    import re

    words = [w for w in re.findall(r"[a-z0-9]+", title.lower())
             if w not in _QUERY_STOPWORDS and len(w) > 2]
    return words[:keep]


def search(session: PoliteSession, work: CanonWork, max_results: int = 20) -> list[Edition]:
    """Hunt Gutenberg for open editions of `work`.

    Two-pass: first a targeted query (author surname + the most distinctive
    title word), and if that finds nothing, a broad author-only query. The
    ranker's title-relevance gate then discards other works by the same
    author, so broadening never lets the wrong book through. Editions with no
    retrievable text body are dropped."""
    surname = work.author.split()[-1]
    title_words = _clean_title_words(work.title, keep=2)

    queries = [f"{surname} {' '.join(title_words)}".strip()]
    if title_words:
        queries.append(surname)  # fallback: broaden to the whole author

    for q in queries:
        page = session.get(GUTENDEX_URL, params={"search": q, "languages": "en"}).json()
        editions = [e for e in _editions_from_page(page) if e.url]
        if editions:
            return editions[:max_results]
    return []


def _clean_person(name: str) -> str:
    """Drop Gutendex's parenthetical name expansions for display, e.g.
    'Meiklejohn, J. M. D. (John Miller Dow)' -> 'Meiklejohn, J. M. D.'."""
    import re

    return re.sub(r"\s*\([^)]*\)", "", name).strip()


def _attribution(work: CanonWork, ed: Edition) -> str:
    translator = _clean_person(ed.translators[0]) if ed.translators else None
    trans = f" ({translator}, Trans.)" if translator else ""
    return (
        f"{work.author} ({work.original_year}). {work.title}{trans}. "
        f"Project Gutenberg. Public domain."
    )


class GutenbergAdapter(Adapter):
    """Canon-driven harvest: ingest the best open edition of each canon work
    that hunts Gutenberg. Unlike arXiv (a firehose we filter), Gutenberg is
    pulled *by name* — the resolver already decided which edition; harvest
    fetches and ingests it, tagging it a canon hit so significance triage is
    short-circuited (a work we deliberately asked for is significant by
    definition).

    Idempotent with no cursor: each run re-resolves and the body-hash dedup
    absorbs re-fetches of unchanged texts."""

    id = "gutenberg"
    canon_driven = True

    #: set by the runner before harvest() for canon-driven adapters
    canon: Canon | None = None

    def harvest(
        self,
        session: PoliteSession,
        source: SourceConfig,
        cursor: str | None,
        limit: int | None,
    ) -> Iterator[Candidate]:
        if self.canon is None:
            raise RuntimeError("GutenbergAdapter.harvest requires canon to be set")

        yielded = 0
        for work in self.canon.works:
            if "gutenberg" not in work.hunt.sources:
                continue
            if work.domain not in source.domain:
                continue

            editions = search(session, work)
            if not editions:
                continue
            viable = [s for s in rank_editions(work, editions) if s.disqualified is None]
            if not viable:
                continue
            ed = viable[0].edition

            yield self._candidate(work, ed)
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def _candidate(self, work: CanonWork, ed: Edition) -> Candidate:
        translator = ed.translators[0] if ed.translators else None
        fm_fields = {
            "id": f"gutenberg:{ed.edition_id}",
            "canon_id": work.id,
            "title": ed.title or work.title,
            "authors": ed.authors or [work.author],
            "translator": translator,
            "domain": work.domain,
            "source_id": "gutenberg",
            "source_url": f"https://www.gutenberg.org/ebooks/{ed.edition_id}",
            "publisher": "Project Gutenberg",
            "original_year": work.original_year if work.original_year >= 0 else None,
            "pd_basis": "us_public_domain",
            "source_format": "text",
            "converter": "ds-corpus/0.2.0",
            "body_status": "full_text",
            # Canon hit short-circuits triage: deterministic significance.
            "significance_score": 100,
            "significance_signals": {
                "canon_hit": True,
                "authority_count": len(work.authorities),
                "primary_source": work.primary_source,
            },
            "triage": None,
            "tags": ["primary-source"] if work.primary_source else [],
            "attribution": _attribution(work, ed),
        }

        def fetch_body(session: PoliteSession) -> str:
            raw = session.get(ed.url).text
            return text_to_markdown(strip_gutenberg_boilerplate(raw))

        return Candidate(
            fm_fields=fm_fields,
            raw_license=ed.raw_license,
            rel_path=f"{work.domain}/gutenberg/{work.id}.md",
            fetch_body=fetch_body,
        )
