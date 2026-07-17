"""Document store — filesystem backend (local mode). GCS backend lands in P9
behind the same interface. Writes are atomic; nothing is ever deleted."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterator, Protocol


class Store(Protocol):
    def write(self, rel_path: str, content: str) -> None: ...
    def read(self, rel_path: str) -> str: ...
    def exists(self, rel_path: str) -> bool: ...
    def list(self, prefix: str = "") -> Iterator[str]: ...
    def halted(self) -> bool: ...


class FilesystemStore:
    """Local library rooted at a directory. Layout mirrors the GCS bucket."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _abs(self, rel_path: str) -> Path:
        p = PurePosixPath(rel_path)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe path: {rel_path}")
        return self.root / Path(*p.parts)

    def write(self, rel_path: str, content: str) -> None:
        """Atomic: write to a temp file in the target dir, then os.replace."""
        target = self._abs(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def read(self, rel_path: str) -> str:
        return self._abs(rel_path).read_text(encoding="utf-8")

    def exists(self, rel_path: str) -> bool:
        return self._abs(rel_path).exists()

    def list(self, prefix: str = "") -> Iterator[str]:
        base = self._abs(prefix) if prefix else self.root
        if not base.exists():
            return
        for p in sorted(base.rglob("*.md")):
            yield p.relative_to(self.root).as_posix()

    def halted(self) -> bool:
        """Kill switch: a HALT file at the library root aborts at the next
        task boundary."""
        return (self.root / "HALT").exists()

    def append_run_event(self, run_id: str, event: dict) -> None:
        """Run log lines. Append-only JSONL; not routed through write()
        because appends aren't replace-atomic and don't need to be."""
        runs = self.root / "_runs"
        runs.mkdir(parents=True, exist_ok=True)
        with open(runs / f"{run_id}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
