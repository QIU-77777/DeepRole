"""记忆整理流程的 prompt 组装。"""

from __future__ import annotations

import re

from repository.narrator_output import raw_message_text
from models.character import get_display_name
from repository.narrator_output import role_to_speaker
from repository.agent_files import read_agent_file


def _get_owner_display_name(agent_name: str) -> str:
    """读取当前整理对象的显示名，失败时回退到 agent_name。"""
    soul_content = read_agent_file(agent_name, "soul.md") or ""
    return get_display_name(agent_name, soul_content)


def render_raw_history(
    raw_messages: list[dict],
    *,
    visible_to: str | None = None,
    turn_le: int | None = None,
    turn_ge: int | None = None,
) -> str:
    """无窗口副作用地渲染 raw 消息为 `[turn=N] 角色: 内容` 行。

    - visible_to 非空：按消息 visible_to 过滤。
    - turn_le / turn_ge：turn 区间过滤（含端点）。
    - 不对旁白做"只留最后一条"折叠（consolidation 需要完整上下文）。
    """
    lines: list[str] = []
    for msg in raw_messages:
        if visible_to is not None and visible_to not in msg.get("visible_to", []):
            continue
        turn = msg.get("turn")
        if turn_le is not None and (not isinstance(turn, int) or turn > turn_le):
            continue
        if turn_ge is not None and (not isinstance(turn, int) or turn < turn_ge):
            continue
        content = re.sub(r"\n+", "\n", raw_message_text(msg))
        if not content:
            continue
        role = msg.get("role", "unknown")
        prefix = f"[turn={turn}] " if isinstance(turn, int) and turn > 0 else ""
        lines.append(f"{prefix}{role_to_speaker(role)}: {content}")
    return "\n\n".join(lines)


def build_episode_closure_payload(
    history_transcript: str,
) -> str:
    """构造 EpisodeClosureDetector 的 user payload。

    Args:
        history_transcript: 渲染好的近若干 turn 的对话（带 `[turn=N]` 前缀）。
    """
    return f"<recent_history>\n{history_transcript}\n</recent_history>"


def build_memory_owner_block(agent_name: str) -> str:
    """构造 EpisodeMemoryGenerator 的记忆主体约束块。"""
    display_name = _get_owner_display_name(agent_name)
    no_self_ref = f"\u201c{display_name}\u201d\u6216\u201c{display_name}\uff08\u6211\uff09\u201d"
    lines = [
        "<memory_owner>",
        f"当前整理对象：{display_name}（agent_name={agent_name}）",
        f"最终输出：{display_name} 的长期记忆",
        "代词约定：",
        f"- 我 = {display_name}",
        "- 他 = 玩家",
        "- 其他角色保留实名",
        "- 旁白 = narrator 的场景描述，提供场景信息",
        "- 旁白中的\u201c你 / 你家 / 你这边\u201d默认指玩家一侧，不指当前整理对象",
        "写作要求：",
        "- 最终输出始终写成当前整理对象的记忆",
        f"- 用\u201c我\u201d指代 {display_name}",
        "- 用\u201c他\u201d指代玩家",
        "- 其他角色直接使用实名",
        "- 用 raw_dialogue 校正事实、顺序和台词",
        "- 输出前先把输入材料中的视角转换成当前整理对象视角，再写入时间 / 地点 / 在场 / 内容",
        f"- 不要把当前整理对象再写成{no_self_ref}",
        "- 不要保留\u201c玩家\u201d这种材料标签；要改写成当前视角下自然成立的称呼",
        "</memory_owner>",
    ]
    return "\n".join(lines)


def build_episode_memory_generator_payload(
    agent_name: str,
    memory_entries: str,
    raw_dialogue: str = "",
) -> str:
    """构造 EpisodeMemoryGenerator 的 user payload。"""
    payload_parts = [
        build_memory_owner_block(agent_name),
        f"<memory_entries>\n{memory_entries}\n</memory_entries>",
    ]
    if raw_dialogue:
        payload_parts.append(f"<raw_dialogue>\n{raw_dialogue}\n</raw_dialogue>")
    return "\n\n".join(payload_parts)


def build_understanding_patch_payload(
    existing_understandings_text: str,
    new_record_json: str,
) -> str:
    """构造 UnderstandingPatch 的 user payload。"""
    return (
        f"<existing_entries>\n{existing_understandings_text}\n</existing_entries>\n\n"
        f"<new_record>\n{new_record_json}\n</new_record>"
    )
