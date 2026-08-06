"""对话历史持久化 - 从 narrator raw JSONL 文件读写消息"""

import glob
import json
import os
from collections.abc import Iterator
from datetime import datetime

from models.dates import canonical_cn_date
from repository.config import character_path
from repository.narrator_output import extract_narrator_output, raw_message_text


def _iter_jsonl(filepath: str) -> Iterator[dict]:
    """逐行解析 JSONL，自动跳过空行与解析失败的行。"""
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                continue


def load_conversation_history(
    *,
    limit: int | None = None,
    turns: int | None = None,
) -> list:
    """从 narrator 的 raw/ 目录加载最近的对话历史（跨所有日期的 jsonl 文件）。

    narrator 拥有上帝视角，包含所有消息。返回原始消息列表（未过滤、未格式化）。

    Args:
        limit: 按消息数返回最近 N 条（UI 显示等场景）。
        turns: 按 turn 号返回最近 N 个 turn 的所有消息（LLM 上下文场景，基于 `message['turn']`）。
        都不传 → 返回全部历史；同时传 → 抛 ValueError（语义冲突）。

    Returns:
        消息列表，按时间正序。每条是 dict（含 role / content / visible_to / turn 等字段）。
    """
    if limit is not None and turns is not None:
        raise ValueError("load_conversation_history: limit 与 turns 互斥")

    raw_dir = character_path("narrator", "raw")
    if not os.path.exists(raw_dir):
        return []

    jsonl_files = sorted(glob.glob(f"{raw_dir}/*.jsonl"))
    if not jsonl_files:
        return []

    if (limit is not None and limit <= 0) or (turns is not None and turns <= 0):
        return []

    # 从最新日期往回读，达成上限即停，避免每轮全量扫旧日志
    collected: list = []
    threshold_turn: int | None = None
    for filepath in reversed(jsonl_files):
        file_messages: list = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    file_messages.append(json.loads(stripped))
                except json.JSONDecodeError:
                    continue
        if not file_messages:
            continue

        if turns is not None and threshold_turn is None:
            max_turn = max((int(m.get("turn") or 0) for m in file_messages), default=0)
            threshold_turn = max(0, max_turn - turns + 1)

        collected = file_messages + collected

        if limit is not None and len(collected) >= limit:
            return collected[-limit:]
        if turns is not None and threshold_turn is not None:
            first_turn = min((int(m.get("turn") or 0) for m in file_messages), default=0)
            if first_turn <= threshold_turn:
                break

    if turns is not None and threshold_turn is not None:
        return [m for m in collected if int(m.get("turn") or 0) >= threshold_turn]
    return collected


def load_history_before(*, before_turn: int, limit: int) -> list[dict]:
    """游标分页：加载 turn < before_turn 的最近 limit 条消息（按时间正序返回）。

    用于前端聊天框无限上滑加载更早消息，游标取自当前已加载的最旧 turn。
    """
    if limit <= 0 or before_turn <= 1:
        return []

    raw_dir = character_path("narrator", "raw")
    if not os.path.exists(raw_dir):
        return []

    jsonl_files = sorted(glob.glob(f"{raw_dir}/*.jsonl"))
    if not jsonl_files:
        return []

    collected: list = []
    for filepath in reversed(jsonl_files):
        file_messages = [
            msg for msg in _iter_jsonl(filepath)
            if int(msg.get("turn") or 0) < before_turn
        ]
        collected = file_messages + collected
        if len(collected) >= limit:
            return collected[-limit:]
    return collected


_anchors_cache: dict[str, tuple[tuple[float, int], list[dict]]] = {}


def search_history(query: str, *, limit: int) -> list[dict]:
    """子串搜索（不区分大小写），按时间倒序返回最多 limit 条匹配消息。"""
    if not query or limit <= 0:
        return []

    raw_dir = character_path("narrator", "raw")
    if not os.path.exists(raw_dir):
        return []

    q_lower = query.lower()
    matches: list[dict] = []
    for filepath in sorted(glob.glob(f"{raw_dir}/*.jsonl"), reverse=True):
        file_msgs = [
            msg for msg in _iter_jsonl(filepath)
            if q_lower in raw_message_text(msg).lower()
        ]
        for msg in reversed(file_msgs):
            matches.append(msg)
            if len(matches) >= limit:
                return matches
    return matches


def extract_game_date_anchors() -> list[dict]:
    """游戏内日期段（按出现顺序），同一日期中途切走又切回会出现多个段。

    依赖 narrator 消息正文出现 `X月X日` 字样。按 (newest_mtime, file_count)
    缓存：narrator raw JSONL 是 append-only，新 turn 落盘会刷新最新文件 mtime
    天然失效缓存。
    """
    raw_dir = character_path("narrator", "raw")
    if not os.path.exists(raw_dir):
        return []

    jsonl_files = sorted(glob.glob(f"{raw_dir}/*.jsonl"))
    if not jsonl_files:
        return []

    cache_key = (max(os.path.getmtime(f) for f in jsonl_files), len(jsonl_files))
    cached = _anchors_cache.get(raw_dir)
    if cached and cached[0] == cache_key:
        return cached[1]

    anchors: list[dict] = []
    current: dict | None = None
    for filepath in jsonl_files:
        for msg in _iter_jsonl(filepath):
            if msg.get("role") != "narrator":
                continue
            turn = int(msg.get("turn") or 0)
            if turn <= 0:
                continue
            payload = extract_narrator_output(msg)
            date_source = payload.get("date") if payload else msg.get("content")
            date = canonical_cn_date(str(date_source or ""))
            if not date:
                continue
            if current is None or current["date"] != date:
                current = {"date": date, "first_turn": turn, "last_turn": turn}
                anchors.append(current)
            else:
                current["last_turn"] = turn

    _anchors_cache[raw_dir] = (cache_key, anchors)
    return anchors


async def append_message(message: dict) -> None:
    """将一条消息追加到 narrator 的 raw JSONL 文件（单一数据源）。"""
    date = datetime.now().strftime("%Y-%m-%d")
    raw_path = character_path("narrator", "raw", f"{date}.jsonl")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    with open(raw_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")
