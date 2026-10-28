"""Local note storage shared by the web server and isolated capture worker."""

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


NOTE_ID = re.compile(r"^[0-9a-f]{24}$")
NOTE_FIELDS = ("id", "title", "description", "author", "kind", "published_at",
               "likes", "collects", "comment_count", "source_keyword", "detailed",
               "updated_at", "comments_checked_at", "comments_complete", "last_scan_count",
               "creator_id", "replies_checked_at", "replies_complete", "last_reply_count")
COMMENT_FIELDS = ("id", "note_id", "author", "content", "published_at", "likes", "reply_count", "parent_id")


def valid_note_id(value):
    if not isinstance(value, str) or not NOTE_ID.fullmatch(value):
        raise ValueError("笔记编号格式不正确。")
    return value


class NotesStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "notes.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '', published_at REAL NOT NULL DEFAULT 0,
                    likes TEXT NOT NULL DEFAULT '0', collects TEXT NOT NULL DEFAULT '0',
                    comment_count TEXT NOT NULL DEFAULT '0', source_keyword TEXT NOT NULL DEFAULT '',
                    detailed INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    comments_checked_at REAL, comments_complete INTEGER,
                    last_scan_count INTEGER NOT NULL DEFAULT 0,
                    xsec_token TEXT NOT NULL DEFAULT '', xsec_source TEXT NOT NULL DEFAULT 'pc_search'
                );
                CREATE TABLE IF NOT EXISTS comments (
                    id TEXT NOT NULL, note_id TEXT NOT NULL REFERENCES notes(id),
                    author TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
                    published_at REAL NOT NULL DEFAULT 0, likes TEXT NOT NULL DEFAULT '0',
                    reply_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(note_id, id)
                );
                CREATE INDEX IF NOT EXISTS comments_note_time ON comments(note_id, published_at);
                CREATE TABLE IF NOT EXISTS note_media (
                    note_id TEXT NOT NULL REFERENCES notes(id), name TEXT NOT NULL, kind TEXT NOT NULL,
                    bytes INTEGER NOT NULL, saved_at REAL NOT NULL, PRIMARY KEY(note_id,name)
                );
                CREATE TABLE IF NOT EXISTS note_monitors (
                    note_id TEXT PRIMARY KEY REFERENCES notes(id), enabled INTEGER NOT NULL DEFAULT 0,
                    interval_minutes INTEGER NOT NULL DEFAULT 10, max_comments INTEGER NOT NULL DEFAULT 100,
                    include_replies INTEGER NOT NULL DEFAULT 0, max_replies INTEGER NOT NULL DEFAULT 100,
                    next_run REAL, last_run REAL, last_error TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0
                );
            """)
            for table, columns in {
                "notes": {"creator_id": "TEXT NOT NULL DEFAULT ''", "replies_checked_at": "REAL",
                          "replies_complete": "INTEGER", "last_reply_count": "INTEGER NOT NULL DEFAULT 0"},
                "comments": {"parent_id": "TEXT NOT NULL DEFAULT ''"},
            }.items():
                existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                for column, declaration in columns.items():
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        item = dict(row)
        for key in ("detailed", "comments_complete", "replies_complete", "enabled", "include_replies"):
            if key in item and item[key] is not None:
                item[key] = bool(item[key])
        return item

    def save_note(self, note, *, token="", source="pc_search", detailed=False, keyword=""):
        note_id = valid_note_id(note.get("id"))
        with self.connect() as db:
            old = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            # A fresh search card must never replace previously captured full text.
            fields = ("title", "description", "author", "kind", "published_at", "likes", "collects", "comment_count")
            values = {key: note.get(key, 0 if key == "published_at" else "") for key in fields}
            if old and old["detailed"] and not detailed:
                values = {key: old[key] for key in fields}
            values.update(id=note_id, updated_at=time.time(), detailed=int(detailed or bool(old and old["detailed"])),
                          creator_id=note.get("creator_id") or (old["creator_id"] if old else ""),
                          source_keyword=keyword or (old["source_keyword"] if old else ""),
                          xsec_token=token or (old["xsec_token"] if old else ""),
                          xsec_source=(source or "pc_search") if token or not old else old["xsec_source"])
            names = list(values)
            assignments = ",".join(f"{key}=excluded.{key}" for key in names if key != "id")
            db.execute(f"INSERT INTO notes ({','.join(names)}) VALUES ({','.join('?' for _ in names)}) "
                       f"ON CONFLICT(id) DO UPDATE SET {assignments}", [values[key] for key in names])

    def get(self, note_id):
        valid_note_id(note_id)
        with self.connect() as db:
            row = db.execute(f"SELECT {','.join(NOTE_FIELDS)}, "
                             "(SELECT count(*) FROM comments WHERE note_id=notes.id) AS saved_comments "
                             "FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("笔记尚未保存。")
        item = self.public(row)
        with self.connect() as db:
            item["saved_replies"] = db.execute("SELECT count(*) FROM comments WHERE note_id=? AND parent_id!=''", (note_id,)).fetchone()[0]
        item["media"] = self.media(note_id)
        return item

    def access(self, note_id):
        self.get(note_id)
        with self.connect() as db:
            row = db.execute("SELECT xsec_token AS token,xsec_source AS source FROM notes WHERE id=?", (note_id,)).fetchone()
        return dict(row)

    def list_notes(self, search="", page=1, size=20):
        where, args = "", []
        if search:
            where = " WHERE instr(lower(title || ' ' || description || ' ' || author || ' ' || source_keyword), lower(?))>0"
            args = [search]
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM notes" + where, args).fetchone()[0]
            rows = db.execute(f"SELECT {','.join(NOTE_FIELDS)}, "
                              "(SELECT count(*) FROM comments WHERE note_id=notes.id) AS saved_comments FROM notes" + where +
                              " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", args + [size, (page - 1) * size]).fetchall()
        return {"total": total, "items": [self.public(row) for row in rows], "page": page, "size": size}

    def save_comments(self, note_id, comments, *, complete, replies_complete=None):
        valid_note_id(note_id)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone():
                raise FileNotFoundError("请先保存笔记。")
            for item in comments:
                if not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 128:
                    raise ValueError("评论缺少有效编号，已保留原有记录。")
                db.execute("""INSERT INTO comments (id,note_id,author,content,published_at,likes,reply_count,parent_id)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(note_id,id) DO UPDATE SET
                    author=excluded.author,content=excluded.content,published_at=excluded.published_at,
                    likes=excluded.likes,reply_count=excluded.reply_count,parent_id=excluded.parent_id""",
                    (item["id"], note_id, item.get("author", ""), item.get("content", ""),
                     item.get("published_at", 0), str(item.get("likes", "0")), item.get("reply_count", 0), item.get("parent_id", "")))
            roots = sum(not item.get("parent_id") for item in comments)
            db.execute("UPDATE notes SET comments_checked_at=?,comments_complete=?,last_scan_count=?,updated_at=? WHERE id=?",
                       (time.time(), int(complete), roots, time.time(), note_id))
            if replies_complete is not None:
                db.execute("UPDATE notes SET replies_checked_at=?,replies_complete=?,last_reply_count=? WHERE id=?",
                           (time.time(), int(replies_complete), len(comments) - roots, note_id))

    def comments(self, note_id="", search="", page=1, size=30, level="all"):
        where, args = self.comment_filter(note_id, search, level)
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM comments c" + where, args).fetchone()[0]
            rows = db.execute("SELECT " + ",".join("c." + key for key in COMMENT_FIELDS) +
                              ",n.title AS note_title,p.content AS parent_content FROM comments c JOIN notes n ON n.id=c.note_id "
                              "LEFT JOIN comments p ON p.note_id=c.note_id AND p.id=c.parent_id" + where +
                              " ORDER BY c.published_at DESC,c.id LIMIT ? OFFSET ?", args + [size, (page - 1) * size]).fetchall()
        return {"total": total, "items": [dict(row) for row in rows], "page": page, "size": size}

    @staticmethod
    def comment_filter(note_id, search, level="all"):
        clauses, args = [], []
        if level not in {"all", "roots", "replies"}:
            raise ValueError("评论层级不正确。")
        if level != "all":
            clauses.append("c.parent_id=''" if level == "roots" else "c.parent_id!=''")
        if note_id:
            valid_note_id(note_id)
            clauses.append("c.note_id=?")
            args.append(note_id)
        if search:
            clauses.append("instr(lower(c.content || ' ' || c.author),lower(?))>0")
            args.append(search)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def export_rows(self, dataset, note_id="", search="", level="all"):
        if note_id:
            self.get(note_id)
        with self.connect() as db:
            if dataset == "notes":
                query = "SELECT " + ",".join(NOTE_FIELDS) + " FROM notes"
                args = [note_id] if note_id else []
                if note_id:
                    query += " WHERE id=?"
                if search:
                    query += (" AND " if note_id else " WHERE ") + "instr(lower(title || ' ' || description || ' ' || author || ' ' || source_keyword),lower(?))>0"
                    args.append(search)
            elif dataset == "comments":
                where, args = self.comment_filter(note_id, search, level)
                query = "SELECT " + ",".join("c." + key for key in COMMENT_FIELDS) + " FROM comments c" + where
            else:
                raise ValueError("不支持的导出类型。")
            for row in db.execute(query, args):
                yield self.public(row)

    def stats(self):
        with self.connect() as db:
            notes, revision = db.execute("SELECT count(*),max(updated_at) FROM notes").fetchone()
            comments = db.execute("SELECT count(*) FROM comments").fetchone()[0]
            replies = db.execute("SELECT count(*) FROM comments WHERE parent_id!=''").fetchone()[0]
            media = db.execute("SELECT count(*) FROM note_media").fetchone()[0]
        return {"notes": notes, "comments": comments, "replies": replies, "media": media, "revision": str(revision or 0)}

    def save_media(self, note_id, name, kind, size):
        valid_note_id(note_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif|mp4|mov|flv)", name):
            raise ValueError("媒体文件名不正确。")
        with self.connect() as db:
            db.execute("INSERT INTO note_media VALUES (?,?,?,?,?) ON CONFLICT(note_id,name) DO UPDATE SET bytes=excluded.bytes,saved_at=excluded.saved_at",
                       (note_id, name, kind, size, time.time()))
            db.execute("UPDATE notes SET updated_at=? WHERE id=?", (time.time(), note_id))

    def media(self, note_id):
        valid_note_id(note_id)
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT name,kind,bytes,saved_at FROM note_media WHERE note_id=? ORDER BY name", (note_id,))]

    def set_monitor(self, note_id, enabled, interval, maximum, include_replies, max_replies):
        self.get(note_id)
        with self.connect() as db:
            if enabled and db.execute("SELECT count(*) FROM note_monitors WHERE enabled=1 AND note_id!=?", (note_id,)).fetchone()[0] >= 10:
                raise ValueError("最多同时监测 10 篇笔记，请先暂停一项。")
            db.execute("""INSERT INTO note_monitors(note_id,enabled,interval_minutes,max_comments,include_replies,max_replies,next_run,version)
                VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(note_id) DO UPDATE SET enabled=excluded.enabled,
                interval_minutes=excluded.interval_minutes,max_comments=excluded.max_comments,
                include_replies=excluded.include_replies,max_replies=excluded.max_replies,next_run=excluded.next_run,
                last_error='',version=note_monitors.version+1""",
                (note_id, int(enabled), interval, maximum, int(include_replies), max_replies, time.time()+interval*60 if enabled else None))

    def monitors(self):
        with self.connect() as db:
            return [self.public(row) for row in db.execute("SELECT m.*,n.title FROM note_monitors m JOIN notes n ON n.id=m.note_id ORDER BY m.next_run,m.note_id")]

    def pause_monitors_for_restart(self):
        with self.connect() as db:
            db.execute("UPDATE note_monitors SET enabled=0,next_run=NULL,version=version+1 WHERE enabled=1")

    def finish_monitor(self, note_id, version, status, message):
        with self.connect() as db:
            db.execute("""UPDATE note_monitors SET last_run=?, next_run=CASE WHEN ?='succeeded' THEN ?+interval_minutes*60 ELSE NULL END,
                enabled=CASE WHEN ?='succeeded' THEN enabled ELSE 0 END,last_error=? WHERE note_id=? AND version=?""",
                (time.time(), status, time.time(), status, "" if status=="succeeded" else message, note_id, version))
