"""角色「打算」/ 旁白「待触发事件」的类型化列表存储。

取代原先「把列表存进 status.md 的 ## 段、再用正则增删」的做法：每个 agent 一个 JSON
文件，内容是 `list[str]`（事件行，如「【三玖：散步的约定】描述」）。增删即 list 操作，
按【名】去重的不变量自然成立，不再需要白名单 / 禁止整段覆盖的守卫。渲染成 markdown 块供
prompt 注入，使 LLM 看到的格式与改造前一致。
"""

import json
import re
from pathlib import Path

from models import status_fields
from repository.config import character_path
from repository.status_file import FileUpdateResult

# section 标题 → 落盘文件名
_SECTION_FILES = {
    status_fields.PLANS: "intents.json",
    status_fields.PENDING_EVENTS: "pending_events.json",
}

_SENTINEL = "无"


def _queue_path(agent_name: str, section: str) -> Path:
    return Path(character_path(agent_name, _SECTION_FILES[section]))


def _event_name(item: str) -> str | None:
    """提取条目【】内的名称，无则返回 None。"""
    m = re.search(r"【(.+?)】", item)
    return m.group(1) if m else None


def read_queue(agent_name: str, section: str) -> list[str]:
    """读取队列；文件缺失或损坏返回空列表。"""
    path = _queue_path(agent_name, section)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _write_queue(agent_name: str, section: str, items: list[str]) -> None:
    path = _queue_path(agent_name, section)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add(agent_name: str, section: str, item: str) -> FileUpdateResult | None:
    """新增一条；哨兵「无」/空返回 None，【名】重复返回 skip 结果。新条目置于队首。"""
    stripped = item.strip()
    if stripped == _SENTINEL or not stripped:
        return None

    items = read_queue(agent_name, section)
    name = _event_name(stripped)
    if name is not None and any(f"【{name}】" in existing for existing in items):
        return FileUpdateResult(
            file=_SECTION_FILES[section],
            target=section,
            operation="skip",
            reason=f"【{name}】已存在，跳过",
        )

    items.insert(0, stripped)
    _write_queue(agent_name, section, items)
    return FileUpdateResult(
        file=_SECTION_FILES[section], target=section, operation="add", added=stripped
    )


def remove(agent_name: str, section: str, event_name: str) -> FileUpdateResult | None:
    """删除【event_name】对应的条目；未命中返回 None。"""
    items = read_queue(agent_name, section)
    kept = [item for item in items if f"【{event_name}】" not in item]
    if len(kept) == len(items):
        return None

    removed = "\n".join(item for item in items if f"【{event_name}】" in item)
    _write_queue(agent_name, section, kept)
    return FileUpdateResult(
        file=_SECTION_FILES[section], target=section, operation="remove", removed=removed
    )


def render(items: list[str], section: str) -> str:
    """把队列渲染成 status 注入用的 markdown 段；空队列返回空串。"""
    if not items:
        return ""
    lines = "\n".join(f"- [ ] {item}" for item in items)
    return f"## {section}\n{lines}"
