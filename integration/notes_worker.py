"""Read-only MediaCrawler adapter. No browser, publishing or account writes.

Calls MediaCrawler (NON-COMMERCIAL LEARNING LICENSE 1.1); see THIRD_PARTY_NOTICES.md.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from webapp.notes import NotesStore, valid_note_id

MC_COMMIT = "68d9ef1c1da22eab75940dfd7cec259907006538"
ALLOWED_HOSTS = {"www.xiaohongshu.com", "xiaohongshu.com", "xhslink.com", "www.xhslink.com"}


class NotesFailure(RuntimeError):
    pass


def validate_url(value):
    match = re.search(r"https?://[^\s<>\"\u3002]+", value)
    if not match:
        raise ValueError("请粘贴小红书笔记链接或分享文案。")
    url = match.group(0).rstrip("，。)）]")
    parsed = urlsplit(url)
    if (parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password
            or parsed.port not in (None, 80, 443) or len(url) > 4096):
        raise ValueError("只支持小红书笔记链接和 xhslink.com 分享链接。")
    return url


def parse_note_url(url):
    parsed = urlsplit(validate_url(url))
    match = re.fullmatch(r"/(?:explore|discovery/item)/([0-9a-f]{24})/?", parsed.path)
    if not match or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}:
        raise ValueError("请提供笔记详情链接，不能使用用户主页或群链接。")
    query = parse_qs(parsed.query)
    return match.group(1), query.get("xsec_token", [""])[0], query.get("xsec_source", ["pc_search"])[0]


async def resolve_note_url(url):
    import httpx
    current = validate_url(url)
    if urlsplit(current).hostname in {"www.xiaohongshu.com", "xiaohongshu.com"}:
        return parse_note_url(current)
    # Never send account credentials to a shared URL or follow an unchecked redirect.
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
        for _ in range(5):
            response = await http.get(current)
            if response.is_redirect and response.headers.get("location"):
                current = validate_url(urljoin(current, response.headers["location"]))
                if urlsplit(current).hostname in {"www.xiaohongshu.com", "xiaohongshu.com"}:
                    return parse_note_url(current)
            else:
                break
    raise NotesFailure("notes_link")


def progress(stage, count=0):
    print("notes_progress=" + json.dumps({"stage": stage, "count": count}), flush=True)


def timestamp(value):
    try:
        number = float(value or 0)
        return number / 1000 if number > 100000000000 else number
    except (TypeError, ValueError):
        return 0


def normalize_note(card, note_id):
    valid_note_id(note_id)
    user = card.get("user") or {}
    counts = card.get("interact_info") or {}
    return {"id": note_id, "title": str(card.get("title") or card.get("display_title") or "无标题笔记"),
            "description": str(card.get("desc") or ""), "author": str(user.get("nickname") or ""),
            "kind": str(card.get("type") or "normal"), "published_at": timestamp(card.get("time")),
            "likes": str(counts.get("liked_count") or "0"), "collects": str(counts.get("collected_count") or "0"),
            "comment_count": str(counts.get("comment_count") or "0")}


async def fetch_comments(client, note_id, token, limit, delay=1.2):
    cursor, seen_cursors, comments = "", set(), {}
    for _ in range(50):
        response = await client.get_note_comments(note_id, token, cursor=cursor)
        if not isinstance(response, dict) or not isinstance(response.get("comments"), list):
            raise NotesFailure("notes_response")
        for item in response["comments"]:
            if not isinstance(item, dict) or not item.get("id"):
                raise NotesFailure("notes_response")
            comment_id = str(item["id"])
            if comment_id not in comments and len(comments) >= limit:
                return list(comments.values()), False
            user = item.get("user_info") or {}
            comments[comment_id] = {"id": comment_id, "author": str(user.get("nickname") or ""),
                                    "content": str(item.get("content") or ""),
                                    "published_at": timestamp(item.get("create_time")),
                                    "likes": str(item.get("like_count") or "0"),
                                    "reply_count": int(item.get("sub_comment_count") or 0)}
        progress("comments", len(comments))
        if response.get("has_more") is False:
            return list(comments.values()), True
        if "has_more" not in response:
            raise NotesFailure("notes_response")
        if len(comments) >= limit:
            return list(comments.values()), False
        next_cursor = response.get("cursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise NotesFailure("notes_pagination")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        await asyncio.sleep(delay)
    raise NotesFailure("notes_pagination")


def create_client(root, cookie_file):
    revision = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if revision != MC_COMMIT:
        raise NotesFailure("notes_runtime")
    sys.path.insert(0, str(root))
    os.chdir(root)
    logging.disable(logging.CRITICAL)
    import config
    from media_platform.xhs import client as module
    config.XHS_INTERNATIONAL = False
    config.ENABLE_GET_SUB_COMMENTS = False
    config.DISABLE_SSL_VERIFY = False
    raw = cookie_file.read_text(encoding="utf-8-sig").strip()
    cookies = dict(part.strip().split("=", 1) for part in raw.split(";") if "=" in part)
    if not cookies.get("a1") or not cookies.get("web_session"):
        raise NotesFailure("notes_auth")
    original_factory = module.make_async_client
    async def check_response(response):
        if response.status_code in (401, 403):
            raise NotesFailure("notes_auth")
        if response.status_code in (429, 461, 471):
            raise NotesFailure("notes_blocked")
    def factory(**kwargs):
        kwargs["event_hooks"] = {"response": [check_response]}
        kwargs.setdefault("timeout", 20)
        return original_factory(**kwargs)
    module.make_async_client = factory
    client = module.XiaoHongShuClient(timeout=20, headers={
        "accept": "application/json, text/plain, */*", "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache", "content-type": "application/json;charset=UTF-8",
        "origin": "https://www.xiaohongshu.com", "pragma": "no-cache", "priority": "u=1, i",
        "referer": "https://www.xiaohongshu.com/",
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Cookie": raw,
    }, playwright_page=None, cookie_dict=cookies)
    # Stop on the first rejection; the caller can retry after inspecting the cause.
    client.request = types.MethodType(module.XiaoHongShuClient.request.__wrapped__, client)
    return client


async def run(args):
    request = json.loads(args.request.read_text(encoding="utf-8"))
    store = NotesStore(args.data / "notes")
    client = create_client(args.runtime, args.data / "auth" / "xhs-cookie.txt")
    progress("auth")
    info = await client.query_self()
    if not info or not (info.get("data") or {}).get("result", {}).get("success"):
        raise NotesFailure("notes_auth")
    if request["mode"] == "search":
        from media_platform.xhs.field import SearchSortType
        from media_platform.xhs.help import get_search_id
        found, seen, search_id = [], set(), get_search_id()
        progress("search")
        for page in range(1, 3):
            response = await client.get_note_by_keyword(request["keyword"], search_id=search_id, page=page,
                                                        sort=SearchSortType(request["sort"]))
            if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                raise NotesFailure("notes_response")
            for item in response["items"]:
                if item.get("model_type") != "note" or item.get("id") in seen:
                    continue
                card = item.get("note_card")
                if not isinstance(card, dict):
                    raise NotesFailure("notes_response")
                found.append((normalize_note(card, item["id"]), str(item.get("xsec_token") or ""),
                              str(item.get("xsec_source") or "pc_search")))
                seen.add(item["id"])
                if len(found) >= request["max_notes"]:
                    break
            if len(found) >= request["max_notes"] or not response.get("has_more"):
                break
            await asyncio.sleep(1.2)
        for note, token, source in found:
            store.save_note(note, token=token, source=source, keyword=request["keyword"])
        progress("search_saved", len(found))
        return
    if request.get("url"):
        note_id, token, source = await resolve_note_url(request["url"])
        if not token:
            try:
                access = store.access(note_id)
                token, source = access["token"], access["source"]
            except FileNotFoundError:
                pass
    else:
        note_id = request["note_id"]
        access = store.access(note_id)
        token, source = access["token"], access["source"]
    if not token:
        raise NotesFailure("notes_token")
    if request["mode"] == "detail":
        progress("detail")
        note = await client.get_note_by_id(note_id, source, token)
        if not note or note.get("note_id") != note_id:
            raise NotesFailure("notes_response")
        store.save_note(normalize_note(note, note_id), token=token, source=source, detailed=True)
    if request["max_comments"]:
        rows, complete = await fetch_comments(client, note_id, token, request["max_comments"])
        store.save_comments(note_id, rows, complete=complete)
        progress("comments_saved" if complete else "comments_limited", len(rows))
    else:
        progress("detail_saved", 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
        return 0
    except Exception as error:
        if isinstance(error, NotesFailure):
            code = str(error)
        else:
            code = {"NoteNotFoundError": "notes_missing", "IPBlockError": "notes_blocked",
                    "PlatformAccessError": "notes_blocked", "DataFetchError": "notes_access",
                    "ReadTimeout": "notes_network", "ConnectError": "notes_network"}.get(type(error).__name__, "notes_failed")
        print("error=" + code, file=sys.stderr, flush=True)
        return 1
    finally:
        args.request.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
