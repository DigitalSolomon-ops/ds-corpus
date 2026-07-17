"""Body normalization and format conversion.

Dedup hashes the NORMALIZED body (post-conversion, pre-front-matter), so
normalization must be deterministic: same input bytes -> same markdown ->
same sha256, regardless of when or where it ran.
"""

from __future__ import annotations

import hashlib
import re


def normalize_markdown(text: str) -> str:
    """Deterministic cleanup: unify newlines, strip trailing space,
    collapse 3+ blank lines to one blank line, single trailing newline."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n" if text.strip() else ""


def body_sha256(normalized_body: str) -> str:
    return hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()


def word_count(text: str) -> int:
    return len(text.split())


class ConversionError(Exception):
    """Raised when a converter cannot produce trustworthy markdown.
    The writer routes these to _quarantine/ — never to the library."""


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """PDF -> markdown via pymupdf4llm. Imported lazily: it's heavy and only
    PDF-bearing adapters pay for it."""
    try:
        import pymupdf  # noqa: F401
        import pymupdf4llm
    except ImportError as e:  # pragma: no cover
        raise ConversionError(f"pymupdf4llm not installed: {e}") from e
    import pymupdf as fitz

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            md = pymupdf4llm.to_markdown(doc)
    except Exception as e:
        raise ConversionError(f"pdf conversion failed: {e}") from e
    md = normalize_markdown(md)
    if not md:
        raise ConversionError("pdf conversion produced empty text")
    return md


def text_to_markdown(text: str) -> str:
    """Plain text passthrough with normalization."""
    md = normalize_markdown(text)
    if not md:
        raise ConversionError("empty text body")
    return md


# Project Gutenberg wraps every text in a legal header/footer. The body is
# between the START and END sentinel lines; everything outside is licensing
# boilerplate that is not the work and must not be indexed as it.
_PG_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I)
_PG_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I)


def strip_gutenberg_boilerplate(text: str) -> str:
    """Return only the work between PG's START/END sentinels.

    If a sentinel is missing (older texts vary), keep the whole thing rather
    than guess — better a little boilerplate than a truncated work."""
    start = _PG_START.search(text)
    body = text[start.end():] if start else text
    end = _PG_END.search(body)
    if end:
        body = body[: end.start()]
    return body
