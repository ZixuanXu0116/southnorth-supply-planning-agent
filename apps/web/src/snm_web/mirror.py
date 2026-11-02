"""Copies of the pages' run records in a Unity Catalog volume (a deployed app, docs/DEPLOYMENT.md).

The pages write the access log (``access.jsonl``) and each agent run's trace (``agents/<run>/trace.jsonl``)
under the runs directory. In a Databricks App that directory is local disk, lost when the app stops. A
background thread copies every new or changed file to the volume, under one folder per app start (the
access log starts again with each start), every ``interval`` seconds, right after each planner run
(``kick``), and once more when the app stops. A failed upload is retried on the next pass; it never stops
the pages.
"""

from __future__ import annotations

import io
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


class RunsMirror:
    def __init__(self, local: Path, target: str, files: Callable[[], Any], interval: float = 20.0) -> None:
        self.local = Path(local)
        self.target = target.rstrip("/")
        self._files = files  # returns a workspace client whose ``files`` API writes the volume
        self.interval = interval
        self._copied: dict[str, tuple[int, int]] = {}  # path -> (size, mtime) of the copy in the volume
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def sync(self) -> int:
        """Copy the files that are new or changed since their last copy; the number copied."""
        if not self.local.exists():
            return 0
        copied = 0
        for path in sorted(p for p in self.local.rglob("*") if p.is_file()):
            relative = path.relative_to(self.local).as_posix()
            stat = path.stat()
            mark = (stat.st_size, stat.st_mtime_ns)
            if self._copied.get(relative) == mark:
                continue
            try:
                self._files().files.upload(
                    f"{self.target}/{relative}", io.BytesIO(path.read_bytes()), overwrite=True
                )
            except Exception as error:  # the next pass retries; the pages keep working
                self.errors += 1
                if self.errors <= 3:
                    print(f"runs mirror: {relative} not copied: {error}", file=sys.stderr)
                continue
            self._copied[relative] = mark
            copied += 1
        return copied

    def start(self) -> RunsMirror:
        def loop() -> None:
            while not self._stopping.is_set():
                self._wake.wait(self.interval)
                self._wake.clear()
                self.sync()

        self._thread = threading.Thread(target=loop, name="runs-mirror", daemon=True)
        self._thread.start()
        return self

    def kick(self) -> None:
        """Copy now (after a planner run) instead of at the next interval."""
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self.sync()
