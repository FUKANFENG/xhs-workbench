import json

import pytest
from fastapi.testclient import TestClient

from webapp.notes import NotesStore


NOTE = "1234567890abcdef12345678"


def test_search_refresh_preserves_full_note_and_exports_never_include_access_token(tmp_path):
    store = NotesStore(tmp_path)
    store.save_note({"id": NOTE, "title": "完整笔记", "description": "完整正文"},
                    token="PRIVATE_ACCESS", detailed=True)
    store.save_note({"id": NOTE, "title": "搜索摘要", "description": "短摘要"}, keyword="Python")
    note = store.get(NOTE)
    assert note["title"] == "完整笔记"
    assert note["description"] == "完整正文"
    assert note["detailed"] is True
    assert store.list_notes()["total"] == 1
    assert store.access(NOTE)["token"] == "PRIVATE_ACCESS"
    assert "PRIVATE_ACCESS" not in json.dumps(list(store.export_rows("notes")))
    assert "xsec_token" not in json.dumps(note)


def test_comment_refresh_merges_by_id_and_does_not_erase_older_messages(tmp_path):
    store = NotesStore(tmp_path)
    store.save_note({"id": NOTE, "title": "笔记"})
    store.save_comments(NOTE, [{"id": "a", "content": "旧评论"}, {"id": "b", "content": "初始文字"}], complete=True)
    store.save_comments(NOTE, [{"id": "b", "content": "更新文字"}, {"id": "c", "content": "新评论"}], complete=False)
    rows = store.comments(note_id=NOTE)
    assert rows["total"] == 3
    assert {v["content"] for v in rows["items"]} == {"旧评论", "更新文字", "新评论"}
    assert store.get(NOTE)["comments_complete"] is False
    assert store.comments(search="新评论")["total"] == 1
    assert NotesStore(tmp_path).comments(note_id=NOTE)["total"] == 3


def test_failed_comment_transaction_preserves_previous_scan(tmp_path):
    store = NotesStore(tmp_path)
    store.save_note({"id": NOTE})
    store.save_comments(NOTE, [{"id": "a", "content": "保留"}], complete=True)
    with pytest.raises(ValueError):
        store.save_comments(NOTE, [{"id": "b", "content": "不应保存"}, {"content": "缺少编号"}], complete=False)
    assert store.comments()["total"] == 1
    assert store.get(NOTE)["comments_complete"] is True


def test_invalid_ids_are_rejected(tmp_path):
    store = NotesStore(tmp_path)
    with pytest.raises(ValueError):
        store.get("../../auth")
    with pytest.raises(FileNotFoundError):
        store.get(NOTE)
