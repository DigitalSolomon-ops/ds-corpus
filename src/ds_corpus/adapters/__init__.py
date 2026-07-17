"""Source adapters. Register here; the registry's `adapter` field selects one."""

from __future__ import annotations

from ds_corpus.adapters.arxiv import ArxivAdapter

ADAPTERS = {
    "arxiv": ArxivAdapter,
}
