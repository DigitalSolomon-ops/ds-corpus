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

from ds_corpus.canon import CanonWork
from ds_corpus.editions import Edition
from ds_corpus.http import PoliteSession

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
