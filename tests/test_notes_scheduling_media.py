import time

import pytest
from fastapi.testclient import TestClient

from webapp.app import create_app
from webapp.service import LocalService

NOTE = "1234567890abcdef12345678"


def test_due_note_monitor_uses_shared_runner_and_pauses_on_failure(tmp_path, monkeypatch):
    service = LocalService(tmp_path)
    service.notes.save_note({"id": NOTE})
    service.notes.set_monitor(NOTE, True, 5, 20, True, 50)
    with service.notes.connect() as db:
        db.execute("UPDATE note_monitors SET next_run=0")
    calls = []
    def start(request):
        calls.append(request)
        service.runner.active_id = "scheduled"
        return {"id": "scheduled"}
    monkeypatch.setattr(service, "start_note_job", start)
    service.note_schedule.tick()
    service.note_schedule.tick()
    assert len(calls) == 1
    assert calls[0]["include_replies"] is True
    service.runner.active_id = None
    service.note_schedule.finished({"id": "scheduled", "status": "failed", "message": "平台限制"})
    item = service.notes.monitors()[0]
    assert item["enabled"] is False
    assert item["last_error"] == "平台限制"


def test_old_job_cannot_override_new_monitor_settings(tmp_path):
    service = LocalService(tmp_path)
    service.notes.save_note({"id": NOTE})
    service.notes.set_monitor(NOTE, True, 5, 20, False, 50)
    old = service.notes.monitors()[0]
    service.notes.set_monitor(NOTE, False, 30, 100, True, 100)
    service.notes.finish_monitor(NOTE, old["version"], "succeeded", "完成")
    saved = service.notes.monitors()[0]
    assert saved["enabled"] is False
    assert saved["next_run"] is None
    assert saved["interval_minutes"] == 30


def test_monitor_restart_pauses_without_discarding_configuration(tmp_path):
    service = LocalService(tmp_path)
    service.notes.save_note({"id": NOTE})
    service.notes.set_monitor(NOTE, True, 5, 20, True, 50)
    restored = LocalService(tmp_path)
    saved = restored.notes.monitors()[0]
    assert saved["enabled"] is False
    assert saved["max_replies"] == 50


def test_media_and_monitors_require_local_access_and_safe_file_paths(tmp_path):
    app = create_app(tmp_path)
    service = app.state.service
    service.notes.save_note({"id": NOTE})
    folder = tmp_path / "notes/media/xhs/media" / NOTE
    folder.mkdir(parents=True)
    (folder / "001.jpg").write_bytes(b"fixture-image")
    service.notes.save_media(NOTE, "001.jpg", "image", 13)
    with TestClient(app, base_url="http://127.0.0.1") as browser:
        url = f"/api/notes/{NOTE}/media/001.jpg"
        assert browser.get(url).status_code == 403
        browser.headers["X-Local-Token"] = browser.get("/api/bootstrap").json()["token"]
        assert browser.get(url).content == b"fixture-image"
        assert browser.get(f"/api/notes/{NOTE}/media/missing.jpg").status_code == 404
        assert browser.get(f"/api/notes/{NOTE}/media/secret.txt").status_code == 400
        assert browser.post("/api/note-monitors", json={"note_id": NOTE, "enabled": True, "interval_minutes": 1}).status_code == 422
        assert browser.post("/api/note-monitors", json={"note_id": NOTE, "enabled": True}).status_code == 400
        assert browser.post("/api/note-monitors", json={"note_id": NOTE, "enabled": False}).status_code == 200
        assert browser.get("/api/note-monitors").json()["items"][0]["enabled"] is False


def test_more_than_ten_enabled_monitors_are_rejected(tmp_path):
    service = LocalService(tmp_path)
    for index in range(11):
        note_id = format(index, "024x")
        service.notes.save_note({"id": note_id})
        if index < 10:
            service.notes.set_monitor(note_id, True, 5, 20, False, 20)
        else:
            with pytest.raises(ValueError):
                service.notes.set_monitor(note_id, True, 5, 20, False, 20)
