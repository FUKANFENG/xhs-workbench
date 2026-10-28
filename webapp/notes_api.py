"""Note endpoints; all requests pass the application's local access checks."""

import csv
import io
import json
import re
import mimetypes
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .notes import COMMENT_FIELDS, NOTE_FIELDS


class NoteJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["search", "detail", "comments", "creator", "media"]
    keyword: str = Field(default="", max_length=100)
    url: str = Field(default="", max_length=4096)
    note_id: str = Field(default="", max_length=24)
    sort: Literal["general", "time_descending", "popularity_descending"] = "general"
    max_notes: int = Field(default=20, ge=1, le=40)
    max_comments: int = Field(default=100, ge=0, le=500)
    include_replies: bool = False
    max_replies: int = Field(default=100, ge=1, le=500)
    note_type: Literal[0, 1, 2] = 0


class NoteMonitorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: str = Field(min_length=24, max_length=24)
    enabled: bool
    interval_minutes: int = Field(default=10, ge=5, le=1440)
    max_comments: int = Field(default=100, ge=1, le=500)
    include_replies: bool = False
    max_replies: int = Field(default=100, ge=1, le=500)


def export_stream(store, dataset, kind, note_id, search="", level="all"):
    rows = store.export_rows(dataset, note_id, search, level)
    if kind == "json":
        yield '{"dataset":' + json.dumps(dataset) + ',"items":['
        separator = ""
        for row in rows:
            yield separator + json.dumps(row, ensure_ascii=False)
            separator = ","
        yield "]}"
    else:
        buffer = io.StringIO(newline="")
        fields = NOTE_FIELDS if dataset == "notes" else COMMENT_FIELDS
        writer = csv.writer(buffer)
        writer.writerow(fields)
        yield "\ufeff" + buffer.getvalue()
        for row in rows:
            buffer.seek(0)
            buffer.truncate()
            values = []
            for key in fields:
                value = row.get(key, "")
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
                    value = "'" + value
                values.append(value)
            writer.writerow(values)
            yield buffer.getvalue()


def notes_router(service):
    router = APIRouter()

    @router.post("/api/notes/jobs")
    def start(body: NoteJobInput):
        return service.start_note_job(body.model_dump())

    @router.get("/api/notes")
    def listing(search: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1),
                size: int = Query(default=20, ge=1, le=100)):
        return service.notes.list_notes(search, page, size)

    @router.get("/api/note-comments")
    def comments(note_id: str = "", search: str = Query(default="", max_length=200),
                 page: int = Query(default=1, ge=1), size: int = Query(default=30, ge=1, le=100),
                 level: Literal["all", "roots", "replies"] = "all"):
        return service.notes.comments(note_id, search, page, size, level)

    @router.get("/api/note-monitors")
    def monitors():
        return {"items": service.notes.monitors()}

    @router.post("/api/note-monitors")
    def set_monitor(body: NoteMonitorInput):
        if body.enabled and (not service.credential_saved() or not service.notes_ready()):
            raise ValueError("请先完成登录和采集环境配置。")
        with service.lock:
            service.notes.set_monitor(body.note_id, body.enabled, body.interval_minutes, body.max_comments,
                                      body.include_replies, body.max_replies)
        return {"items": service.notes.monitors()}

    @router.get("/api/notes/export")
    def export(dataset: Literal["notes", "comments"], kind: Literal["csv", "json"], note_id: str = "",
               search: str = Query(default="", max_length=200), level: Literal["all", "roots", "replies"] = "all"):
        if note_id:
            service.notes.get(note_id)
        filename = f"xhs-{dataset}{'-' + note_id if note_id else ''}.{kind}"
        return StreamingResponse(export_stream(service.notes, dataset, kind, note_id, search, level),
                                 media_type="application/json" if kind == "json" else "text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/api/notes/{note_id}")
    def detail(note_id: str):
        return service.notes.get(note_id)

    @router.get("/api/notes/{note_id}/media/{name}")
    def media(note_id: str, name: str):
        service.notes.get(note_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif|mp4|mov|flv)", name):
            raise ValueError("媒体文件名不正确。")
        if not any(item["name"] == name for item in service.notes.media(note_id)):
            raise FileNotFoundError()
        root = (service.root / "notes" / "media").resolve()
        path = (root / "xhs" / "media" / note_id / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError()
        return FileResponse(path, filename=name, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream")

    return router
