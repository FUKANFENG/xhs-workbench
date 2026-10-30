"""Protected local publishing endpoints; media arrives as bounded raw uploads."""
import os
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .publish_store import EDITABLE, valid_id


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "video"]
    title: str = Field(min_length=1, max_length=20)
    body: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def clean(self):
        self.title = self.title.strip()
        self.tags = list(dict.fromkeys(tag.strip().lstrip("#") for tag in self.tags))
        if not self.title or any(not t or len(t) > 20 or re.search(r"[\s#<>]", t) for t in self.tags):
            raise ValueError("标题或话题格式不正确。")
        if len(self.body) + sum(len(t) + 2 for t in self.tags) > 1000:
            raise ValueError("正文与话题合计不能超过 1000 字。")
        return self


class SubmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    version: int = Field(ge=1, strict=True)


def matches_media(header, suffix):
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return suffix in {".mp4", ".mov"} and header[4:8] == b"ftyp"


def publish_router(service):
    router = APIRouter()
    publisher = service.publishing
    store = publisher.store

    @router.get("/api/publish/status")
    def status():
        return publisher.status()

    @router.post("/api/publish/login")
    def login():
        return publisher.start("login")

    @router.post("/api/publish/check")
    def check():
        return publisher.start("check")

    @router.get("/api/publish/qr")
    def qr():
        jobs = service.runner.list()
        if not jobs or jobs[0]["kind"] != "publish_login" or jobs[0]["status"] != "running" or not publisher.qr.is_file():
            raise HTTPException(404, "二维码尚未生成或已经失效。")
        return FileResponse(publisher.qr, media_type="image/png")

    @router.get("/api/publish/drafts")
    def drafts():
        return {"items": store.list()}

    @router.post("/api/publish/drafts")
    def create(body: DraftInput):
        return store.create(body.model_dump())

    @router.get("/api/publish/drafts/{draft_id}")
    def detail(draft_id: str):
        return store.get(draft_id)

    @router.put("/api/publish/drafts/{draft_id}")
    def update(draft_id: str, body: DraftInput):
        return store.update(draft_id, body.model_dump())

    @router.post("/api/publish/drafts/{draft_id}/assets")
    async def upload(draft_id: str, request: Request, name: str = Query(min_length=1, max_length=180)):
        draft = store.get(draft_id)
        if draft["state"] not in EDITABLE:
            raise ValueError("这个草稿已经冻结，不能添加素材。")
        if "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
            raise ValueError("素材文件名不正确。")
        suffix = Path(name).suffix.lower()
        allowed = {".mp4", ".mov"} if draft["kind"] == "video" else {".jpg", ".jpeg", ".png", ".webp"}
        if suffix not in allowed:
            raise ValueError("请选择支持的图片或视频文件。")
        maximum = (500 if draft["kind"] == "video" else 20) * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and (not declared.isdecimal() or int(declared) > maximum):
            raise HTTPException(413, "素材文件超过大小限制。")
        asset_id = uuid.uuid4().hex
        folder = (store.root / valid_id(draft_id)).resolve()
        if not folder.is_relative_to(store.root):
            raise ValueError("素材路径不可访问。")
        folder.mkdir(mode=0o700, exist_ok=True)
        temporary = folder / (asset_id + ".part")
        destination = folder / (asset_id + suffix)
        size, header = 0, b""
        try:
            with temporary.open("xb") as file:
                os.chmod(temporary, 0o600)
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, "素材文件超过大小限制。")
                    if len(header) < 16:
                        header = (header + chunk)[:16]
                    file.write(chunk)
            if size == 0 or not matches_media(header, suffix):
                raise ValueError("文件内容与素材格式不匹配，请选择原始图片或 MP4/MOV 视频。")
            os.replace(temporary, destination)
            return store.add_asset(draft_id, {"id": asset_id, "name": name, "suffix": suffix, "size": size})
        except BaseException:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise

    @router.delete("/api/publish/drafts/{draft_id}/assets/{asset_id}")
    def remove(draft_id: str, asset_id: str):
        path = store.asset_path(draft_id, asset_id)
        result = store.remove_asset(draft_id, asset_id)
        path.unlink(missing_ok=True)
        return result

    @router.get("/api/publish/drafts/{draft_id}/assets/{asset_id}")
    def media(draft_id: str, asset_id: str):
        path = store.asset_path(draft_id, asset_id)
        if not path.is_file():
            raise FileNotFoundError()
        types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime"}
        return FileResponse(path, media_type=types[path.suffix])

    @router.post("/api/publish/drafts/{draft_id}/submit")
    def submit(draft_id: str, body: SubmitInput):
        return publisher.start("publish", draft_id, body.version)

    @router.post("/api/publish/drafts/{draft_id}/verify")
    def verify(draft_id: str):
        return publisher.start("verify", draft_id)

    return router
