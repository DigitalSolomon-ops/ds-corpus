from __future__ import annotations

from ds_corpus.canon import AcceptableEditions, CanonWork
from ds_corpus.editions import Edition, rank_editions, score_edition


def _work(**over):
    base = dict(
        id="kant_critique_pure_reason",
        title="Critique of Pure Reason",
        author="Immanuel Kant",
        original_year=1781,
        domain="metaphysics",
        why_canonical="Foundational to modern metaphysics.",
        authorities=["philpapers_core"],
        primary_source=True,
        hunt={"sources": ["gutenberg"]},
    )
    base.update(over)
    w = CanonWork.model_validate(base)
    return w


def _ed(**over):
    base = dict(
        source_id="gutenberg",
        edition_id="4280",
        title="The Critique of Pure Reason",
        authors=["Kant, Immanuel"],
        url="https://www.gutenberg.org/ebooks/4280.txt.utf-8",
        raw_license="Public domain in the USA.",
        format_type="text",
        translators=[],
        language="en",
        download_count=500,
    )
    base.update(over)
    return Edition(**base)


def test_preferred_translation_beats_unlisted():
    work = _work(acceptable_editions=AcceptableEditions(prefer_translations=["Meiklejohn", "Kemp Smith"]))
    good = _ed(edition_id="4280", translators=["Meiklejohn, J. M. D."])
    plain = _ed(edition_id="9999", translators=["Anon"])
    ranked = rank_editions(work, [plain, good])
    assert ranked[0].edition.edition_id == "4280"
    assert ranked[0].signals["preferred_translation"] == "Meiklejohn"


def test_avoided_form_is_disqualified_even_if_otherwise_strong():
    work = _work(acceptable_editions=AcceptableEditions(avoid=["abridged", "summary"]))
    abridged = _ed(edition_id="bad", title="Critique of Pure Reason (Abridged)", download_count=100000)
    full = _ed(edition_id="good", download_count=10)
    ranked = rank_editions(work, [abridged, full])
    assert ranked[0].edition.edition_id == "good"        # popularity didn't save the abridgement
    assert ranked[-1].disqualified and "avoid" in ranked[-1].disqualified


def test_below_min_word_count_disqualified_when_known():
    work = _work(acceptable_editions=AcceptableEditions(min_word_count=100000))
    excerpt = _ed(edition_id="excerpt", word_count=5000)
    full = _ed(edition_id="full", word_count=310000)
    ranked = rank_editions(work, [excerpt, full])
    assert ranked[0].edition.edition_id == "full"
    assert ranked[-1].disqualified and "word_count" in ranked[-1].disqualified


def test_clean_text_beats_images_only():
    work = _work()
    images = _ed(edition_id="scan", format_type="images_only", title="Critique of Pure Reason")
    text = _ed(edition_id="clean", format_type="text", title="Critique of Pure Reason")
    ranked = rank_editions(work, [images, text])
    assert ranked[0].edition.edition_id == "clean"


def test_wrong_work_by_same_author_is_disqualified():
    # Hunting Aristotle's Metaphysics broadly returns Poetics, Politics, etc.
    work = _work(id="aristotle_metaphysics", title="Metaphysics", author="Aristotle",
                 original_year=-350, acceptable_editions=AcceptableEditions())
    poetics = _ed(edition_id="1974", title="The Poetics of Aristotle", authors=["Aristotle"])
    ranked = rank_editions(work, [poetics])
    assert ranked[0].disqualified and "title mismatch" in ranked[0].disqualified


def test_title_relevance_rewards_more_overlap():
    work = _work()  # Critique of Pure Reason
    strong = _ed(edition_id="full", title="The Critique of Pure Reason")
    weak = _ed(edition_id="part", title="Critique of Judgement")  # shares only 'critique'
    ranked = rank_editions(work, [weak, strong])
    assert ranked[0].edition.edition_id == "full"
    assert ranked[0].signals["title_relevance"] > ranked[1].signals["title_relevance"]


def test_score_breakdown_is_transparent():
    work = _work(acceptable_editions=AcceptableEditions(prefer_translations=["Meiklejohn"]))
    s = score_edition(work, _ed(translators=["Meiklejohn, J. M. D."]))
    assert s.signals["format"] == 40
    assert s.signals["preferred_translation"] == "Meiklejohn"
    assert s.signals["language_en"] == 10
    assert s.disqualified is None
