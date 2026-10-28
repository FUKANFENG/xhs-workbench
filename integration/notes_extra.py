"""Bounded reply, creator and media operations for the isolated worker."""

import asyncio
import re
from urllib.parse import parse_qs, urlsplit

from webapp.notes import valid_note_id


class NotesFailure(RuntimeError):
    pass


def timestamp(value):
    try:
        number = float(value or 0)
        return number / 1000 if number > 100000000000 else number
    except (TypeError, ValueError):
        return 0


def comment_row(item, parent_id=""):
    if not isinstance(item, dict) or not item.get("id"):
        raise NotesFailure("notes_response")
    return {"id": str(item["id"]), "parent_id": parent_id,
            "author": str((item.get("user_info") or {}).get("nickname") or ""),
            "content": str(item.get("content") or ""), "published_at": timestamp(item.get("create_time")),
            "likes": str(item.get("like_count") or "0"), "reply_count": int(item.get("sub_comment_count") or 0)}


async def fetch_replies(client, note_id, token, roots, limit, delay=1.2):
    replies, requests = {}, 0
    root_ids = {root["id"] for root in roots}
    for root in roots:
        embedded = root.get("_sub_comments") or []
        if not isinstance(embedded, list):
            raise NotesFailure("notes_response")
        if not root.get("reply_count") and not embedded:
            continue
        for item in embedded:
            row = comment_row(item, root["id"])
            if row["id"] in root_ids:
                raise NotesFailure("notes_response")
            if row["id"] not in replies and len(replies) >= limit:
                return list(replies.values()), False
            replies[row["id"]] = row
        has_more = root.get("_sub_has_more")
        if has_more is False:
            continue
        if has_more is None and len(embedded) >= root.get("reply_count", 0):
            continue
        cursor, seen = root.get("_sub_cursor") or "", set()
        while True:
            if len(replies) >= limit:
                return list(replies.values()), False
            requests += 1
            if requests > 100:
                return list(replies.values()), False
            await asyncio.sleep(delay)
            response = await client.get_note_sub_comments(note_id=note_id, root_comment_id=root["id"],
                                                          xsec_token=token, cursor=cursor, num=10)
            if not isinstance(response, dict) or not isinstance(response.get("comments"), list) or type(response.get("has_more")) is not bool:
                raise NotesFailure("notes_response")
            for item in response["comments"]:
                row = comment_row(item, root["id"])
                if row["id"] in root_ids:
                    raise NotesFailure("notes_response")
                if row["id"] not in replies and len(replies) >= limit:
                    return list(replies.values()), False
                replies[row["id"]] = row
            if not response["has_more"]:
                break
            next_cursor = response.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen or next_cursor == cursor:
                raise NotesFailure("notes_pagination")
            seen.add(next_cursor)
            cursor = next_cursor
    return list(replies.values()), True


def parse_creator_url(value):
    value = value.strip()
    if re.fullmatch(r"[0-9a-f]{24}", value):
        return value, "", "pc_feed"
    parsed = urlsplit(value)
    if (parsed.scheme not in {"https", "http"} or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError("请填写小红书作者主页链接或作者 ID。")
    match = re.fullmatch(r"/user/profile/([0-9a-f]{24})/?", parsed.path)
    if not match:
        raise ValueError("请使用 /user/profile/ 开头的作者主页链接。")
    params = parse_qs(parsed.query)
    return match.group(1), params.get("xsec_token", [""])[0], params.get("xsec_source", ["pc_feed"])[0]


async def fetch_creator_notes(client, value, limit):
    from integration.notes_worker import normalize_note
    creator, token, source = parse_creator_url(value)
    cursor, cursors, notes = "", set(), {}
    for _ in range(5):
        response = await client.get_notes_by_creator(creator, cursor, xsec_token=token, xsec_source=source)
        if not isinstance(response, dict) or not isinstance(response.get("notes"), list):
            raise NotesFailure("notes_response")
        for card in response["notes"]:
            note_id = card.get("note_id") or card.get("id")
            note = normalize_note(card, note_id)
            note["creator_id"] = creator
            notes[note_id] = (note, str(card.get("xsec_token") or ""), str(card.get("xsec_source") or "pc_user"))
            if len(notes) >= limit:
                return list(notes.values())
        if response.get("has_more") is False:
            return list(notes.values())
        next_cursor = response.get("cursor")
        if not next_cursor or next_cursor in cursors or next_cursor == cursor:
            raise NotesFailure("notes_pagination")
        cursors.add(next_cursor)
        cursor = next_cursor
        await asyncio.sleep(1.2)
    return list(notes.values())


def valid_media_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or parsed.port not in (None,80,443)
            or not (host == "xhscdn.com" or host.endswith(".xhscdn.com"))):
        raise ValueError("不支持的媒体来源。")
    return url


async def limit_media_response(response, limit):
    import httpx
    class LimitedStream(httpx.AsyncByteStream):
        def __init__(self, stream, limit):
            self.stream, self.limit = stream, limit
        async def __aiter__(self):
            count = 0
            async for chunk in self.stream:
                count += len(chunk)
                if count > self.limit:
                    raise NotesFailure("notes_media_size")
                yield chunk
        async def aclose(self):
            await self.stream.aclose()
    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
        raise NotesFailure("notes_media_failed")
    if int(response.headers.get("content-length", "0")) > limit:
        raise NotesFailure("notes_media_size")
    response.stream = LimitedStream(response.stream, limit)


async def download_media(note, data_root, store):
    import httpx
    from media_downloader import MediaDownloader
    from media_platform.xhs.media import build_media_items
    note_id = valid_note_id(note.get("note_id"))
    items = build_media_items(note)
    if not items:
        raise NotesFailure("notes_no_media")
    if len(items) > 30:
        raise NotesFailure("notes_media_limit")
    base = data_root / "notes" / "media"
    downloader = MediaDownloader("xhs", base_dir=base, max_retries=0, max_candidates=2,
                                 extra_headers={"Referer": "https://www.xiaohongshu.com/"})
    failure, file_limit = {}, 64 * 1024 * 1024
    async def guard(request):
        valid_media_url(str(request.url))
    async def limit_response(response):
        # Enforce on redirect hops too; no account Cookie is sent to CDN hosts.
        try:
            await limit_media_response(response, file_limit)
        except NotesFailure as error:
            failure["code"] = str(error)
            raise
    saved = 0
    async with httpx.AsyncClient(timeout=45, follow_redirects=True, max_redirects=5,
                                 event_hooks={"request": [guard], "response": [limit_response]}) as client:
        for item in items[:30]:
            file_limit = (512 if item.media_type.value == "video" else 64) * 1024 * 1024
            failure.clear()
            valid_media_url(item.url)
            for url in item.backup_urls:
                valid_media_url(url)
            path = await downloader.download(item, client=client)
            if path is None:
                raise NotesFailure(failure.get("code", "notes_media_failed"))
            expected = (base / "xhs" / "media" / note_id).resolve()
            if not path.resolve().is_relative_to(expected):
                raise NotesFailure("notes_media_failed")
            store.save_media(note_id, path.name, item.media_type.value, path.stat().st_size)
            saved += 1
            print('notes_progress={"stage":"media","count":' + str(saved) + '}', flush=True)
    return saved
