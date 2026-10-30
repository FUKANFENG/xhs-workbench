"""Local data access and scheduling; credentials never leave this module's paths."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import threading
import time
import uuid
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime import BusyError, JobRunner
from .notes import NotesStore, valid_note_id
from .note_schedule import NoteSchedule
from .publishing import Publishing

PROJECT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT / "skills" / "xhs-group-chat-insights" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from prepare_chat import normalize  # noqa: E402
from xhs_ext.group_chat_exporter import parse_group_id  # noqa: E402

GROUP_ID = re.compile(r"^[0-9]{1,32}$")


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


class LocalService:
    def __init__(self, data_root: Path):
        self.root = data_root.resolve()
        self.runtime = Path(os.environ.get("XHS_RUNTIME_ROOT", self.root / "runtime" / "Spider_XHS")).resolve()
        self.python = self.runtime / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.cookie = self.root / "auth" / "xhs-cookie.txt"
        self.qr = self.root / "auth" / "login-qr.png"
        self.exports = self.root / "exports"
        self.config_file = self.root / "preferences.json"
        for path in (self.root, self.cookie.parent, self.exports):
            path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.preferences = read_json(self.config_file, {"groups": {}, "monitor": {}})
        self.monitor = {"enabled": False, "group_id": "", "interval_minutes": 10,
                        "max_pages": 100, "next_run": None, "last_error": ""}
        saved = self.preferences.get("monitor", {})
        for key in ("group_id", "interval_minutes", "max_pages"):
            if key in saved:
                self.monitor[key] = saved[key]
        self.runner = JobRunner(PROJECT, self.finished)
        self.stop_event = threading.Event()
        self.cache: dict[str, tuple[float, dict, list[dict]]] = {}
        self.notes = NotesStore(self.root / "notes")
        self.media_root = Path(os.environ.get("XHS_MEDIA_ROOT", self.root / "runtime" / "MediaCrawler")).resolve()
        self.media_python = self.media_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.note_requests: dict[str, Path] = {}
        self.note_schedule = NoteSchedule(self)
        self.publishing = Publishing(self)

    def notes_ready(self):
        return self.media_python.is_file() and (self.media_root / "media_platform/xhs/client.py").is_file()

    def start_note_job(self, request):
        from integration.notes_worker import validate_url, parse_note_url
        from urllib.parse import urlsplit
        if not self.credential_saved():
            raise ValueError("请先在群记录工作台扫码登录，笔记采集会共用这次登录。")
        if not self.notes_ready():
            raise ValueError("笔记采集环境未就绪，请运行 Install-Notes.ps1。")
        mode = request["mode"]
        if mode == "comments" and not request["max_comments"]:
            raise ValueError("更新评论时，采集上限必须大于零。")
        if mode == "creator":
            from integration.notes_extra import parse_creator_url
            parse_creator_url(request["url"])
        elif mode == "search":
            request["keyword"] = request["keyword"].strip()
            if not request["keyword"]:
                raise ValueError("请输入搜索关键词。")
        elif request.get("url") and mode == "detail":
            request["url"] = validate_url(request["url"])
            if urlsplit(request["url"]).hostname in {"www.xiaohongshu.com", "xiaohongshu.com"}:
                parse_note_url(request["url"])
        else:
            valid_note_id(request["note_id"])
            self.notes.get(request["note_id"])
        with self.lock:
            if self.runner.busy:
                raise BusyError("已有任务正在执行，请等待完成或先取消。")
            folder = self.root / "notes" / "requests"
            folder.mkdir(exist_ok=True)
            path = folder / (uuid.uuid4().hex + ".json")
            path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            command = [str(self.media_python), str(PROJECT / "integration/notes_worker.py"),
                       "--runtime", str(self.media_root), "--data", str(self.root), "--request", str(path)]
            try:
                job = self.runner.start("notes_" + mode, {"search": "搜索笔记", "detail": "采集笔记与评论", "comments": "更新评论", "creator": "采集作者作品", "media": "下载笔记媒体"}[mode],
                                        [command], timeout=900)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            self.note_requests[job["id"]] = path
            return job

    def start_scheduler(self) -> None:
        threading.Thread(target=self._schedule_loop, daemon=True).start()

    def save(self) -> None:
        self.preferences["monitor"] = {key: self.monitor[key] for key in
                                       ("group_id", "interval_minutes", "max_pages")}
        temporary = self.config_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.preferences, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_file)

    def valid_group(self, value: str) -> str:
        try:
            group_id = parse_group_id(value)
        except ValueError as error:
            raise ValueError("请输入小红书群会话链接，或纯数字群 ID。") from error
        if not GROUP_ID.fullmatch(group_id):
            raise ValueError("群 ID 的格式不正确。")
        return group_id

    def group_path(self, group_id: str) -> Path:
        if not GROUP_ID.fullmatch(group_id):
            raise ValueError("群 ID 的格式不正确。")
        path = (self.exports / group_id).resolve()
        if path.parent != self.exports.resolve():
            raise ValueError("记录目录不可访问。")
        return path

    def credential_saved(self) -> bool:
        if not self.cookie.is_file():
            return False
        try:
            text = self.cookie.read_text(encoding="utf-8-sig")
            names = {part.split("=", 1)[0].strip() for part in text.split(";") if "=" in part}
            return {"a1", "web_session"} <= names
        except OSError:
            return False

    def base_command(self, action: str) -> list[str]:
        if not self.python.is_file():
            raise ValueError("运行环境尚未安装完成。")
        return [str(self.python), str(SCRIPTS / "xhs_group_tool.py"), action,
                "--spider-root", str(self.runtime), "--cookie-file", str(self.cookie)]

    def login(self) -> dict:
        command = self.base_command("login-qr") + ["--qr-file", str(self.qr), "--overwrite"]
        with self.lock:
            if self.runner.busy:
                raise BusyError("已有任务正在执行，请等待完成或先取消。")
            self.monitor["enabled"] = False
            self.notes.pause_monitors_for_restart()
            self.qr.unlink(missing_ok=True)
            return self.runner.start("login", "扫码登录", [command], timeout=210)

    def capture(self, value: str, name: str, mode: str, max_pages: int) -> dict:
        group_id = self.valid_group(value)
        if not self.credential_saved():
            raise ValueError("请先扫码登录，再采集群记录。")
        exists = (self.group_path(group_id) / "messages.json").is_file()
        if mode == "full" and exists:
            raise ValueError("这个群已经有记录，请选择增量更新或继续历史采集。")
        if mode != "full" and not exists:
            raise ValueError("请先采集这个群的记录。")
        command = self.base_command("group-chat") + ["--chat-url", group_id,
                  "--output-dir", str(self.exports), "--max-pages", str(max_pages), "--delay", "1.2"]
        if mode != "full":
            command.append("--" + mode)
        titles = {"full": "采集群记录", "resume": "继续历史采集", "update": "增量更新"}
        with self.lock:
            job = self.runner.start("capture", titles[mode], [command], group_id)
            self.preferences.setdefault("groups", {})[group_id] = {"name": name.strip() or group_id}
            self.save()
            return job

    def analyze(self, group_id: str) -> dict:
        source = self.group_path(group_id) / "messages.json"
        if not source.is_file():
            raise ValueError("请先采集这个群的记录。")
        derived = self.root / "derived" / group_id
        analysis = self.root / "analysis" / group_id
        name = self.preferences.get("groups", {}).get(group_id, {}).get("name", group_id)
        commands = [
            [str(self.python), str(SCRIPTS / "prepare_chat.py"), "--messages-json", str(source),
             "--output-dir", str(derived), "--group-name", name, "--overwrite"],
            [str(self.python), str(SCRIPTS / "analyze_chats.py"), "--input", str(derived / "normalized_messages.csv"),
             "--output-dir", str(analysis), "--overwrite"],
        ]
        return self.runner.start("analysis", "整理话题与互动", commands, group_id)

    def finished(self, job: dict) -> None:
        with self.lock:
            if job["kind"].startswith("publish_"):
                self.publishing.finished(job)
                return
            if job["kind"].startswith("notes_"):
                self.note_schedule.finished(job)
                path = self.note_requests.pop(job["id"], None)
                if path:
                    path.unlink(missing_ok=True)
            if job["kind"] == "login":
                try:
                    self.qr.unlink(missing_ok=True)
                except OSError:
                    pass  # A Windows response may briefly hold the temporary QR image open.
                self.preferences["last_login_attempt"] = {
                    key: job.get(key) for key in ("status", "message", "diagnostic", "started_at", "finished_at")
                }
                if job["status"] == "succeeded":
                    self.preferences["last_login"] = time.time()
                self.save()
            if job["kind"] == "capture" and self.monitor["group_id"] == job["group_id"]:
                if job["status"] == "failed":
                    self.monitor.update(enabled=False, last_error=job["message"], next_run=None)
                else:
                    self.monitor["next_run"] = time.time() + self.monitor["interval_minutes"] * 60

    def set_monitor(self, group_id: str, enabled: bool, interval: int, pages: int) -> dict:
        with self.lock:
            if enabled:
                group_id = self.valid_group(group_id)
                if not (self.group_path(group_id) / "messages.json").is_file():
                    raise ValueError("先采集一次群记录，再开启定时增量更新。")
                if not self.credential_saved():
                    raise ValueError("请先扫码登录。")
            self.monitor.update(enabled=enabled, group_id=group_id, interval_minutes=interval,
                                max_pages=pages, next_run=time.time() + interval * 60 if enabled else None,
                                last_error="")
            self.save()
            return dict(self.monitor)

    def _schedule_loop(self) -> None:
        while not self.stop_event.wait(2):
            try:
                self.note_schedule.tick()
            except sqlite3.Error:
                # A temporary note-database lock must not kill group scheduling.
                pass
            with self.lock:
                monitor = dict(self.monitor)
            if not monitor["enabled"] or self.runner.busy or time.time() < (monitor["next_run"] or 0):
                continue
            try:
                name = self.preferences.get("groups", {}).get(monitor["group_id"], {}).get("name", "")
                self.capture(monitor["group_id"], name, "update", monitor["max_pages"])
            except BusyError:
                continue
            except Exception:
                with self.lock:
                    self.monitor.update(enabled=False, next_run=None, last_error="定时更新未能启动，已暂停。")

    def load_group(self, group_id: str) -> tuple[dict, list[dict]]:
        source = self.group_path(group_id) / "messages.json"
        if not source.is_file():
            raise FileNotFoundError("没有这个群的本地记录。")
        if source.stat().st_size > 150 * 1024 * 1024:
            raise ValueError("文件较大，请下载 JSON 后离线处理。")
        stamp = source.stat().st_mtime_ns
        cached = self.cache.get(group_id)
        if cached and cached[0] == stamp:
            return cached[1], cached[2]
        payload = read_json(source)
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise ValueError("本地记录格式无法读取。")
        name = self.preferences.get("groups", {}).get(group_id, {}).get("name", group_id)
        zone = ZoneInfo("Asia/Shanghai")
        rows = [normalize(message, name, group_id, zone) for message in payload["messages"]]
        self.cache[group_id] = (stamp, payload, rows)
        return payload, rows

    def groups(self) -> list[dict]:
        result = []
        for folder in self.exports.iterdir():
            if not folder.is_dir() or not GROUP_ID.fullmatch(folder.name):
                continue
            try:
                payload, rows = self.load_group(folder.name)
                times = [row["timestamp"] for row in rows if row["timestamp"]]
                result.append({"id": folder.name, "name": self.preferences.get("groups", {}).get(folder.name, {}).get("name", folder.name),
                               "count": len(rows), "completed": bool(payload.get("completed")),
                               "earliest": min(times) if times else "", "latest": max(times) if times else "",
                               "updated_at": (folder / "messages.json").stat().st_mtime,
                               "has_analysis": (self.root / "analysis" / folder.name / "analysis_summary.json").is_file()})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    def analysis(self, group_id: str) -> dict:
        self.group_path(group_id)
        folder = self.root / "analysis" / group_id
        summary = read_json(folder / "analysis_summary.json", {})
        topics = []
        if (folder / "topic_table.csv").is_file():
            with (folder / "topic_table.csv").open(encoding="utf-8-sig", newline="") as file:
                topics = list(csv.DictReader(file))
        return {"summary": summary, "topics": sorted(topics, key=lambda item: int(item["matched_messages"]), reverse=True)}

    def close(self) -> None:
        self.stop_event.set()
        self.runner.close()
