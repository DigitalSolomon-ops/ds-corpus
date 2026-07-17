"""arXiv adapter — OAI-PMH, the reference pattern every adapter clones.

Endpoint: https://oaipmh.arxiv.org/oai (verified live 2026-07-16; the old
export.arxiv.org/oai2 host now serves a disallow-all robots.txt). PDFs come
from arxiv.org/pdf, which robots.txt allows for * with Crawl-delay: 15 —
the polite session honors that delay automatically. Cursor is the max OAI
datestamp harvested;
re-harvesting from it overlaps slightly and dedup absorbs the overlap —
better than a gap.

License note: most arXiv papers carry only the arXiv non-exclusive
distribution grant, which is NOT open and NOT in any allowlist. Those are
skipped by the gate, by design. Only CC-licensed/public-domain papers land.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterator

from ds_corpus.adapters.base import Adapter, Candidate
from ds_corpus.http import PoliteSession
from ds_corpus.normalize import pdf_to_markdown
from ds_corpus.registry import SourceConfig

OAI_URL = "https://oaipmh.arxiv.org/oai"
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arxiv": "http://arxiv.org/OAI/arXiv/",
}


@dataclass
class ArxivRecord:
    arxiv_id: str
    datestamp: str          # OAI header datestamp (cursor material)
    title: str
    abstract: str
    authors: list[str]      # "Keyname, Forenames"
    created: str            # first submission date
    categories: list[str]
    license_uri: str | None


def parse_oai_page(xml_text: str) -> tuple[list[ArxivRecord], str | None]:
    """Pure parse of one ListRecords page -> (records, resumption_token)."""
    root = ET.fromstring(xml_text)
    err = root.find("oai:error", NS)
    if err is not None:
        code = err.get("code", "")
        if code == "noRecordsMatch":
            return [], None
        raise RuntimeError(f"OAI error {code}: {err.text}")

    records: list[ArxivRecord] = []
    for rec in root.findall(".//oai:record", NS):
        header = rec.find("oai:header", NS)
        if header is not None and header.get("status") == "deleted":
            continue
        meta = rec.find(".//arxiv:arXiv", NS)
        if meta is None:
            continue

        def txt(tag: str) -> str:
            el = meta.find(f"arxiv:{tag}", NS)
            return (el.text or "").strip() if el is not None else ""

        authors = []
        for a in meta.findall(".//arxiv:author", NS):
            keyname = a.findtext("arxiv:keyname", "", NS).strip()
            forenames = a.findtext("arxiv:forenames", "", NS).strip()
            authors.append(f"{keyname}, {forenames}" if forenames else keyname)

        records.append(
            ArxivRecord(
                arxiv_id=txt("id"),
                datestamp=(header.findtext("oai:datestamp", "", NS) or "").strip(),
                title=" ".join(txt("title").split()),
                abstract=txt("abstract"),
                authors=authors,
                created=txt("created"),
                categories=txt("categories").split(),
                license_uri=txt("license") or None,
            )
        )

    token_el = root.find(".//oai:resumptionToken", NS)
    token = token_el.text.strip() if token_el is not None and token_el.text else None
    return records, token


def _rel_path(arxiv_id: str) -> str:
    # new-style "2601.01234" -> coding/arxiv/2601/2601.01234.md
    # old-style "cs/0309136" -> coding/arxiv/legacy/cs-0309136.md
    if "/" in arxiv_id:
        return f"coding/arxiv/legacy/{arxiv_id.replace('/', '-')}.md"
    return f"coding/arxiv/{arxiv_id.split('.')[0]}/{arxiv_id}.md"


class ArxivAdapter(Adapter):
    id = "arxiv"

    def harvest(
        self,
        session: PoliteSession,
        source: SourceConfig,
        cursor: str | None,
        limit: int | None,
    ) -> Iterator[Candidate]:
        sets = source.query.get("sets", ["cs"])
        params: dict = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": sets[0]}
        # cursor wins; otherwise an optional configured start date keeps the
        # first-ever harvest from walking 30 years of pre-CC records.
        start = cursor or source.query.get("initial_from")
        if start:
            params["from"] = start

        yielded = 0
        max_datestamp = cursor or ""
        while True:
            resp = session.get(OAI_URL, params=params)
            records, token = parse_oai_page(resp.text)
            for r in records:
                if r.datestamp > max_datestamp:
                    max_datestamp = r.datestamp
                    self.new_cursor = max_datestamp
                yield self._candidate(r)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if token is None:
                return
            params = {"verb": "ListRecords", "resumptionToken": token}

    def _candidate(self, r: ArxivRecord) -> Candidate:
        aid = r.arxiv_id
        year = int(r.created[:4]) if r.created[:4].isdigit() else None
        attribution = (
            f"{'; '.join(r.authors)} ({year or 'n.d.'}). {r.title}. "
            f"arXiv:{aid}. License: {r.license_uri or 'unresolved'}."
        )
        fm_fields = {
            "id": f"arxiv:{aid}",
            "canon_id": None,
            "title": r.title,
            "authors": r.authors or ["Unknown"],
            "domain": "coding",
            "source_id": "arxiv",
            "source_url": f"https://arxiv.org/abs/{aid}",
            "license_url": r.license_uri,
            "publisher": "arXiv",
            "original_year": year,
            "published_at": r.created or None,
            "source_format": "pdf",
            "converter": "ds-corpus/0.2.0",
            "body_status": "full_text",
            "tags": r.categories,
            "attribution": attribution,
        }

        def fetch_body(session: PoliteSession) -> str:
            pdf = session.get(f"https://arxiv.org/pdf/{aid}").content
            return pdf_to_markdown(pdf)

        return Candidate(
            fm_fields=fm_fields,
            raw_license=r.license_uri,
            rel_path=_rel_path(aid),
            fetch_body=fetch_body,
        )
