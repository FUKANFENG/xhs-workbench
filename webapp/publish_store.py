"""Persistent publishing snapshots. A submitted draft can never be sent again."""
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

EDITABLE = {"draft", "failed"}
ACTIVE = {"queued", "preparing", "submitting"}
STATES = EDITABLE | ACTIVE | {"uncertain", "published"}
MESSAGES = {"draft": "草稿尚未发布。", "queued": "正在等待发布任务。",
            "preparing": "正在上传素材并填写内容。", "submitting": "已开始提交，正在核对结果。",
            "uncertain": "提交结果尚未确认，请核对作品记录；不会自动重发。",
            "published": "已在创作者中心核对到作品记录；审核和可见状态以平台为准。",
            "failed": "提交前任务已停止，可以修改草稿后重试。"}


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("草稿或素材编号不正确。")
    return value


class PublishStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "publishing.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL, updated REAL NOT NULL)")
        os.chmod(self.database, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row, private=False):
        if row is None:
            raise FileNotFoundError()
        data = json.loads(row["data"])
        if not private:
            data = {k: v for k, v in data.items() if not k.startswith("_")}
        return {**data, "id": row["id"], "state": row["state"], "version": row["version"],
                "updated_at": row["updated"], "message": data.get("message", MESSAGES[row["state"]])}

    def get(self, draft_id, private=False):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM drafts WHERE id=?", (valid_id(draft_id),)).fetchone(), private)

    def list(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM drafts ORDER BY updated DESC LIMIT 100")]

    def create(self, fields):
        draft_id = uuid.uuid4().hex
        data = {**fields, "assets": [], "created_at": time.time()}
        with self.connect() as db:
            db.execute("INSERT INTO drafts VALUES(?,?,'draft',1,?)", (draft_id, json.dumps(data, ensure_ascii=False), time.time()))
        return self.get(draft_id)

    def _change(self, draft_id, change):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM drafts WHERE id=?", (valid_id(draft_id),)).fetchone()
            current = self.decode(row, private=True)
            data = json.loads(row["data"])
            state = change(current, data)
            db.execute("UPDATE drafts SET data=?,state=?,version=version+1,updated=? WHERE id=?",
                       (json.dumps(data, ensure_ascii=False), state, time.time(), draft_id))
        return self.get(draft_id)

    @staticmethod
    def require_editable(current):
        if current["state"] not in EDITABLE:
            raise ValueError("这个草稿已冻结或已经提交，不能修改或再次发布。")

    def update(self, draft_id, fields):
        def change(current, data):
            self.require_editable(current)
            if fields["kind"] != current["kind"] and current["assets"]:
                raise ValueError("请先移除素材再更换发布类型。")
            data.update(fields)
            data.pop("message", None)
            return "draft"
        return self._change(draft_id, change)

    def add_asset(self, draft_id, asset):
        def change(current, data):
            self.require_editable(current)
            allowed = {".mp4", ".mov"} if current["kind"] == "video" else {".png", ".jpg", ".jpeg", ".webp"}
            limit = 1 if current["kind"] == "video" else 9
            maximum = (500 if current["kind"] == "video" else 20) * 1024 * 1024
            if asset["suffix"] not in allowed or len(data["assets"]) >= limit or not 0 < asset["size"] <= maximum:
                raise ValueError("素材类型、数量或大小超出本草稿允许范围。")
            valid_id(asset["id"])
            data["assets"].append(asset)
            return current["state"]
        return self._change(draft_id, change)

    def remove_asset(self, draft_id, asset_id):
        valid_id(asset_id)
        def change(current, data):
            self.require_editable(current)
            data["assets"] = [a for a in data["assets"] if a["id"] != asset_id]
            return current["state"]
        return self._change(draft_id, change)

    def asset_path(self, draft_id, asset_id):
        valid_id(asset_id)
        draft = self.get(draft_id)
        asset = next((a for a in draft["assets"] if a["id"] == asset_id), None)
        if asset is None:
            raise FileNotFoundError()
        folder = (self.root / valid_id(draft_id)).resolve()
        path = (folder / (asset_id + asset["suffix"])).resolve()
        if not path.is_relative_to(self.root) or not path.is_relative_to(folder):
            raise ValueError("素材路径不可访问。")
        return path

    def claim(self, draft_id, version):
        def change(current, data):
            self.require_editable(current)
            if version != current["version"]:
                raise ValueError("草稿发生变化，请刷新并重新确认。")
            if not data["assets"]:
                raise ValueError("请先上传发布素材。")
            data.pop("message", None)
            data.pop("_verification", None)
            return "queued"
        return self._change(draft_id, change)

    def mark(self, draft_id, state, **fields):
        if state not in STATES:
            raise ValueError("发布状态不正确。")
        def change(current, data):
            if current["state"] == "published" and state != "published":
                return "published"
            if current["state"] in {"submitting", "uncertain"} and state in EDITABLE | {"queued", "preparing"}:
                raise ValueError("提交结果待核对，不能退回可重发状态。")
            data.pop("message", None)
            data.update(fields)
            return state
        return self._change(draft_id, change)

    def begin_submit(self, draft_id, verification):
        def change(current, data):
            if current["state"] != "preparing":
                raise ValueError("这个草稿不能再次提交。")
            data["_verification"] = verification
            data.pop("message", None)
            return "submitting"
        return self._change(draft_id, change)

    def interrupted(self, draft_id):
        def change(current, data):
            if current["state"] in ACTIVE:
                data.pop("message", None)
                return "uncertain" if current["state"] == "submitting" else "failed"
            return current["state"]
        return self._change(draft_id, change)

    def recover(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM drafts WHERE state IN ('queued','preparing','submitting')")]
        for draft_id in ids:
            self.interrupted(draft_id)
