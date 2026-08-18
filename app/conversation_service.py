"""角色对话编排：搜记忆 → 构建 prompt → 跑 SDK → 经 repo 写回。

吸收原 engine.character.Character 的 run / _build_prompt / _apply_updates。
实体（Character）只承载 name + soul；I/O 走 CharacterRepository。
"""

from typing import cast

from app.agent_factory import get_conversation_agent
from app.llm_schema import LLMCharacterOutput, LLMToolCall
from repository.sdk_runner import run_app_agent
from app.memory_query_builder import build_retrieval_queries
from app.prompt_builder import build_user_message
from repository.llm.config import get_llm_config
from repository.llm.embedding import embed_sync
from repository.log_config.routing import log_file_updates
from repository.log_config.routing import routing_logger
from app.memory.retrieval import search_memories, search_understandings
from models import Character, status_fields
from repository.config import HISTORY_RAW_SCAN_TURNS
from repository import intent_queue
from repository.character_repo import character_repo
from repository.history import load_conversation_history
from repository.status_file import FileUpdateResult
from repository.relationship_state import sync_relationship_from_status
from repository.spatial_state import read_spatial_state, write_spatial_state
from models.spatial import MapId, move_npc


class ConversationService:
    """单个角色的一次对话编排。无状态，直接调用 CharacterRepository 单例。"""

    async def run_turn(
        self,
        character: Character,
        user_input: str,
        raw_messages: list[dict] | None = None,
    ) -> LLMCharacterOutput:
        """搜记忆 → 构建 prompt → 运行 SDK → 写回文件，返回 LLMCharacterOutput。"""
        if raw_messages is None:
            raw_messages = load_conversation_history(turns=HISTORY_RAW_SCAN_TURNS)

        user_message = self._build_prompt(character, user_input, raw_messages)
        config = get_llm_config()
        output = await run_app_agent(
            get_conversation_agent(character.name),
            user_message,
            LLMCharacterOutput,
            workflow_name="deeprole_turn",
            usage_agent=character.name,
            model_name=config["model_id"],
        )
        self._apply_tool_calls(character.name, output.tool_calls)
        self._apply_updates(character.name, output)
        return output

    def _apply_tool_calls(self, agent_name: str, tool_calls: list[LLMToolCall]) -> None:
        """执行角色允许的语义工具；任何越权或坐标式调用都被拒绝。"""
        valid_maps = {"campus_center", "arts_hallway", "clubroom", "rooftop"}
        for call in tool_calls:
            if call.name != "move_npc" or call.npc_id != agent_name:
                routing_logger.warning(
                    "[%s] 拒绝越权空间工具调用: %s/%s",
                    agent_name,
                    call.name,
                    call.npc_id,
                )
                continue
            if call.destination not in valid_maps:
                routing_logger.warning(
                    "[%s] 拒绝非法空间目的地: %s",
                    agent_name,
                    call.destination,
                )
                continue
            state = read_spatial_state()
            updated = move_npc(
                state,
                npc_id=agent_name,
                destination=cast(MapId, call.destination),
            )
            write_spatial_state(updated)

    def _build_prompt(
        self,
        character: Character,
        user_input: str,
        raw_messages: list[dict],
    ) -> str:
        """组装角色 user message（含记忆与长期判断召回前缀）。"""
        name = character.name
        queries = build_retrieval_queries(name, user_input, raw_messages)

        # 两条 query 相同则只 embed 一次，复用同一个向量
        same_query = queries.understanding == queries.episode
        texts = [queries.episode] if same_query else [queries.episode, queries.understanding]
        try:
            vecs = embed_sync(texts)
            memory_qvec = vecs[0]
            understanding_qvec = vecs[0] if same_query else vecs[1]
        except Exception:
            memory_qvec = understanding_qvec = None

        relevant_memories = search_memories(
            name,
            queries.episode,
            qvec=memory_qvec,
            bm25_query=queries.episode_bm25,
        )
        relevant_understandings = search_understandings(
            name,
            queries.understanding,
            qvec=understanding_qvec,
            bm25_query=queries.understanding_bm25,
        )
        memories_block = (
            f"<relevant_memories>\n{relevant_memories}\n</relevant_memories>"
            if relevant_memories
            else ""
        )
        understandings_block = (
            f"<relevant_understandings>\n{relevant_understandings}\n</relevant_understandings>"
            if relevant_understandings
            else ""
        )
        message, _ = build_user_message(
            name,
            user_input,
            memories_block,
            understandings_prefix=understandings_block,
            raw_messages=raw_messages,
        )
        return message

    def _apply_updates(self, name: str, output: LLMCharacterOutput) -> None:
        """把 LLMCharacterOutput 的所有字段经 repo 落盘，并一次性记录结构化日志。"""
        results: list[FileUpdateResult] = []

        if output.memory:
            r = character_repo.append_memory(name, output.memory)
            if r is not None:
                results.append(r)

        results.extend(character_repo.apply_status_fields(name, output.status))
        relationship_entry = sync_relationship_from_status(name, output.status)
        if relationship_entry is not None:
            results.append(
                FileUpdateResult(
                    file="relationship_state.json",
                    target="和玩家的关系",
                    operation="replace",
                    after=relationship_entry.model_dump_json(ensure_ascii=False),
                )
            )

        for event_name in output.triggered:
            r = intent_queue.remove(name, status_fields.PLANS, event_name)
            if r is not None:
                results.append(r)

        for event_desc in output.add_event:
            r = intent_queue.add(name, status_fields.PLANS, event_desc)
            if r is not None:
                results.append(r)

        log_file_updates(name, results)


conversation_service = ConversationService()
