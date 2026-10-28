#!/usr/bin/env python3
"""Validate and normalize one Xiaohongshu group-chat messages.json export."""

from __future__ import annotations

import argparse
import csv
import json
import os
import stat
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from xhs_ext.private_paths import ensure_private_dir
from xhs_ext.group_chat_exporter import safe_csv_value


TEXT_TYPES = {0, 1, 3, 8, 14}
MEDIA_KIND = {2: "image", 9: "voice", 11: "video", 13: "sticker", 16: "sticker", 20: "file"}


def parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def outer_content(message: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_json(message.get("content"))
    return parsed if isinstance(parsed, dict) else {}


def useful_text(outer: dict[str, Any]) -> str:
    content_type = outer.get("content_type")
    raw = outer.get("content", "")
    inner = parse_json(raw)
    if isinstance(inner, dict):
        candidates = [
            inner.get("content"),
            inner.get("title"),
            inner.get("frontChain"),
            inner.get("topicName"),
            inner.get("desc"),
            inner.get("fileName"),
            inner.get("searchContent"),
        ]
        values = [
            str(v).strip()
            for v in candidates
            if isinstance(v, (str, int, float)) and str(v).strip()
        ]
        if values:
            return " | ".join(dict.fromkeys(values))
    if content_type in {1, 8, 14, 15} and isinstance(raw, str) and raw.strip():
        return raw.strip()
    front = outer.get("front_chain")
    if isinstance(front, str) and front.strip() and front.strip() != "[消息] 点击查看":
        return front.strip()
    if content_type == 0 and isinstance(raw, str) and raw.strip() and inner is None:
        return raw.strip()
    return ""


def collect_urls(value: Any, found: list[str]) -> None:
    if len(found) >= 20:
        return
    if isinstance(value, dict):
        for item in value.values():
            collect_urls(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_urls(item, found)
    elif isinstance(value, str) and value.startswith(("http://", "https://")) and value not in found:
        found.append(value)


def timestamp_parts(raw_ms: Any, timezone: ZoneInfo) -> tuple[str, str]:
    try:
        value = int(raw_ms)
    except (TypeError, ValueError):
        return "", ""
    dt = datetime.fromtimestamp(value / 1000, timezone)
    return dt.isoformat(sep=" ", timespec="seconds"), dt.date().isoformat()


def normalize(message: dict[str, Any], group_name: str, group_id: str, timezone: ZoneInfo) -> dict[str, Any]:
    outer = outer_content(message)
    ref = message.get("ref_message")
    if isinstance(ref, str):
        ref = parse_json(ref)
    ref = ref if isinstance(ref, dict) else {}
    ref_outer = outer_content(ref) if ref else {}
    message_type = outer.get("content_type", "")
    try:
        message_type = int(message_type)
    except (TypeError, ValueError):
        pass
    timestamp, date = timestamp_parts(message.get("created_at"), timezone)
    urls: list[str] = []
    collect_urls(parse_json(outer.get("content")), urls)
    return {
        "group": group_name,
        "group_id": group_id,
        "store_id": message.get("store_id", ""),
        "message_id": message.get("id", ""),
        "uuid": message.get("uuid", ""),
        "timestamp_ms": message.get("created_at", ""),
        "timestamp": timestamp,
        "date": date,
        "sender_id": message.get("sender_id", ""),
        "nickname": outer.get("nickname", ""),
        "message_type": message_type,
        "media_kind": MEDIA_KIND.get(message_type, ""),
        "text": useful_text(outer),
        "front_chain": outer.get("front_chain", ""),
        "media_urls": "\n".join(urls),
        "revoked": bool(message.get("revoked")),
        "reply_to_sender_id": ref.get("sender_id", ""),
        "reply_to_nickname": ref_outer.get("nickname", ""),
        "reply_to_store_id": ref.get("store_id", ""),
        "reply_to_message_id": ref.get("id", ""),
        "quoted_text": useful_text(ref_outer),
    }


def secure_write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--group-name")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_csv = args.output_dir / "normalized_messages.csv"
    output_summary = args.output_dir / "source_summary.json"
    if not args.overwrite and (output_csv.exists() or output_summary.exists()):
        print("Refusing to replace derived outputs without --overwrite", file=sys.stderr)
        return 2

    try:
        payload = json.loads(args.messages_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Cannot parse {args.messages_json}: {exc}", file=sys.stderr)
        return 2

    if isinstance(payload, dict):
        messages = payload.get("messages")
        group_id = str(payload.get("group_id") or "")
        declared_count = payload.get("message_count")
    else:
        messages = payload
        group_id = ""
        declared_count = None
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        print("Expected a JSON object with a messages array, or a top-level message array", file=sys.stderr)
        return 2
    if declared_count is not None and int(declared_count) != len(messages):
        print(f"Declared message_count={declared_count}, actual={len(messages)}", file=sys.stderr)
        return 2

    store_ids = [str(m.get("store_id")) for m in messages if m.get("store_id") not in (None, "")]
    duplicate_store_ids = len(store_ids) - len(set(store_ids))
    if duplicate_store_ids:
        print(f"Duplicate store_id values: {duplicate_store_ids}", file=sys.stderr)
        return 2

    try:
        timezone = ZoneInfo(args.timezone)
    except Exception as exc:
        print(f"Invalid timezone {args.timezone}: {exc}", file=sys.stderr)
        return 2
    group_name = args.group_name or args.messages_json.parent.name or group_id or "group"
    rows = [normalize(m, group_name, group_id, timezone) for m in messages]
    def numeric_sort(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    rows.sort(key=lambda r: (numeric_sort(r["timestamp_ms"]), numeric_sort(r["store_id"])))

    ensure_private_dir(args.output_dir)
    fieldnames = list(rows[0]) if rows else list(normalize({}, group_name, group_id, timezone))
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: safe_csv_value(value) for key, value in row.items()} for row in rows)
    os.chmod(output_csv, 0o600)

    human_text = [
        row for row in rows
        if row["text"]
        and row["message_type"] in TEXT_TYPES
        and row["sender_id"] != group_id
    ]
    type_counts = Counter(str(row["message_type"]) for row in rows)
    checkpoint_path = args.checkpoint
    if checkpoint_path is None:
        candidate = args.messages_json.parent / "checkpoint.json"
        checkpoint_path = candidate if candidate.exists() else None
    checkpoint: Any = None
    if checkpoint_path:
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except Exception as exc:
            checkpoint = {"parse_error": str(exc), "path": str(checkpoint_path)}

    summary = {
        "source": str(args.messages_json.resolve()),
        "source_file_mode": oct(stat.S_IMODE(args.messages_json.stat().st_mode)),
        "source_permissions_private": (stat.S_IMODE(args.messages_json.stat().st_mode) & 0o077) == 0,
        "group": group_name,
        "group_id": group_id,
        "messages": len(rows),
        "declared_message_count": declared_count,
        "unique_store_ids": len(set(store_ids)),
        "messages_missing_store_id": len(rows) - len(store_ids),
        "earliest": rows[0]["timestamp"] if rows else "",
        "latest": rows[-1]["timestamp"] if rows else "",
        "readable_human_text_messages": len(human_text),
        "active_sender_ids": len({row["sender_id"] for row in human_text}),
        "direct_reply_messages": sum(bool(row["reply_to_sender_id"]) for row in rows),
        "image_messages": type_counts.get("2", 0),
        "voice_messages": type_counts.get("9", 0),
        "video_messages": type_counts.get("11", 0),
        "sticker_messages": type_counts.get("13", 0) + type_counts.get("16", 0),
        "file_messages": type_counts.get("20", 0),
        "revoked_messages": sum(bool(row["revoked"]) or row["message_type"] in {4, 15} for row in rows),
        "message_type_counts": dict(type_counts),
        "checkpoint": checkpoint,
        "semantic_gaps": ["image bodies", "voice transcripts", "video bodies", "revoked content", "private replies"],
    }
    secure_write_json(output_summary, summary)
    if not summary["source_permissions_private"]:
        print(
            f"Warning: supplied source permissions are {summary['source_file_mode']}; consider mode 600 for private chat data",
            file=sys.stderr,
        )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
