import json
from webapp.service import LocalService


def test_only_account_label_is_exposed_not_browser_state(tmp_path):
    service = LocalService(tmp_path)
    publisher = service.publishing
    state = {"cookies": [{"name": "customer-sso-sid", "value": "PRIVATE_SESSION"}], "origins": [{"origin": "https://creator.xiaohongshu.com", "localStorage": [{"name": "USER_INFO", "value": json.dumps({"nickname": "我的测试账号", "access_token": "PRIVATE_TOKEN"})}]}]}
    publisher.session.write_text(json.dumps(state), encoding="utf-8")
    status = publisher.status()
    assert status["account_label"] == "我的测试账号"
    assert status["credential_saved"] is True
    assert status["auth_state"] == "unknown"
    assert "PRIVATE" not in json.dumps(status)
    service.close()


def test_non_json_storage_entry_does_not_hide_account_identity(tmp_path):
    service = LocalService(tmp_path)
    publisher = service.publishing
    state = {"cookies": [{"name": "x-user-id-creator.xiaohongshu.com", "value": "a" * 24}], "origins": [{"origin": "https://creator.xiaohongshu.com", "localStorage": [{"name": "snsWebPublishCurrentUser", "value": "not-json"}]}]}
    publisher.session.write_text(json.dumps(state), encoding="utf-8")
    assert publisher.status()["account_label"] == "账号 ID 尾号 aaaaaa"
    service.close()
