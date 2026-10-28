import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.app import create_app

NOTE = "1234567890abcdef12345678"


@pytest.fixture
def browser(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["X-Local-Token"] = client.get("/api/bootstrap").json()["token"]
        yield client, app.state.service


def test_note_and_comment_apis_are_paged_and_export_safe(browser):
    client, service = browser
    service.notes.save_note({"id": NOTE, "title": "=危险公式", "description": "学习资料"}, token="PRIVATE_TOKEN", detailed=True)
    service.notes.save_comments(NOTE, [{"id": "a", "author": "+名称", "content": "=1+1"}], complete=False)
    listing = client.get("/api/notes?search=学习")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert "PRIVATE_TOKEN" not in listing.text
    assert client.get(f"/api/notes/{NOTE}").json()["saved_comments"] == 1
    assert client.get(f"/api/note-comments?note_id={NOTE}").json()["total"] == 1
    export = client.get(f"/api/notes/export?dataset=comments&kind=csv&note_id={NOTE}")
    assert export.status_code == 200
    assert "'=1+1" in export.text
    exported = client.get("/api/notes/export?dataset=notes&kind=json")
    assert exported.json()["items"][0]["title"] == "=危险公式"
    assert "PRIVATE_TOKEN" not in exported.text
    assert client.get("/api/notes/export?dataset=notes&kind=json&search=不存在").json()["items"] == []
    assert client.get("/api/notes/export?dataset=comments&kind=json&search=不存在").json()["items"] == []
    assert client.get("/api/notes/export?dataset=cookie&kind=json").status_code == 422
    assert client.get("/api/notes?size=100000").status_code == 422
    assert client.get("/api/notes", headers={"X-Local-Token": "wrong"}).status_code == 403


def test_note_jobs_validate_inputs_and_share_runner(browser, monkeypatch):
    client, service = browser
    assert client.post("/api/notes/jobs", json={"mode": "search", "keyword": "Python"}).status_code == 400
    service.cookie.write_text("a1=EXAMPLE; web_session=EXAMPLE", encoding="utf-8")
    service.media_python = Path(sys.executable)
    (service.media_root / "media_platform/xhs").mkdir(parents=True)
    (service.media_root / "media_platform/xhs/client.py").touch()
    calls = []
    def start(*args, **kwargs):
        calls.append((args, kwargs))
        return {"id": "test-job", "status": "running"}
    monkeypatch.setattr(service.runner, "start", start)
    result = client.post("/api/notes/jobs", json={"mode": "search", "keyword": " Python ", "max_notes": 5})
    assert result.status_code == 200
    command = calls[0][0][2][0]
    request_file = Path(command[command.index("--request") + 1])
    assert json.loads(request_file.read_text())["keyword"] == "Python"
    assert "EXAMPLE" not in " ".join(command)
    assert client.post("/api/notes/jobs", json={"mode": "detail", "url": "http://127.0.0.1:9000"}).status_code == 400
    assert client.post("/api/notes/jobs", json={"mode": "search", "keyword": "  "}).status_code == 400
    assert client.post("/api/notes/jobs", json={"mode": "search", "keyword": "x", "max_notes": 500}).status_code == 422
    assert client.post("/api/notes/jobs", json={"mode": "comments", "note_id": NOTE, "max_comments": 0}).status_code == 400
    service.runner.active_id = "other-task"
    assert client.post("/api/notes/jobs", json={"mode": "search", "keyword": "Python"}).status_code == 409
    service.runner.active_id = None
    service.finished({"id": "test-job", "kind": "notes_search", "status": "succeeded"})
    assert not request_file.exists()
