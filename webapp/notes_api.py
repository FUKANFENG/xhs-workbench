"""Note endpoints; all requests pass the application's local access checks."""

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .notes import COMMENT_FIELDS, NOTE_FIELDS


class NoteJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["search", "detail", "comments"]
    keyword: str = Field(default="", max_length=100)
    url: str = Field(default="", max_length=4096)
    note_id: str = Field(default="", max_length=24)
    sort: Literal["general", "time_descending", "popularity_descending"] = "general"
    max_notes: int = Field(default=20, ge=1, le=40)
    max_comments: int = Field(default=100, ge=0, le=500)


def export_stream(store, dataset, kind, note_id):
    rows = store.export_rows(dataset, note_id)
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
                 page: int = Query(default=1, ge=1), size: int = Query(default=30, ge=1, le=100)):
        return service.notes.comments(note_id, search, page, size)

    @router.get("/api/notes/export")
    def export(dataset: Literal["notes", "comments"], kind: Literal["csv", "json"], note_id: str = ""):
        if note_id:
            service.notes.get(note_id)
        filename = f"xhs-{dataset}{'-' + note_id if note_id else ''}.{kind}"
        return StreamingResponse(export_stream(service.notes, dataset, kind, note_id),
                                 media_type="application/json" if kind == "json" else "text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/api/notes/{note_id}")
    def detail(note_id: str):
        return service.notes.get(note_id)

    return router
