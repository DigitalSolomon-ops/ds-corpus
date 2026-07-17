from __future__ import annotations

import pytest

from ds_corpus.licensing import is_allowed, resolve

# Table-driven fixture: every string form we expect to meet, and what it
# must resolve to. None = unknown = never written.
CASES = [
    # public domain, many spellings
    ("Public domain", "public-domain"),
    ("public-domain", "public-domain"),
    ("Public domain in the USA.", "public-domain"),
    ("US Government Work", "public-domain"),
    ("https://creativecommons.org/publicdomain/mark/1.0/", "public-domain"),
    # CC0
    ("CC0", "cc0-1.0"),
    ("cc0-1.0", "cc0-1.0"),
    ("http://creativecommons.org/publicdomain/zero/1.0/", "cc0-1.0"),
    ("https://creativecommons.org/publicdomain/zero/1.0", "cc0-1.0"),
    # CC BY
    ("CC-BY-4.0", "cc-by-4.0"),
    ("CC BY 4.0", "cc-by-4.0"),
    ("http://creativecommons.org/licenses/by/4.0/", "cc-by-4.0"),
    ("https://creativecommons.org/licenses/by/4.0/legalcode", "cc-by-4.0"),
    ("https://creativecommons.org/licenses/by/3.0/", "cc-by-3.0"),
    # CC BY-SA
    ("CC-BY-SA-4.0", "cc-by-sa-4.0"),
    ("https://creativecommons.org/licenses/by-sa/4.0/", "cc-by-sa-4.0"),
    # known but never open — must normalize (for logs), never allowlisted
    ("http://creativecommons.org/licenses/by-nc/4.0/", "cc-by-nc-4.0"),
    ("http://creativecommons.org/licenses/by-nc-sa/4.0/", "cc-by-nc-sa-4.0"),
    ("http://creativecommons.org/licenses/by-nd/4.0/", "cc-by-nd-4.0"),
    ("http://arxiv.org/licenses/nonexclusive-distrib/1.0/", "arxiv-nonexclusive-1.0"),
    # unknown forms — fail closed
    (None, None),
    ("", None),
    ("   ", None),
    ("cc-by", None),                    # no version: guessing = a false claim
    ("all rights reserved", None),
    ("see terms of service", None),
    ("https://example.com/my-cool-license", None),
    ("GPL-3.0", None),                  # software license, not a text license we accept
    ("CC BY 5.0", None),                # no such version
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_resolve_table(raw, expected):
    assert resolve(raw) == expected


ALLOWLIST = ["public-domain", "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0"]


def test_none_is_never_allowed():
    assert not is_allowed(None, ALLOWLIST)


def test_unknown_string_end_to_end_never_allowed():
    assert not is_allowed(resolve("all rights reserved"), ALLOWLIST)


def test_known_but_not_open_never_allowed():
    assert not is_allowed(resolve("http://creativecommons.org/licenses/by-nc/4.0/"), ALLOWLIST)
    assert not is_allowed(resolve("http://arxiv.org/licenses/nonexclusive-distrib/1.0/"), ALLOWLIST)


def test_open_licenses_allowed():
    for raw in ("Public domain", "CC0", "CC-BY-4.0", "https://creativecommons.org/licenses/by-sa/4.0/"):
        assert is_allowed(resolve(raw), ALLOWLIST), raw
