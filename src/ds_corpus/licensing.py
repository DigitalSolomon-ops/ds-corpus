"""License resolution — governing constraint #1, fail-closed.

`resolve()` maps the many string forms a source may report (URLs, SPDX ids,
prose) to a normalized license id, or None when the form is unknown. There is
deliberately no fuzzy matching: an unrecognized license is None, and a None
license is never written. Known-but-not-open licenses (CC-NC/ND, arXiv's
non-exclusive grant) DO normalize — so logs can distinguish "known, not
allowed" from "unknown" — but they will never appear in an allowlist.
"""

from __future__ import annotations

import re


def _norm_key(raw: str) -> str:
    """Canonicalize a raw license string for table lookup."""
    s = raw.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.rstrip("/").rstrip(".")
    s = re.sub(r"\s+", " ", s)
    return s


# Lookup table: normalized raw form -> license id. Extend deliberately;
# every addition needs a row in tests/fixtures of the licensing table test.
_TABLE: dict[str, str] = {
    # --- public domain ---
    "public domain": "public-domain",
    "public-domain": "public-domain",
    "publicdomain": "public-domain",
    "public domain in the usa": "public-domain",  # Gutenberg's phrasing
    "us government work": "public-domain",
    "creativecommons.org/publicdomain/mark/1.0": "public-domain",
    # --- CC0 ---
    "cc0": "cc0-1.0",
    "cc0 1.0": "cc0-1.0",
    "cc0-1.0": "cc0-1.0",
    "cc-zero": "cc0-1.0",
    "creativecommons.org/publicdomain/zero/1.0": "cc0-1.0",
    # --- CC BY ---
    "cc-by-4.0": "cc-by-4.0",
    "cc by 4.0": "cc-by-4.0",
    "creativecommons.org/licenses/by/4.0": "cc-by-4.0",
    "creativecommons.org/licenses/by/4.0/legalcode": "cc-by-4.0",
    "cc-by-3.0": "cc-by-3.0",
    "creativecommons.org/licenses/by/3.0": "cc-by-3.0",
    # --- CC BY-SA ---
    "cc-by-sa-4.0": "cc-by-sa-4.0",
    "cc by-sa 4.0": "cc-by-sa-4.0",
    "creativecommons.org/licenses/by-sa/4.0": "cc-by-sa-4.0",
    "cc-by-sa-3.0": "cc-by-sa-3.0",
    "creativecommons.org/licenses/by-sa/3.0": "cc-by-sa-3.0",
    # --- known but never open (normalize for honest logging) ---
    "creativecommons.org/licenses/by-nc/4.0": "cc-by-nc-4.0",
    "creativecommons.org/licenses/by-nc-sa/4.0": "cc-by-nc-sa-4.0",
    "creativecommons.org/licenses/by-nc-nd/4.0": "cc-by-nc-nd-4.0",
    "creativecommons.org/licenses/by-nd/4.0": "cc-by-nd-4.0",
    "arxiv.org/licenses/nonexclusive-distrib/1.0": "arxiv-nonexclusive-1.0",
    "arxiv.org/licenses/assumed-1991-2003": "arxiv-assumed-1991-2003",
}


def resolve(raw: str | None) -> str | None:
    """Normalized license id, or None if the form is not recognized.

    None means "unknown" and unknown means "do not write". Bare 'cc-by'
    with no version is deliberately unknown — guessing a version would be
    a licensing claim we can't back.
    """
    if raw is None or not raw.strip():
        return None
    return _TABLE.get(_norm_key(raw))


def is_allowed(license_id: str | None, allowlist: list[str]) -> bool:
    """The gate. None never passes; nothing outside the allowlist passes."""
    return license_id is not None and license_id in allowlist
