"""对话编排流程。

职责：UI 适配 + 一次性编排。具体的路由 / 状态更新 / 记忆写回都封装在
Narrator / Character 实体方法里，这里只负责把编排串起来。
"""

from app.agent_factory import get_choices_agent
from repository.sdk_runner import run_structured_agent
from app.llm_schema import LLMChoices, LLMNarratorOutput, LLMNewCharacterRequest
from app.conversation_service import conversation_service
from app.character_factory import CreatedCharacterInfo, create_character
from repository.character_repo import character_repo
from app.prompt_builder import build_history_transcript
from repository.llm.config import get_llm_config
from repository.log_config.routing import routing_logger
from repository.config import AGENT_RUN_TIMEOUT_SECONDS, HISTORY_RAW_SCAN_TURNS
from app.responses import clean_response, is_valid_response
from repository.history import load_conversation_history


async def generate_choices(
    narrator_output: LLMNarratorOutput, agent_responses: list[tuple[str, str]]
) -> list[str]:
    """根据当前场景和角色回应生成玩家可选行动（2-3 个）。"""
    raw_messages = load_conversation_history(turns=HISTORY_RAW_SCAN_TURNS)
    parts: list[str] = []
    history, _ = build_history_transcript("narrator", raw_messages)
    if history:
        parts.append(f"【近期对话】\n{history}")
    parts.append(f"【当前场景JSON】\n{narrator_output.model_dump_json()}")
    for name, response in agent_responses:
        parts.append(f"【{name}】\n{response}")

    config = get_llm_config()
    try:
        output = await run_structured_agent(
            agent=get_choices_agent(),
            user_input="\n\n".join(parts),
            output_type=LLMChoices,
            timeout_seconds=AGENT_RUN_TIMEOUT_SECONDS,
            workflow_name="deeprole_choices",
            trace_metadata=None,
            usage_agent="choices",
            usage_phase="agent_run",
            model_name=config["model_id"],
        )
    except Exception as exc:
        routing_logger.warning("[choices] 生成选项失败: %s", exc)
        return []
    return output.choices[:3]


async def bootstrap_new_characters(
    specs: list[LLMNewCharacterRequest],
    targets: list[str],
) -> tuple[list[str], list[CreatedCharacterInfo]]:
    """孵化 narrator 请求的新角色，并把成功创建的角色补入最终 targets。

    返回 (最新 targets, 成功创建的角色信息列表)。
    """
    if not specs:
        return targets, []

    created: list[CreatedCharacterInfo] = []
    for spec in specs:
        created_info = await create_character(spec)
        if created_info:
            created.append(created_info)

    created_ids = [info.character_id for info in created]
    missing_created = [character_id for character_id in created_ids if character_id not in targets]
    for character_id in missing_created:
        routing_logger.info(
            "[bootstrap_new_characters] 新角色 %s 创建成功，已补入最终 targets",
            character_id,
        )
    return list(dict.fromkeys(targets + created_ids)), created


async def run_agent_in_scene(
    agent_name: str,
    targets: list[str],
    user_input: str,
    *,
    visible_to: list[str] | None = None,
) -> str | None:
    """在场景上下文中运行单个角色并广播响应。"""
    from repository.message_router import message_router

    character = character_repo.load(agent_name)
    output = await conversation_service.run_turn(character, user_input)
    response = clean_response(output.content)
    if is_valid_response(response, agent_name):
        await message_router.broadcast_agent_response(
            agent_name,
            targets,
            response,
            visible_to=visible_to,
        )
    return response
