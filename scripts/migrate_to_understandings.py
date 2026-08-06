#!/usr/bin/env python3
"""Seed understanding.jsonl from legacy growth.md, user.md, and relations.md using LLM.

One-time migration: reads old file-based storage (growth.md / user.md / relations.md)
that characters may still have on disk from before those files were removed from the
runtime, and converts them to understanding.jsonl entries.

Idempotent: agents that already have understanding.jsonl are skipped.
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from pydantic import BaseModel, Field

from app.agent_factory import _build_agent
from repository.sdk_runner import run_structured_agent
from app.llm_schema import LLMUnderstandingEntry
from repository.llm.config import get_llm_config
from models import Understanding
from repository.memory_store import read_understandings, write_understandings
from repository.config import AGENT_RUN_TIMEOUT_SECONDS, CONSOLIDATION_MAX_TOKENS, CONSOLIDATION_TEMPERATURE, get_agent_names
from repository.agent_files import read_agent_file
from repository.vector_store import vector_store


# ---------------------------------------------------------------------------
# Inline parsers for legacy file formats
# ---------------------------------------------------------------------------

_GROWTH_LINE_RE = re.compile(r"^\[(P\d+)\|(.+?)\]\s*(.+)$")


def _parse_growth_md(content: str) -> list[dict[str, str]]:
    """Parse legacy growth.md format: [P001|dimension] content"""
    entries: list[dict[str, str]] = []
    for line in content.splitlines():
        m = _GROWTH_LINE_RE.match(line.strip())
        if m:
            entries.append({"id": m.group(1), "dimension": m.group(2).strip(), "content": m.group(3).strip()})
    return entries


def _parse_relations_md(content: str) -> dict[str, str]:
    """Parse legacy relations.md format: ## Target\ncontent"""
    sections: dict[str, str] = {}
    current_target = ""
    current_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_target and current_lines:
                sections[current_target] = "\n".join(current_lines).strip()
            current_target = line[3:].strip()
            current_lines = []
        elif current_target:
            current_lines.append(line)
    if current_target and current_lines:
        sections[current_target] = "\n".join(current_lines).strip()
    return sections


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

MIGRATION_PROMPT = r"""<task>
你负责从角色的成长记录、对玩家的认知档案、以及对其���角色的认知中，提取稳定的长期判断（Understanding）。

这是一次性数据迁移：将原本分散在多个文件中的认知，统一转换为 Understanding 节点。
</task>

<inputs>
你会收到三个来源的内容（可能部分为空）：
- <growth>：角色的行为偏移记录，格式为 `[P001|dimension] content`。
  每个 dimension（如"对玩家：克制关心→主动靠近"）记录了一种跨情境的行为变化，
  content 描述了触发原因和此后的默认行为。
- <user_profile>：角色对玩家的主观认知档案，格式为 markdown sections。
  包含基本信息、性格判断、相处规律等。
- <relations>：角色对其��角色的主观认知，每个 `## {角色名}` section 代表对一个角色的看法。
</inputs>

<extraction_rules>
从以上三个来源中提取所有可以独立存在的稳定认知。原则：

1. 每条认知应是一个跨情境成立的判断——将来角色需要判断人、关系、互动方式时能直接用上
2. subject 写认知对象：人（如"玩家"、"美月"）、关系（如"我和玩家的相处方式"）、
   互动模式或行为规律（如"我在压力下的应对方式"）
3. content 写一句面向未来的稳定判断（40-120 字），不描述具体事件经过，
   而是提炼出"以后判断这个对象或情境时应该记住什么"
4. keywords 用 1-5 个词概括核心对象和主题

各来源的具体处理：
- growth.md：每个 dimension 提炼为一条认知。
  例："对玩家：克制关心→主动靠近" → subject="我对玩家的靠近方式",
  content="我已经不再克制对玩家的关心，会更直接地靠近和表达。"
- user.md：提取对玩家的稳定印象和相处规律，基本信息（姓名/年龄/身份）不提取。
  每条 ## section 下的要点通常对应一条认知。
- relations.md：每个角色的 section 提炼 1-3 条核心认知。

去重：如果多个来源表达了同一认知，合并为一条，不重复输出。
</extraction_rules>

<output_format>
严格 JSON，无 markdown：
{"entries": [{"subject": "...", "keywords": ["词1", "词2"], "content": "一句稳定认知。", "linked_episodes": []}]}

linked_episodes 在迁移阶段始终为空数组。
无有效认知时返回 {"entries": []}。
</output_format>

<examples>
<example_growth>
输入 growth：
[P001|对玩家：克制关心→主动靠近] [2024-04-25] 因为玩家留下来听我说完，此后想要时会先承认，靠近他也越来越不犹豫。

输出：
{"entries": [{"subject": "我对玩家的靠近方式", "keywords": ["玩家", "靠近", "主动"], "content": "我已经不再克制对玩家的关心，想要靠近时会先承认，不会再把请求咽回去。", "linked_episodes": []}]}
</example_growth>

<example_user_profile>
输入 user_profile：
## 对方是什么人
- 他习惯在压力下先行动后解释，很少停下来讨论
- 他在人多的地方会不动声色地护着我

输出：
{"entries": [{"subject": "玩家在压力下的行为方式", "keywords": ["玩家", "压力", "行动"], "content": "他在压力下倾向于先行动再解释，不会停下来讨论；这是他面对突发情况的默认节奏。", "linked_episodes": []}, {"subject": "玩家在公开场合对我的保护", "keywords": ["玩家", "公开场合", "保护"], "content": "在人多的地方他会不动声色地把我护在里侧，但不会特别说出来。", "linked_episodes": []}]}
</example_user_profile>

<example_relations>
输入 relations：
## 莉莉丝
同班同学，会留意玩家的异常。和她保持距离但偶尔交换情报。

输出：
{"entries": [{"subject": "莉莉丝的角色定位", "keywords": ["莉莉丝", "同学", "情报"], "content": "莉莉丝是同班同学，会留意玩家的异常；我和她保持距离但偶尔交换情报。", "linked_episodes": []}]}
</example_relations>
</examples>
"""


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


class MigrationOutput(BaseModel):
    entries: list[LLMUnderstandingEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Migration logic
# ---------------------------------------------------------------------------


def _build_migration_input(agent_name: str) -> str | None:
    """Build LLM input from agent's legacy growth/user/relations files. Returns None if no source data."""
    parts: list[str] = []

    growth_content = read_agent_file(agent_name, "growth.md").strip()
    if growth_content:
        entries = _parse_growth_md(growth_content)
        if entries:
            lines = ["<growth>"]
            for entry in entries:
                lines.append(f"[{entry['id']}|{entry['dimension']}] {entry['content']}")
            lines.append("</growth>")
            parts.append("\n".join(lines))

    user_content = read_agent_file(agent_name, "user.md").strip()
    if user_content:
        parts.append(f"<user_profile>\n{user_content}\n</user_profile>")

    relations_content = read_agent_file(agent_name, "relations.md").strip()
    if relations_content:
        relations = _parse_relations_md(relations_content)
        if relations:
            lines = ["<relations>"]
            for target, content in relations.items():
                if content.strip():
                    lines.append(f"## {target}\n{content}")
            lines.append("</relations>")
            parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else None


async def migrate_agent(agent_name: str) -> int:
    existing = read_understandings(agent_name)
    if existing:
        print(
            f"  {agent_name}: already has {len(existing)} understandings, skipping",
            flush=True,
        )
        return 0

    user_input = _build_migration_input(agent_name)
    if not user_input:
        print(f"  {agent_name}: nothing to migrate", flush=True)
        return 0

    config = get_llm_config(temperature=CONSOLIDATION_TEMPERATURE)
    agent = _build_agent(
        name="understanding_migration",
        instructions=MIGRATION_PROMPT,
        config=config,
        output_type=MigrationOutput,
        max_tokens=CONSOLIDATION_MAX_TOKENS,
    )

    try:
        output = await run_structured_agent(
            agent=agent,
            user_input=user_input,
            output_type=MigrationOutput,
            timeout_seconds=AGENT_RUN_TIMEOUT_SECONDS,
            workflow_name="deeprole_migration",
            trace_metadata={"agent_name": agent_name},
            usage_agent=agent_name,
            usage_phase="migration.understandings",
            model_name=config["model_id"],
            error_label=f"{agent_name}.migration",
        )
    except Exception as e:
        print(f"  {agent_name}: LLM call failed: {e}", flush=True)
        return 0

    entries = output.entries
    if not entries:
        print(f"  {agent_name}: LLM returned no understandings", flush=True)
        return 0

    understandings: dict[str, Understanding] = {}
    for entry in entries:
        if not entry.content.strip():
            continue
        uid = uuid.uuid4().hex
        understandings[uid] = Understanding(
            id=uid,
            memory_owner=agent_name,
            subject=entry.subject.strip(),
            keywords=[k.strip() for k in entry.keywords if k.strip()],
            content=entry.content.strip(),
            linked_episodes=[],
        )

    if not understandings:
        print(f"  {agent_name}: no valid understandings after filtering", flush=True)
        return 0

    write_understandings(agent_name, understandings)
    for u in understandings.values():
        await vector_store.add_understanding(u)
    print(
        f"  {agent_name}: migrated {len(understandings)} understandings", flush=True
    )
    return len(understandings)


async def main() -> None:
    try:
        total = 0
        for name in get_agent_names(include_narrator=False):
            total += await migrate_agent(name)
        print(f"[understanding migration] total migrated: {total}", flush=True)
    finally:
        await vector_store.close()


if __name__ == "__main__":
    asyncio.run(main())
