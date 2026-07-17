"""The significance gate wired into run_source for a non-canon adapter."""

from __future__ import annotations

import httpx
import pytest

from ds_corpus.adapters import ADAPTERS
from ds_corpus.adapters.base import Adapter, Candidate
from ds_corpus.http import PoliteSession
from ds_corpus.index import SQLiteIndex
from ds_corpus.registry import SourceConfig
from ds_corpus.scheduler import run_source
from ds_corpus.settings import load_settings
from ds_corpus.store import FilesystemStore

from conftest import CONFIG_DIR


class _StubAdapter(Adapter):
    """A non-canon adapter emitting two CC-BY docs: one strong, one weak."""
    id = "arxiv"

    def harvest(self, session, source, cursor, limit):
        for doc_id, authorities, cited in [
            ("arxiv:strong", ["a", "b"], 1000),   # clears deterministic floor
            ("arxiv:weak", [], None),             # below floor -> rejected free
        ]:
            fm = {
                "id": doc_id, "canon_id": None, "title": doc_id, "authors": ["X"],
                "domain": "coding", "source_id": "arxiv",
                "source_url": "https://arxiv.org/abs/x", "source_format": "pdf",
                "converter": "ds-corpus/0.2.0", "body_status": "full_text",
                "original_year": 2000,
                "significance_signals": {"authorities": authorities, "cited_by": cited,
                                         "primary_source": True},
                "attribution": "X (2000). T. arXiv. CC-BY-4.0.",
            }
            yield Candidate(
                fm_fields=fm, raw_license="cc-by-4.0",
                rel_path=f"coding/arxiv/{doc_id.split(':')[1]}.md",
                fetch_body=lambda s: "word " * 2000,
            )


class _FakeTriage:
    def __init__(self, sig):
        self.sig = sig
        self.seen = []

    def triage(self, records):
        from ds_corpus.triage import TriageVerdict
        self.seen = [r.doc_id for r in records]
        return [TriageVerdict(r.doc_id, self.sig, "cat", True, False, "judged") for r in records]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setitem(ADAPTERS, "arxiv", _StubAdapter)
    store = FilesystemStore(tmp_path / "lib")
    index = SQLiteIndex(tmp_path / "lib" / "_index.sqlite3")
    yield store, index
    index.close()


def _source():
    return SourceConfig.model_validate({
        "id": "arxiv", "domain": ["coding"], "tier": "open_api", "adapter": "arxiv",
        "license_policy": {"mode": "per_record", "allow": ["cc-by-4.0"]},
        "rate": {"requests_per_second": 10}, "schedule": "weekly",
    })


def _patch_session(monkeypatch):
    real = PoliteSession.__init__

    def fake(self, ua, rate, respect_robots=True, transport=None, sleep=None):
        real(self, ua, rate, respect_robots=False,
             transport=httpx.MockTransport(lambda r: httpx.Response(200)),
             sleep=lambda _: None)
    monkeypatch.setattr(PoliteSession, "__init__", fake)


def test_gate_accepts_strong_rejects_weak(env, monkeypatch):
    store, index = env
    _patch_session(monkeypatch)
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    fake = _FakeTriage(sig=90)                    # judge accepts the survivor

    monkeypatch.setattr("ds_corpus.scheduler._make_triage_client", lambda s: fake)
    stats = run_source(_source(), settings, store, index, log=lambda *_: None)

    assert fake.seen == ["arxiv:strong"]          # only the floor-clearer is judged
    assert stats.written == 1                     # strong accepted -> written
    assert stats.rejected == 1                    # weak rejected below floor
    assert store.exists("coding/arxiv/strong.md")
    assert not store.exists("coding/arxiv/weak.md")
    # rejection is kept with rationale, never deleted
    rej = list(store.list("_rejected"))
    assert any("weak" in p for p in rej)


def test_gate_triage_reject_written_to_rejected(env, monkeypatch):
    store, index = env
    _patch_session(monkeypatch)
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    fake = _FakeTriage(sig=10)                    # judge rejects the survivor too

    monkeypatch.setattr("ds_corpus.scheduler._make_triage_client", lambda s: fake)
    stats = run_source(_source(), settings, store, index, log=lambda *_: None)

    assert stats.written == 0                     # nothing survived triage
    assert stats.rejected == 2                    # weak (floor) + strong (triage)
    body = "".join(store.read(p) for p in store.list("_rejected"))
    assert "reject_triage" in body and "reject_deterministic" in body


def test_cost_cap_leaves_pending_no_spend(env, monkeypatch):
    store, index = env
    _patch_session(monkeypatch)
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    settings.budgets.max_triage_usd = 0.0000001   # cap below any real cost
    fake = _FakeTriage(sig=90)

    monkeypatch.setattr("ds_corpus.scheduler._make_triage_client", lambda s: fake)
    stats = run_source(_source(), settings, store, index, log=lambda *_: None)

    assert fake.seen == []                         # cap tripped, no Claude call
    assert stats.pending == 1                      # strong left pending
    assert stats.written == 0
    assert stats.rejected == 1                     # weak still floor-rejected
