import pytest

from webapp.publish_store import PublishStore


def test_confirmed_snapshot_is_frozen_and_cannot_be_submitted_twice(tmp_path):
    store = PublishStore(tmp_path)
    draft = store.create({"kind": "image", "title": "测试", "body": "正文", "tags": []})
    store.add_asset(draft["id"], {"id": "a" * 32, "name": "照片.png", "suffix": ".png", "size": 100})
    with pytest.raises(ValueError, match="变化"):
        store.claim(draft["id"], draft["version"])
    saved = store.get(draft["id"])
    store.claim(saved["id"], saved["version"])
    with pytest.raises(ValueError):
        store.update(saved["id"], {"kind": "image", "title": "偷偷修改", "body": "", "tags": []})
    with pytest.raises(ValueError):
        store.add_asset(saved["id"], {"id": "b" * 32, "name": "另一个.png", "suffix": ".png", "size": 10})
    store.mark(saved["id"], "submitting", _verification={"baseline": ["PRIVATE_BASELINE"]})
    store.interrupted(saved["id"])
    assert store.get(saved["id"])["state"] == "uncertain"
    with pytest.raises(ValueError):
        store.claim(saved["id"], store.get(saved["id"])["version"])
    assert "PRIVATE_BASELINE" not in str(store.list())
    assert "PRIVATE_BASELINE" not in str(store.get(saved["id"]))


def test_recovery_distinguishes_before_and_after_submit(tmp_path):
    store = PublishStore(tmp_path)
    before = store.create({"kind": "video", "title": "准备中", "body": "", "tags": []})
    after = store.create({"kind": "video", "title": "已提交", "body": "", "tags": []})
    done = store.create({"kind": "video", "title": "成功", "body": "", "tags": []})
    store.mark(before["id"], "preparing")
    store.mark(after["id"], "submitting")
    store.mark(done["id"], "published", note_id="f" * 24)
    reopened = PublishStore(tmp_path)
    reopened.recover()
    assert reopened.get(before["id"])["state"] == "failed"
    assert reopened.get(after["id"])["state"] == "uncertain"
    reopened.interrupted(done["id"])
    assert reopened.get(done["id"])["state"] == "published"


def test_empty_draft_and_media_limits_are_rejected(tmp_path):
    store = PublishStore(tmp_path)
    draft = store.create({"kind": "video", "title": "测试", "body": "", "tags": []})
    with pytest.raises(ValueError, match="素材"):
        store.claim(draft["id"], draft["version"])
    store.add_asset(draft["id"], {"id": "a" * 32, "name": "视频.mp4", "suffix": ".mp4", "size": 100})
    with pytest.raises(ValueError):
        store.add_asset(draft["id"], {"id": "b" * 32, "name": "第二个.mp4", "suffix": ".mp4", "size": 100})
    with pytest.raises(ValueError):
        store.update(draft["id"], {"kind": "image", "title": "测试", "body": "", "tags": []})
    with pytest.raises(ValueError):
        store.asset_path(draft["id"], "../../auth")
