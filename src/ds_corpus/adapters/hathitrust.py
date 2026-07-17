"""HathiTrust adapter — full-view-only, metadata records.

HathiTrust holds millions of digitized volumes, but most are **in copyright and
limited-view** — not open. The brief's rule is absolute (§6, §12.2): a
limited-view item is never ingested, full stop. This adapter enforces that by
gating on HathiTrust's per-item `rightsCode` against an allowlist of
**full-view, openly-licensed** codes; every other code (in-copyright `ic`,
undetermined `und`, `nobody`, out-of-print `op`, orphan `orph`, …) is excluded.

HathiTrust's terms restrict programmatic full-text download, so this produces
**metadata records** (`body_status: metadata_only`, empty body) with a link to
the volume — honest about the access limit while still recording coverage. It
hunts by curated bibliographic ids (`oclc:…`, `lccn:…`, `recordnumber:…`) in the
source config, since the Bib API has no title search.
"""

from __future__ import annotations

from typing import Iterator

from ds_corpus.adapters.base import Adapter, Candidate
from ds_corpus.canon import Canon
from ds_corpus.http import PoliteSession
from ds_corpus.registry import SourceConfig

BIB_API = "https://catalog.hathitrust.org/api/volumes/brief/json"

# Full-view, openly-licensed rightsCodes -> the license they map to. A code NOT
# in this table is treated as limited-view / not-open and is never ingested.
_FULL_VIEW_RIGHTS: dict[str, str] = {
    "pd": "public domain",
    "pdus": "public domain",           # PD in the US
    "world": "public domain",          # PD worldwide
    "pd-google": "public domain",
    "cc-zero": "cc0-1.0",
    "cc-by-3.0": "cc-by-3.0",
    "cc-by-4.0": "cc-by-4.0",
    "cc-by-sa-3.0": "cc-by-sa-3.0",
    "cc-by-sa-4.0": "cc-by-sa-4.0",
}


def is_full_view(rights_code: str | None) -> bool:
    return (rights_code or "").strip().lower() in _FULL_VIEW_RIGHTS


def license_for_rights(rights_code: str | None) -> str | None:
    return _FULL_VIEW_RIGHTS.get((rights_code or "").strip().lower())


def select_full_view_item(record_block: dict) -> dict | None:
    """Return the first full-view item in a Bib API record block, or None.
    Limited-view items are ignored entirely — they can never be selected."""
    for item in record_block.get("items", []):
        if is_full_view(item.get("rightsCode")):
            return item
    return None


class HathiTrustAdapter(Adapter):
    id = "hathitrust"
    canon_driven = True
    canon: Canon | None = None

    def harvest(self, session, source: SourceConfig, cursor, limit) -> Iterator[Candidate]:
        ids = source.query.get("ids", [])
        domain = source.domain[0] if source.domain else "law"
        yielded = 0
        for bib_id in ids:
            data = session.get(f"{BIB_API}/{bib_id}").json()
            block = data.get(bib_id, {})
            item = select_full_view_item(block)
            if item is None:                       # only limited-view -> never ingest
                continue
            cand = self._candidate(bib_id, block, item, domain)
            if cand is None:
                continue
            yield cand
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def _candidate(self, bib_id, block: dict, item: dict, domain: str) -> Candidate | None:
        raw_license = license_for_rights(item.get("rightsCode"))
        if raw_license is None:                    # belt-and-suspenders with is_full_view
            return None

        records = block.get("records", {})
        rec = next(iter(records.values()), {})
        titles = rec.get("titles") or ["Untitled"]
        title = titles[0]
        htid = item.get("htid", bib_id)
        slug = str(htid).replace(".", "_").replace("/", "_").replace(":", "_")[:80]

        fm_fields = {
            "id": f"hathitrust:{htid}",
            "canon_id": None,
            "title": title,
            "authors": ["Unknown"],
            "domain": domain,
            "source_id": "hathitrust",
            "source_url": item.get("itemURL", rec.get("recordURL", "")),
            "publisher": "HathiTrust",
            "source_format": "metadata",
            "converter": "ds-corpus/0.2.0",
            "body_status": "metadata_only",
            "significance_score": 100,
            "significance_signals": {"curated_id": True, "hathitrust_rights": item.get("rightsCode")},
            "triage": None,
            "tags": ["metadata-only", "full-view"],
            "attribution": f"{title}. HathiTrust ({htid}). rights: {item.get('rightsCode')}.",
        }

        def fetch_body(session: PoliteSession) -> str:
            return ""                              # metadata only; no full text fetched

        return Candidate(
            fm_fields=fm_fields,
            raw_license=raw_license,
            rel_path=f"{domain}/hathitrust/{slug}.md",
            fetch_body=fetch_body,
        )
