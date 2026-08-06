"""memory.jsonl / understanding.jsonl 的 JSONL 持久化。

机制层：负责领域实体（models）与磁盘 JSONL 之间的读写，不含检索 / 整理策略。
"""

import json
import re
import uuid
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from models import EpisodeMemory, Understanding
from repository.config import character_path


def normalize(content: str) -> str:
    """修复记忆/状态文本常见格式问题：字面 \\n、日期标题不规范、字段标签、多余空行。"""
    content = content.replace("\\n", "\n")
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    out = []
    for line in content.split("\n"):
        stripped = line.strip()
        m = re.match(r"^(?:#{1,2}\s*|\*\*)?(\d{1,2}月\d{1,2}日)(?:\*\*)?(?:\s.*)?$", stripped)
        if m:
            out.append(f"## {m.group(1)}")
        elif re.match(
            r"^(?:\*\*(时间|地点|在场|关键词|重要度|内容)\*\*|(时间|地点|在场|关键词|重要度|内容))：",
            stripped,
        ):
            out.append(f"- {stripped}")
        else:
            out.append(line)
    content = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return content.strip()


# ===== memory.jsonl 读写 =====


def memory_jsonl_path(agent_name: str) -> Path:
    """返回角色的 memory.jsonl 路径（不负责存在性检查）。"""
    return Path(character_path(agent_name, "memory.jsonl"))


def parse_jsonl_line(line: str) -> EpisodeMemory | None:
    """解析一行 JSONL 为 EpisodeMemory；格式非法返回 None。"""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        return EpisodeMemory.model_validate_json(stripped)
    except ValidationError:
        return None


def serialize_episode(episode: EpisodeMemory) -> str:
    """将 EpisodeMemory 序列化为 JSONL 一行（不含换行）。"""
    return episode.model_dump_json()


def read_memory_jsonl(agent_name: str) -> list[EpisodeMemory]:
    """按文件顺序读取 memory.jsonl；不存在或全空返回空列表；非法行跳过。"""
    path = memory_jsonl_path(agent_name)
    if not path.exists():
        return []
    records: list[EpisodeMemory] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            record = parse_jsonl_line(line)
            if record is not None:
                records.append(record)
    return records


def append_memory_records(
    agent_name: str,
    records: Iterable[EpisodeMemory],
) -> list[EpisodeMemory]:
    """将一组 EpisodeMemory 追加写入 memory.jsonl。

    Returns:
        实际写入的记录列表（过滤掉 content 为空的条目，保持原顺序）。
    """
    valid = [
        r.model_copy(
            update={
                "memory_owner": agent_name,
                "id": r.id or uuid.uuid4().hex,
            }
        )
        for r in records
        if r.content.strip()
    ]
    if not valid:
        return []
    path = memory_jsonl_path(agent_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for record in valid:
            f.write(serialize_episode(record) + "\n")
    return valid


# ===== understanding.jsonl 读写 =====


def understanding_jsonl_path(agent_name: str) -> Path:
    """返回角色的 understanding.jsonl 路径（不负责存在性检查）。"""
    return Path(character_path(agent_name, "understanding.jsonl"))


def read_understandings(agent_name: str) -> dict[str, Understanding]:
    """读取 understanding.jsonl，返回 {id: Understanding}；不存在返回空字典；非法行跳过。"""
    path = understanding_jsonl_path(agent_name)
    if not path.exists():
        return {}
    result: dict[str, Understanding] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                u = Understanding.model_validate_json(stripped)
                result[u.id] = u
            except ValidationError:
                pass
    return result


def write_understandings(
    agent_name: str,
    understandings: dict[str, Understanding],
) -> None:
    """全量写回 understanding.jsonl；空字典时删除文件。"""
    path = understanding_jsonl_path(agent_name)
    if not understandings:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for u in understandings.values():
            record = u.model_copy(update={"memory_owner": agent_name})
            f.write(record.model_dump_json() + "\n")


# ===== memory_draft.jsonl 读写（带 turn 标记的归并缓冲） =====


def _memory_draft_path(agent_name: str) -> Path:
    return Path(character_path(agent_name, "memory_draft.jsonl"))


def read_memory_draft(agent_name: str) -> list[dict]:
    """返回 [{turn: int, text: str}, ...]，按写入顺序；非法行跳过。"""
    path = _memory_draft_path(agent_name)
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                record = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and isinstance(record.get("text"), str):
                records.append(record)
    return records


def append_memory_draft(agent_name: str, turn: int, text: str) -> None:
    """追加一条 draft 记录到 memory_draft.jsonl。"""
    path = _memory_draft_path(agent_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"turn": int(turn), "text": text}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def split_memory_draft_by_turn(
    agent_name: str,
    until_turn: int,
) -> tuple[list[dict], list[dict]]:
    """按 turn 切片，返回 (<=until_turn 的条目, 剩余条目)，不修改文件。"""
    records = read_memory_draft(agent_name)
    taken = [r for r in records if int(r.get("turn", 0)) <= until_turn]
    remaining = [r for r in records if int(r.get("turn", 0)) > until_turn]
    return taken, remaining


def rewrite_memory_draft(agent_name: str, records: list[dict]) -> None:
    """用 records 覆盖写 memory_draft.jsonl；records 为空则删除文件。"""
    path = _memory_draft_path(agent_name)
    if not records:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
