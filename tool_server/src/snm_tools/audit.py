"""Append-only audit log of every tool call that touches data (JSON lines)."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> AuditLog:
        directory = os.environ.get("SNM_AUDIT_DIR")
        if not directory:
            return cls(None)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return cls(Path(directory) / f"audit-{stamp}-{os.getpid()}.jsonl")

    def record(self, **fields: Any) -> None:
        if self.path is None:
            return
        entry = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), **fields}
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
