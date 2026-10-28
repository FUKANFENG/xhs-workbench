import argparse
import json
import sys
import time
import types
from pathlib import Path

import pytest

from webapp.runtime import JobRunner
from webapp.service import LocalService, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import xhs_group_tool


def test_login_failure_keeps_stage_without_exposing_credentials(tmp_path):
    runner = JobRunner(tmp_path)
    event = {"stage": "exchanging_session", "http_status": 471, "error_type": "RuntimeError"}
    script = f"print('login_diagnostic=' + {json.dumps(event)!r}); print('error=web_session=SECRET'); raise SystemExit(1)"
    runner.start("login", "扫码登录", [[sys.executable, "-c", script]])
    deadline = time.monotonic() + 5
    while runner.busy and time.monotonic() < deadline:
        time.sleep(0.02)
    job = runner.list()[0]
    assert job["status"] == "failed"
    assert job["diagnostic"] == event
    assert "平台要求额外验证" in job["message"]
    assert "SECRET" not in json.dumps(job)


def test_login_attempt_survives_service_restart(tmp_path):
    service = LocalService(tmp_path)
    job = {"id": "attempt", "kind": "login", "status": "failed", "message": "登录确认失败",
           "diagnostic": {"stage": "exchanging_session", "http_status": 471}, "logs": []}
    service.finished(job)
    restored = LocalService(tmp_path)
    attempt = restored.preferences["last_login_attempt"]
    assert attempt["status"] == "failed"
    assert attempt["diagnostic"] == job["diagnostic"]
    assert attempt["message"] == job["message"]


@pytest.mark.parametrize("guest", [False, True])
def test_only_verified_login_replaces_credentials(tmp_path, monkeypatch, capsys, guest):
    class LoginAPI:
        def __init__(self):
            self.polls = 0
            self.http = types.SimpleNamespace(request=lambda *a, **kw: None)
        def generate_init_cookies(self):
            return {"a1": "DEVICE", "web_session": "VISITOR"}
        def generate_qrcode(self, cookies):
            return True, "", {"cookies": cookies, "qr_id": "test", "code": "SECRET", "qr_url": "https://example.invalid"}
        def check_qrcode_status(self, *args):
            self.polls += 1
            return self.polls > 1, "请扫描二维码", {"a1": "DEVICE", "web_session": "AUTHENTICATED"}
        def ensure_webprofile(self, cookies):
            pass
        def get_user_info(self, cookies):
            return True, {"guest": guest}, cookies
        def cookies_to_str(self, cookies):
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
        def close(self):
            pass
    module = types.ModuleType("apis.xhs_pc_login_apis")
    module.XHSLoginApi = LoginAPI
    monkeypatch.setitem(sys.modules, "apis.xhs_pc_login_apis", module)
    monkeypatch.setattr(xhs_group_tool, "spider_root", lambda value: tmp_path)
    target = tmp_path / "cookie.txt"
    target.write_text("OLD_CREDENTIAL", encoding="utf-8")
    args = argparse.Namespace(spider_root=tmp_path, cookie_file=target, qr_file=tmp_path / "qr.png",
                              overwrite=True, timeout=5)
    if guest:
        with pytest.raises(RuntimeError):
            xhs_group_tool.cmd_login_qr(args)
        assert target.read_text(encoding="utf-8") == "OLD_CREDENTIAL"
    else:
        assert xhs_group_tool.cmd_login_qr(args) == 0
        assert "web_session=AUTHENTICATED" in target.read_text(encoding="utf-8")
    assert not args.qr_file.exists()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "AUTHENTICATED" not in output
    assert "SECRET" not in output
    assert "login_progress=verifying_session" in output
