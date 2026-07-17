"""Adapter contract.

An adapter turns a source's API into a stream of Candidates. It never writes,
never gates licenses (the writer does, fail-closed — the adapter's pre-check
only avoids pointless downloads), and never touches the network except through
the PoliteSession it is handed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Iterator

from ds_corpus.http import PoliteSession
from ds_corpus.registry import SourceConfig


@dataclass
class Candidate:
    # Front-matter fields the adapter knows. The writer owns license,
    # content_sha256, word_count, retrieved_at.
    fm_fields: dict
    raw_license: str | None
    rel_path: str
    # Deferred body fetch: called only after the license pre-check passes
    # and only outside --dry-run. Returns markdown body.
    fetch_body: Callable[[PoliteSession], str] = field(repr=False, default=lambda s: "")


class Adapter(ABC):
    id: str

    #: set by harvest() as it progresses; persisted by the runner on clean
    #: completion so the next run resumes where this one left off.
    new_cursor: str | None = None

    @abstractmethod
    def harvest(
        self,
        session: PoliteSession,
        source: SourceConfig,
        cursor: str | None,
        limit: int | None,
    ) -> Iterator[Candidate]: ...
