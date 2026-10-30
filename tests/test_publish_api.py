import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.app import create_app
from webapp.runtime import BusyError

PNG = b"\x89PNG\r\n\x1a\n" + b"test-data"


@pytest.fixture
def browser(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["X-Local-Token"] = client.get("/api/bootstrap").json()["token"]
        yield client, app.state.service


def make_draft(client):
    return client.post("/api/publish/drafts", json={"kind": "image", "title": "测试笔记", "body": "正文", "tags": ["学习"]}).json()


def ready(service):
    publisher = service.publishing
    publisher.python = Path(sys.executable)
    (publisher.runtime / "uploader/xiaohongshu_uploader").mkdir(parents=True)
    (publisher.runtime / "uploader/xiaohongshu_uploader/main.py").touch()
    publisher.session.write_text("PRIVATE_PUBLISH_STATE", encoding="utf-8")


def test_media_upload_is_bounded_and_cannot_expose_files(browser):
    client, service = browser
    draft = make_draft(client)
    endpoint = f'/api/publish/drafts/{draft["id"]}/assets'
    assert client.post(endpoint, params={"name": "../auth.png"}, content=PNG).status_code == 400
    assert client.post(endpoint, params={"name": "script.svg"}, content=PNG).status_code == 400
    assert client.post(endpoint, params={"name": "假照片.png"}, content=b"<script>bad</script>").status_code == 400
    assert client.post(endpoint, params={"name": "照片.png"}, content=PNG, headers={"Content-Length": str(21 * 1024 * 1024)}).status_code == 413
    result = client.post(endpoint, params={"name": "照片.PNG"}, content=PNG)
    assert result.status_code == 200
    asset = result.json()["assets"][0]
    path = f'{endpoint}/{asset["id"]}'
    assert client.get(path).content == PNG
    assert client.get(path, headers={"X-Local-Token": "bad"}).status_code == 403
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404
    assert not list(service.publishing.store.root.rglob("*.part"))
    assert client.get("/api/publish/qr").status_code == 404
    assert client.post("/api/publish/login", headers={"Origin": "https://other.example"}).status_code == 403


def test_submit_requires_confirmation_and_exact_saved_revision(browser, monkeypatch):
    client, service = browser
    ready(service)
    draft = make_draft(client)
    endpoint = f'/api/publish/drafts/{draft["id"]}'
    uploaded = client.post(endpoint + "/assets", params={"name": "a.png"}, content=PNG).json()
    calls = []
    monkeypatch.setattr(service.runner, "start", lambda *a, **kw: calls.append((a, kw)) or {"id": "job", "status": "running"})
    assert client.post(endpoint + "/submit", json={"version": uploaded["version"]}).status_code == 422
    assert client.post(endpoint + "/submit", json={"confirmed": False, "version": uploaded["version"]}).status_code == 422
    assert client.post(endpoint + "/submit", json={"confirmed": True, "version": draft["version"]}).status_code == 400
    assert not calls
    assert client.post(endpoint + "/submit", json={"confirmed": True, "version": uploaded["version"]}).status_code == 200
    assert len(calls) == 1
    assert "PRIVATE" not in str(calls)
    assert "publish_worker.py" in str(calls)
    assert client.post(endpoint + "/submit", json={"confirmed": True, "version": uploaded["version"]}).status_code == 400
    assert client.post(endpoint + "/assets", params={"name": "new.png"}, content=PNG).status_code == 400
    service.publishing.store.mark(draft["id"], "submitting")
    service.finished({"kind": "publish_publish", "group_id": draft["id"], "status": "cancelled"})
    assert client.get(endpoint).json()["state"] == "uncertain"
    assert "PRIVATE" not in client.get("/api/status").text
    assert "PRIVATE" not in client.get("/api/publish/status").text


def test_busy_start_does_not_freeze_draft_and_partial_upload_is_removed(browser, monkeypatch):
    client, service = browser
    ready(service)
    draft = make_draft(client)
    endpoint = f'/api/publish/drafts/{draft["id"]}'
    uploaded = client.post(endpoint + "/assets", params={"name": "a.png"}, content=PNG).json()
    def busy(*args, **kwargs):
        raise BusyError("已有任务")
    monkeypatch.setattr(service.runner, "start", busy)
    assert client.post(endpoint + "/submit", json={"confirmed": True, "version": uploaded["version"]}).status_code == 409
    assert client.get(endpoint).json()["state"] == "failed"
    assert client.post("/api/publish/drafts", json={"kind": "image", "title": "  ", "tags": []}).status_code == 422
    assert client.post("/api/publish/drafts", json={"kind": "image", "title": "测试", "tags": ["bad tag"]}).status_code == 422
