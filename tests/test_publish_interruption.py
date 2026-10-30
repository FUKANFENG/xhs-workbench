import sys
import time
from pathlib import Path

import pytest

from webapp.service import LocalService


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_actual_process_interruption_keeps_submitted_draft_uncertain(tmp_path, stop):
    service = LocalService(tmp_path)
    store = service.publishing.store
    draft = store.create({"kind": "image", "title": "中断测试", "body": "", "tags": []})
    store.mark(draft["id"], "preparing")
    project = str(Path(__file__).resolve().parents[1])
    script = ("import sys,time;sys.path.insert(0," + repr(project) + ");"
              "from pathlib import Path;from webapp.publish_store import PublishStore;"
              "s=PublishStore(Path(" + repr(str(store.root)) + "));"
              "s.begin_submit(" + repr(draft["id"]) + ",{'baseline':[]});"
              "print('publish_progress={\"stage\":\"submitting\"}',flush=True);time.sleep(30)")
    try:
        job = service.runner.start("publish_publish", "离线中断测试", [[sys.executable, "-u", "-c", script]],
                                   group_id=draft["id"], timeout=1 if stop == "timeout" else 10)
        deadline = time.monotonic() + 7
        while store.get(draft["id"])["state"] != "submitting" and time.monotonic() < deadline:
            time.sleep(.02)
        assert store.get(draft["id"])["state"] == "submitting"
        if stop == "cancel":
            service.runner.cancel(job["id"])
        while (service.runner.busy or store.get(draft["id"])["state"] != "uncertain") and time.monotonic() < deadline:
            time.sleep(.02)
        assert not service.runner.busy
        assert store.get(draft["id"])["state"] == "uncertain"
        assert service.runner.list()[0]["status"] == ("failed" if stop == "timeout" else "cancelled")
    finally:
        service.close()
    reopened = LocalService(tmp_path)
    assert reopened.publishing.store.get(draft["id"])["state"] == "uncertain"
    reopened.close()
