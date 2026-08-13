"""对话运行时的 prompt 构建（角色 / narrator user message、history 窗口）。"""

from __future__ import annotations

import re

from models import status_fields
from repository.config import HISTORY_HIGH, HISTORY_LOW, get_agent_names
from repository.narrator_output import raw_message_text
from models.character import extract_identity, get_display_name
from repository.narrator_output import role_to_speaker
from repository.status_file import extract_status_field
from repository import intent_queue
from repository.agent_files import (
    read_agent_file,
    read_sidecar_json,
    write_sidecar_json,
)
from repository.runtime_state import extract_player_name, read_player_name


# ---------------------------------------------------------------------------
# 对话历史窗口（原 engine/history.py）
# ---------------------------------------------------------------------------


def _msg_turn(msg: dict) -> int:
    return int(msg.get("turn") or 0)


def _apply_high_low_watermark(
    visible: list[dict],
    anchor_turn: int,
    high: int,
    low: int,
) -> tuple[int, list[dict], bool]:
    """按 turn 号锚定的高低水位缓冲，以 distinct turn 数为截断单位。

    返回 (新锚点 turn, 保留消息, 是否触发截断)。
    """
    if not visible:
        return anchor_turn, [], False

    kept = [m for m in visible if _msg_turn(m) >= anchor_turn]
    if not kept:
        kept = visible
        anchor_turn = _msg_turn(kept[0])

    was_truncated = False
    distinct_turns = sorted({_msg_turn(m) for m in kept})
    if len(distinct_turns) > high:
        kept_turns = set(distinct_turns[-low:])
        kept = [m for m in kept if _msg_turn(m) in kept_turns]
        anchor_turn = min(kept_turns)
        was_truncated = True

    return anchor_turn, kept, was_truncated


def _get_windowed_visible_messages(
    agent_name: str, raw_messages: list[dict]
) -> tuple[list[dict], bool]:
    """按 agent 维度应用高低水位窗口，并持久化窗口起点。返回 (消息列表, 是否触发截断)。"""
    if not raw_messages:
        return [], False

    if agent_name != "narrator":
        visible = [m for m in raw_messages if agent_name in m.get("visible_to", [])]
    else:
        visible = list(raw_messages)

    if not visible:
        return [], False

    sidecar = read_sidecar_json(agent_name, ".history_window_state.json")
    anchor_turn = max(0, int(sidecar.get("start_turn", 0)))
    new_anchor, kept, was_truncated = _apply_high_low_watermark(
        visible, anchor_turn, HISTORY_HIGH, HISTORY_LOW
    )
    write_sidecar_json(
        agent_name,
        ".history_window_state.json",
        {"start_turn": new_anchor},
    )
    return kept, was_truncated


def build_history_transcript(
    agent_name: str,
    raw_messages: list[dict],
    *,
    inject_turn_markers: bool = False,
) -> tuple[str, bool]:
    """将 JSONL 原始消息转为历史文本。

    高低水位窗口与可见性过滤仍照常应用。
    inject_turn_markers=True 时在每条前加 `[turn=N]` 前缀，供 detector / 整理输入使用。
    返回 (文本, 是否触发截断)。
    """
    visible, was_truncated = _get_windowed_visible_messages(agent_name, raw_messages)

    if not visible:
        return "", False

    lines: list[str] = []
    for idx, msg in enumerate(visible):
        role = msg.get("role", "unknown")
        content = re.sub(r"\n+", "\n", raw_message_text(msg))
        if not content:
            continue
        prefix = ""
        if inject_turn_markers:
            turn = msg.get("turn")
            if isinstance(turn, int) and turn > 0:
                prefix = f"[turn={turn}] "
        lines.append(f"{prefix}{role_to_speaker(role)}: {content}")

    return "\n\n".join(lines), was_truncated


# ---------------------------------------------------------------------------
# 对话 prompt 构建
# ---------------------------------------------------------------------------


def build_characters_block(tag: str = "characters") -> str:
    """列出所有主要角色的 id / 显示名 / identity。

    narrator user message 用 tag='fields'，state_updater 用默认 tag='characters'。
    """
    rows: list[str] = []
    for name in get_agent_names(include_narrator=False):
        soul = read_agent_file(name, "soul.md")
        display = get_display_name(name, soul)
        identity = extract_identity(soul)
        suffix = f"｜{identity}" if identity else ""
        rows.append(f"- {name}: {display}{suffix}")
    if not rows:
        return ""
    return f"<{tag}>\n" + "\n".join(rows) + f"\n</{tag}>"


def build_player_block(latest_user_input: str) -> str:
    """Expose the player's display name to narrator prompts when known."""
    player_name = read_player_name() or extract_player_name(latest_user_input)
    if not player_name:
        return ""
    return f"<player>\ndisplay_name: {player_name}\n</player>"


def render_player_relations() -> str:
    """现算旁白视角的「和玩家的关系」汇总：从各角色 status.md 源头读取，不落盘。

    取代原 narrator_repo.sync_player_relations 的物化做法（避免副本过期）。无可汇总时返回空串。
    """
    lines: list[str] = []
    for agent_name in get_agent_names(include_narrator=False):
        status_content = read_agent_file(agent_name, "status.md")
        relation = " ".join(extract_status_field(status_content, status_fields.PLAYER_RELATION).split())
        if not relation:
            continue
        soul_content = read_agent_file(agent_name, "soul.md")
        lines.append(f"- {get_display_name(agent_name, soul_content)}：{relation}")
    if not lines:
        return ""
    return f"## {status_fields.PLAYER_RELATION}\n" + "\n".join(lines)


def build_status_block(agent_name: str) -> str:
    """组装注入 prompt 的 status 文本：散文 status.md + 现算的队列段 / 派生关系。

    打算 / 待触发事件 来自 intent_queue（渲染回原 markdown 段，使 LLM 输入格式不变）；
    narrator 的「和玩家的关系」为现算派生。
    """
    base = read_agent_file(agent_name, "status.md").strip()
    extra: list[str] = []
    if agent_name == "narrator":
        pending = intent_queue.read_queue(agent_name, status_fields.PENDING_EVENTS)
        extra.append(intent_queue.render(pending, status_fields.PENDING_EVENTS))
        extra.append(render_player_relations())
    else:
        plans = intent_queue.read_queue(agent_name, status_fields.PLANS)
        extra.append(intent_queue.render(plans, status_fields.PLANS))
    return "\n\n".join(block for block in [base, *extra] if block)


def build_user_message(
    agent_name: str,
    latest_user_input: str,
    memory_prefix: str,
    raw_messages: list[dict] | None = None,
    understandings_prefix: str = "",
) -> tuple[str, bool]:
    """构建单条大 user message，按稳定度排序上下文。返回 (消息文本, 是否触发历史截断)。"""
    parts: list[str] = []

    is_narrator = agent_name == "narrator"
    history, was_truncated = build_history_transcript(agent_name, raw_messages or [])
    status_content = build_status_block(agent_name)

    parts.append(build_player_block(latest_user_input) if is_narrator else "")
    parts.append(build_characters_block(tag="fields") if is_narrator else "")
    parts.append(f"最近对话历史:\n\n{history}" if history else "")
    parts.append(
        f"<status>\n{status_content.strip()}\n</status>" if status_content.strip() else ""
    )
    parts.append(memory_prefix if memory_prefix else "")
    parts.append(understandings_prefix if understandings_prefix else "")
    parts.append(f"玩家新消息：{latest_user_input}")

    return "\n\n---\n\n".join(part for part in parts if part), was_truncated
