"""Publishing jobs use their own browser state and the workbench's serial runner."""
import json
import os
import re
from pathlib import Path

from .publish_store import PublishStore
from .runtime import BusyError

REVISION = "3847fff01f5434a8a32829833163cd6723cabc59"


class Publishing:
    def __init__(self, service):
        self.service = service
        self.store = PublishStore(service.root / "publishing")
        self.store.recover()
        self.runtime = Path(os.environ.get("XHS_PUBLISH_ROOT", service.root / "runtime" / "SocialAutoUpload")).resolve()
        self.python = self.runtime / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.auth = service.root / "auth" / "publisher"
        self.auth.mkdir(parents=True, exist_ok=True)
        self.session = self.auth / "storage-state.json"
        self.qr = self.auth / "login-qr.png"

    def status(self):
        checked = {}
        label = ""
        try:
            checked = json.loads((self.auth / "checked.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        try:
            saved = json.loads(self.session.read_text(encoding="utf-8"))
            for origin in saved.get("origins", []):
                if origin.get("origin") != "https://creator.xiaohongshu.com":
                    continue
                for entry in origin.get("localStorage", []):
                    if entry.get("name") in {"USER_INFO", "snsWebPublishCurrentUser"}:
                        try:
                            info = json.loads(entry["value"])
                        except (ValueError, TypeError, KeyError):
                            continue
                        if isinstance(info, dict):
                            nickname = info.get("nickname") or info.get("nickName") or info.get("nick_name")
                            if isinstance(nickname, str):
                                label = "".join(c for c in nickname if ord(c) >= 32)[:60]
            if not label:
                owner = next((c.get("value", "") for c in saved.get("cookies", []) if c.get("name") == "x-user-id-creator.xiaohongshu.com"), "")
                if re.fullmatch(r"[a-fA-F0-9]{24}", owner):
                    label = "账号 ID 尾号 " + owner[-6:]
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return {"environment_ready": self.python.is_file() and (self.runtime / "uploader/xiaohongshu_uploader/main.py").is_file(),
                "credential_saved": self.session.is_file(), "auth_state": checked.get("state", "unknown"),
                "checked_at": checked.get("checked_at"), "account_label": label, "busy": self.service.runner.busy}

    def start(self, action, draft_id="", version=None):
        service = self.service
        with service.lock:
            if not self.status()["environment_ready"]:
                raise ValueError("发布环境未就绪，请运行 Install-Publish.ps1 后重启工作台。")
            if service.runner.busy:
                raise BusyError("已有任务正在执行，请等待完成或先取消。")
            if action != "login" and not self.session.is_file():
                raise ValueError("请先在发布页面扫码登录创作者中心。")
            if action == "publish":
                draft = self.store.get(draft_id)
                for asset in draft["assets"]:
                    path = self.store.asset_path(draft_id, asset["id"])
                    if not path.is_file() or path.stat().st_size != asset["size"]:
                        raise ValueError("素材文件缺失或发生变化，请重新上传。")
                self.store.claim(draft_id, version)
            elif action == "verify":
                if self.store.get(draft_id)["state"] != "uncertain":
                    raise ValueError("只有待核对的提交需要查询结果。")
            elif action not in {"login", "check"}:
                raise ValueError("不支持这个发布操作。")
            if action == "login":
                self.qr.unlink(missing_ok=True)
            project = Path(__file__).resolve().parent.parent
            command = [str(self.python), str(project / "integration/publish_worker.py"),
                       "--runtime", str(self.runtime), "--data", str(service.root), "--action", action]
            if draft_id:
                command += ["--draft", draft_id]
            try:
                return service.runner.start("publish_" + action,
                                            {"login": "登录创作者中心", "check": "检查发布登录", "publish": "发布小红书笔记", "verify": "核对发布结果"}[action],
                                            [command], group_id=draft_id, timeout=360 if action == "login" else 1200)
            except Exception:
                if action == "publish":
                    self.store.interrupted(draft_id)
                raise

    def finished(self, job):
        if job["kind"] == "publish_login":
            try:
                self.qr.unlink(missing_ok=True)
            except OSError:
                pass  # A Windows image response may briefly hold the file open.
        if job["kind"] == "publish_publish":
            self.store.interrupted(job["group_id"])
