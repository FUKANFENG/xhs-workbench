import asyncio

import pytest

from integration.publish_worker import PublishFailure, matched_note, note_rows, submit_once
from webapp.publish_store import PublishStore


def prepared(tmp_path):
    store = PublishStore(tmp_path)
    draft = store.create({"kind": "image", "title": "测试发布", "body": "", "tags": []})
    store.mark(draft["id"], "preparing")
    return store, draft


class Page:
    url = "https://creator.xiaohongshu.com/publish/publish"

    def __init__(self, store, draft, click_error=False):
        self.store, self.draft = store, draft
        self.clicks, self.click_error = 0, click_error

    def get_by_role(self, *args, **kwargs):
        return self

    async def click(self, **kwargs):
        assert self.store.get(self.draft["id"])["state"] == "submitting"
        self.clicks += 1
        if self.click_error:
            raise TimeoutError("A response containing PRIVATE_TOKEN must not escape")

    async def wait_for_url(self, *args, **kwargs):
        raise TimeoutError()


def test_missing_success_redirect_still_checks_records_without_reposting(tmp_path):
    store, draft = prepared(tmp_path)
    page = Page(store, draft)
    async def lookup():
        return [{"id": "a" * 24, "title": draft["title"]}]
    asyncio.run(submit_once(page, store, draft, [], lookup))
    assert page.clicks == 1
    assert store.get(draft["id"])["state"] == "published"
    assert store.get(draft["id"])["note_id"] == "a" * 24


@pytest.mark.parametrize("failure", ["click", "lookup", "missing"])
def test_uncertain_submission_is_persistent_and_never_clicked_again(tmp_path, monkeypatch, failure):
    store, draft = prepared(tmp_path)
    page = Page(store, draft, click_error=failure == "click")
    async def no_delay(*args):
        pass
    monkeypatch.setattr(asyncio, "sleep", no_delay)
    async def lookup():
        if failure == "lookup":
            raise TimeoutError("PRIVATE_RESPONSE")
        return []
    with pytest.raises(PublishFailure, match="^publish_uncertain$"):
        asyncio.run(submit_once(page, store, draft, [], lookup))
    assert page.clicks == 1
    reopened = PublishStore(tmp_path)
    reopened.recover()
    assert reopened.get(draft["id"])["state"] == "uncertain"
    with pytest.raises(ValueError):
        reopened.claim(draft["id"], reopened.get(draft["id"])["version"])
    assert "PRIVATE" not in str(reopened.list())
    with pytest.raises(ValueError):
        asyncio.run(submit_once(page, reopened, draft, [], lookup))
    assert page.clicks == 1


def test_verification_excludes_existing_and_ambiguous_notes():
    title = "相同标题"
    old = {"id": "a" * 24, "title": title}
    one = {"id": "b" * 24, "title": title}
    two = {"id": "c" * 24, "title": title}
    verification = {"baseline": [old["id"]]}
    assert matched_note([old], title, verification) is None
    assert matched_note([old, one, two], title, verification) is None
    assert matched_note([old, one], title, verification) == one
    assert matched_note([old, one, two], title, {**verification, "expected_id": two["id"]}) == two
    assert note_rows({"data": {"notes": [old, {"note_id": one["id"], "display_title": title}]}}) == [old, one]
