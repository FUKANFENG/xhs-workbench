import asyncio
import sqlite3

import pytest

from webapp.notes import NotesStore
from integration.notes_extra import fetch_replies, parse_creator_url, valid_media_url

NOTE = "1234567890abcdef12345678"


def test_replies_keep_parent_and_can_be_filtered_and_exported(tmp_path):
    store = NotesStore(tmp_path)
    store.save_note({"id": NOTE, "creator_id": NOTE})
    store.save_comments(NOTE, [{"id": "root", "content": "原评论"},
                              {"id": "reply", "content": "回复", "parent_id": "root"}],
                        complete=True, replies_complete=False)
    assert store.comments(level="replies")["items"][0]["parent_content"] == "原评论"
    assert store.comments(level="roots")["total"] == 1
    assert list(store.export_rows("comments", level="replies"))[0]["parent_id"] == "root"
    store.save_comments(NOTE, [{"id": "root", "content": "更新"}], complete=True)
    assert store.comments()["total"] == 2
    assert store.get(NOTE)["replies_complete"] is False


def test_existing_database_is_migrated_without_losing_data(tmp_path):
    with sqlite3.connect(tmp_path / "notes.sqlite3") as db:
        db.executescript("""CREATE TABLE notes(id TEXT PRIMARY KEY,title TEXT,description TEXT,author TEXT,kind TEXT,
            published_at REAL,likes TEXT,collects TEXT,comment_count TEXT,source_keyword TEXT,detailed INTEGER,
            updated_at REAL,comments_checked_at REAL,comments_complete INTEGER,last_scan_count INTEGER,
            xsec_token TEXT,xsec_source TEXT);
            CREATE TABLE comments(id TEXT,note_id TEXT,author TEXT,content TEXT,published_at REAL,likes TEXT,reply_count INTEGER,
            PRIMARY KEY(note_id,id));""")
        db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (NOTE,"保留","原文","作者","normal",0,"1","0","1","",1,1,1,1,1,"PRIVATE","pc_search"))
        db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?,?)", ("old",NOTE,"昵称","评论",1,"0",0))
    store = NotesStore(tmp_path)
    assert store.get(NOTE)["title"] == "保留"
    assert store.access(NOTE)["token"] == "PRIVATE"
    assert store.comments()["items"][0]["parent_id"] == ""
    assert store.get(NOTE)["replies_complete"] is None


def test_reply_limit_and_embedded_replies_are_deduplicated():
    class Client:
        async def get_note_sub_comments(self, **kwargs):
            return {"comments": [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}], "has_more": False}
    root = {"id": "root", "reply_count": 3, "_sub_comments": [{"id": "r1"}], "_sub_has_more": True}
    rows, complete = asyncio.run(fetch_replies(Client(), NOTE, "token", [root], 2, delay=0))
    assert [r["id"] for r in rows] == ["r1", "r2"]
    assert all(r["parent_id"] == "root" for r in rows)
    assert complete is False


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.mp4", "https://xhscdn.com.evil.test/a.jpg", "https://u@a.xhscdn.com/x"])
def test_media_downloads_reject_non_platform_hosts(url):
    with pytest.raises(ValueError):
        valid_media_url(url)


def test_creator_link_and_media_domain_are_validated():
    assert parse_creator_url("https://www.xiaohongshu.com/user/profile/" + NOTE) == (NOTE, "", "pc_feed")
    assert valid_media_url("https://sns-video-bd.xhscdn.com/a.mp4")
    with pytest.raises(ValueError):
        parse_creator_url("https://evil.test/user/profile/" + NOTE)


def test_monitor_settings_persist_without_auto_enabling_after_restart(tmp_path):
    store = NotesStore(tmp_path)
    store.save_note({"id": NOTE})
    store.set_monitor(NOTE, True, 5, 20, True, 30)
    assert store.monitors()[0]["enabled"] is True
    store.pause_monitors_for_restart()
    monitor = NotesStore(tmp_path).monitors()[0]
    assert monitor["enabled"] is False
    assert monitor["include_replies"] is True
    assert monitor["interval_minutes"] == 5


def test_media_size_limit_applies_without_content_length():
    import httpx
    from integration.notes_extra import limit_media_response, NotesFailure
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"1234"
            yield b"5678"
    async def consume():
        response = httpx.Response(200, stream=Stream())
        await limit_media_response(response, 6)
        await response.aread()
    with pytest.raises(NotesFailure, match="notes_media_size"):
        asyncio.run(consume())


def test_media_compression_cannot_bypass_download_limit():
    import httpx
    from integration.notes_extra import limit_media_response, NotesFailure
    with pytest.raises(NotesFailure, match="notes_media_failed"):
        asyncio.run(limit_media_response(httpx.Response(200, headers={"content-encoding": "gzip"}), 100))


def test_reply_pagination_error_is_not_reported_as_complete():
    from integration.notes_extra import NotesFailure
    class Client:
        async def get_note_sub_comments(self, **kwargs):
            return {"has_more": False}
    with pytest.raises(NotesFailure, match="notes_response"):
        asyncio.run(fetch_replies(Client(), NOTE, "token", [{"id":"root","reply_count":1}], 10, delay=0))
