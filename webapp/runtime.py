"""Run the upstream CLI as bounded, cancellable local jobs."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


class BusyError(RuntimeError):
    pass


LOGIN_STAGES = {
    "initializing_device": "正在初始化登录环境",
    "generating_qr": "正在生成二维码",
    "preflight": "正在检查二维码",
    "preparing_web_profile": "正在准备登录环境",
    "waiting_for_scan": "请用手机小红书扫描二维码",
    "waiting_for_confirmation": "已扫码，请在手机上确认登录",
    "exchanging_session": "手机已确认，正在获取网页登录凭据",
    "verifying_session": "正在验证网页登录状态",
    "saving_credentials": "正在保存本地凭据",
}


def login_error(diagnostic: dict) -> str:
    stage = diagnostic.get("stage")
    if diagnostic.get("http_status") in (471, 461):
        return "平台要求额外验证，本次登录未完成。请先在小红书官方页面完成验证后再试。"
    if stage == "saving_credentials":
        return "登录已验证，但本地凭据保存失败，请检查数据目录权限和磁盘空间。"
    if "timeout" in diagnostic.get("error_type", "").lower():
        return "等待扫码或平台响应超时，请重新生成二维码。"
    if stage == "exchanging_session":
        return "手机已确认，但平台登录凭据获取失败；诊断信息已保留。"
    if stage == "verifying_session":
        return "手机已确认，但网页登录状态验证失败，凭据尚未保存。"
    return f"{LOGIN_STAGES.get(stage, '登录')}阶段未完成；诊断信息已保留，请重试。"


def public_error(line: str) -> str:
    """Translate expected CLI errors without returning credentials or traces."""
    value = line.lower()
    note_errors = {
        "notes_auth": "登录已失效或访问被拒绝，请重新扫码后再试。",
        "notes_blocked": "平台限制了本次请求，任务已停止，请稍后再试。",
        "notes_runtime": "笔记采集环境版本不匹配，请重新运行 Install-Notes.ps1。",
        "notes_token": "链接缺少访问参数。请复制完整分享链接，或先搜索这篇笔记再采集。",
        "notes_link": "无法解析分享短链接，请复制小红书网页版的完整笔记链接。",
        "notes_missing": "这篇笔记已删除、不可见，或当前账号无法访问。",
        "notes_access": "平台未返回可用内容，请重新搜索笔记或使用新的完整分享链接。",
        "notes_response": "平台返回的数据格式不完整，本次采集已停止，旧记录仍保留。",
        "notes_pagination": "评论分页未能继续，本次评论未写入，旧记录仍保留。",
        "notes_network": "连接小红书超时或失败，请检查网络后重试。",
        "notes_failed": "笔记采集未完成，已保留之前保存的记录。请检查链接、登录状态后重试。",
        "notes_no_media": "这篇笔记没有返回可下载的媒体文件。",
        "notes_media_failed": "部分媒体下载失败，已完成的文件会保留，可稍后重试。",
        "notes_media_size": "媒体文件超出本次大小限制，下载已停止。",
        "notes_media_limit": "这篇笔记的媒体超过本次 30 个文件上限，未启动下载。",
    }
    if value.removeprefix("error=") in note_errors:
        return note_errors[value.removeprefix("error=")]
    if "incremental update reached" in value:
        return "新增消息超过本次页数上限，旧记录已保留。请增加页数后重试。"
    if any(word in value for word in ("qr code expired", "timed out waiting", "timeout")):
        return "等待扫码或网络响应超时，请重新尝试。"
    if any(word in value for word in ("471", "461", "verifytype", "risk control", "风控", "人机验证")):
        return "平台要求额外验证，本次登录未完成。请先在小红书官方页面完成验证后再试。"
    if any(word in value for word in ("cookie", "login", "authenticated", "authorization", "401", "403")):
        return "登录凭据无效或账号无权访问，请重新扫码并确认群链接。"
    if "already exists" in value or "existing export" in value:
        return "这个群已有记录，请选择增量更新或继续历史采集。"
    if "resume requires" in value or "requires an existing" in value:
        return "请先完成一次群记录采集，再进行续抓或增量更新。"
    if "cursor" in value or "out_message_list" in value:
        return "群消息接口返回格式发生变化，采集已停止。"
    if "interrupted" in value:
        return "任务已取消。"
    return "任务未完成，请检查网络、登录状态和群访问权限后重试。"


class JobRunner:
    def __init__(self, cwd: Path, on_finish: Callable[[dict], None] | None = None):
        self.cwd = cwd
        self.on_finish = on_finish
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.process: subprocess.Popen | None = None
        self.active_id: str | None = None
        self.stopping = False

    @property
    def busy(self) -> bool:
        with self.lock:
            return self.active_id is not None

    def list(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(list(self.jobs.values())[-20:][::-1])

    def start(self, kind: str, title: str, commands: list[list[str]],
              group_id: str = "", timeout: int = 3600) -> dict:
        with self.lock:
            if self.active_id or self.stopping:
                raise BusyError("已有任务正在执行，请等待完成或先取消。")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "kind": kind, "title": title,
                   "group_id": group_id, "status": "running", "message": "正在准备…",
                   "started_at": time.time(), "finished_at": None,
                   "new_messages": None, "message_count": None, "diagnostic": None, "logs": []}
            self.jobs[job_id] = job
            self.active_id = job_id
            while len(self.jobs) > 40:
                self.jobs.pop(next(iter(self.jobs)))
            threading.Thread(target=self._run, args=(job_id, commands, timeout), daemon=True).start()
            return copy.deepcopy(job)

    def _progress(self, job: dict, line: str) -> None:
        if job["status"] != "running":
            return
        match = re.search(r"page (\d+): (\d+) unique messages", line)
        message = None
        if match:
            message = f"已读取 {match[1]} 页，累计 {match[2]} 条消息。"
        elif line.startswith("qr_file=") or "status=waiting_for_scan" in line:
            message = "请用手机小红书扫描二维码并确认登录。"
        elif "status=login_succeeded" in line:
            message = "扫码登录成功，本地凭据已保存。"
        elif line.startswith("login_progress="):
            stage = line.split("=", 1)[1]
            message = LOGIN_STAGES.get(stage)
        elif line.startswith("notes_progress="):
            try:
                event = json.loads(line.split("=", 1)[1])
                count = event.get("count", 0)
                if type(count) is not int or not 0 <= count <= 100000:
                    return
                message = {"auth": "正在校验共享登录状态。", "search": "正在搜索小红书笔记。",
                           "detail": "正在读取笔记详情。", "comments": f"已读取 {count} 条一级评论。",
                           "search_saved": f"已保存 {count} 条搜索结果，可选择笔记采集完整详情。",
                           "comments_saved": f"已合并 {count} 条一级评论，本次已读到分页末尾。",
                           "comments_limited": f"已合并 {count} 条一级评论，达到本次上限；已有记录保留。",
                           "detail_saved": "笔记详情已保存。", "replies": "正在获取二级回复。",
                           "replies_saved": f"已合并 {count} 条回复，本轮选取的评论回复已读取完成。",
                           "replies_limited": f"已合并 {count} 条回复，达到本次回复上限。",
                           "creator": "正在读取作者作品列表。", "creator_saved": f"已保存 {count} 篇作者作品摘要。",
                           "media": f"已保存 {count} 个媒体文件。", "media_saved": f"已保存 {count} 个媒体文件，可在笔记详情中预览或下载。"}.get(event.get("stage"))
            except (TypeError, ValueError, AttributeError):
                pass
        elif match := re.search(r"added (\d+) new messages", line):
            job["new_messages"] = int(match[1])
            message = f"本次新增 {match[1]} 条消息。"
        elif match := re.search(r"exported (\d+) unique messages", line):
            job["message_count"] = int(match[1])
            message = f"已保存 {match[1]} 条消息。"
        elif line.startswith("error="):
            job["error"] = public_error(line)
        elif line.startswith("login_diagnostic="):
            try:
                diagnostic = json.loads(line.split("=", 1)[1])
                if isinstance(diagnostic, dict):
                    safe = {}
                    if diagnostic.get("stage") in LOGIN_STAGES:
                        safe["stage"] = diagnostic["stage"]
                    for key in ("http_status", "api_code", "api_success", "codeStatus", "code_status", "guest"):
                        if type(diagnostic.get(key)) in (int, bool):
                            safe[key] = diagnostic[key]
                    for key in ("error_type", "source"):
                        value = diagnostic.get(key)
                        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:<>-]{1,160}", value):
                            safe[key] = value
                    if safe:
                        job["diagnostic"] = safe
                        message = login_error(safe)
            except (TypeError, ValueError):
                pass
        if message and message != job["message"]:
            job["message"] = message
            job["logs"].append({"time": time.time(), "text": message})
            job["logs"] = job["logs"][-30:]

    def _run(self, job_id: str, commands: list[list[str]], timeout: int) -> None:
        timer = threading.Timer(timeout, self._timeout, args=(job_id,))
        timer.daemon = True
        timer.start()
        try:
            env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            for command in commands:
                with self.lock:
                    job = self.jobs[job_id]
                    if job["status"] != "running":
                        break
                    self.process = subprocess.Popen(
                        command, cwd=self.cwd, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    process = self.process
                assert process.stdout is not None
                for line in process.stdout:
                    with self.lock:
                        self._progress(job, line.strip())
                code = process.wait()
                with self.lock:
                    self.process = None
                    if job["status"] != "running":
                        break
                    if code != 0:
                        diagnostic = job.get("diagnostic")
                        job.update(status="failed", message=login_error(diagnostic) if diagnostic else job.get("error", public_error("")))
                        break
            with self.lock:
                if job["status"] == "running":
                    job.update(status="succeeded", message="任务已完成。" if not job["logs"] else job["message"])
        except Exception:
            with self.lock:
                self.jobs[job_id].update(status="failed", message="无法启动本地任务，请检查运行环境。")
        finally:
            timer.cancel()
            with self.lock:
                job = self.jobs[job_id]
                job["finished_at"] = time.time()
                self.process = None
                self.active_id = None
                snapshot = copy.deepcopy(job)
            if self.on_finish:
                self.on_finish(snapshot)

    def _timeout(self, job_id: str) -> None:
        self.cancel(job_id, timed_out=True)

    def cancel(self, job_id: str | None = None, *, timed_out: bool = False) -> None:
        with self.lock:
            if not self.active_id or (job_id and job_id != self.active_id):
                return
            job = self.jobs[self.active_id]
            job.update(status="failed" if timed_out else "cancelled",
                       message="任务超时，已停止。" if timed_out else "任务已取消。")
            process = self.process
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                               timeout=15, check=False)
            else:
                process.terminate()

    def close(self) -> None:
        with self.lock:
            self.stopping = True
        self.cancel()
