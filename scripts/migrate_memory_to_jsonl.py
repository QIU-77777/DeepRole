"""将旧 memory.md 一次性迁移为 memory.jsonl。

用法:
    uv run python scripts/migrate_memory_to_jsonl.py [--dry-run] [--agent NAME] [--force]

迁移后：
- memory.md 被重命名为 memory.md.bak
- 新写入的 memory.jsonl 可直接被 consolidation / indexer 使用
- 结尾调用 rebuild_memory_index() 同步向量库
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import OrderedDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from models import EpisodeMemory  # noqa: E402
from repository.memory_store import normalize
from repository.memory_store import append_memory_records, memory_jsonl_path  # noqa: E402


CHARACTERS_DIR = PROJECT_ROOT / "data" / "runtime" / "characters"


# ===== 旧 memory.md 解析工具（仅此迁移脚本使用） =====

_EVENT_FIELD_PATTERN = r"^\s*(?:-\s*)?(?:\*\*{field}\*\*|{field})：\s*(.*)$"
_MEMORY_FIELD_ORDER = ("时间", "地点", "在场", "关键词", "重要度", "内容")
_FIELD_LINE_PATTERN = re.compile(
    r"^\s*(?:-\s*)?(?:\*\*(时间|地点|在场|关键词|重要度|内容)\*\*|(时间|地点|在场|关键词|重要度|内容))：\s*(.*)$"
)
_DATE_HEADING_PATTERN = re.compile(r"^\s*##\s*(\d{1,2}月\d{1,2}日)(?:\s.*)?$")
_BOLD_HEADER_PATTERN = re.compile(r"^\s*(?:-\s*)?\*\*(.+?)\*\*：")


def _split_by_date(content: str) -> "OrderedDict[str, str]":
    sections: OrderedDict[str, str] = OrderedDict()
    current_date: str | None = None
    current_lines: list[str] = []
    for line in content.split("\n"):
        m = re.match(r"^##\s*(\d{1,2}月\d{1,2}日)(?:\s.*)?$", line.strip())
        if m:
            if current_date:
                body = "\n".join(current_lines).strip()
                sections[current_date] = (
                    sections[current_date] + "\n\n" + body if current_date in sections else body
                )
            current_date = m.group(1)
            current_lines = []
        elif current_date:
            current_lines.append(line)
    if current_date:
        body = "\n".join(current_lines).strip()
        sections[current_date] = (
            sections[current_date] + "\n\n" + body if current_date in sections else body
        )
    return sections


def _split_event_blocks(content: str) -> list[str]:
    stripped = (content or "").strip()
    if not stripped:
        return []
    return [block.strip() for block in re.split(r"\n\s*\n+", stripped) if block.strip()]


def _is_structured_memory_block(block_text: str) -> bool:
    lines = [line.rstrip() for line in (block_text or "").strip().splitlines() if line.strip()]
    if not lines:
        return False
    expected_index = 0
    current_field: str | None = None
    for line in lines:
        stripped = line.strip()
        if _DATE_HEADING_PATTERN.match(stripped):
            return False
        field_match = _FIELD_LINE_PATTERN.match(stripped)
        if field_match:
            field_name = field_match.group(1) or field_match.group(2)
            if expected_index >= len(_MEMORY_FIELD_ORDER) or field_name != _MEMORY_FIELD_ORDER[expected_index]:
                return False
            expected_index += 1
            current_field = field_name
            continue
        if _BOLD_HEADER_PATTERN.match(stripped):
            return False
        if current_field != _MEMORY_FIELD_ORDER[-1]:
            return False
    return expected_index == len(_MEMORY_FIELD_ORDER)


def _extract_event_field(event_text: str, field_name: str) -> str:
    pattern = re.compile(
        _EVENT_FIELD_PATTERN.format(field=re.escape(field_name)),
        re.MULTILINE,
    )
    match = pattern.search(event_text or "")
    return match.group(1).strip() if match else ""


def _parse_event_importance(event_text: str, default: int = 3) -> int:
    raw = _extract_event_field(event_text, "重要度")
    if not raw:
        return default
    match = re.search(r"\d+", raw)
    if not match:
        return default
    return max(1, min(5, int(match.group(0))))


def _parse_keywords(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(",", "、").replace("，", "、").split("、")]
    return [p for p in parts if p]


def _block_to_episode(date: str, block_text: str, memory_owner: str) -> EpisodeMemory:
    """把单个事件块解析成 EpisodeMemory；不合规范时退化为 content-only。

    title 在旧 markdown 里没有对应字段，留空由后续整理（或首次召回时的运行时 fallback）补。
    """
    if _is_structured_memory_block(block_text):
        return EpisodeMemory(
            date=date,
            time=_extract_event_field(block_text, "时间") or date,
            location=_extract_event_field(block_text, "地点"),
            participants=_extract_event_field(block_text, "在场"),
            keywords=_parse_keywords(_extract_event_field(block_text, "关键词")),
            importance=_parse_event_importance(block_text, default=3),
            content=_extract_event_field(block_text, "内容") or block_text.strip(),
            memory_owner=memory_owner,
            title="",
        )
    # 非规范块：整段落入 content，其它字段留空，importance 默认 3
    return EpisodeMemory(
        date=date,
        time=date,
        location="",
        participants="",
        keywords=[],
        importance=3,
        content=block_text.strip(),
        memory_owner=memory_owner,
        title="",
    )


def _convert_agent(agent_dir: Path) -> list[EpisodeMemory]:
    md_path = agent_dir / "memory.md"
    if not md_path.exists():
        return []
    content = normalize(md_path.read_text(encoding="utf-8"))
    if not content.strip():
        return []
    sections = _split_by_date(content)
    memory_owner = agent_dir.name
    episodes: list[EpisodeMemory] = []
    for date, body in sections.items():
        for block in _split_event_blocks(body):
            if not block.strip():
                continue
            episodes.append(_block_to_episode(date, block, memory_owner))
    return episodes


def _list_agents(filter_name: str | None) -> list[Path]:
    if not CHARACTERS_DIR.exists():
        return []
    agents: list[Path] = []
    for item in sorted(CHARACTERS_DIR.iterdir()):
        if not item.is_dir() or item.name == "narrator":
            continue
        if filter_name and item.name != filter_name:
            continue
        agents.append(item)
    return agents


async def _migrate(args: argparse.Namespace) -> int:
    agents = _list_agents(args.agent)
    if not agents:
        print("[迁移] 没有匹配的角色目录")
        return 1

    total_records = 0
    migrated: list[str] = []
    for agent_dir in agents:
        agent_name = agent_dir.name
        md_path = agent_dir / "memory.md"
        jsonl_path = memory_jsonl_path(agent_name)

        if not md_path.exists():
            print(f"[迁移] 跳过 {agent_name}: 无 memory.md")
            continue

        if jsonl_path.exists() and jsonl_path.stat().st_size > 0 and not args.force:
            print(f"[迁移] 跳过 {agent_name}: memory.jsonl 已存在（--force 覆盖）")
            continue

        episodes = _convert_agent(agent_dir)
        print(f"[迁移] {agent_name}: 解析到 {len(episodes)} 条事件")
        total_records += len(episodes)

        if args.dry_run:
            for ep in episodes[:3]:
                print(f"  · {ep.date} | {ep.time} | {ep.location} | 重要度={ep.importance}")
            if len(episodes) > 3:
                print(f"  · ... 共 {len(episodes)} 条")
            continue

        if args.force and jsonl_path.exists():
            jsonl_path.unlink()
        if episodes:
            append_memory_records(agent_name, episodes)

        bak_path = md_path.with_suffix(".md.bak")
        md_path.rename(bak_path)
        print(f"[迁移] {agent_name}: 写入 {jsonl_path.name}；原文件改名为 {bak_path.name}")
        migrated.append(agent_name)

    print(f"[迁移] 完成：共 {total_records} 条事件，涉及 {len(migrated)} 个角色")

    if not args.dry_run and migrated:
        print("[迁移] 重建向量索引 ...")
        from app.memory.indexer import rebuild_memory_index
        await rebuild_memory_index()
        print("[迁移] 向量索引重建完成")

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate memory.md → memory.jsonl")
    p.add_argument("--dry-run", action="store_true", help="只打印解析结果，不落盘")
    p.add_argument("--agent", default=None, help="只迁移指定角色")
    p.add_argument("--force", action="store_true", help="即使 memory.jsonl 已存在也覆盖")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_migrate(_parse_args())))
