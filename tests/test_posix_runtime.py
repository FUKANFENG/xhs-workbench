import os
import sys
import time

import pytest

from webapp.runtime import JobRunner


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation")
def test_cancel_closes_child_process_pipes(tmp_path):
    marker = tmp_path / "child.pid"
    script = ("import pathlib,subprocess,sys,time; "
              "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
              f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(30)")
    runner = JobRunner(tmp_path)
    job = runner.start("test", "test", [[sys.executable, "-c", script]])
    deadline = time.monotonic() + 5
    try:
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        runner.cancel(job["id"])
        deadline = time.monotonic() + 3
        while runner.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not runner.busy, "A surviving child is still holding the worker output pipe open"
        assert runner.list()[0]["status"] == "cancelled"
    finally:
        runner.close()
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), 9)
            except ProcessLookupError:
                pass
