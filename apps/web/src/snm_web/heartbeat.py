"""The app's heartbeat: the time, written every minute to ``heartbeat.json`` in the runs volume.

The uptime check (``deploy/databricks/jobs/uptime_check.py``) reads its age: running with a heartbeat younger
than 3 minutes is healthy. A failed write is counted and retried on the next beat; it never stops the pages.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


class Heartbeat:
    def __init__(self, files: Callable[[], Any], path: str, app_start: str, interval: float = 60.0) -> None:
        self._files = files  # returns a workspace client whose ``files`` API writes the volume
        self.path, self.app_start, self.interval = path, app_start, interval
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.beats = self.errors = 0

    def beat(self, now: datetime | None = None) -> None:
        at = (now or datetime.now(UTC)).isoformat(timespec="seconds").replace("+00:00", "Z")
        body = json.dumps({"at": at, "app_start": self.app_start}).encode("utf-8")
        try:
            self._files().files.upload(self.path, io.BytesIO(body), overwrite=True)
            self.beats += 1
        except Exception:
            self.errors += 1

    def start(self) -> Heartbeat:
        def loop() -> None:
            while not self._stopping.is_set():
                self.beat()
                self._stopping.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="heartbeat", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
