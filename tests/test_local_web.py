import argparse
import json
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.app import create_app
from webapp.runtime import JobRunner
from webapp.service import SCRIPTS, LocalService

sys.path.insert(0, str(SCRIPTS))
import xhs_group_tool
from xhs_ext.group_chat_exporter import fetch_group_history, write_export


def message(number, text="讨论工作计划"):
    return {"store_id": str(number), "id": str(number), "sender_id": "member-1",
            "created_at": 1793059200000 + number * 1000,
            "content": json.dumps({"nickname": "测试成员", "content_type": 1, "content": text}, ensure_ascii=False)}


class HistoryAPI:
    def get_group_history_page(self, group_id, last_id, **kwargs):
        pages = {"0": [6, 5, 4], "4": [4, 3, 2], "2": [2, 1]}
        return True, "", {"data": {"out_message_list": [message(i) for i in pages[last_id]]}}

    def close(self):
        pass


def test_incremental_overflow_preserves_previous_files(tmp_path, monkeypatch):
    write_export({"group_id": "123", "messages": [message(1), message(2)], "pages_fetched": 1,
                  "completed": True, "next_last_id": "1"}, tmp_path)
    before = (tmp_path / "123/messages.json").read_bytes()
    cookie = tmp_path / "cookie.txt"
    cookie.write_text("a1=example; web_session=example", encoding="utf-8")
    if sys.platform != "win32":
        cookie.chmod(0o600)
    fake_module = types.ModuleType("xhs_ext.xhs_im_apis")
    fake_module.XHS_IM_Apis = HistoryAPI
    monkeypatch.setitem(sys.modules, "xhs_ext.xhs_im_apis", fake_module)
    args = argparse.Namespace(spider_root=Path(__file__).parents[1], cookie_file=cookie, chat_url="123",
                              output_dir=tmp_path, last_id="0", max_pages=1, limit=3, delay=0,
                              update=True, resume=False, overwrite=False)
    monkeypatch.setattr(xhs_group_tool, "spider_root", lambda value: value)
    with pytest.raises(RuntimeError, match="page limit"):
        xhs_group_tool.cmd_group_chat(args)
    assert (tmp_path / "123/messages.json").read_bytes() == before
    args.max_pages = 4
    assert xhs_group_tool.cmd_group_chat(args) == 0
    result = json.loads((tmp_path / "123/messages.json").read_text(encoding="utf-8"))
    assert [item["store_id"] for item in result["messages"]] == [str(i) for i in range(1, 7)]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token = client.get("/api/bootstrap").json()["token"]
        client.headers["X-Local-Token"] = token
        yield client, app.state.service


def test_local_boundary_and_private_files(client):
    browser, service = client
    assert browser.get("/").status_code == 200
    assert browser.get("/api/status").json()["credential_saved"] is False
    assert browser.get("/api/status", headers={"X-Local-Token": "wrong"}).status_code == 403
    assert browser.post("/api/login", headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.get("/api/bootstrap", headers={"Host": "other.example"}).status_code == 400
    assert browser.get("/api/groups/123/download/cookie").status_code == 404
    assert browser.get("/api/groups/invalid/messages").status_code == 400
    response = browser.post("/api/capture", json={"chat_url": "https://other.example/chat/123"})
    assert response.status_code == 400
    assert "请输入小红书" in response.json()["detail"]
    assert browser.post("/api/monitor", json={"enabled": True, "group_id": "123", "interval_minutes": 1}).status_code == 422


def test_messages_search_export_and_no_cookie_exposure(client):
    browser, service = client
    write_export({"group_id": "123", "messages": [message(1, "第一条"), message(2, "讨论工作计划")],
                  "pages_fetched": 1, "completed": True, "next_last_id": "1"}, service.exports)
    service.cookie.write_text("a1=PRIVATE_VALUE; web_session=PRIVATE_SECRET", encoding="utf-8")
    status = browser.get("/api/status")
    assert status.json()["total_messages"] == 2
    assert "PRIVATE_" not in status.text
    result = browser.get("/api/groups/123/messages?search=工作").json()
    assert result["total"] == 1
    assert result["items"][0]["store_id"] == "2"
    assert browser.get("/api/groups/123/download/json").json()["message_count"] == 2
    assert browser.get("/api/groups/123/download/csv").status_code == 200


def test_monitor_pauses_on_error_and_stays_off_after_restart(client):
    browser, service = client
    write_export({"group_id": "123", "messages": [message(1)], "pages_fetched": 1,
                  "completed": True, "next_last_id": "1"}, service.exports)
    service.cookie.write_text("a1=example; web_session=example", encoding="utf-8")
    response = browser.post("/api/monitor", json={"enabled": True, "group_id": "123", "interval_minutes": 5})
    assert response.status_code == 200
    service.finished({"kind": "capture", "group_id": "123", "status": "failed", "message": "登录失效"})
    assert service.monitor["enabled"] is False
    assert service.monitor["last_error"] == "登录失效"
    assert "enabled" not in json.loads(service.config_file.read_text(encoding="utf-8"))["monitor"]


def test_background_job_can_be_cancelled_without_returning_raw_secrets(tmp_path):
    runner = JobRunner(tmp_path)
    job = runner.start("test", "测试任务", [[sys.executable, "-u", "-c",
                        "import time; print('error=web_session=PRIVATE_SECRET', flush=True); time.sleep(30)"]])
    deadline = time.monotonic() + 5
    while not runner.process and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    runner.cancel(job["id"])
    while runner.busy and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not runner.busy
    assert runner.list()[0]["status"] == "cancelled"
    assert "PRIVATE_SECRET" not in json.dumps(runner.list())


def test_empty_success_response_is_not_mistaken_for_end_of_history():
    class ChangedAPI:
        def get_group_history_page(self, **kwargs):
            return True, "", {"data": {"renamed_messages": []}}
    with pytest.raises(ValueError, match="out_message_list"):
        fetch_group_history(ChangedAPI(), "123", "example", max_pages=1, delay=0)


def test_csv_formula_cells_are_escaped_but_json_preserves_original(tmp_path):
    item = message(1)
    item["sender_id"] = '=HYPERLINK("https://example.invalid")'
    write_export({"group_id": "123", "messages": [item], "pages_fetched": 1,
                  "completed": True, "next_last_id": "1"}, tmp_path)
    import csv
    with (tmp_path / "123/messages.csv").open(encoding="utf-8-sig", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["sender_id"].startswith("'=")
    source = json.loads((tmp_path / "123/messages.json").read_text(encoding="utf-8"))
    assert source["messages"][0]["sender_id"] == item["sender_id"]


def test_due_monitor_starts_incremental_job(tmp_path, monkeypatch):
    service = LocalService(tmp_path)
    calls = []
    monkeypatch.setattr(service, "capture", lambda *args: calls.append(args))
    service.monitor.update(enabled=True, group_id="123", interval_minutes=5, max_pages=100, next_run=0)
    class OneTick:
        count = 0
        def wait(self, seconds):
            self.count += 1
            return self.count > 1
        def set(self):
            pass
    original = service.stop_event
    service.stop_event = OneTick()
    try:
        service._schedule_loop()
    finally:
        service.stop_event = original
    assert calls == [("123", "", "update", 100)]
