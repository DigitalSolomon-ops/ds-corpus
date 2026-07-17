"""Index — SQLite backend (local mode). Firestore lands in P9 behind the
same interface. The index is the manifest: documents, dedup hashes, cursors,
runs. Dedup key is the sha256 of the normalized body."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    canon_id      TEXT,
    domain        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    path          TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | superseded
    superseded_by TEXT,
    retrieved_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents(domain, status);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id, retrieved_at);
CREATE TABLE IF NOT EXISTS hashes (
    content_sha256 TEXT PRIMARY KEY,
    doc_id         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (
    source_id  TEXT PRIMARY KEY,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats_json  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SQLiteIndex:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    # ---- documents / dedup -------------------------------------------

    def has_hash(self, sha: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM hashes WHERE content_sha256=?", (sha,)
        ).fetchone()
        return row is not None

    def get_document(self, doc_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, canon_id, domain, source_id, path, content_sha256,"
            " status, superseded_by, retrieved_at FROM documents WHERE id=?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        keys = [
            "id", "canon_id", "domain", "source_id", "path",
            "content_sha256", "status", "superseded_by", "retrieved_at",
        ]
        return dict(zip(keys, row))

    def add_document(
        self,
        *,
        doc_id: str,
        canon_id: str | None,
        domain: str,
        source_id: str,
        path: str,
        content_sha256: str,
        retrieved_at: str,
    ) -> None:
        """Insert document + hash in one transaction. The UNIQUE constraint on
        hashes is the dedup guarantee: a concurrent duplicate loses cleanly."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO hashes(content_sha256, doc_id) VALUES(?,?)",
                (content_sha256, doc_id),
            )
            self._conn.execute(
                "INSERT INTO documents(id, canon_id, domain, source_id, path,"
                " content_sha256, status, retrieved_at)"
                " VALUES(?,?,?,?,?,?, 'active', ?)",
                (doc_id, canon_id, domain, source_id, path, content_sha256, retrieved_at),
            )

    def supersede(self, old_id: str, new_id: str) -> None:
        """Never delete: the old document stays, marked superseded."""
        with self._conn:
            self._conn.execute(
                "UPDATE documents SET status='superseded', superseded_by=? WHERE id=?",
                (new_id, old_id),
            )

    def all_documents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, path, content_sha256, status FROM documents ORDER BY id"
        ).fetchall()
        return [
            {"id": r[0], "path": r[1], "content_sha256": r[2], "status": r[3]}
            for r in rows
        ]

    def counts(self) -> dict:
        by_source = dict(
            self._conn.execute(
                "SELECT source_id, COUNT(*) FROM documents WHERE status='active'"
                " GROUP BY source_id"
            ).fetchall()
        )
        total = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        return {"total": total, "active_by_source": by_source}

    # ---- cursors ------------------------------------------------------

    def get_cursor(self, source_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT cursor FROM cursors WHERE source_id=?", (source_id,)
        ).fetchone()
        return row[0] if row else None

    def set_cursor(self, source_id: str, cursor: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO cursors(source_id, cursor, updated_at) VALUES(?,?,?)"
                " ON CONFLICT(source_id) DO UPDATE SET cursor=excluded.cursor,"
                " updated_at=excluded.updated_at",
                (source_id, cursor, _now()),
            )

    # ---- runs ---------------------------------------------------------

    def start_run(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs(run_id, started_at) VALUES(?,?)", (run_id, _now())
            )

    def finish_run(self, run_id: str, stats_json: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET finished_at=?, stats_json=? WHERE run_id=?",
                (_now(), stats_json, run_id),
            )

    def close(self) -> None:
        self._conn.close()
