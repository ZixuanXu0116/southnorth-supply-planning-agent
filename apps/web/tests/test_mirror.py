"""Copies of the pages' run records in a volume (a deployed app)."""

from __future__ import annotations

import time

from snm_web.mirror import RunsMirror


class FakeFiles:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}
        self.fail = 0

    def upload(self, path, content, overwrite):
        assert overwrite
        if self.fail:
            self.fail -= 1
            raise RuntimeError("warehouse unavailable")
        self.uploads[path] = content.read()


class FakeClient:
    def __init__(self):
        self.files = FakeFiles()


def test_new_and_changed_files_are_copied_once(tmp_path):
    client = FakeClient()
    mirror = RunsMirror(tmp_path, "/Volumes/snm/cases/runs/app-1/", lambda: client)
    (tmp_path / "access.jsonl").write_text('{"action": "send"}\n')
    trace = tmp_path / "agents" / "20261102T000000-abc" / "trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"event": "run_start"}\n')
    assert mirror.sync() == 2
    assert set(client.files.uploads) == {
        "/Volumes/snm/cases/runs/app-1/access.jsonl",
        "/Volumes/snm/cases/runs/app-1/agents/20261102T000000-abc/trace.jsonl",
    }
    assert mirror.sync() == 0  # nothing changed
    with trace.open("a") as handle:
        handle.write('{"event": "run_end"}\n')
    assert mirror.sync() == 1
    assert client.files.uploads[
        "/Volumes/snm/cases/runs/app-1/agents/20261102T000000-abc/trace.jsonl"
    ].endswith(b'"run_end"}\n')


def test_a_failed_copy_is_retried_and_never_raises(tmp_path):
    client = FakeClient()
    client.files.fail = 1
    mirror = RunsMirror(tmp_path, "/Volumes/v", lambda: client)
    (tmp_path / "access.jsonl").write_text("{}\n")
    assert mirror.sync() == 0 and mirror.errors == 1
    assert mirror.sync() == 1


def test_a_kick_copies_before_the_interval_and_stop_copies_the_rest(tmp_path):
    client = FakeClient()
    mirror = RunsMirror(tmp_path, "/Volumes/v", lambda: client, interval=3600).start()
    (tmp_path / "access.jsonl").write_text("{}\n")
    mirror.kick()
    deadline = time.monotonic() + 5
    while "/Volumes/v/access.jsonl" not in client.files.uploads and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "/Volumes/v/access.jsonl" in client.files.uploads
    (tmp_path / "late.jsonl").write_text("{}\n")
    mirror.stop()
    assert "/Volumes/v/late.jsonl" in client.files.uploads
