"""Source adapters. Register here; the registry's `adapter` field selects one."""

from __future__ import annotations

from ds_corpus.adapters.arxiv import ArxivAdapter
from ds_corpus.adapters.gutenberg import GutenbergAdapter

ADAPTERS = {
    "arxiv": ArxivAdapter,
    "gutenberg": GutenbergAdapter,
}
