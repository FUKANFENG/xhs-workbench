import pytest

from integration.notes_worker import NotesFailure, fetch_comments, parse_note_url, validate_url

NOTE = "1234567890abcdef12345678"


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "https://xiaohongshu.com.evil.test/explore/" + NOTE,
                                  "https://user@www.xiaohongshu.com/explore/" + NOTE,
                                  "file:///etc/passwd", "https://www.xiaohongshu.com:8777/explore/" + NOTE])
def test_external_and_local_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_share_text_parsed_without_losing_security_token():
    url = validate_url("复制打开小红书 https://www.xiaohongshu.com/explore/" + NOTE + "?xsec_token=EXAMPLE&xsec_source=pc_search 分享")
    assert parse_note_url(url) == (NOTE, "EXAMPLE", "pc_search")


def test_comment_limit_is_exact_and_duplicate_pages_do_not_double_count():
    import asyncio
    class Client:
        async def get_note_comments(self, note_id, token, cursor=""):
            ids = ["1", "2"] if not cursor else ["2", "3", "4"]
            return {"comments": [{"id": v, "content": v} for v in ids], "cursor": "next" if not cursor else "end", "has_more": True}
    rows, complete = asyncio.run(fetch_comments(Client(), NOTE, "secret", 3, delay=0))
    assert [v["id"] for v in rows] == ["1", "2", "3"]
    assert complete is False


def test_missing_comments_is_failure_not_empty_success():
    import asyncio
    class Client:
        async def get_note_comments(self, *args, **kwargs):
            return {"has_more": False}
    with pytest.raises(NotesFailure, match="notes_response"):
        asyncio.run(fetch_comments(Client(), NOTE, "secret", 20, delay=0))


def test_repeated_cursor_does_not_loop_forever():
    import asyncio
    class Client:
        async def get_note_comments(self, *args, **kwargs):
            return {"comments": [], "has_more": True, "cursor": "same"}
    with pytest.raises(NotesFailure, match="notes_pagination"):
        asyncio.run(fetch_comments(Client(), NOTE, "secret", 20, delay=0))
