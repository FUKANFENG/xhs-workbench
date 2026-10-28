import argparse
import json
import sys
import types
from pathlib import Path

import pytest

from webapp.service import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import xhs_group_tool


@pytest.fixture
def login_args(tmp_path, monkeypatch):
    monkeypatch.setattr(xhs_group_tool, "spider_root", lambda value: value)
    return argparse.Namespace(spider_root=tmp_path, cookie_file=tmp_path / "cookie.txt",
                              qr_file=tmp_path / "qr.png", overwrite=True, timeout=10)


def install_login(monkeypatch, *, failure=None, guest=False):
    class Login:
        closed = False

        def __init__(self):
            response = types.SimpleNamespace(status_code=200, json=lambda: {"success": not failure})
            self.http = types.SimpleNamespace(request=lambda *a, **kw: response)

        def generate_init_cookies(self):
            return {"a1": "PRIVATE_DEVICE", "web_session": "PRIVATE_VISITOR"}

        def generate_qrcode(self, cookies):
            return True, "", {"cookies": cookies, "qr_id": "PRIVATE_QR",
                               "code": "PRIVATE_CODE", "qr_url": "https://example.invalid/qr"}

        def check_qrcode_status(self, *args):
            if not hasattr(self, "preflight_done"):
                self.preflight_done = True
                return False, "请扫描二维码", args[-1]
            if failure:
                self.http.request("GET", "https://example.invalid/api/sns/web/v1/login/qrcode/status")
                raise RuntimeError(failure)
            return True, "验证成功", {"a1": "PRIVATE_DEVICE", "web_session": "PRIVATE_SESSION"}

        def ensure_webprofile(self, cookies):
            pass

        def get_user_info(self, cookies):
            return True, {"guest": guest}, cookies

        def cookies_to_str(self, cookies):
            return "; ".join(f"{key}={value}" for key, value in cookies.items())

        def close(self):
            type(self).closed = True

    module = types.ModuleType("apis.xhs_pc_login_apis")
    module.XHSLoginApi = Login
    monkeypatch.setitem(sys.modules, "apis", types.ModuleType("apis"))
    monkeypatch.setitem(sys.modules, "apis.xhs_pc_login_apis", module)
    return Login


def test_confirmed_login_saves_credentials_and_reports_stages(login_args, monkeypatch, capsys):
    login = install_login(monkeypatch)
    assert xhs_group_tool.cmd_login_qr(login_args) == 0
    assert "web_session=PRIVATE_SESSION" in login_args.cookie_file.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "login_progress=verifying_session" in output
    assert "login_progress=saving_credentials" in output
    assert "status=login_succeeded" in output
    assert "PRIVATE_" not in output
    assert not login_args.qr_file.exists()
    assert login.closed


def test_exchange_failure_keeps_saved_credentials_and_reports_specific_cause(login_args, monkeypatch, capsys):
    install_login(monkeypatch, failure="二维码最终登录状态无效 PRIVATE_SERVER_VALUE")
    login_args.cookie_file.write_text("existing credentials", encoding="utf-8")
    with pytest.raises(RuntimeError):
        xhs_group_tool.cmd_login_qr(login_args)
    assert login_args.cookie_file.read_text(encoding="utf-8") == "existing credentials"
    capture = capsys.readouterr()
    output = capture.out + capture.err
    assert '"stage": "exchanging_session"' in output
    assert "PRIVATE_" not in output
    assert not login_args.qr_file.exists()


def test_guest_session_never_replaces_credentials(login_args, monkeypatch, capsys):
    install_login(monkeypatch, guest=True)
    login_args.cookie_file.write_text("existing credentials", encoding="utf-8")
    with pytest.raises(RuntimeError):
        xhs_group_tool.cmd_login_qr(login_args)
    assert login_args.cookie_file.read_text(encoding="utf-8") == "existing credentials"
    assert '"stage": "verifying_session"' in capsys.readouterr().err


def test_http_diagnostics_exclude_request_and_response_secrets(capsys):
    from xhs_ext.login_diagnostics import LoginDiagnostics

    class Response:
        status_code = 471

        def json(self):
            return {"code": -1, "msg": "PRIVATE_MESSAGE", "data": {
                "code_status": 2, "login_info": {"session": "PRIVATE_SESSION"}}}

    class Client:
        def request(self, *args, **kwargs):
            return Response()

    login = types.SimpleNamespace(http=Client())
    diagnostic = LoginDiagnostics(login)
    login.http.request("GET", "https://example.invalid/api/sns/web/v1/login/qrcode/status?code=PRIVATE_CODE",
                       headers={"Cookie": "PRIVATE_COOKIE"})
    diagnostic.failed(RuntimeError("PRIVATE_ERROR"))
    capture = capsys.readouterr()
    output = capture.out + capture.err
    payload = json.loads(capture.err.strip().split("=", 1)[1])
    assert payload["stage"] == "exchanging_session"
    assert payload["http_status"] == 471
    assert payload["api_code"] == -1
    assert payload["code_status"] == 2
    assert "PRIVATE_" not in output


def test_failed_job_explains_exchange_and_preserves_safe_diagnostics(tmp_path):
    import time
    from webapp.service import LocalService

    service = LocalService(tmp_path)
    runner = service.runner
    program = ('print(\'login_diagnostic={"stage": "exchanging_session"}\'); '
               'raise SystemExit(1)')
    runner.start("login", "扫码登录", [[sys.executable, "-u", "-c", program]])
    deadline = time.monotonic() + 5
    while (runner.busy or not service.config_file.exists()) and time.monotonic() < deadline:
        time.sleep(0.01)
    job = runner.list()[0]
    assert job["status"] == "failed"
    assert "登录凭据获取失败" in job["message"]
    diagnostic = json.loads(service.config_file.read_text(encoding="utf-8"))["last_login_attempt"]["diagnostic"]
    assert diagnostic["stage"] == "exchanging_session"
