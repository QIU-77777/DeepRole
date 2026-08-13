"""旁白编排：路由决策 + state_updater 调度。

吸收原 engine.character.Narrator 的 route / update_state 及其辅助方法。
LLM DTO 在此拆解为原始值后调 NarratorRepository 写回（storage 不依赖 agents）。
"""

import asyncio
import json
import re

from app.agent_factory import (
    get_conversation_agent,
    get_state_updater_agent,
)
from app.llm_schema import LLMNarratorOutput, LLMNewCharacterRequest, LLMStateUpdate
from repository.sdk_runner import run_app_agent
from app.prompt_builder import build_status_block, build_user_message
from repository.llm.config import get_llm_config
from repository.log_config.routing import log_file_updates, routing_logger
from models import status_fields
from repository.config import HISTORY_RAW_SCAN_TURNS, get_agent_names
from repository.narrator_output import extract_narrator_output, raw_message_text
from app.responses import clean_response, is_valid_response
from models.character import get_display_name
from repository.narrator_output import role_to_speaker
from repository.status_file import extract_status_field
from repository import intent_queue
from repository.agent_files import read_agent_file
from repository.history import load_conversation_history
from repository.narrator_repo import narrator_repo
from repository.status_file import update_status, update_status_allow_new_field

_NAME = "narrator"


class NarratorService:
    """旁白的路由 / 状态维护编排。无状态，直接调用 NarratorRepository 单例。"""

    # ── 路由 ──

    async def route(
        self,
        user_input: str,
        raw_messages: list[dict] | None = None,
    ) -> tuple[LLMNarratorOutput | None, bool]:
        """运行 narrator → 返回 (清洗后的结构化输出, is_valid)。

        new_characters 是 narrator 本轮请求孵化的新角色 spec 列表；
        此处只做 schema 层过滤（去重、去空描述），实际命名 / 孵化 / 目录校验由
        engine.character_factory.create_character 负责。
        """
        valid_agents = get_agent_names(include_narrator=False)
        if raw_messages is None:
            raw_messages = load_conversation_history(turns=HISTORY_RAW_SCAN_TURNS)

        try:
            output = await self._run_narrator(
                user_input,
                raw_messages,
                output_validator=lambda o: self._validate_route_output(o, valid_agents),
            )
        except asyncio.TimeoutError as e:
            routing_logger.error(f"[narrator] 调用超时: {e}")
            return None, False
        except Exception as e:
            routing_logger.error(f"[narrator] 调用失败: {e}")
            return None, False

        output = output.model_copy(
            update={
                "targets": [t for t in output.targets if t in valid_agents],
                "scene_description": self._sanitize_scene_description(output.scene_description),
                "new_characters": self._filter_new_characters(output.new_characters, valid_agents),
            }
        )

        if not output.targets and not output.new_characters:
            routing_logger.warning("narrator 响应缺少可用路由")
            return output, False

        is_valid = is_valid_response(output.scene_description, "narrator") and bool(
            output.targets or output.new_characters
        )
        if is_valid:
            self._write_scene(output)
        return output, is_valid

    def _write_scene(self, output: LLMNarratorOutput) -> None:
        """把 LLMNarratorOutput 的场景字段同步写入 narrator/status.md。

        场景 / 当前时间 / 角色位置 由 narrator 独占维护，state_updater 不写这些字段。
        """
        results = narrator_repo.write_scene(
            scene=output.location,
            current_time=f"{output.date} {output.time}",
            character_locations=output.character_locations,
        )
        log_file_updates(_NAME, results)

    @staticmethod
    def _validate_route_output(
        output: LLMNarratorOutput,
        existing_agents: list[str],
    ) -> None:
        """校验 schema 无法表达的运行时路由约束，失败交给 runner 重试。"""
        valid_agents = set(existing_agents)
        errors: list[str] = []

        for spec in output.new_characters:
            label = spec.name_hint.strip() or spec.background_hint.strip()[:20] or "new_character"
            if not spec.background_hint.strip():
                errors.append(f"{label!r} missing background_hint")

        if not any(t in valid_agents for t in output.targets) and not output.new_characters:
            errors.append("no valid targets or new characters")

        if errors:
            raise ValueError("; ".join(errors))

    @staticmethod
    def _filter_new_characters(
        specs: list[LLMNewCharacterRequest],
        existing_agents: list[str],
    ) -> list[LLMNewCharacterRequest]:
        """过滤 narrator 提交的 new_characters：去重、去空描述。"""
        kept: list[LLMNewCharacterRequest] = []
        seen: set[tuple[str, str]] = set()
        for spec in specs:
            name_hint = spec.name_hint.strip()
            background_hint = spec.background_hint.strip()
            dedupe_key = (name_hint, background_hint)
            label = name_hint or background_hint[:20] or "（未命名新角色）"
            if dedupe_key in seen:
                continue
            if not background_hint:
                routing_logger.warning(
                    f"[narrator] new_characters 中 {label!r} 缺 background_hint，跳过"
                )
                continue
            kept.append(
                LLMNewCharacterRequest(
                    name_hint=name_hint,
                    background_hint=background_hint,
                    initial_location=spec.initial_location.strip(),
                )
            )
            seen.add(dedupe_key)
        return kept

    async def _run_narrator(
        self,
        user_input: str,
        raw_messages: list[dict],
        *,
        output_validator=None,
    ) -> LLMNarratorOutput:
        """构建 prompt → 运行 narrator SDK，返回 LLMNarratorOutput。"""
        user_message, _ = build_user_message(_NAME, user_input, "", raw_messages=raw_messages)
        config = get_llm_config()
        sdk = get_conversation_agent(_NAME)
        return await run_app_agent(
            sdk,
            user_message,
            LLMNarratorOutput,
            workflow_name="deeprole_turn",
            usage_agent=_NAME,
            model_name=config["model_id"],
            output_validator=output_validator,
        )

    def _sanitize_scene_description(self, scene_description: str) -> str:
        """截断 narrator 输出中混入的角色发言，避免写入错误 canon。"""
        sanitized = clean_response(scene_description)
        valid_agents = get_agent_names(include_narrator=False)

        for agent_name in valid_agents:
            soul_content = read_agent_file(agent_name, "soul.md")
            display_name = get_display_name(agent_name, soul_content) if soul_content else None
            names = [agent_name]
            if display_name and display_name != agent_name:
                names.append(display_name)

            for name in names:
                dialogue_match = re.search(
                    rf"^{re.escape(name)}\s*[:：]", sanitized, re.MULTILINE | re.IGNORECASE
                )
                header_match = re.search(
                    rf"^##\s*{re.escape(name)}", sanitized, re.MULTILINE | re.IGNORECASE
                )
                match = dialogue_match or header_match
                if match:
                    routing_logger.warning(
                        f"[narrator] 场景描述中检测到角色台词 '{name}'，已截断"
                    )
                    sanitized = sanitized[: match.start()].strip()
                    break

        return sanitized

    # ── 回合结束后维护世界状态 ──

    async def update_state(self) -> None:
        """调用 state_updater 写回 narrator/status.md。"""
        user_message = self._build_state_updater_input()
        config = get_llm_config()
        try:
            output = await run_app_agent(
                get_state_updater_agent(),
                user_message,
                LLMStateUpdate,
                workflow_name="deeprole_state_update",
                usage_agent="state_updater",
                model_name=config["model_id"],
            )
        except Exception as e:
            routing_logger.error(f"[state_updater] 运行失败: {e}")
            return

        self._apply_state_updates(output)

    def _build_state_updater_input(self) -> str:
        narrator_status = build_status_block(_NAME)
        characters_status = self._format_characters_status()
        raw_messages = load_conversation_history(turns=5)
        latest_scene = next(
            (
                payload
                for msg in reversed(raw_messages)
                if (payload := extract_narrator_output(msg))
            ),
            {},
        )
        history_lines: list[str] = []
        for msg in raw_messages:
            role = msg.get("role", "unknown")
            content = raw_message_text(msg)
            if not content:
                continue
            content = "\n".join(line.rstrip() for line in content.splitlines())
            if len(content) > 900:
                content = content[:900].rstrip() + "..."
            history_lines.append(f"{role_to_speaker(role)}: {content}")
        recent_history = "\n\n".join(history_lines) if history_lines else "无"

        world_schedule_content = narrator_repo.read_world_schedule_text()

        parts: list[str] = []
        parts.append(f"<characters_status>\n{characters_status}\n</characters_status>")
        if world_schedule_content:
            parts.append(
                f"<world_schedule>\n{world_schedule_content.strip()}\n</world_schedule>"
            )
        if latest_scene:
            parts.append(
                "<latest_scene_json>\n"
                + json.dumps(latest_scene, ensure_ascii=False, indent=2)
                + "\n</latest_scene_json>"
            )
        parts.append(f"<current_narrator_status>\n{narrator_status}\n</current_narrator_status>")
        parts.append(f"<recent_history>\n{recent_history}\n</recent_history>")
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _format_characters_status() -> str:
        """提取所有角色的 goal + 身份/心境/在意的事/打算，供 state_updater 评估剧情状态与同步待触发事件。"""
        blocks: list[str] = []
        for agent_name in get_agent_names(include_narrator=False):
            status_content = read_agent_file(agent_name, "status.md")
            soul_content = read_agent_file(agent_name, "soul.md")
            display_name = get_display_name(agent_name, soul_content)
            lines: list[str] = []
            goal_match = re.search(r"<goal>(.*?)</goal>", soul_content or "", re.S)
            if goal_match:
                goal_text = " ".join(goal_match.group(1).split())
                lines.append(f"深层目标：{goal_text}")
            for field in status_fields.CHARACTER_STATUS_FIELDS:
                value = extract_status_field(status_content, field).strip()
                if value:
                    lines.append(f"{field}：{value}")
            plans = intent_queue.read_queue(agent_name, status_fields.PLANS)
            if plans:
                plans_text = "\n".join(f"- [ ] {x}" for x in plans)
                lines.append(f"{status_fields.PLANS}：{plans_text}")
            content = "\n".join(lines) if lines else "（暂无）"
            blocks.append(f"【{agent_name} / {display_name}】\n{content}")
        return "\n\n".join(blocks) if blocks else "无"

    def _apply_state_updates(self, output: LLMStateUpdate) -> None:
        """把 LLMStateUpdate 的字段 / 触发 / 新增事件经 repo 落盘，并记录结构化日志。"""
        results = []
        if output.narrative_focus:
            results.append(
                update_status(_NAME, status_fields.NARRATIVE_FOCUS, output.narrative_focus)
            )
        if output.recent_world_event:
            results.append(
                update_status_allow_new_field(
                    _NAME, status_fields.RECENT_WORLD_EVENT, output.recent_world_event
                )
            )

        schedule: dict | None = None
        if output.world_schedule_update:
            try:
                schedule = json.loads(output.world_schedule_update)
            except json.JSONDecodeError as e:
                routing_logger.warning(
                    "[state_updater] world_schedule_update JSON 解析失败，已忽略: %s",
                    e,
                )
        elif output.triggered_world_events:
            schedule = narrator_repo.read_world_schedule()

        if output.triggered_world_events and schedule is not None:
            names = set(output.triggered_world_events)
            for event in schedule.get("events", []):
                if event.get("name") in names and event.get("status") != "triggered":
                    event["status"] = "triggered"

        if schedule is not None:
            results.append(narrator_repo.write_world_schedule(schedule))

        for event_name in output.triggered:
            r = intent_queue.remove(_NAME, status_fields.PENDING_EVENTS, event_name)
            if r is not None:
                results.append(r)

        for event_desc in output.add_event:
            r = intent_queue.add(_NAME, status_fields.PENDING_EVENTS, event_desc)
            if r is not None:
                results.append(r)

        log_file_updates(_NAME, results)


narrator_service = NarratorService()
