"""Pagination and file export helpers for Xiaohongshu group chat history."""

import csv
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .private_paths import ensure_private_dir


CHAT_PATH_RE = re.compile(r"^/chat/(\d+)/?$")
GROUP_ID_RE = re.compile(r"^\d+$")
PREFERRED_CSV_COLUMNS = (
    "store_id",
    "message_id",
    "msg_id",
    "group_id",
    "sender_id",
    "type",
    "message_type",
    "create_time",
    "timestamp",
    "content",
)


def parse_group_id(chat_url_or_id: str) -> str:
    """Return a numeric group id from a chat URL or a bare id."""
    value = str(chat_url_or_id).strip()
    if GROUP_ID_RE.fullmatch(value):
        return value

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("chat URL must use http or https")
    if parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}:
        raise ValueError("chat URL must be on xiaohongshu.com")
    match = CHAT_PATH_RE.fullmatch(parsed.path)
    if not match:
        raise ValueError("expected a URL like https://www.xiaohongshu.com/chat/<group_id>")
    return match.group(1)


def read_cookie_file(cookie_file: Path) -> str:
    """Read a raw Cookie request header without ever logging its value."""
    path = Path(cookie_file).expanduser()
    value = path.read_text(encoding="utf-8-sig").strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    value = " ".join(part.strip() for part in value.splitlines() if part.strip())
    if not value:
        raise ValueError("cookie file is empty")
    cookie_names = {
        item.split("=", 1)[0].strip()
        for item in value.split(";")
        if "=" in item
    }
    if "a1" not in cookie_names:
        raise ValueError("cookie file must contain the a1 cookie")
    return value


def cookie_file_is_private(cookie_file: Path) -> bool:
    """Return whether POSIX permissions prevent group/other access."""
    if os.name != "posix":
        return True
    mode = stat.S_IMODE(Path(cookie_file).expanduser().stat().st_mode)
    return mode & 0o077 == 0


def extract_message_list(response_json: dict) -> List[dict]:
    data = (response_json or {}).get("data") or {}
    if not isinstance(data, dict) or "out_message_list" not in data:
        raise ValueError("history response is missing data.out_message_list")
    messages = data.get("out_message_list") or []
    if not isinstance(messages, list):
        raise ValueError("data.out_message_list is not a list")
    if not all(isinstance(message, dict) for message in messages):
        raise ValueError("data.out_message_list contains a non-object item")
    return messages


def message_identity(message: dict) -> str:
    """Build a stable identity while avoiding hashes of private message bodies."""
    for key in ("store_id", "message_id", "msg_id", "id"):
        value = message.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    raise ValueError("message has no stable id field")


def oldest_store_id(messages: Iterable[dict]) -> str:
    store_ids = [
        str(item["store_id"])
        for item in messages
        if item.get("store_id") not in (None, "")
    ]
    if not store_ids:
        raise ValueError("history page has no store_id cursor")
    try:
        return str(min(int(value) for value in store_ids))
    except ValueError:
        return store_ids[-1]


def _store_id_sort_key(message: dict):
    value = message.get("store_id")
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value or ""))


def chronological_messages(messages: Iterable[dict]) -> List[dict]:
    return sorted(messages, key=_store_id_sort_key)


def fetch_group_history(
    api,
    group_id: str,
    cookies_str: str,
    initial_last_id: str = "0",
    max_pages: int = 10,
    limit: int = 30,
    delay: float = 1.2,
    proxies: dict = None,
    initial_messages: Optional[Iterable[dict]] = None,
    stop_at_known_page: bool = False,
    on_page: Optional[Callable[[dict], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Fetch a bounded number of pages and return resumable state."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    if delay < 0:
        raise ValueError("delay cannot be negative")

    collected = []
    seen_ids = set()
    for message in initial_messages or []:
        identity = message_identity(message)
        if identity not in seen_ids:
            seen_ids.add(identity)
            collected.append(message)

    cursor = str(initial_last_id)
    seen_cursors = set()
    pages_fetched = 0
    completed = False

    for page_index in range(max_pages):
        if cursor in seen_cursors:
            raise RuntimeError(f"pagination cursor repeated: {cursor}")
        seen_cursors.add(cursor)

        success, message, response_json = api.get_group_history_page(
            group_id=group_id,
            last_id=cursor,
            cookies_str=cookies_str,
            start_id="0",
            limit=limit,
            proxies=proxies,
        )
        if not success or response_json is None:
            raise RuntimeError(message or "group history request failed")

        page_messages = extract_message_list(response_json)
        pages_fetched += 1
        if not page_messages:
            completed = True
            break

        page_is_fully_known = bool(page_messages) and all(
            message_identity(item) in seen_ids for item in page_messages
        )

        for item in page_messages:
            identity = message_identity(item)
            if identity not in seen_ids:
                seen_ids.add(identity)
                collected.append(item)

        next_cursor = oldest_store_id(page_messages)
        if next_cursor == cursor:
            # The endpoint can return the boundary message inclusively. A page
            # containing only that item means there is nothing older to fetch.
            if len(page_messages) == 1:
                completed = True
                cursor = next_cursor
                break
            raise RuntimeError(f"pagination did not advance from cursor {cursor}")
        cursor = next_cursor

        if stop_at_known_page and page_is_fully_known:
            completed = True
            break

        state = {
            "group_id": group_id,
            "next_last_id": cursor,
            "pages_fetched": pages_fetched,
            "completed": False,
            "messages": chronological_messages(collected),
        }
        if on_page:
            on_page(state)

        if len(page_messages) < limit:
            completed = True
            break
        if page_index + 1 < max_pages and delay:
            sleep(delay)

    return {
        "group_id": group_id,
        "next_last_id": cursor,
        "pages_fetched": pages_fetched,
        "completed": completed,
        "messages": chronological_messages(collected),
    }


def safe_csv_value(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _json_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    return safe_csv_value(value)


def csv_columns(messages: Iterable[dict]) -> List[str]:
    keys = set()
    for message in messages:
        keys.update(message.keys())
    preferred = [key for key in PREFERRED_CSV_COLUMNS if key in keys]
    return preferred + sorted(keys.difference(preferred))


def _atomic_text_write(path: Path, text: str):
    ensure_private_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    if os.name == "posix":
        temporary.chmod(0o600)
    os.replace(temporary, path)
    if os.name == "posix":
        path.chmod(0o600)


def write_export(state: dict, output_dir: Path) -> Dict[str, Path]:
    """Write full-fidelity JSON, spreadsheet-friendly CSV, and a checkpoint."""
    target = Path(output_dir).expanduser().resolve() / str(state["group_id"])
    ensure_private_dir(target)
    json_path = target / "messages.json"
    csv_path = target / "messages.csv"
    checkpoint_path = target / "checkpoint.json"

    export_payload = {
        "group_id": state["group_id"],
        "message_count": len(state["messages"]),
        "pages_fetched": state["pages_fetched"],
        "completed": state["completed"],
        "next_last_id": state["next_last_id"],
        "messages": state["messages"],
    }
    _atomic_text_write(
        json_path,
        json.dumps(export_payload, ensure_ascii=False, indent=2) + "\n",
    )

    columns = csv_columns(state["messages"])
    temporary_csv = csv_path.with_suffix(".csv.tmp")
    file_descriptor = os.open(
        temporary_csv, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    with os.fdopen(
        file_descriptor, "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        if columns:
            writer.writeheader()
            for message in state["messages"]:
                writer.writerow({key: _json_value(message.get(key)) for key in columns})
    os.replace(temporary_csv, csv_path)
    if os.name == "posix":
        csv_path.chmod(0o600)

    checkpoint = {
        "group_id": state["group_id"],
        "next_last_id": state["next_last_id"],
        "pages_fetched": state["pages_fetched"],
        "completed": state["completed"],
    }
    _atomic_text_write(
        checkpoint_path,
        json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
    )
    return {"json": json_path, "csv": csv_path, "checkpoint": checkpoint_path}


def load_resume_state(output_dir: Path, group_id: str) -> dict:
    target = Path(output_dir).expanduser().resolve() / str(group_id)
    json_path = target / "messages.json"
    checkpoint_path = target / "checkpoint.json"
    if not json_path.exists() or not checkpoint_path.exists():
        raise FileNotFoundError("resume requires messages.json and checkpoint.json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if str(payload.get("group_id")) != str(group_id) or str(
        checkpoint.get("group_id")
    ) != str(group_id):
        raise ValueError("resume files belong to a different group")
    return {
        "messages": payload.get("messages") or [],
        "next_last_id": str(checkpoint["next_last_id"]),
        "pages_fetched": int(checkpoint.get("pages_fetched", 0)),
        "completed": bool(checkpoint.get("completed")),
    }
