"""Loopback-only API and static interface for local group-chat capture."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .runtime import BusyError
from .service import PROJECT, LocalService
from .notes_api import notes_router


class CaptureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_url: str = Field(min_length=1, max_length=512)
    name: str = Field(default="", max_length=80)
    mode: Literal["full", "resume", "update"] = "full"
    max_pages: int = Field(default=100, ge=1, le=1000)


class MonitorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(default="", max_length=32)
    enabled: bool
    interval_minutes: int = Field(default=10, ge=5, le=1440)
    max_pages: int = Field(default=100, ge=1, le=1000)


def create_app(data_root: Path | None = None) -> FastAPI:
    legacy = PROJECT.parent.parent / "work" / "xhs-local-data"
    root = data_root or Path(os.environ.get("XHS_LOCAL_DATA", legacy if legacy.is_dir() else PROJECT / "data"))
    service = LocalService(root)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.start_scheduler()
        yield
        service.close()

    app = FastAPI(title="群聊洞察 · 本地工作台", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.service = service
    app.state.local_token = token
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "仅允许当前本地页面访问。"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path not in {"/api/bootstrap", "/api/health"}:
            supplied = request.headers.get("x-local-token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "页面会话已过期，请刷新页面。"}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        })
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(BusyError)
    async def already_busy(request: Request, error: BusyError):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return JSONResponse({"detail": "输入格式或数量超出允许范围，请检查后重试。"}, status_code=422)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request: Request, error: FileNotFoundError):
        return JSONResponse({"detail": "没有找到对应的本地记录。"}, status_code=404)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "application": "xhs-group-chat-insights-local"}

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"token": token, "app_name": "小红书工作台", "version": "本地版 1.2"}

    @app.get("/api/status")
    def status():
        groups = service.groups()
        return {"environment_ready": service.python.is_file() and (service.runtime / "node_modules").is_dir(),
                "notes_environment_ready": service.notes_ready(), "notes_summary": service.notes.stats(),
                "credential_saved": service.credential_saved(), "last_login": service.preferences.get("last_login"),
                "last_login_attempt": service.preferences.get("last_login_attempt"),
                "busy": service.runner.busy, "jobs": service.runner.list(), "groups": groups,
                "monitor": dict(service.monitor), "total_messages": sum(group["count"] for group in groups)}

    @app.post("/api/login")
    def login():
        return service.login()

    @app.get("/api/login/qr")
    def login_qr():
        jobs = service.runner.list()
        if not jobs or jobs[0]["kind"] != "login" or jobs[0]["status"] != "running" or not service.qr.is_file():
            raise HTTPException(404, "二维码尚未生成或已经失效。")
        return FileResponse(service.qr, media_type="image/png")

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        service.runner.cancel(job_id)
        return {"status": "cancelled"}

    @app.post("/api/capture")
    def capture(body: CaptureInput):
        return service.capture(body.chat_url, body.name, body.mode, body.max_pages)

    @app.post("/api/groups/{group_id}/analyze")
    def analyze(group_id: str):
        return service.analyze(group_id)

    @app.post("/api/monitor")
    def monitor(body: MonitorInput):
        return service.set_monitor(body.group_id, body.enabled, body.interval_minutes, body.max_pages)

    @app.get("/api/groups/{group_id}/messages")
    def messages(group_id: str, search: str = Query(default="", max_length=200),
                 page: int = Query(default=1, ge=1), size: int = Query(default=30, ge=1, le=100)):
        payload, rows = service.load_group(group_id)
        query = search.casefold().strip()
        filtered = [row for row in reversed(rows) if not query or query in
                    (str(row["text"]) + " " + str(row["nickname"])).casefold()]
        start = (page - 1) * size
        fields = ("store_id", "timestamp", "nickname", "text", "media_kind", "quoted_text", "revoked")
        return {"total": len(filtered), "page": page, "size": size,
                "items": [{key: row[key] for key in fields} for row in filtered[start:start + size]]}

    @app.get("/api/groups/{group_id}/analysis")
    def analysis(group_id: str):
        return service.analysis(group_id)

    @app.get("/api/groups/{group_id}/download/{kind}")
    def download(group_id: str, kind: str):
        folder = service.group_path(group_id)
        files = {"json": folder / "messages.json", "csv": folder / "messages.csv",
                 "topics": service.root / "analysis" / group_id / "topic_table.csv",
                 "participants": service.root / "analysis" / group_id / "participant_table.csv",
                 "events": service.root / "analysis" / group_id / "event_candidates.csv"}
        if kind not in files:
            raise HTTPException(404, "没有这个导出文件。")
        path = files[kind].resolve()
        if not path.is_relative_to(service.root) or not path.is_file():
            raise HTTPException(404, "请先生成对应的记录或分析。")
        return FileResponse(path, filename=f"{group_id}-{path.name}", media_type="application/octet-stream")

    @app.get("/")
    def index():
        return FileResponse(PROJECT / "webapp" / "static" / "index.html")

    @app.get("/notes")
    @app.get("/comments")
    def notes_page():
        return FileResponse(PROJECT / "webapp" / "static" / "notes.html")

    app.include_router(notes_router(service))
    app.mount("/static", StaticFiles(directory=PROJECT / "webapp" / "static"), name="static")
    return app
