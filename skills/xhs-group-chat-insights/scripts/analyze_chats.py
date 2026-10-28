#!/usr/bin/env python3
"""Build cross-group topic, participant, and event-candidate tables."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from xhs_ext.private_paths import ensure_private_dir
from xhs_ext.group_chat_exporter import safe_csv_value


TEXT_TYPES = {"0", "1", "3", "8", "14"}
QUESTION_MARKERS = ["?", "？", "怎么办", "咋办", "有没有", "怎么", "求助", "请问", "能不能", "要不要"]
SUPPORT_MARKERS = ["抱抱", "加油", "没关系", "理解", "可以试试", "建议", "别怕", "辛苦", "陪你", "接住", "夸夸"]
UPDATE_MARKERS = ["后来", "现在", "今天", "已经", "决定", "完成", "收到", "去了", "回来", "结果", "入职", "离职"]


def parse_args() -> argparse.Namespace:
    default_taxonomy = Path(__file__).resolve().parent.parent / "references" / "topic-taxonomy.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--taxonomy", type=Path, default=default_taxonomy)
    parser.add_argument("--quiet-gap-minutes", type=int, default=30)
    parser.add_argument("--min-event-replies", type=int, default=2)
    parser.add_argument("--max-event-candidates", type=int, default=200)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"group", "group_id", "store_id", "timestamp", "sender_id", "nickname", "message_type", "text"}
    missing = required.difference(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
    return rows


def is_human_text(row: dict[str, str]) -> bool:
    return bool(row.get("text")) and row.get("message_type") in TEXT_TYPES and row.get("sender_id") != row.get("group_id")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def matched_topics(text: str, taxonomy: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    return [name for name, terms in taxonomy.items() if any(term.lower() in lowered for term in terms)]


def has_marker(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: safe_csv_value(value) for key, value in row.items()} for row in rows)
    os.chmod(path, 0o600)


def find_later_candidate(source: dict[str, str], human: list[dict[str, str]], source_topics: set[str], taxonomy: dict[str, list[str]]) -> str:
    source_time = parse_time(source.get("timestamp", ""))
    if source_time is None:
        return ""
    limit = source_time + timedelta(hours=48)
    for row in human:
        row_time = parse_time(row.get("timestamp", ""))
        if row_time is None or row_time <= source_time:
            continue
        if row_time > limit:
            break
        if row.get("sender_id") != source.get("sender_id"):
            continue
        row_topics = set(matched_topics(row.get("text", ""), taxonomy))
        if source_topics.intersection(row_topics) or has_marker(row.get("text", ""), UPDATE_MARKERS):
            text = " ".join(row.get("text", "").split())
            return f"{row.get('timestamp', '')} | {text[:240]}"
    return ""


def main() -> int:
    args = parse_args()
    outputs = [
        args.output_dir / "topic_table.csv",
        args.output_dir / "participant_table.csv",
        args.output_dir / "event_candidates.csv",
        args.output_dir / "analysis_summary.json",
    ]
    if not args.overwrite and any(path.exists() for path in outputs):
        print("Refusing to replace analysis outputs without --overwrite", file=sys.stderr)
        return 2
    try:
        taxonomy = json.loads(args.taxonomy.read_text(encoding="utf-8"))
        if not isinstance(taxonomy, dict) or not all(isinstance(v, list) for v in taxonomy.values()):
            raise ValueError("taxonomy must be an object of topic -> keyword list")
    except Exception as exc:
        print(f"Cannot load taxonomy {args.taxonomy}: {exc}", file=sys.stderr)
        return 2

    topic_rows: list[dict[str, Any]] = []
    participant_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    summary_groups: dict[str, Any] = {}
    overall_topics = Counter()
    overall_cooccurrence = Counter()
    overall_messages = 0

    for input_path in args.input:
        try:
            rows = read_rows(input_path)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
        rows.sort(key=lambda row: row.get("timestamp", ""))
        human = [row for row in rows if is_human_text(row)]
        if not human:
            continue
        group = human[0].get("group") or input_path.parent.name
        replies_by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            source_id = str(row.get("reply_to_store_id", ""))
            if source_id:
                replies_by_source[source_id].append(row)

        topic_messages: dict[str, list[dict[str, str]]] = defaultdict(list)
        cooccurrence = Counter()
        sender_text = Counter()
        sender_replies = Counter()
        sender_received = Counter()
        sender_starts = Counter()
        sender_targets: dict[str, set[str]] = defaultdict(set)
        sender_nicknames: dict[str, Counter[str]] = defaultdict(Counter)
        last_time: datetime | None = None
        question_count = 0
        support_count = 0

        for row in human:
            text = row.get("text", "")
            labels = matched_topics(text, taxonomy)
            for topic in labels:
                topic_messages[topic].append(row)
            for pair in itertools.combinations(sorted(set(labels)), 2):
                cooccurrence[pair] += 1
            sender_id = str(row.get("sender_id") or "")
            sender = sender_id or f"missing:{row.get('nickname') or 'unknown'}"
            nickname = str(row.get("nickname") or "").strip()
            if nickname:
                sender_nicknames[sender][nickname] += 1
            sender_text[sender] += 1
            question_count += has_marker(text, QUESTION_MARKERS)
            support_count += has_marker(text, SUPPORT_MARKERS)
            dt = parse_time(row.get("timestamp", ""))
            if dt and (last_time is None or (dt - last_time).total_seconds() >= args.quiet_gap_minutes * 60):
                sender_starts[sender] += 1
            if dt:
                last_time = dt
            if row.get("reply_to_sender_id"):
                target = str(row.get("reply_to_sender_id"))
                sender_replies[sender] += 1
                sender_received[target] += 1
                sender_targets[sender].add(target)

        for topic in taxonomy:
            matched = topic_messages.get(topic, [])
            if not matched:
                continue
            source_ids = {str(row.get("store_id", "")) for row in matched if row.get("store_id")}
            direct_replies = sum(len(replies_by_source.get(source_id, [])) for source_id in source_ids)
            topic_rows.append({
                "group": group,
                "topic": topic,
                "matched_messages": len(matched),
                "share_of_readable_text_percent": round(len(matched) * 100 / len(human), 2),
                "unique_sender_ids": len({row.get("sender_id") for row in matched}),
                "first_timestamp": matched[0].get("timestamp", ""),
                "last_timestamp": matched[-1].get("timestamp", ""),
                "direct_replies_to_matched_messages": direct_replies,
            })
            overall_topics[topic] += len(matched)

        participants = sorted(sender_text, key=lambda sender: (-sender_text[sender], sender))
        for sender in participants:
            nickname_counts = sender_nicknames[sender]
            nickname_variants = sorted(
                nickname_counts,
                key=lambda name: (-nickname_counts[name], name),
            )
            display_nickname = nickname_variants[0] if nickname_variants else ""
            roles = []
            if sender_starts[sender] >= 3:
                roles.append("conversation_starter")
            if sender_replies[sender] >= 5:
                roles.append("persistent_responder")
            if len(sender_targets[sender]) >= 5:
                roles.append("broad_responder")
            if sender_received[sender] >= 5:
                roles.append("frequently_replied_to")
            participant_rows.append({
                "group": group,
                "sender_id": sender,
                "display_nickname": display_nickname,
                "nickname_variants": ";".join(nickname_variants),
                "nickname_or_sender": display_nickname or sender,
                "text_messages": sender_text[sender],
                "conversation_starts_after_quiet_gap": sender_starts[sender],
                "direct_replies_written": sender_replies[sender],
                "unique_people_replied_to": len(sender_targets[sender]),
                "direct_replies_received": sender_received[sender],
                "candidate_roles": ";".join(roles),
            })

        candidates = []
        for row in human:
            source_id = str(row.get("store_id", ""))
            direct = replies_by_source.get(source_id, [])
            question = has_marker(row.get("text", ""), QUESTION_MARKERS)
            if len(direct) < args.min_event_replies and not question:
                continue
            labels = matched_topics(row.get("text", ""), taxonomy)
            candidates.append((len(direct), bool(labels), len(row.get("text", "")), row, direct, labels))
        candidates.sort(key=lambda item: (-item[0], -int(item[1]), -item[2], item[3].get("timestamp", "")))
        for reply_count, _, _, row, direct, labels in candidates[: args.max_event_candidates]:
            reply_times = [parse_time(item.get("timestamp", "")) for item in direct]
            reply_times = sorted(dt for dt in reply_times if dt)
            event_rows.append({
                "group": group,
                "timestamp": row.get("timestamp", ""),
                "store_id": row.get("store_id", ""),
                "nickname": row.get("nickname") or row.get("sender_id"),
                "topics": ";".join(labels),
                "question_or_help_marker": has_marker(row.get("text", ""), QUESTION_MARKERS),
                "direct_reply_count": reply_count,
                "unique_direct_repliers": len({item.get("sender_id") for item in direct if item.get("sender_id")}),
                "first_direct_reply": reply_times[0].isoformat(sep=" ", timespec="seconds") if reply_times else "",
                "last_direct_reply": reply_times[-1].isoformat(sep=" ", timespec="seconds") if reply_times else "",
                "text": row.get("text", ""),
                "possible_later_same_sender_message": find_later_candidate(row, human, set(labels), taxonomy),
                "review_note": "Context and later state require manual verification",
            })

        group_topic_counts = {topic: len(items) for topic, items in topic_messages.items()}
        summary_groups[group] = {
            "source": str(input_path.resolve()),
            "readable_human_text_messages": len(human),
            "active_sender_ids": len({row.get("sender_id") for row in human}),
            "first_readable_text_timestamp": human[0].get("timestamp", ""),
            "last_readable_text_timestamp": human[-1].get("timestamp", ""),
            "question_or_help_marked_messages": question_count,
            "support_marked_messages": support_count,
            "eligible_event_candidates": len(candidates),
            "selected_event_candidates": min(len(candidates), args.max_event_candidates),
            "topic_counts": dict(sorted(group_topic_counts.items(), key=lambda item: (-item[1], item[0]))),
            "top_topic_cooccurrences": [
                {"topics": list(pair), "messages": count}
                for pair, count in cooccurrence.most_common(20)
            ],
            "note": "Topic labels are multi-label keyword matches; event candidates require contextual review.",
        }
        overall_messages += len(human)
        overall_cooccurrence.update(cooccurrence)

    ensure_private_dir(args.output_dir)
    write_csv(
        outputs[0],
        ["group", "topic", "matched_messages", "share_of_readable_text_percent", "unique_sender_ids", "first_timestamp", "last_timestamp", "direct_replies_to_matched_messages"],
        topic_rows,
    )
    write_csv(
        outputs[1],
        ["group", "sender_id", "display_nickname", "nickname_variants", "nickname_or_sender", "text_messages", "conversation_starts_after_quiet_gap", "direct_replies_written", "unique_people_replied_to", "direct_replies_received", "candidate_roles"],
        participant_rows,
    )
    write_csv(
        outputs[2],
        ["group", "timestamp", "store_id", "nickname", "topics", "question_or_help_marker", "direct_reply_count", "unique_direct_repliers", "first_direct_reply", "last_direct_reply", "text", "possible_later_same_sender_message", "review_note"],
        event_rows,
    )
    summary = {
        "inputs": [str(path.resolve()) for path in args.input],
        "taxonomy": str(args.taxonomy.resolve()),
        "groups": summary_groups,
        "overall": {
            "readable_human_text_messages": overall_messages,
            "topic_counts": dict(overall_topics.most_common()),
            "topic_share_percent": {
                topic: round(count * 100 / overall_messages, 2) if overall_messages else 0
                for topic, count in overall_topics.items()
            },
            "top_topic_cooccurrences": [
                {"topics": list(pair), "messages": count}
                for pair, count in overall_cooccurrence.most_common(30)
            ],
            "counting_note": "Multi-label keyword evidence; topic counts are not mutually exclusive.",
        },
        "event_candidate_settings": {
            "minimum_direct_replies_or_question_marker": args.min_event_replies,
            "maximum_candidates_per_group": args.max_event_candidates,
            "selected_candidates": len(event_rows),
            "later_message_warning": "possible_later_same_sender_message is an unverified retrieval hint and may be unrelated.",
        },
    }
    outputs[3].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(outputs[3], 0o600)
    print(json.dumps({"groups": len(summary_groups), "readable_messages": overall_messages, "outputs": [str(p) for p in outputs]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
