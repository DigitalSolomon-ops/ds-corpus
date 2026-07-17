"""Edition selection and quality ranking.

Given a canonical work and a pool of candidate open editions found by hunting,
score them so the best one is chosen and the rest are recorded as alternates.
The ranker is where "we found Kant" becomes "we found the *right* Kant" — a bad
translation slipping through here is the failure the P5 gate exists to catch.

Scoring is transparent and additive: each signal contributes a documented
number, and `score_edition` returns the breakdown so a human reviewing a pick
can see exactly why it won.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ds_corpus.canon import CanonWork

# Text-layer quality tiers. A clean embedded text layer is worth far more than
# OCR, which is worth more than page images with no text at all.
FORMAT_SCORE = {
    "text": 40,        # clean plain-text / HTML with real text
    "epub": 30,        # structured, usually clean
    "good_ocr": 20,
    "poor_ocr": 8,
    "images_only": 0,  # no body we can trust — never fabricate one
}

# Structural title words carrying no identifying signal. Dropped before
# comparing an edition's title to the work's title, so "The Republic" and
# "Republic" match and "The Poetics of Aristotle" does not match "Metaphysics".
_TITLE_STOPWORDS = frozenset(
    "the a an of on in and or to for with concerning being its part vol volume "
    "book complete works selected".split()
)


def _significant_tokens(title: str) -> set[str]:
    import re

    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in _TITLE_STOPWORDS and len(w) > 2}


@dataclass
class Edition:
    """A candidate open edition of a canonical work."""

    source_id: str
    edition_id: str                 # source-native id (e.g. gutendex book id)
    title: str
    authors: list[str]
    url: str                        # best retrievable body URL (or manifest)
    raw_license: str | None
    format_type: str = "text"       # key into FORMAT_SCORE
    translators: list[str] = field(default_factory=list)
    language: str | None = None
    download_count: int | None = None   # popularity proxy where available
    word_count: int | None = None       # usually unknown until fetch
    iiif_manifest: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ScoredEdition:
    edition: Edition
    score: float
    signals: dict
    disqualified: str | None = None   # set when the edition must not be chosen


def _matches_any(needles: list[str], haystacks: list[str]) -> str | None:
    """Case-insensitive substring match; returns the matched needle or None."""
    hay = " | ".join(h.lower() for h in haystacks)
    for n in needles:
        if n.lower() in hay:
            return n
    return None


def score_edition(work: CanonWork, ed: Edition) -> ScoredEdition:
    import math

    ae = work.acceptable_editions
    signals: dict = {}
    score = 0.0

    # Title relevance. When hunting broadly by author, the pool contains other
    # works by the same author (Aristotle's Poetics, Politics, ...). An edition
    # sharing no significant title token with the canonical work is a different
    # work and is disqualified — this is what keeps a broad hunt honest.
    want_tokens = _significant_tokens(work.title)
    have_tokens = _significant_tokens(ed.title)
    overlap = want_tokens & have_tokens
    if want_tokens and not overlap:
        return ScoredEdition(
            ed, -1.0, {"title_tokens": sorted(have_tokens)},
            disqualified=f"title mismatch: wanted {sorted(want_tokens)}",
        )
    if overlap:
        rel = min(25.0, 12.0 * len(overlap))
        signals["title_relevance"] = round(rel, 1)
        score += rel

    # Format / text-layer quality.
    fmt = FORMAT_SCORE.get(ed.format_type, 0)
    signals["format"] = fmt
    score += fmt

    # Translation preference. A preferred translation is a strong positive;
    # an avoided form (abridged, summary, adaptation) is disqualifying — the
    # whole point is to reject the wrong edition, not merely rank it lower.
    searchable = [ed.title] + ed.translators + ed.authors
    avoided = _matches_any(ae.avoid, searchable)
    if avoided:
        return ScoredEdition(ed, -1.0, {"avoided": avoided}, disqualified=f"matches avoid:{avoided!r}")

    if ae.prefer_translations:
        matched = _matches_any(ae.prefer_translations, ed.translators + [ed.title])
        if matched:
            signals["preferred_translation"] = matched
            score += 30
        else:
            # not disqualifying — an unlisted translation is still usable
            signals["preferred_translation"] = None

    # Language: prefer English editions for the English-canon hunt unless the
    # work is natively in another script we accept. Kept simple: en gets a nudge.
    if ed.language == "en":
        signals["language_en"] = 10
        score += 10

    # Completeness vs the work's minimum. Word count is usually unknown until
    # fetch; when known, a below-minimum edition is disqualified (likely an
    # excerpt or abridgement mislabeled).
    if ae.min_word_count and ed.word_count is not None:
        if ed.word_count < ae.min_word_count:
            return ScoredEdition(
                ed, -1.0, {"word_count": ed.word_count},
                disqualified=f"word_count {ed.word_count} < min {ae.min_word_count}",
            )
        signals["meets_min_words"] = True
        score += 10

    # Popularity as a weak quality proxy (a heavily-downloaded Gutenberg text
    # is usually the canonical clean one). Log-scaled so it never dominates.
    if ed.download_count:
        pop = min(10.0, math.log10(ed.download_count + 1) * 3)
        signals["popularity"] = round(pop, 1)
        score += pop

    # License clarity: a resolvable open license beats an ambiguous one. The
    # hard gate is enforced later by the writer; here it only breaks ties.
    if ed.raw_license:
        signals["has_license_signal"] = True
        score += 2

    return ScoredEdition(ed, round(score, 1), signals)


def rank_editions(work: CanonWork, editions: list[Edition]) -> list[ScoredEdition]:
    """Best first. Disqualified editions sink to the bottom, kept for audit."""
    scored = [score_edition(work, e) for e in editions]
    scored.sort(key=lambda s: (s.disqualified is not None, -s.score))
    return scored
