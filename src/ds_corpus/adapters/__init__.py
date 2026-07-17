"""Source adapters. Register here; the registry's `adapter` field selects one."""

from __future__ import annotations

from ds_corpus.adapters.arxiv import ArxivAdapter
from ds_corpus.adapters.gutenberg import GutenbergAdapter
from ds_corpus.adapters.internet_archive import InternetArchiveAdapter

ADAPTERS = {
    "arxiv": ArxivAdapter,
    "gutenberg": GutenbergAdapter,
    "internet_archive": InternetArchiveAdapter,
}
