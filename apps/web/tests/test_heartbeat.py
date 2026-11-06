"""The app's heartbeat in the runs volume, read by the uptime check."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from snm_web.heartbeat import Heartbeat


class FakeFiles:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}
        self.fail = 0

    def upload(self, path, content, overwrite):
        assert overwrite
        if self.fail:
            self.fail -= 1
            raise RuntimeError("volume unavailable")
        self.uploads[path] = content.read()


class FakeClient:
    def __init__(self):
        self.files = FakeFiles()


def test_a_beat_writes_the_time_and_the_app_start():
    client = FakeClient()
    beat = Heartbeat(lambda: client, "/Volumes/snm/cases/runs/heartbeat.json", "app-20261105T150946Z")
    beat.beat(datetime(2026, 11, 5, 15, 12, 0, tzinfo=UTC))
    body = json.loads(client.files.uploads["/Volumes/snm/cases/runs/heartbeat.json"])
    assert body == {"at": "2026-11-05T15:12:00Z", "app_start": "app-20261105T150946Z"}


def test_a_failed_beat_is_counted_and_the_next_one_writes():
    client = FakeClient()
    client.files.fail = 1
    beat = Heartbeat(lambda: client, "/Volumes/v/heartbeat.json", "app-1", interval=0.01).start()
    deadline = time.monotonic() + 2
    while "/Volumes/v/heartbeat.json" not in client.files.uploads and time.monotonic() < deadline:
        time.sleep(0.01)
    beat.stop()
    assert beat.errors == 1 and beat.beats >= 1
