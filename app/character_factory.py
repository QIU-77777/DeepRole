"""动态生成新角色：narrator 请求时给新人搭骨架。

流程：校验锚点 → 调 character_factory agent 生成
character_id/role/identity/goal/dynamic/behavior/voice/status → 写文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent_factory import get_character_factory_agent, reload_conversation_agent
from repository.sdk_runner import run_structured_agent
from app.llm_schema import LLMNewCharacterProfile, LLMNewCharacterRequest
from repository.llm.config import get_llm_config
from repository.log_config.routing import routing_logger
from repository.config import AGENT_RUN_TIMEOUT_SECONDS, CHARACTERS_DIR, get_agent_names
from models.character import extract_identity, get_display_name
from repository.status_file import extract_status_field
from repository.agent_files import read_agent_file
from repository.status_file import update_status


_RESERVED_NAMES = {"player", "narrator", ""}
_AGENT_NAME_MAX_LEN = 32
_STATUS_ORDER = [
    "身份",
    "心境",
    "和玩家的关系",
    "在意的事",
    "打算",
]

@dataclass(frozen=True, slots=True)
class CreatedCharacterInfo:
    character_id: str
    display_name: str
    identity: str


def _build_factory_user_message(spec: LLMNewCharacterRequest) -> str:
    narrator_status = read_agent_file("narrator", "status.md")
    current_time = extract_status_field(narrator_status, "当前时间").strip() or "（未知）"
    scene = extract_status_field(narrator_status, "场景").strip() or "（未知）"
    story_setting = read_agent_file("narrator", "soul.md").strip()

    spec_lines = [
        "<spec>",
        f"background_hint: {spec.background_hint}",
    ]
    if spec.name_hint.strip():
        spec_lines.append(f"name_hint: {spec.name_hint.strip()}")
    if spec.initial_location.strip():
        spec_lines.append(f"initial_location: {spec.initial_location.strip()}")
    spec_lines.append("</spec>")
    spec_block = "\n".join(spec_lines)

    blocks: list[str] = [
        f"<story_setting>\n{story_setting}\n</story_setting>",
        spec_block,
    ]
    blocks.append(
        "<world_now>\n"
        f"当前时间：{current_time}\n"
        f"当前场景：{scene}\n"
        "</world_now>"
    )
    return "\n\n".join(blocks)


def _validate_spec(spec: LLMNewCharacterRequest) -> str | None:
    """返回错误描述；None 表示校验通过。"""
    if not spec.background_hint.strip():
        return "background_hint 为空"
    return None


def _validate_creation_character_id(character_id: str) -> str | None:
    """返回错误描述；None 表示 character_factory 生成的 character_id 通过运行时校验。"""
    if not character_id:
        return f"非法 character_id: {character_id!r}"
    if len(character_id) > _AGENT_NAME_MAX_LEN:
        return f"character_id 过长: {character_id!r}"
    if character_id in _RESERVED_NAMES:
        return f"character_id 为保留名: {character_id!r}"
    existing = set(get_agent_names(include_narrator=True))
    if character_id in existing:
        return f"character_id 已存在: {character_id}"
    return None


def _write_status_md(
    agent_dir: Path,
    status: dict[str, str],
    display_name: str,
) -> None:
    """按 _STATUS_ORDER 顺序写 status.md；缺失字段用合理默认补齐。"""
    fields = {k: (v or "").strip() for k, v in status.items()}
    fields.pop("当前位置", None)
    if not fields.get("打算"):
        fields["打算"] = "- [ ] 【初次出现】找到合适的时机出现在场景里"

    ordered_keys = list(_STATUS_ORDER)
    for key in fields:
        if key not in ordered_keys:
            ordered_keys.append(key)

    lines = [f"# {display_name} 的状态", ""]
    for key in ordered_keys:
        lines.append(f"## {key}")
        lines.append(fields.get(key, ""))
        lines.append("")
    (agent_dir / "status.md").write_text("\n".join(lines), encoding="utf-8")




def _format_paragraph_block(items: list[str]) -> str:
    """渲染段落列表：各条之间空行分隔；空条目跳过。"""
    return "\n\n".join(item.strip() for item in items if item and item.strip())


def _format_voice_block(items: list[str]) -> str:
    """渲染 voice 样例：每句独立一行；空条目跳过。"""
    return "\n".join(item.strip() for item in items if item and item.strip())


def _build_soul_md(creation: LLMNewCharacterProfile) -> str:
    """按模板结构拼装 soul.md：role / identity / goal / past / habits / reactions / voice。"""
    past_block = _format_paragraph_block(creation.past)
    habits_block = _format_paragraph_block(creation.habits)
    reactions_block = _format_paragraph_block(creation.reactions)
    voice_block = _format_voice_block(creation.voice)

    parts = [
        f"<role>{creation.display_name}</role>",
        "",
        "<identity>",
        creation.identity,
        "</identity>",
        "",
        "<goal>",
        creation.goal,
        "</goal>",
    ]
    if past_block:
        parts.extend(["", "<past>", past_block, "</past>"])
    if habits_block:
        parts.extend(["", "<habits>", habits_block, "</habits>"])
    if reactions_block:
        parts.extend(["", "<reactions>", reactions_block, "</reactions>"])
    if voice_block:
        parts.extend(["", "<voice>", voice_block, "</voice>"])
    return "\n".join(parts).strip() + "\n"


def _append_to_narrator_locations(display_name: str, location: str) -> None:
    """把新角色的初始位置追加到 narrator/status.md 的「角色位置」列表。"""
    narrator_status = read_agent_file("narrator", "status.md")
    current = extract_status_field(narrator_status, "角色位置").strip()
    new_entry = f"- {display_name}：{location}"
    new_value = f"{current}\n{new_entry}" if current else new_entry
    update_status("narrator", "角色位置", new_value)


def _write_bootstrap_files(
    spec: LLMNewCharacterRequest,
    creation: LLMNewCharacterProfile,
    soul_content: str,
) -> None:
    agent_dir = CHARACTERS_DIR / creation.character_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    (agent_dir / "soul.md").write_text(soul_content, encoding="utf-8")

    _write_status_md(agent_dir, creation.initial_status, creation.display_name)

    if spec.initial_location.strip():
        _append_to_narrator_locations(creation.display_name, spec.initial_location.strip())


async def create_character(spec: LLMNewCharacterRequest) -> CreatedCharacterInfo | None:
    """孵化新角色；成功返回 CreatedCharacterInfo，失败返回 None 并记录日志。"""
    error = _validate_spec(spec)
    if error:
        label = spec.name_hint.strip() or spec.background_hint[:20] or "（未命名新角色）"
        routing_logger.warning(f"[character_factory] 拒绝生成 {label!r}：{error}")
        return None

    config = get_llm_config()
    try:
        creation = await run_structured_agent(
            agent=get_character_factory_agent(),
            user_input=_build_factory_user_message(spec),
            output_type=LLMNewCharacterProfile,
            timeout_seconds=AGENT_RUN_TIMEOUT_SECONDS,
            workflow_name="deeprole_character_factory",
            trace_metadata={
                "agent_name": "character_factory",
                "target": spec.name_hint.strip() or spec.background_hint[:20],
            },
            usage_agent="character_factory",
            usage_phase="agent_run",
            model_name=config["model_id"],
        )
    except Exception as e:
        label = spec.name_hint.strip() or spec.background_hint[:20] or "（未命名新角色）"
        routing_logger.error(f"[character_factory] 生成 {label!r} 失败: {e}")
        return None

    creation_id_error = _validate_creation_character_id(creation.character_id)
    if creation_id_error:
        routing_logger.error(
            "[character_factory] 生成 %r 失败：%s",
            creation.character_id,
            creation_id_error,
        )
        return None

    soul_content = _build_soul_md(creation)
    try:
        _write_bootstrap_files(spec, creation, soul_content)
    except Exception as e:
        routing_logger.error(f"[character_factory] 写入 {creation.character_id!r} 文件失败: {e}")
        return None

    try:
        for agent_name in get_agent_names(include_narrator=True):
            reload_conversation_agent(agent_name)
    except Exception as e:
        routing_logger.warning(f"[character_factory] 刷新对话 agents 失败: {e}")

    routing_logger.info(
        f"[character_factory] 生成 {creation.character_id!r}（{spec.background_hint[:30]}）"
    )
    return CreatedCharacterInfo(
        character_id=creation.character_id,
        display_name=get_display_name(creation.character_id, soul_content),
        identity=extract_identity(soul_content) or creation.identity.strip(),
    )
