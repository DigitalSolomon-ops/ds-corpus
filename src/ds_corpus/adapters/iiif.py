"""Generic IIIF adapter — the images-only / manuscript tier.

Subsumes the brief's IIIF sources (Gallica, DigiVatLib, Digital Bodleian): they
all serve a IIIF Presentation manifest describing page images, not text. This
adapter reads a curated list of manifest URLs (their search endpoints are
mostly robots-disallowed, so a human curates the pointers — author-side effort,
the same ethos as the canon) and produces a **metadata record per manifest**:

- `body_status: images_only`, **empty body** — never OCR'd, never invented
  (brief §4.3, §12.3). A metadata record pointing at a digitized manuscript is
  valuable; a hallucinated transcription is worse than nothing.
- `iiif_manifest` set to the manifest URL.
- **Fail-closed license.** The manifest's own rights/license statement must
  resolve to an allowlisted open license, or the manifest is skipped. This is
  the openness constraint at work: an image set marked "All rights reserved"
  (much of DigiVatLib) is *not* open and is not ingested, however revered.

Handles IIIF Presentation API 2.0 (`@id`/`label`/`license`/`attribution`) and
3.0 (`id`/language-map `label`/`rights`/`requiredStatement`).
"""

from __future__ import annotations

import re
from typing import Iterator

from ds_corpus import licensing
from ds_corpus.adapters.base import Adapter, Candidate
from ds_corpus.canon import Canon
from ds_corpus.http import PoliteSession
from ds_corpus.registry import SourceConfig


def _label(manifest: dict) -> str:
    label = manifest.get("label")
    if isinstance(label, str):
        return label
    if isinstance(label, dict):  # IIIF 3.0 language map: {"en": ["..."], "none": [...]}
        for key in ("en", "none", *label.keys()):
            if key in label and label[key]:
                return label[key][0] if isinstance(label[key], list) else str(label[key])
    if isinstance(label, list) and label:  # 2.0 multi-value
        v = label[0]
        return v.get("@value", str(v)) if isinstance(v, dict) else str(v)
    return "Untitled manuscript"


def _rights_url(manifest: dict) -> str | None:
    """The manifest's declared license/rights URL (2.0 `license`, 3.0 `rights`)."""
    val = manifest.get("license") or manifest.get("rights")
    if isinstance(val, list):
        val = val[0] if val else None
    if isinstance(val, dict):
        val = val.get("@id") or val.get("id")
    return val if isinstance(val, str) else None


def _attribution_text(manifest: dict) -> str:
    att = manifest.get("attribution")
    if isinstance(att, str):
        return att
    req = manifest.get("requiredStatement")  # IIIF 3.0
    if isinstance(req, dict):
        val = req.get("value", {})
        if isinstance(val, dict):
            for v in val.values():
                if v:
                    return v[0] if isinstance(v, list) else str(v)
    return ""


def _slug(manifest_url: str, manifest: dict) -> str:
    ident = str(manifest.get("@id") or manifest.get("id") or manifest_url)
    # Strip a trailing /manifest, /manifest.json, or /iiif/manifest so the slug
    # is the object's real identifier (qm670kv1873), not the literal "iiif".
    ident = re.sub(r"/(iiif/)?manifest(\.json)?$", "", ident)
    tail = ident.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", tail)[:80] or "manifest"


class IIIFAdapter(Adapter):
    """Curated IIIF manifests -> images_only metadata records. Canon-driven in
    the sense that a human curated the manifest list (curated significance);
    it bypasses the triage gate but each record is still license-gated."""

    id = "iiif"
    canon_driven = True
    canon: Canon | None = None

    def harvest(self, session, source: SourceConfig, cursor, limit) -> Iterator[Candidate]:
        manifest_urls = source.query.get("manifests", [])
        domain = source.domain[0] if source.domain else "art"
        yielded = 0
        for url in manifest_urls:
            manifest = session.get(url).json()
            cand = self._candidate(url, manifest, domain)
            if cand is None:                      # license fail-closed -> skip
                continue
            yield cand
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def _candidate(self, manifest_url: str, manifest: dict, domain: str) -> Candidate | None:
        raw_license = _rights_url(manifest)
        # Fail closed: no resolvable open license -> not ingested, even as metadata.
        if licensing.resolve(raw_license) is None:
            return None

        slug = _slug(manifest_url, manifest)
        title = _label(manifest)
        attribution = _attribution_text(manifest) or f"{title}. IIIF manifest: {manifest_url}"
        fm_fields = {
            "id": f"iiif:{slug}",
            "canon_id": None,
            "title": title,
            "authors": ["Unknown"],
            "domain": domain,
            "source_id": "iiif",
            "source_url": manifest_url,
            "canonical_url": manifest_url,
            "publisher": "IIIF manifest",
            "source_format": "iiif",
            "converter": "ds-corpus/0.2.0",
            "body_status": "images_only",
            "significance_score": 100,            # human-curated manifest = significant
            "significance_signals": {"curated_manifest": True},
            "triage": None,
            "iiif_manifest": manifest_url,
            "tags": ["images-only", "metadata-only", "manuscript"],
            "attribution": attribution,
        }

        def fetch_body(session: PoliteSession) -> str:
            return ""                             # never fabricate a manuscript body

        return Candidate(
            fm_fields=fm_fields,
            raw_license=raw_license,
            rel_path=f"{domain}/iiif/{slug}.md",
            fetch_body=fetch_body,
        )
